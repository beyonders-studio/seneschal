"""Purchases of materials and products from suppliers, their lines, and
receiving them.

A purchase names its supplier by an Initiative contact's id. A line buys a
material or a product's variation, so many purchase units that its purchase
conversion makes so many tracking units (``hoard.units``); a base or a
bundle's variation is refused ``PURCHASE_LINE_KIND``. Receiving a line,
or part of one, makes its lot and its purchase adjustment on the day it arrived
(``hoard.purchases``); every change to a purchase or its lines re-derives what
its received lines cost. A purchase past the day it was expected with lines
still on order is overdue.

Without :data:`~hoard.api.deps.SeesCostsDep` no unit cost, discount, shipping,
tax or total is sent, and a purchase that sends one is refused ``COSTS_HIDDEN``.
"""

from __future__ import annotations

from collections.abc import Collection
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Query, status
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlmodel import col, exists, select

from hoard import codes, events, purchases, units
from hoard.api.adjustments import (
    NOT_FOUND,
    NOTES,
    AdjustmentItem,
    NonNegative,
    Responses,
    changes,
    describe_items,
    known,
    location_or_main,
)
from hoard.api.deps import HoardError, SeesCostsDep
from hoard.api.shapes import Page, PageDep
from hoard.purchases import PurchaseError, PurchaseRefused
from hoard.records import (
    PLACES,
    Item,
    Lot,
    Material,
    Product,
    Purchase,
    PurchaseLine,
    Variation,
)
from hoard.stock import StockRefused
from seneschal_core.api import OrganizationSessionDep, SessionDep, WriteSessionDep, refuse
from seneschal_core.db import OrganizationSession, WriteSession

router = APIRouter(prefix="/purchases", tags=["purchases"])

#: A quantity bought: more than none.
Positive = Annotated[Decimal, Field(gt=0, max_digits=20, decimal_places=6)]
Code = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]
#: The fields of a purchase that hold money.
PURCHASE_COSTS = frozenset({"discount", "shipping", "tax"})
#: A line naming an item that can't be bought, or a quantity too small to keep.
UNBOUGHT: Responses = {422: {"description": "PURCHASE_LINE_KIND, AMOUNT_TOO_SMALL"}}
CONFLICT: Responses = {
    409: {
        "description": "PURCHASE_LINE_RECEIVED, PURCHASE_LINE_NOT_RECEIVED, LOT_IN_USE, "
        "RECEIVED_BEFORE_PURCHASE"
    }
}


class PurchaseLot(BaseModel):
    """The lot a received line made."""

    id: int
    number: str
    expires_on: date | None
    notes: str | None


class PurchaseLineRead(BaseModel):
    id: int
    #: The material or the product's variation it buys.
    item: AdjustmentItem
    #: The material's or the variation's SKU.
    sku: str | None
    #: The material's category, or the variation's product's.
    category_id: int | None
    purchase_quantity: Decimal
    tracking_quantity: Decimal
    #: Sent only to a member who sees costs, as are the others below.
    unit_cost: Decimal | None = None
    #: What one tracking unit cost, with its share of discount and shipping.
    landed_cost: Decimal | None = None
    #: The day it arrived; none while it is on order.
    received_on: date | None
    lot: PurchaseLot | None


class PurchaseRead(BaseModel):
    id: int
    code: str
    dated_on: date
    #: The supplier: an Initiative contact's id.
    supplier_contact_id: int | None
    location_id: int
    paid: bool
    #: The day it is due.
    expected_on: date | None
    #: Past the day it was expected, with lines still on order.
    overdue: bool
    notes: str | None
    discount: Decimal | None = None
    shipping: Decimal | None = None
    tax: Decimal | None = None
    #: Lines, less discount, with shipping and tax.
    total: Decimal | None = None
    lines: list[PurchaseLineRead]
    created_at: datetime
    created_by: str | None
    updated_at: datetime
    updated_by: str | None


