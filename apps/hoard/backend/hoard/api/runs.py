"""Production runs: making a variation or a base from its recipe, and completing it.

Opening a run fills its lines from its item's recipe (``hoard.runs``), over
the batches planned or the batches that use an amount of one of its lines; a
draft is planned the same way without saving it (``POST /runs/preview``),
each line beside what is usable at the run's location. A line records the lot
it consumes, or one is assumed when the run is completed.
Completing a run moves stock and costs it; reopening takes that back. A run
keeps the initiative it was opened from.

A run is read with its own notes and its recipe's method, the order whose
auto-manufacture made it (the order whose lines name it), and the orders that
shipped what it made: from its output lot, or a lot it was moved into, as
the lot's forward trace finds them (``hoard.trace``).

Without :data:`~hoard.api.deps.SeesCostsDep` no cost or price is sent: not
what it consumed, its labour or an order line's total. Whether a run is
uncosted is sent to everyone: it says a cost is missing, not what one is.
"""

from __future__ import annotations

from collections.abc import Awaitable, Collection
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Query, status
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy import func
from sqlmodel import col, exists, or_, select

from hoard import runs, summary, trace, units
from hoard.api.adjustments import (
    NOT_FOUND,
    NOTES,
    AdjustmentItem,
    Responses,
    changes,
    describe_items,
    known,
    location_or_main,
)
from hoard.api.deps import HoardError, SeesCostsDep
from hoard.api.shapes import Page, PageDep
from hoard.events import item_name
from hoard.records import (
    PLACES,
    Adjustment,
    Base,
    Image,
    Item,
    Location,
    Lot,
    Material,
    Order,
    OrderLine,
    OrganizationSettings,
    ProductionRun,
    ProductionRunLine,
    Recipe,
    RecipeLine,
    RunStatus,
    ShipmentLine,
)
from hoard.units import UnitRefused
from seneschal_core.api import (
    ErrorCode,
    OrganizationSessionDep,
    SessionDep,
    WriteSessionDep,
    refuse,
)
from seneschal_core.db import OrganizationSession, WriteSession

router = APIRouter(prefix="/runs", tags=["runs"])

#: A quantity planned or made: more than none.
Positive = Annotated[Decimal, Field(gt=0, max_digits=20, decimal_places=6)]
NonNegative = Annotated[Decimal, Field(ge=0, max_digits=20, decimal_places=6)]
Code = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]
Minutes = Annotated[int, Field(ge=0)]
Member = Annotated[str, StringConstraints(min_length=1, max_length=200)]
CONFLICT: Responses = {
    409: {"description": "RUN_COMPLETED, RUN_NOT_COMPLETED, RUN_STARTED, LOT_IN_USE"}
}


class RunLot(BaseModel):
    """A lot a run made, or one a line consumes or may."""

    id: int
    number: str
    expires_on: date | None
    #: What is left of it.
    balance: Decimal


class RunLineRead(BaseModel):
    id: int
    item: AdjustmentItem
    #: The material's or the base's SKU.
    sku: str | None
    #: Its picture's id, which reads it (``/items/{id}/picture/{picture_id}``).
    picture_id: int | None
    #: The unit its quantity and its unit cost are in: its item's tracking unit.
    unit_id: int | None
    quantity: Decimal
    #: The lot it consumes: recorded, or assumed when the run was completed.
    lot: RunLot | None
    assumed: bool
    #: Until the run is completed, the lots of its item at the run's location
    #: that hold stock, the earliest expiry first.
    lots: list[RunLot]
    #: Until the run is completed, how much more it takes than is usable at
    #: the run's location (``hoard.summary``); null when it is not short.
    short: Decimal | None = None
    #: What one unit cost on the run's date, once it is completed; sent only to
    #: a member who sees costs, as is ``cost``.
    unit_cost: Decimal | None = None
    cost: Decimal | None = None


