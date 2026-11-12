"""Production runs: making a variation or a base from its recipe, and completing it.

Opening a run fills its lines from its item's recipe (``hoard.runs``); a line
records the lot it consumes, or one is assumed when the run is completed.
Completing a run moves stock and costs it; reopening takes that back. A run
keeps the initiative it was opened from.

Without :data:`~hoard.api.deps.SeesCostsDep` no cost is sent. Whether a run is
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
from sqlmodel import col, or_, select

from hoard import runs
from hoard.api.adjustments import (
    NOT_FOUND,
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
    Item,
    Lot,
    ProductionRun,
    ProductionRunLine,
    Recipe,
    RunStatus,
)
from seneschal_core.api import (
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
    quantity: Decimal
    #: The lot it consumes: recorded, or assumed when the run was completed.
    lot: RunLot | None
    assumed: bool
    #: Until the run is completed, the lots of its item at the run's location
    #: that hold stock, the earliest expiry first.
    lots: list[RunLot]
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


class RunRead(RunSummary):
    recipe_id: int | None
    labour_minutes: int | None
    expires_on: date | None
    output_lot: RunLot | None
    lines: list[RunLineRead]
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
            id=item_id, kind=item.kind, name=await item_name(db, item_id), attributes={}
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
    [summary] = await _summary(db, [row], sees_costs)
    lines = (
        await db.exec(
            select(ProductionRunLine)
            .where(ProductionRunLine.run_id == run.id)
            .order_by(col(ProductionRunLine.id))
            .execution_options(populate_existing=True)
        )
    ).all()
    items = await _describe(db, [line.item_id for line in lines])
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
    consumed: dict[int, Decimal] = {}
    if sees_costs:
        found = await db.exec(
            select(col(Adjustment.production_run_line_id), col(Adjustment.unit_cost)).where(
                Adjustment.production_run_id == run.id
            )
        )
        consumed = {line_id: cost for line_id, cost in found.all() if line_id is not None}
    return RunRead(
        **summary,
        recipe_id=run.recipe_id,
        labour_minutes=run.labour_minutes,
        expires_on=run.expires_on,
        output_lot=_lot(lots[run.output_lot_id]) if run.output_lot_id in lots else None,
        lines=[
            RunLineRead(
                id=line.id,
                item=items[line.item_id],
                quantity=line.quantity,
                lot=_lot(lots[line.lot_id]) if line.lot_id in lots else None,
                assumed=line.assumed,
                lots=choices.get(line.item_id, []),
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
        created_at=run.created_at,
        created_by=run.created_by,
        updated_at=run.updated_at,
        updated_by=run.updated_by,
    )


def _refused(refused: runs.RunRefused) -> Exception:
    """The answer to a change :mod:`hoard.runs` refused."""
    codes = {runs.RunError.RUN_NO_RECIPE, runs.RunError.RUN_LOT_MISMATCH}
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
) -> Page[RunSummary]:
    """Production runs, those not completed first, then the latest."""
    query = _runs()
    counted = select(func.count()).select_from(ProductionRun)
    if run_status is not None:
        query = query.where(col(ProductionRun.status) == run_status)
        counted = counted.where(col(ProductionRun.status) == run_status)
    rows = await db.exec(
        query.order_by(
            col(ProductionRun.status) == RunStatus.COMPLETED,
            col(ProductionRun.completed_on).desc().nulls_first(),
            col(ProductionRun.id).desc(),
        )
        .offset(page.offset)
        .limit(page.limit)
    )
    summaries = await _summary(db, list(rows.all()), sees_costs)
    return Page(
        items=[RunSummary(**summary) for summary in summaries],
        total=(await db.exec(counted)).one(),
    )


@router.get("/{run_id}", response_model_exclude_unset=True, responses=NOT_FOUND)
async def read_run(db: OrganizationSessionDep, sees_costs: SeesCostsDep, run_id: int) -> RunRead:
    """A run, its lines and their lots, and the lot it made."""
    return await _read(db, run_id, sees_costs)


class RunCreate(BaseModel):
    #: A variation or a base with a recipe.
    item_id: int
    #: The next free ``R-`` code when not given.
    code: Code | None = None
    planned_batches: Positive = Decimal(1)
    #: The main location when not given.
    location_id: int | None = None
    #: Started today when true.
    start: bool = False
    deadline_on: date | None = None
    expires_on: date | None = None
    assignee_ref: Member | None = None
    labour_minutes: Minutes | None = None


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model_exclude_unset=True,
    responses={
        **NOT_FOUND,
        409: {"description": "RUN_CODE_TAKEN"},
        422: {"description": "RUN_NO_RECIPE"},
    },
)
async def create_run(
    db: WriteSessionDep, session: SessionDep, sees_costs: SeesCostsDep, body: RunCreate
) -> RunRead:
    """Open a run in the page's initiative, its lines filled from its item's recipe."""
    await known(db, Item, body.item_id)
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
    )
    try:
        await runs.open_run(db, run)
    except runs.RunRefused as refused:
        raise _refused(refused) from None
    await db.commit()
    return await _read(db, run.id, sees_costs)


class RunUpdate(BaseModel):
    """A change to a run: only what is sent. Its planned batches and location
    change only until it is completed."""

    code: Code | None = None
    planned_batches: Positive | None = None
    location_id: int | None = None
    #: Null, as the four below may be, takes it off.
    deadline_on: date | None = None
    expires_on: date | None = None
    assignee_ref: Member | None = None
    labour_minutes: Minutes | None = None
    started_on: date | None = None


@router.patch(
    "/{run_id}",
    response_model_exclude_unset=True,
    responses={**NOT_FOUND, 409: {"description": "RUN_COMPLETED, RUN_CODE_TAKEN"}},
)
async def update_run(
    db: WriteSessionDep, sees_costs: SeesCostsDep, run_id: int, body: RunUpdate
) -> RunRead:
    """Change a run. A completed run's code and expiry carry to the lot it made."""
    sent = changes(body, nullable={"deadline_on", "expires_on", "assignee_ref", "labour_minutes"})
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