class PurchaseSummary(BaseModel):
    id: int
    code: str
    dated_on: date
    supplier_contact_id: int | None
    location_id: int
    paid: bool
    expected_on: date | None
    overdue: bool
    lines: int
    received: int
    total: Decimal | None = None


def _total(purchase: Purchase, value: Decimal) -> Decimal:
    return (value - purchase.discount + purchase.shipping + purchase.tax).quantize(PLACES)


async def _read(db: OrganizationSession, purchase: Purchase, sees_costs: bool) -> PurchaseRead:
    """``purchase`` as the database holds it, with its lines and their lots."""
    await db.refresh(purchase)
    lines = list(
        (
            await db.exec(
                select(PurchaseLine)
                .where(PurchaseLine.purchase_id == purchase.id)
                .order_by(col(PurchaseLine.id))
                .execution_options(populate_existing=True)
            )
        ).all()
    )
    ids = [line.item_id for line in lines]
    items = await describe_items(db, ids)
    materials = await db.exec(
        select(Material.id, Material.sku, Material.category_id).where(col(Material.id).in_(ids))
    )
    variations = await db.exec(
        select(Variation.id, Variation.sku, Product.category_id)
        .join(Product, col(Product.id) == col(Variation.product_id))
        .where(col(Variation.id).in_(ids))
    )
    filed = {id_: (sku, category_id) for id_, sku, category_id in [*materials, *variations]}
    lots = {
        lot.purchase_line_id: lot
        for lot in (
            await db.exec(
                select(Lot).where(col(Lot.purchase_line_id).in_([line.id for line in lines]))
            )
        ).all()
    }
    landed = purchases.landed_costs(purchase, lines)
    value = sum((line.purchase_quantity * line.unit_cost for line in lines), Decimal(0))
    return PurchaseRead(
        id=purchase.id,
        code=purchase.code,
        dated_on=purchase.dated_on,
        supplier_contact_id=purchase.supplier_contact_id,
        location_id=purchase.location_id,
        paid=purchase.paid,
        expected_on=purchase.expected_on,
        overdue=purchases.overdue(purchase, any(line.received_on is None for line in lines)),
        notes=purchase.notes,
        lines=[
            PurchaseLineRead(
                id=line.id,
                item=items[line.item_id],
                sku=filed[line.item_id][0],
                category_id=filed[line.item_id][1],
                purchase_quantity=line.purchase_quantity,
                tracking_quantity=line.tracking_quantity,
                received_on=line.received_on,
                lot=PurchaseLot(
                    id=lot.id, number=lot.number, expires_on=lot.expires_on, notes=lot.notes
                )
                if (lot := lots.get(line.id))
                else None,
                **(
                    {"unit_cost": line.unit_cost, "landed_cost": landed[line.id]}
                    if sees_costs
                    else {}
                ),
            )
            for line in lines
        ],
        created_at=purchase.created_at,
        created_by=purchase.created_by,
        updated_at=purchase.updated_at,
        updated_by=purchase.updated_by,
        **(
            {
                "discount": purchase.discount,
                "shipping": purchase.shipping,
                "tax": purchase.tax,
                "total": _total(purchase, value),
            }
            if sees_costs
            else {}
        ),
    )


def _no_costs(sees_costs: bool, sent: Collection[object]) -> None:
    """Refuse costs sent by a member who does not see them."""
    if not sees_costs and any(value is not None for value in sent):
        raise refuse(status.HTTP_403_FORBIDDEN, PurchaseError.COSTS_HIDDEN)


async def _flush_code(db: WriteSession) -> None:
    """Flush a purchase, refusing a code another purchase has."""
    try:
        await db.flush()
    except IntegrityError as error:
        if "uq_purchases_code" not in str(error.orig):
            raise
        raise refuse(status.HTTP_409_CONFLICT, PurchaseError.PURCHASE_CODE_TAKEN) from None