class RunSummary(BaseModel):
    id: int
    #: Its output lot's number.
    code: str
    item: AdjustmentItem
    status: RunStatus
    location_id: int
    initiative_id: int | None
    planned_batches: Decimal
    #: The planned batches times what a batch of its recipe makes; null once
    #: the recipe is deleted.
    planned_quantity: Decimal | None
    actual_quantity: Decimal | None
    assignee_ref: str | None
    started_on: date | None
    deadline_on: date | None
    completed_on: date | None
    #: Something it consumed had no cost on its date.
    uncosted: bool
    #: What it consumed cost, once it is completed; sent only to a member who
    #: sees costs, as is ``unit_cost``, that divided by what it made.
    cost: Decimal | None = None
    unit_cost: Decimal | None = None


class RunOrder(BaseModel):
    """An order a run names."""

    id: int
    code: str


class RunShipped(BaseModel):
    """What an order shipped of what a run made, on one of its lines."""

    #: The shipment line it went out on.
    id: int
    order: RunOrder
    ordered_on: date
    #: The store's number for its customer, for a store's order.
    store_customer: str | None
    shipped_on: date
    #: What the line sells: the variation.
    item: AdjustmentItem
    #: How much of the run's lot it shipped.
    quantity: Decimal
    #: Whether its lot was assumed rather than recorded.
    assumed: bool
    #: That quantity at the line's price; sent only to a member who sees costs,
    #: and never for a bundle's part, whose bundle's line carries the price.
    total: Decimal | None = None


class RunRead(RunSummary):
    recipe_id: int | None
    #: Whether its recipe changed since the run was opened: its lines stay as
    #: they were filled.
    recipe_changed: bool
    #: Its recipe's method, as the recipe stands now; null once it is deleted.
    recipe_notes: str | None
    notes: str | None
    labour_minutes: int | None
    #: Its labour minutes at the labour rate (``hoard.summary.recipe_cost``):
    #: null without either, and sent only to a member who sees costs. Its lot's
    #: cost leaves it out, as pricing adds labour on top.
    labour_cost: Decimal | None = None
    #: The order whose auto-manufacture made it.
    made_for: RunOrder | None
    expires_on: date | None
    output_lot: RunLot | None
    lines: list[RunLineRead]
    #: What orders shipped of what it made, once it is completed.
    shipped: list[RunShipped]
    created_at: datetime
    created_by: str | None
    updated_at: datetime
    updated_by: str | None


def _lot(lot: Lot) -> RunLot:
    return RunLot(id=lot.id, number=lot.number, expires_on=lot.expires_on, balance=lot.balance)


async def _describe(
    db: OrganizationSession, item_ids: Collection[int]
) -> dict[int, AdjustmentItem]:
    """Each item described, bases among them."""
    described = await describe_items(db, item_ids)
    for item_id in set(item_ids) - described.keys():
        item = await db.get_one(Item, item_id)
        described[item_id] = AdjustmentItem(
            id=item_id,
            kind=item.kind,
            name=await item_name(db, item_id),
            attributes={},
            archived=False,
        )
    return described


async def _costs(db: OrganizationSession, run_ids: Collection[int]) -> dict[int, Decimal]:
    """What each completed run consumed cost, by run."""
    totals = await db.exec(
        select(Adjustment.production_run_id, func.sum(-Adjustment.change * Adjustment.unit_cost))
        .where(
            col(Adjustment.production_run_id).in_(list(run_ids)),
            col(Adjustment.production_run_line_id).is_not(None),
        )
        .group_by(col(Adjustment.production_run_id))
    )
    return {run_id: Decimal(total).quantize(PLACES) for run_id, total in totals.all() if run_id}


