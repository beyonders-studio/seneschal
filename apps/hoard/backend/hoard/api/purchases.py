"""Purchases of materials from suppliers, their lines, and receiving them.

A purchase names its supplier by an Initiative contact's id. Receiving a line
makes its lot and its purchase adjustment (``hoard.purchases``); every change to
a purchase or its lines re-derives what its received lines cost.

Without :data:`~hoard.api.deps.SeesCostsDep` no unit cost, discount, shipping,
tax or total is sent, and a purchase that sends one is refused ``COSTS_HIDDEN``.
"""

from __future__ import annotations

from collections.abc import Collection
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated

from fastapi import APIRouter, status
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlmodel import col, select

from hoard import purchases
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
from hoard.records import PLACES, Lot, Material, Purchase, PurchaseLine
from hoard.stock import StockRefused
from seneschal_core.api import OrganizationSessionDep, WriteSessionDep, refuse
from seneschal_core.db import OrganizationSession, WriteSession

router = APIRouter(prefix="/purchases", tags=["purchases"])

#: A quantity bought: more than none.
Positive = Annotated[Decimal, Field(gt=0, max_digits=20, decimal_places=6)]
Code = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]
#: The fields of a purchase that hold money.
PURCHASE_COSTS = frozenset({"discount", "shipping", "tax"})
CONFLICT: Responses = {
    409: {"description": "PURCHASE_LINE_RECEIVED, PURCHASE_LINE_NOT_RECEIVED, LOT_IN_USE"}
}


class PurchaseError(StrEnum):
    """Why a purchase was refused."""

    #: A cost was sent by a member who does not see costs.
    COSTS_HIDDEN = "COSTS_HIDDEN"
    #: Another purchase has the code.
    PURCHASE_CODE_TAKEN = "PURCHASE_CODE_TAKEN"
    #: The line is received: it can't be received again or bought as another material.
    PURCHASE_LINE_RECEIVED = "PURCHASE_LINE_RECEIVED"
    #: The line has not been received.
    PURCHASE_LINE_NOT_RECEIVED = "PURCHASE_LINE_NOT_RECEIVED"


class PurchaseLot(BaseModel):
    """The lot a received line made."""

    id: int
    number: str
    expires_on: date | None
    notes: str | None


class PurchaseLineRead(BaseModel):
    id: int
    material: AdjustmentItem
    purchase_quantity: Decimal
    tracking_quantity: Decimal
    #: Sent only to a member who sees costs, as are the others below.
    unit_cost: Decimal | None = None
    #: What one tracking unit cost, with its share of discount and shipping.
    landed_cost: Decimal | None = None
    received: bool
    lot: PurchaseLot | None


class PurchaseRead(BaseModel):
    id: int
    code: str
    dated_on: date
    #: The supplier: an Initiative contact's id.
    supplier_contact_id: int | None
    location_id: int
    paid: bool
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
    lines: int
    received: int
    total: Decimal | None = None


def _total(purchase: Purchase, value: Decimal) -> Decimal:
    return (value - purchase.discount + purchase.shipping + purchase.tax).quantize(PLACES)