async def _rederive(db: WriteSession, purchase: Purchase) -> None:
    """Re-derive what ``purchase`` received, refusing a line received before its date."""
    try:
        await purchases.rederive_purchase(db, purchase)
    except PurchaseRefused as refused:
        raise refuse(status.HTTP_409_CONFLICT, refused.code) from None


@router.get("", response_model_exclude_unset=True)
async def list_purchases(
    db: OrganizationSessionDep,
    sees_costs: SeesCostsDep,
    page: PageDep,
    item_id: Annotated[int | None, Query()] = None,
) -> Page[PurchaseSummary]:
    """Purchases, the latest first, with how many of their lines are
    received: all, or those with a line buying one item."""
    lines = (
        select(
            PurchaseLine.purchase_id,
            func.count().label("lines"),
            func.count(col(PurchaseLine.received_on)).label("received"),
            func.sum(PurchaseLine.purchase_quantity * PurchaseLine.unit_cost).label("value"),
        )
        .group_by(col(PurchaseLine.purchase_id))
        .subquery()
    )
    conditions = []
    if item_id is not None:
        conditions.append(
            exists().where(
                col(PurchaseLine.purchase_id) == Purchase.id, col(PurchaseLine.item_id) == item_id
            )
        )
    total = (await db.exec(select(func.count()).select_from(Purchase).where(*conditions))).one()
    rows = await db.exec(
        select(Purchase, lines.c.lines, lines.c.received, lines.c.value)
        .outerjoin(lines, lines.c.purchase_id == col(Purchase.id))
        .where(*conditions)
        .order_by(col(Purchase.dated_on).desc(), col(Purchase.id).desc())
        .offset(page.offset)
        .limit(page.limit)
    )
    return Page(
        items=[
            PurchaseSummary(
                id=purchase.id,
                code=purchase.code,
                dated_on=purchase.dated_on,
                supplier_contact_id=purchase.supplier_contact_id,
                location_id=purchase.location_id,
                paid=purchase.paid,
                expected_on=purchase.expected_on,
                overdue=purchases.overdue(purchase, (received or 0) < (count or 0)),
                lines=count or 0,
                received=received or 0,
                **({"total": _total(purchase, value or Decimal(0))} if sees_costs else {}),
            )
            for purchase, count, received, value in rows.all()
        ],
        total=total,
    )


@router.get("/{purchase_id}", response_model_exclude_unset=True, responses=NOT_FOUND)
async def read_purchase(
    db: OrganizationSessionDep, sees_costs: SeesCostsDep, purchase_id: int
) -> PurchaseRead:
    """A purchase, its lines, and the lots received from them."""
    return await _read(db, await known(db, Purchase, purchase_id), sees_costs)


class PurchaseLineCreate(BaseModel):
    #: A material, or a variation of a product that is not a bundle.
    item_id: int
    #: How many purchase units.
    purchase_quantity: Positive
    #: How many tracking units they make; the item's conversion when not given.
    tracking_quantity: Positive | None = None
    #: What one purchase unit cost; none when not given.
    unit_cost: NonNegative | None = None


class PurchaseCreate(BaseModel):
    #: The next free ``P-`` number when not given.
    code: Code | None = None
    #: Today when not given.
    dated_on: date | None = None
    supplier_contact_id: int | None = None
    #: The main location when not given.
    location_id: int | None = None
    paid: bool = False
    expected_on: date | None = None
    notes: str | None = NOTES
    discount: NonNegative | None = None
    shipping: NonNegative | None = None
    tax: NonNegative | None = None
    lines: list[PurchaseLineCreate] = Field(default_factory=list, max_length=500)