async def _summary(
    db: OrganizationSession,
    rows: list[tuple[ProductionRun, Decimal | None]],
    sees_costs: bool,
) -> list[dict[str, Any]]:
    """Each run's summary fields, with what a batch of its recipe makes."""
    items = await _describe(db, [run.item_id for run, _ in rows])
    costs = await _costs(db, [run.id for run, _ in rows]) if sees_costs else {}
    summaries = []
    for run, batch_quantity in rows:
        fields = run.model_dump(
            include={
                "id",
                "code",
                "status",
                "location_id",
                "initiative_id",
                "planned_batches",
                "actual_quantity",
                "assignee_ref",
                "started_on",
                "deadline_on",
                "completed_on",
                "uncosted",
            }
        )
        fields |= {
            "item": items[run.item_id],
            "planned_quantity": None
            if batch_quantity is None
            else (batch_quantity * run.planned_batches).quantize(PLACES),
        }
        if sees_costs and run.status is RunStatus.COMPLETED:
            cost = costs.get(run.id, Decimal(0))
            fields["cost"] = cost
            if run.actual_quantity:
                fields["unit_cost"] = (cost / run.actual_quantity).quantize(PLACES)
        summaries.append(fields)
    return summaries


def _runs() -> Any:
    return select(ProductionRun, col(Recipe.batch_quantity)).outerjoin(
        Recipe, col(Recipe.id) == col(ProductionRun.recipe_id)
    )


async def _read(db: OrganizationSession, run_id: int, sees_costs: bool) -> RunRead:
    """A run as the database holds it, with its lines and their lots."""
    row = (
        await db.exec(
            _runs().where(col(ProductionRun.id) == run_id).execution_options(populate_existing=True)
        )
    ).first()
    if row is None:
        raise refuse(status.HTTP_404_NOT_FOUND, HoardError.NOT_FOUND)
    run = row[0]
    [fields] = await _summary(db, [row], sees_costs)
    recipe = None if run.recipe_id is None else await db.get_one(Recipe, run.recipe_id)
    lines = (
        await db.exec(
            select(ProductionRunLine)
            .where(ProductionRunLine.run_id == run.id)
            .order_by(col(ProductionRunLine.id))
            .execution_options(populate_existing=True)
        )
    ).all()
    item_ids = [line.item_id for line in lines]
    items = await _describe(db, item_ids)
    materials = await db.exec(
        select(Material.id, Material.sku).where(col(Material.id).in_(item_ids))
    )
    bases = await db.exec(select(Base.id, Base.sku).where(col(Base.id).in_(item_ids)))
    skus = dict([*materials.all(), *bases.all()])
    pictures = await db.exec(
        select(Image.item_id, Image.id).where(col(Image.item_id).in_(item_ids))
    )
    pictured = {item_id: picture_id for item_id, picture_id in pictures.all() if item_id}
    counted = await units.tracking_units(db, item_ids)
    held = (
        await db.exec(
            select(Lot)
            .where(
                or_(
                    col(Lot.id).in_([line.lot_id for line in lines if line.lot_id]),
                    col(Lot.id) == run.output_lot_id,
                )
            )
            .execution_options(populate_existing=True)
        )
    ).all()
    lots = {lot.id: lot for lot in held}
    choices: dict[int, list[RunLot]] = {}
    if run.status is not RunStatus.COMPLETED:
        available = await db.exec(
            select(Lot)
            .where(
                col(Lot.item_id).in_([line.item_id for line in lines]),
                Lot.location_id == run.location_id,
                col(Lot.balance) > 0,
            )
            .order_by(col(Lot.expires_on).asc().nulls_last(), col(Lot.dated_on), col(Lot.id))
        )
        for lot in available.all():
            choices.setdefault(lot.item_id, []).append(_lot(lot))
    shortages: dict[int, dict[str, Decimal | None]] = {}
    if run.status is not RunStatus.COMPLETED:
        stock = await summary.stock(db, [line.item_id for line in lines], date.today())
        for item_id, at in stock.items():
            taken = sum((line.quantity for line in lines if line.item_id == item_id), Decimal(0))
            can_use = at.get(run.location_id, summary.Stock()).usable
            shortages[item_id] = {"short": runs.short(taken, can_use)}
    consumed: dict[int, Decimal] = {}
    if sees_costs:
        found = await db.exec(
            select(col(Adjustment.production_run_line_id), col(Adjustment.unit_cost)).where(
                Adjustment.production_run_id == run.id
            )
        )
        consumed = {line_id: cost for line_id, cost in found.all() if line_id is not None}
        settings = (await db.exec(select(OrganizationSettings))).first()
        fields["labour_cost"] = summary.recipe_cost(
            [],
            [],
            {},
            makes=Decimal(1),
            batches=Decimal(1),
            minutes=run.labour_minutes,
            rate=None if settings is None else settings.labour_rate,
        ).labour
    made_for = (
        await db.exec(
            select(Order.id, Order.code)
            .join(OrderLine, col(OrderLine.order_id) == Order.id)
            .where(OrderLine.production_run_id == run.id)
        )
    ).first()
    return RunRead(
        **fields,
        recipe_id=run.recipe_id,
        recipe_changed=await _recipe_changed(db, run, recipe),
        recipe_notes=None if recipe is None else recipe.notes,
        notes=run.notes,
        labour_minutes=run.labour_minutes,
        made_for=None if made_for is None else RunOrder(id=made_for[0], code=made_for[1]),
        expires_on=run.expires_on,
        output_lot=_lot(lots[run.output_lot_id]) if run.output_lot_id in lots else None,
        lines=[
            RunLineRead(
                id=line.id,
                item=items[line.item_id],
                sku=skus.get(line.item_id),
                picture_id=pictured.get(line.item_id),
                unit_id=counted.get(line.item_id),
                quantity=line.quantity,
                lot=_lot(lots[line.lot_id]) if line.lot_id in lots else None,
                assumed=line.assumed,
                lots=choices.get(line.item_id, []),
                **shortages.get(line.item_id, {}),
                **(
                    {
                        "unit_cost": consumed[line.id],
                        "cost": (consumed[line.id] * line.quantity).quantize(PLACES),
                    }
                    if line.id in consumed
                    else {}
                ),
            )
            for line in lines
        ],
        shipped=[]
        if run.output_lot_id is None
        else await _shipped(db, run.output_lot_id, sees_costs),
        created_at=run.created_at,
        created_by=run.created_by,
        updated_at=run.updated_at,
        updated_by=run.updated_by,
    )