async def _read(db: OrganizationSession, purchase: Purchase, sees_costs: bool) -> PurchaseRead:
    lines = list(
        (
            await db.exec(
                select(PurchaseLine)
                .where(PurchaseLine.purchase_id == purchase.id)
                .order_by(col(PurchaseLine.id))
            )
        ).all()
    )
    materials = await describe_items(db, [line.material_id for line in lines])
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
        lines=[
            PurchaseLineRead(
                id=line.id,
                material=materials[line.material_id],
                purchase_quantity=line.purchase_quantity,
                tracking_quantity=line.tracking_quantity,
                received=line.received,
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


@router.get("", response_model_exclude_unset=True)
async def list_purchases(
    db: OrganizationSessionDep, sees_costs: SeesCostsDep, page: PageDep
) -> Page[PurchaseSummary]:
    """Purchases, the latest first, with how many of their lines are received."""
    lines = (
        select(
            PurchaseLine.purchase_id,
            func.count().label("lines"),
            func.count().filter(col(PurchaseLine.received)).label("received"),
            func.sum(PurchaseLine.purchase_quantity * PurchaseLine.unit_cost).label("value"),
        )
        .group_by(col(PurchaseLine.purchase_id))
        .subquery()
    )
    total = (await db.exec(select(func.count()).select_from(Purchase))).one()
    rows = await db.exec(
        select(Purchase, lines.c.lines, lines.c.received, lines.c.value)
        .outerjoin(lines, lines.c.purchase_id == col(Purchase.id))
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
    material_id: int
    #: How many purchase units.
    purchase_quantity: Positive
    #: How many tracking units they make; the material's conversion when not given.
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
    discount: NonNegative | None = None
    shipping: NonNegative | None = None
    tax: NonNegative | None = None
    lines: list[PurchaseLineCreate] = Field(default_factory=list, max_length=500)


async def _tracking(
    db: WriteSession, material_id: int, purchase_quantity: Decimal, given: Decimal | None
) -> Decimal:
    """The tracking quantity ``given``, or what the material's conversion makes
    of ``purchase_quantity``."""
    material = await known(db, Material, material_id)
    return given or (purchase_quantity * material.purchase_conversion).quantize(PLACES)


async def _next_code(db: WriteSession) -> str:
    latest = (await db.exec(select(func.max(Purchase.id)))).one()
    return f"P-{(latest or 0) + 1:04d}"


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model_exclude_unset=True,
    responses={
        **NOT_FOUND,
        403: {"description": "COSTS_HIDDEN"},
        409: {"description": "PURCHASE_CODE_TAKEN"},
    },
)
async def create_purchase(
    db: WriteSessionDep, sees_costs: SeesCostsDep, body: PurchaseCreate
) -> PurchaseRead:
    """Add a purchase, with its lines, none of them received."""
    _no_costs(
        sees_costs,
        [body.discount, body.shipping, body.tax, *(line.unit_cost for line in body.lines)],
    )
    purchase = Purchase(
        organization_id=db.organization_id,
        code=body.code or await _next_code(db),
        dated_on=body.dated_on or date.today(),
        supplier_contact_id=body.supplier_contact_id,
        location_id=await location_or_main(db, body.location_id),
        paid=body.paid,
        discount=body.discount or Decimal(0),
        shipping=body.shipping or Decimal(0),
        tax=body.tax or Decimal(0),
    )
    db.add(purchase)
    await _flush_code(db)
    for line in body.lines:
        db.add(
            PurchaseLine(
                organization_id=db.organization_id,
                purchase_id=purchase.id,
                material_id=line.material_id,
                purchase_quantity=line.purchase_quantity,
                tracking_quantity=await _tracking(
                    db, line.material_id, line.purchase_quantity, line.tracking_quantity
                ),
                unit_cost=line.unit_cost or Decimal(0),
            )
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
    discount: NonNegative | None = None
    shipping: NonNegative | None = None
    tax: NonNegative | None = None


@router.patch(
    "/{purchase_id}",
    response_model_exclude_unset=True,
    responses={
        **NOT_FOUND,
        403: {"description": "COSTS_HIDDEN"},
        409: {"description": "PURCHASE_CODE_TAKEN"},
    },
)
async def update_purchase(
    db: WriteSessionDep, sees_costs: SeesCostsDep, purchase_id: int, body: PurchaseUpdate
) -> PurchaseRead:
    """Change a purchase, and carry the change into what it received."""
    sent = changes(body, nullable={"supplier_contact_id"})
    _no_costs(sees_costs, [sent.get(name) for name in PURCHASE_COSTS])
    purchase = await known(db, Purchase, purchase_id)
    if "location_id" in sent:
        sent["location_id"] = await location_or_main(db, sent["location_id"])
    for name, value in sent.items():
        setattr(purchase, name, value)
    await _flush_code(db)
    await purchases.rederive_purchase(db, purchase)
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
    responses={**NOT_FOUND, 403: {"description": "COSTS_HIDDEN"}},
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
            material_id=body.material_id,
            purchase_quantity=body.purchase_quantity,
            tracking_quantity=await _tracking(
                db, body.material_id, body.purchase_quantity, body.tracking_quantity
            ),
            unit_cost=body.unit_cost or Decimal(0),
        )
    )
    await purchases.rederive_purchase(db, purchase)
    await db.commit()
    return await _read(db, purchase, sees_costs)


class PurchaseLineUpdate(BaseModel):
    """A change to a line: only what is sent. A new purchase quantity without a
    tracking quantity makes as many tracking units as the material's conversion
    says."""

    material_id: int | None = None
    purchase_quantity: Positive | None = None
    tracking_quantity: Positive | None = None
    unit_cost: NonNegative | None = None


@router.patch(
    "/{purchase_id}/lines/{line_id}",
    response_model_exclude_unset=True,
    responses={**NOT_FOUND, 403: {"description": "COSTS_HIDDEN"}, **CONFLICT},
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
    if line.received and sent.get("material_id", line.material_id) != line.material_id:
        raise refuse(status.HTTP_409_CONFLICT, PurchaseError.PURCHASE_LINE_RECEIVED)
    if "tracking_quantity" not in sent and {"material_id", "purchase_quantity"} & sent.keys():
        sent["tracking_quantity"] = await _tracking(
            db,
            sent.get("material_id", line.material_id),
            sent.get("purchase_quantity", line.purchase_quantity),
            None,
        )
    elif "material_id" in sent:
        await known(db, Material, sent["material_id"])
    for name, value in sent.items():
        setattr(line, name, value)
    await purchases.rederive_purchase(db, purchase)
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
    """The lot a line is received into."""

    #: The purchase's code and the line's id when not given.
    number: Code | None = None
    expires_on: date | None = None
    notes: str | None = NOTES


@router.post(
    "/{purchase_id}/lines/{line_id}/receive",
    response_model_exclude_unset=True,
    responses={**NOT_FOUND, **CONFLICT},
)
async def receive_purchase_line(
    db: WriteSessionDep,
    sees_costs: SeesCostsDep,
    purchase_id: int,
    line_id: int,
    body: PurchaseLineReceive,
) -> PurchaseRead:
    """Receive a line: its lot, and the stock it brings in at its landed cost."""
    purchase, line = await _line(db, purchase_id, line_id)
    if line.received:
        raise refuse(status.HTTP_409_CONFLICT, PurchaseError.PURCHASE_LINE_RECEIVED)
    await purchases.receive_line(
        db, line, number=body.number, expires_on=body.expires_on, notes=body.notes
    )
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
    """Take back what a line received, while nothing else draws on its lot."""
    purchase, line = await _line(db, purchase_id, line_id)
    if not line.received:
        raise refuse(status.HTTP_409_CONFLICT, PurchaseError.PURCHASE_LINE_NOT_RECEIVED)
    try:
        await purchases.unreceive_line(db, line)
    except StockRefused as refused:
        raise refuse(status.HTTP_409_CONFLICT, refused.code) from None
    await db.commit()
    return await _read(db, purchase, sees_costs)