async def _bought(db: WriteSession, item_id: int) -> Material | Variation:
    """The material or variation a line buys: 404 for no item, and 422
    ``PURCHASE_LINE_KIND`` for one that can't be bought."""
    await known(db, Item, item_id)
    try:
        return await purchases.bought(db, item_id)
    except PurchaseRefused as refused:
        raise refuse(status.HTTP_422_UNPROCESSABLE_CONTENT, refused.code) from None


async def _tracking(
    db: WriteSession, item_id: int, purchase_quantity: Decimal, given: Decimal | None
) -> Decimal:
    """The tracking quantity ``given``, or what the item's purchase conversion
    makes of ``purchase_quantity``."""
    bought = await _bought(db, item_id)
    if given is not None:
        return given
    try:
        return units.purchased(purchase_quantity, bought.purchase_conversion)
    except units.UnitRefused as refused:
        raise refuse(status.HTTP_422_UNPROCESSABLE_CONTENT, refused.code) from None


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model_exclude_unset=True,
    responses={
        **NOT_FOUND,
        **UNBOUGHT,
        403: {"description": "COSTS_HIDDEN"},
        409: {"description": "PURCHASE_CODE_TAKEN"},
    },
)
async def create_purchase(
    db: WriteSessionDep, session: SessionDep, sees_costs: SeesCostsDep, body: PurchaseCreate
) -> PurchaseRead:
    """Add a purchase, with its lines, none of them received, in the
    initiative the page was opened in; it is recorded as placed."""
    _no_costs(
        sees_costs,
        [body.discount, body.shipping, body.tax, *(line.unit_cost for line in body.lines)],
    )
    purchase = Purchase(
        organization_id=db.organization_id,
        code=body.code or await codes.next_code(db, Purchase, "P"),
        dated_on=body.dated_on or date.today(),
        supplier_contact_id=body.supplier_contact_id,
        location_id=await location_or_main(db, body.location_id),
        paid=body.paid,
        expected_on=body.expected_on,
        notes=body.notes,
        discount=body.discount or Decimal(0),
        shipping=body.shipping or Decimal(0),
        tax=body.tax or Decimal(0),
        initiative_id=session.initiative_id,
    )
    db.add(purchase)
    await _flush_code(db)
    for line in body.lines:
        db.add(
            PurchaseLine(
                organization_id=db.organization_id,
                purchase_id=purchase.id,
                item_id=line.item_id,
                purchase_quantity=line.purchase_quantity,
                tracking_quantity=await _tracking(
                    db, line.item_id, line.purchase_quantity, line.tracking_quantity
                ),
                unit_cost=line.unit_cost or Decimal(0),
            )
        )
    await db.flush()
    await events.record(
        db, await events.purchase_event(db, events.EventName.PURCHASE_PLACED, purchase)
    )
    await db.commit()
    return await _read(db, purchase, sees_costs)


class PurchaseUpdate(BaseModel):
    """A change to a purchase: only what is sent."""

    code: Code | None = None
    dated_on: date | None = None
    #: Null takes the supplier off.
    supplier_contact_id: int | None = None
    location_id: int | None = None
    paid: bool | None = None
    #: Null takes the date off.
    expected_on: date | None = None
    #: Null takes the notes off.
    notes: str | None = NOTES
    discount: NonNegative | None = None
    shipping: NonNegative | None = None
    tax: NonNegative | None = None


@router.patch(
    "/{purchase_id}",
    response_model_exclude_unset=True,
    responses={
        **NOT_FOUND,
        403: {"description": "COSTS_HIDDEN"},
        409: {"description": "PURCHASE_CODE_TAKEN, RECEIVED_BEFORE_PURCHASE"},
    },
)
async def update_purchase(
    db: WriteSessionDep, sees_costs: SeesCostsDep, purchase_id: int, body: PurchaseUpdate
) -> PurchaseRead:
    """Change a purchase, and carry the change into what it received."""
    sent = changes(body, nullable={"supplier_contact_id", "expected_on", "notes"})
    _no_costs(sees_costs, [sent.get(name) for name in PURCHASE_COSTS])
    purchase = await known(db, Purchase, purchase_id)
    if "location_id" in sent:
        sent["location_id"] = await location_or_main(db, sent["location_id"])
    for name, value in sent.items():
        setattr(purchase, name, value)
    await _flush_code(db)
    await _rederive(db, purchase)
    await db.commit()
    return await _read(db, purchase, sees_costs)