async def _shipped(db: OrganizationSession, lot_id: int, sees_costs: bool) -> list[RunShipped]:
    """What orders shipped of a run's output lot, or of a lot it was moved
    into, in the order its forward trace finds them."""
    sent = (await trace.forward(db, lot_id)).shipped()
    found = await db.exec(
        select(ShipmentLine.id, OrderLine, Order)
        .join(OrderLine, col(OrderLine.id) == ShipmentLine.order_line_id)
        .join(Order, col(Order.id) == OrderLine.order_id)
        .where(col(ShipmentLine.id).in_([step.line_id for step in sent]))
    )
    lines = {line_id: (line, order) for line_id, line, order in found.all()}
    items = await _describe(db, [step.item_id for step in sent])
    shipped = []
    for step in sent:
        line_id = step.line_id or 0
        line, order = lines[line_id]
        shipped.append(
            RunShipped(
                id=line_id,
                order=RunOrder(id=order.id, code=order.code),
                ordered_on=order.ordered_on,
                store_customer=order.store_customer,
                shipped_on=step.dated_on,
                item=items[step.item_id],
                quantity=step.quantity,
                assumed=step.assumed,
                **(
                    {"total": (line.price * step.quantity).quantize(PLACES)}
                    if sees_costs and line.bundle_line_id is None
                    else {}
                ),
            )
        )
    return shipped


async def _recipe_changed(
    db: OrganizationSession, run: ProductionRun, recipe: Recipe | None
) -> bool:
    """Whether the recipe a run was filled from was saved since it was opened."""
    if recipe is None:
        return False
    lines = await db.exec(
        select(func.max(RecipeLine.created_at)).where(RecipeLine.recipe_id == recipe.id)
    )
    return any(
        saved is not None and saved > run.created_at for saved in (recipe.updated_at, lines.one())
    )