@router.delete(
    "/{purchase_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={**NOT_FOUND, 409: {"description": "LOT_IN_USE"}},
)
async def delete_purchase(db: WriteSessionDep, purchase_id: int) -> None:
    """Delete a purchase, taking back what its lines received."""
    purchase = await known(db, Purchase, purchase_id)
    try:
        await purchases.delete_purchase(db, purchase)
    except StockRefused as refused:
        raise refuse(status.HTTP_409_CONFLICT, refused.code) from None
    await db.commit()


async def _line(db: WriteSession, purchase_id: int, line_id: int) -> tuple[Purchase, PurchaseLine]:
    purchase = await known(db, Purchase, purchase_id)
    line = await known(db, PurchaseLine, line_id)
    if line.purchase_id != purchase.id:
        raise refuse(status.HTTP_404_NOT_FOUND, HoardError.NOT_FOUND)
    return purchase, line


@router.post(
    "/{purchase_id}/lines",
    status_code=status.HTTP_201_CREATED,
    response_model_exclude_unset=True,
    responses={**NOT_FOUND, **UNBOUGHT, 403: {"description": "COSTS_HIDDEN"}},
)
async def create_purchase_line(
    db: WriteSessionDep, sees_costs: SeesCostsDep, purchase_id: int, body: PurchaseLineCreate
) -> PurchaseRead:
    """Add a line to a purchase."""
    _no_costs(sees_costs, [body.unit_cost])
    purchase = await known(db, Purchase, purchase_id)
    db.add(
        PurchaseLine(
            organization_id=db.organization_id,
            purchase_id=purchase.id,
            item_id=body.item_id,
            purchase_quantity=body.purchase_quantity,
            tracking_quantity=await _tracking(
                db, body.item_id, body.purchase_quantity, body.tracking_quantity
            ),
            unit_cost=body.unit_cost or Decimal(0),
        )
    )
    await purchases.rederive_purchase(db, purchase)
    await db.commit()
    return await _read(db, purchase, sees_costs)


class PurchaseLineUpdate(BaseModel):
    """A change to a line: only what is sent. A new purchase quantity without a
    tracking quantity makes as many tracking units as the item's conversion
    says."""

    item_id: int | None = None
    purchase_quantity: Positive | None = None
    tracking_quantity: Positive | None = None
    unit_cost: NonNegative | None = None
    #: The day a received line arrived.
    received_on: date | None = None


@router.patch(
    "/{purchase_id}/lines/{line_id}",
    response_model_exclude_unset=True,
    responses={**NOT_FOUND, **UNBOUGHT, 403: {"description": "COSTS_HIDDEN"}, **CONFLICT},
)
async def update_purchase_line(
    db: WriteSessionDep,
    sees_costs: SeesCostsDep,
    purchase_id: int,
    line_id: int,
    body: PurchaseLineUpdate,
) -> PurchaseRead:
    """Change a line, and carry the change into what it received."""
    sent = changes(body)
    _no_costs(sees_costs, [sent.get("unit_cost")])
    purchase, line = await _line(db, purchase_id, line_id)
    if line.received_on and sent.get("item_id", line.item_id) != line.item_id:
        raise refuse(status.HTTP_409_CONFLICT, PurchaseError.PURCHASE_LINE_RECEIVED)
    if line.received_on is None and "received_on" in sent:
        raise refuse(status.HTTP_409_CONFLICT, PurchaseError.PURCHASE_LINE_NOT_RECEIVED)
    if "tracking_quantity" not in sent and {"item_id", "purchase_quantity"} & sent.keys():
        sent["tracking_quantity"] = await _tracking(
            db,
            sent.get("item_id", line.item_id),
            sent.get("purchase_quantity", line.purchase_quantity),
            None,
        )
    elif "item_id" in sent:
        await _bought(db, sent["item_id"])
    for name, value in sent.items():
        setattr(line, name, value)
    await _rederive(db, purchase)
    await db.commit()
    return await _read(db, purchase, sees_costs)


@router.delete(
    "/{purchase_id}/lines/{line_id}",
    response_model_exclude_unset=True,
    responses={**NOT_FOUND, **CONFLICT},
)
async def delete_purchase_line(
    db: WriteSessionDep, sees_costs: SeesCostsDep, purchase_id: int, line_id: int
) -> PurchaseRead:
    """Delete a line, taking back what it received."""
    purchase, line = await _line(db, purchase_id, line_id)
    try:
        await purchases.delete_line(db, line)
    except StockRefused as refused:
        raise refuse(status.HTTP_409_CONFLICT, refused.code) from None
    await db.commit()
    return await _read(db, purchase, sees_costs)


class PurchaseLineReceive(BaseModel):
    """What of a line arrived, when, and the lot it is received into."""

    #: Today when not given; never before the purchase's date.
    received_on: date | None = None
    #: How many tracking units arrived, when less than the line: that much is
    #: split off as a line of its own and received, the rest staying on order.
    quantity: Positive | None = None
    #: The purchase's code and the received line's id when not given.
    number: Code | None = None
    expires_on: date | None = None
    notes: str | None = NOTES


@router.post(
    "/{purchase_id}/lines/{line_id}/receive",
    response_model_exclude_unset=True,
    responses={
        **NOT_FOUND,
        409: {
            "description": "PURCHASE_LINE_RECEIVED, RECEIVED_BEFORE_PURCHASE, "
            "RECEIVED_MORE_THAN_LINE, AMOUNT_TOO_SMALL, PURCHASE_LINE_KIND"
        },
    },
)
async def receive_purchase_line(
    db: WriteSessionDep,
    sees_costs: SeesCostsDep,
    purchase_id: int,
    line_id: int,
    body: PurchaseLineReceive,
) -> PurchaseRead:
    """Receive a line, or part of one: its lot, and the stock it brings in at
    its landed cost on the day it arrived."""
    purchase, line = await _line(db, purchase_id, line_id)
    if line.received_on:
        raise refuse(status.HTTP_409_CONFLICT, PurchaseError.PURCHASE_LINE_RECEIVED)
    try:
        await purchases.receive_line(
            db,
            line,
            received_on=body.received_on,
            quantity=body.quantity,
            number=body.number,
            expires_on=body.expires_on,
            notes=body.notes,
        )
    except PurchaseRefused as refused:
        raise refuse(status.HTTP_409_CONFLICT, refused.code) from None
    await db.commit()
    return await _read(db, purchase, sees_costs)


@router.post(
    "/{purchase_id}/lines/{line_id}/unreceive",
    response_model_exclude_unset=True,
    responses={**NOT_FOUND, **CONFLICT},
)
async def unreceive_purchase_line(
    db: WriteSessionDep, sees_costs: SeesCostsDep, purchase_id: int, line_id: int
) -> PurchaseRead:
    """Take back what a line received, while nothing else draws on its lot. A
    part received from a line still on order goes back into it."""
    purchase, line = await _line(db, purchase_id, line_id)
    if line.received_on is None:
        raise refuse(status.HTTP_409_CONFLICT, PurchaseError.PURCHASE_LINE_NOT_RECEIVED)
    try:
        await purchases.unreceive_line(db, line)
    except StockRefused as refused:
        raise refuse(status.HTTP_409_CONFLICT, refused.code) from None
    await db.commit()
    return await _read(db, purchase, sees_costs)