def _refused(refused: runs.RunRefused) -> Exception:
    """The answer to a change :mod:`hoard.runs` refused."""
    codes = {
        runs.RunError.RUN_NO_RECIPE,
        runs.RunError.RUN_LOT_MISMATCH,
        runs.RunError.RUN_NOT_IN_RECIPE,
        *units.UnitError,
    }
    code = status.HTTP_422_UNPROCESSABLE_CONTENT if refused.code in codes else 409
    return refuse(code, refused.code)


async def _free_code(db: WriteSession, code: str, run_id: int | None = None) -> str:
    """``code``, refused when another run has it."""
    taken = await db.exec(
        select(ProductionRun.id).where(
            ProductionRun.code == code, col(ProductionRun.id).is_distinct_from(run_id)
        )
    )
    if taken.first() is not None:
        raise refuse(status.HTTP_409_CONFLICT, runs.RunError.RUN_CODE_TAKEN)
    return code


@router.get("", response_model_exclude_unset=True)
async def list_runs(
    db: OrganizationSessionDep,
    sees_costs: SeesCostsDep,
    page: PageDep,
    run_status: Annotated[RunStatus | None, Query(alias="status")] = None,
    item_id: Annotated[int | None, Query()] = None,
    used_item_id: Annotated[int | None, Query()] = None,
) -> Page[RunSummary]:
    """Production runs, those not completed first, then the latest: of one
    status, making one item, or with a line using one item, when asked."""
    conditions = []
    if run_status is not None:
        conditions.append(col(ProductionRun.status) == run_status)
    if item_id is not None:
        conditions.append(col(ProductionRun.item_id) == item_id)
    if used_item_id is not None:
        conditions.append(
            exists().where(
                col(ProductionRunLine.run_id) == ProductionRun.id,
                col(ProductionRunLine.item_id) == used_item_id,
            )
        )
    rows = await db.exec(
        _runs()
        .where(*conditions)
        .order_by(
            col(ProductionRun.status) == RunStatus.COMPLETED,
            col(ProductionRun.completed_on).desc().nulls_first(),
            col(ProductionRun.id).desc(),
        )
        .offset(page.offset)
        .limit(page.limit)
    )
    summaries = await _summary(db, list(rows.all()), sees_costs)
    counted = select(func.count()).select_from(ProductionRun).where(*conditions)
    return Page(
        items=[RunSummary(**fields) for fields in summaries],
        total=(await db.exec(counted)).one(),
    )


@router.get("/{run_id}", response_model_exclude_unset=True, responses=NOT_FOUND)
async def read_run(db: OrganizationSessionDep, sees_costs: SeesCostsDep, run_id: int) -> RunRead:
    """A run, its lines and their lots, and the lot it made."""
    return await _read(db, run_id, sees_costs)


class RunUse(BaseModel):
    """Making as much as an amount of one of the recipe's lines allows: the
    run plans the batches that take exactly that amount of it."""

    #: The item, one of the recipe's lines.
    item_id: int
    #: How much of it to use. A preview takes what is usable of it at the
    #: run's location when none is sent; opening a run needs one.
    amount: Positive | None = None
    #: The unit the amount is in: the item's tracking unit when not sent.
    unit_id: int | None = None


class RunPlan(BaseModel):
    """What a run makes: a variation or a base with a recipe, at a location
    (the main one when not given), over its planned batches or the batches
    that ``use`` an amount of one line."""

    item_id: int
    planned_batches: Positive = Decimal(1)
    location_id: int | None = None
    use: RunUse | None = None


class RunPreviewLine(BaseModel):
    item: AdjustmentItem
    #: The unit its quantity is in: its item's tracking unit.
    unit_id: int | None
    quantity: Decimal
    #: What is usable of it at the run's location: available, less what its
    #: expired lots hold.
    usable: Decimal
    #: How much more it takes than is usable; null when it is not short.
    short: Decimal | None


class RunUseRead(BaseModel):
    """The line a run makes as much of as it allows."""

    item_id: int
    #: Its item's tracking unit, which the amounts are in.
    unit_id: int | None
    #: What the run uses of it.
    amount: Decimal
    #: What is usable of it at the run's location: the amount when none was sent.
    usable: Decimal
    #: What its lots past their expiry date hold there, which is not usable.
    expired: Decimal
    #: While another line is short, the most of it what is usable lets the run use.
    most: Decimal | None


class RunPreview(BaseModel):
    """A run as it would be opened, saving nothing."""

    batches: Decimal
    #: What it makes, in its output's unit.
    quantity: Decimal
    #: The unit it makes: a base's; null for a variation.
    unit_id: int | None
    lines: list[RunPreviewLine]
    #: The most batches what is usable at the location makes; null for a
    #: recipe with no lines.
    most_batches: Decimal | None
    use: RunUseRead | None


class RunCreate(RunPlan):
    #: The next free ``R-`` code when not given.
    code: Code | None = None
    #: Started today when true.
    start: bool = False
    deadline_on: date | None = None
    expires_on: date | None = None
    assignee_ref: Member | None = None
    labour_minutes: Minutes | None = None
    notes: str | None = NOTES


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model_exclude_unset=True,
    responses={
        **NOT_FOUND,
        409: {"description": "RUN_CODE_TAKEN"},
        422: {
            "description": "RUN_NO_RECIPE, RUN_NOT_IN_RECIPE, AMOUNT_TOO_SMALL, "
            "UNIT_NOT_CONVERTIBLE, REQUEST_INVALID"
        },
    },
)
async def create_run(
    db: WriteSessionDep, session: SessionDep, sees_costs: SeesCostsDep, body: RunCreate
) -> RunRead:
    """Open a run in the page's initiative, its lines filled from its item's
    recipe over its planned batches, or over the batches that ``use`` exactly
    an amount of one line."""
    await known(db, Item, body.item_id)
    use = None
    if body.use is not None:
        if body.use.amount is None:
            raise refuse(status.HTTP_422_UNPROCESSABLE_CONTENT, ErrorCode.REQUEST_INVALID)
        use = (body.use.item_id, await _tracked(db, body.use, body.use.amount))
    run = ProductionRun(
        code=await _free_code(db, body.code) if body.code else await runs.next_code(db),
        item_id=body.item_id,
        status=RunStatus.IN_PROGRESS if body.start else RunStatus.NOT_STARTED,
        location_id=await location_or_main(db, body.location_id),
        initiative_id=session.initiative_id,
        planned_batches=body.planned_batches,
        deadline_on=body.deadline_on,
        expires_on=body.expires_on,
        assignee_ref=body.assignee_ref,
        labour_minutes=body.labour_minutes,
        notes=body.notes or None,
    )
    try:
        await runs.open_run(db, run, use=use)
    except runs.RunRefused as refused:
        raise _refused(refused) from None
    await db.commit()
    return await _read(db, run.id, sees_costs)


async def _tracked(db: OrganizationSession, use: RunUse, amount: Decimal) -> Decimal:
    """``amount`` of the item ``use`` names, in its tracking unit."""
    try:
        return await units.in_tracking_unit(db, use.item_id, amount, use.unit_id)
    except UnitRefused as refused:
        raise refuse(status.HTTP_422_UNPROCESSABLE_CONTENT, refused.code) from None


@router.post(
    "/preview",
    responses={
        **NOT_FOUND,
        422: {
            "description": "RUN_NO_RECIPE, RUN_NOT_IN_RECIPE, AMOUNT_TOO_SMALL, "
            "UNIT_NOT_CONVERTIBLE"
        },
    },
)
async def preview_run(db: OrganizationSessionDep, body: RunPlan) -> RunPreview:
    """A run as it would be opened, each line beside what is usable at its
    location, saving nothing. Making as much as one line allows, with no
    amount sent, uses what is usable of it."""
    await known(db, Item, body.item_id)
    if body.location_id is not None:
        location_id = (await known(db, Location, body.location_id)).id
    else:
        main = (await db.exec(select(Location.id).where(Location.main))).first()
        if main is None:
            raise refuse(status.HTTP_404_NOT_FOUND, HoardError.NOT_FOUND)
        location_id = main
    today = date.today()
    made_in = (await units.tracking_units(db, [body.item_id])).get(body.item_id)
    use, using = None, None
    if body.use is not None:
        held = await summary.stock_at(db, body.use.item_id, location_id, today)
        can_use = held.usable
        amount = (
            await _tracked(db, body.use, body.use.amount)
            if body.use.amount is not None
            else max(can_use, Decimal(0))
        )
        using = RunUseRead(
            item_id=body.use.item_id,
            unit_id=(await units.tracking_units(db, [body.use.item_id])).get(body.use.item_id),
            amount=amount,
            usable=can_use,
            expired=held.expired,
            most=None,
        )
        if amount <= 0:
            return RunPreview(
                batches=Decimal(0),
                quantity=Decimal(0),
                unit_id=made_in,
                lines=[],
                most_batches=None,
                use=using,
            )
        use = (body.use.item_id, amount)
    try:
        plan = await runs.plan(db, body.item_id, location_id, body.planned_batches, use, today)
    except runs.RunRefused as refused:
        raise _refused(refused) from None
    counted = await units.tracking_units(db, [line.item_id for line in plan.lines])
    described = await _describe(db, [line.item_id for line in plan.lines])
    return RunPreview(
        batches=plan.batches,
        quantity=plan.makes,
        unit_id=made_in,
        lines=[
            RunPreviewLine(
                item=described[line.item_id],
                unit_id=counted.get(line.item_id),
                quantity=line.quantity,
                usable=line.usable,
                short=line.short,
            )
            for line in plan.lines
        ],
        most_batches=plan.most,
        use=None if using is None else using.model_copy(update={"most": plan.most_use}),
    )


class RunUpdate(BaseModel):
    """A change to a run: only what is sent. Its planned batches and location
    change only until it is completed."""

    code: Code | None = None
    planned_batches: Positive | None = None
    location_id: int | None = None
    #: Null, as the four below and the notes may be, takes it off.
    deadline_on: date | None = None
    expires_on: date | None = None
    assignee_ref: Member | None = None
    labour_minutes: Minutes | None = None
    started_on: date | None = None
    notes: str | None = NOTES


@router.patch(
    "/{run_id}",
    response_model_exclude_unset=True,
    responses={**NOT_FOUND, 409: {"description": "RUN_COMPLETED, RUN_CODE_TAKEN"}},
)
async def update_run(
    db: WriteSessionDep, sees_costs: SeesCostsDep, run_id: int, body: RunUpdate
) -> RunRead:
    """Change a run. A completed run's code and expiry carry to the lot it made."""
    sent = changes(
        body, nullable={"deadline_on", "expires_on", "assignee_ref", "labour_minutes", "notes"}
    )
    run = await known(db, ProductionRun, run_id)
    if "code" in sent:
        await _free_code(db, sent["code"], run.id)
    try:
        if "planned_batches" in sent:
            await runs.plan_batches(db, run, sent.pop("planned_batches"))
        if "location_id" in sent:
            runs.not_completed(run)
            sent["location_id"] = await location_or_main(db, sent["location_id"])
    except runs.RunRefused as refused:
        raise _refused(refused) from None
    for name, value in sent.items():
        setattr(run, name, value)
    if run.output_lot_id is not None:
        lot = await db.get_one(Lot, run.output_lot_id)
        lot.number, lot.expires_on = run.code, run.expires_on
    await db.commit()
    return await _read(db, run.id, sees_costs)


async def _changed(
    db: WriteSession, run: ProductionRun, change: Awaitable[None], sees_costs: bool
) -> RunRead:
    """Commit a change :mod:`hoard.runs` made, or answer why it refused."""
    try:
        await change
    except runs.RunRefused as refused:
        raise _refused(refused) from None
    await db.commit()
    return await _read(db, run.id, sees_costs)


class RunStart(BaseModel):
    #: Today when not given.
    started_on: date | None = None


@router.post(
    "/{run_id}/start", response_model_exclude_unset=True, responses={**NOT_FOUND, **CONFLICT}
)
async def start_run(
    db: WriteSessionDep, sees_costs: SeesCostsDep, run_id: int, body: RunStart
) -> RunRead:
    """Start a run that has not started."""
    run = await known(db, ProductionRun, run_id)
    return await _changed(db, run, runs.start(db, run, body.started_on or date.today()), sees_costs)


class RunComplete(BaseModel):
    #: How much it made, in its output's unit.
    actual_quantity: NonNegative
    #: Today when not given.
    completed_on: date | None = None


@router.post(
    "/{run_id}/complete", response_model_exclude_unset=True, responses={**NOT_FOUND, **CONFLICT}
)
async def complete_production_run(
    db: WriteSessionDep, sees_costs: SeesCostsDep, run_id: int, body: RunComplete
) -> RunRead:
    """Complete a run: what its lines consumed out, from their lots or assumed
    ones, and what it made into its output lot."""
    run = await known(db, ProductionRun, run_id)
    completing = runs.complete(db, run, body.actual_quantity, body.completed_on or date.today())
    return await _changed(db, run, completing, sees_costs)


@router.post(
    "/{run_id}/reopen", response_model_exclude_unset=True, responses={**NOT_FOUND, **CONFLICT}
)
async def reopen_run(db: WriteSessionDep, sees_costs: SeesCostsDep, run_id: int) -> RunRead:
    """Reopen a completed run, taking back what it moved, while nothing else
    draws on the lot it made."""
    run = await known(db, ProductionRun, run_id)
    return await _changed(db, run, runs.reopen(db, run), sees_costs)


@router.delete(
    "/{run_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={**NOT_FOUND, **CONFLICT},
)
async def delete_production_run(db: WriteSessionDep, run_id: int) -> None:
    """Delete a run that is not completed."""
    run = await known(db, ProductionRun, run_id)
    try:
        await runs.delete_run(db, run)
    except runs.RunRefused as refused:
        raise _refused(refused) from None
    await db.commit()


class RunLineUpdate(BaseModel):
    """A change to a line: only what is sent."""

    quantity: Positive | None = None
    #: The lot it consumes; null to have one assumed.
    lot_id: int | None = None


@router.patch(
    "/{run_id}/lines/{line_id}",
    response_model_exclude_unset=True,
    responses={**NOT_FOUND, **CONFLICT, 422: {"description": "RUN_LOT_MISMATCH"}},
)
async def update_run_line(
    db: WriteSessionDep,
    sees_costs: SeesCostsDep,
    run_id: int,
    line_id: int,
    body: RunLineUpdate,
) -> RunRead:
    """Change what a line of a run not yet completed consumes, or from which lot."""
    sent = changes(body, nullable={"lot_id"})
    run = await known(db, ProductionRun, run_id)
    line = await known(db, ProductionRunLine, line_id)
    if line.run_id != run.id:
        raise refuse(status.HTTP_404_NOT_FOUND, HoardError.NOT_FOUND)
    if sent.get("lot_id") is not None:
        await known(db, Lot, sent["lot_id"])
    try:
        runs.not_completed(run)
        if "lot_id" in sent:
            await runs.record_lot(db, run, line, sent["lot_id"])
    except runs.RunRefused as refused:
        raise _refused(refused) from None
    if "quantity" in sent:
        line.quantity = sent["quantity"]
    await db.commit()
    return await _read(db, run.id, sees_costs)
