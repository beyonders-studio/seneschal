"""Adjustments: every change to stock, and the changes made by hand.

A manual adjustment changes an item's stock at a location, with a reason. One
that a purchase, a stocktake or an order's shipment or return made, a damaged
return's write-off included, is changed through that record. Editing or
deleting a manual or starting adjustment re-derives its item's cost
(``hoard.costing``). A starting adjustment's unit cost is typed in; every other
one's is derived. Without :data:`~hoard.api.deps.SeesCostsDep` no unit cost is
sent and none is taken.
"""

from __future__ import annotations

from collections.abc import Collection
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any

from fastapi import APIRouter, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlmodel import col, select

from hoard import costing
from hoard.api.deps import HoardError, SeesCostsDep
from hoard.api.shapes import Page, PageDep
from hoard.records import (
    Adjustment,
    AdjustmentKind,
    AdjustmentReason,
    Base,
    ItemKind,
    Location,
    Material,
    Product,
    PurchaseLine,
    ReturnLine,
    Shipment,
    ShipmentLine,
    StocktakeRow,
    Variation,
)
from hoard.stock import StockRefused, adjust, main_location
from seneschal_core.api import ErrorCode, OrganizationSessionDep, WriteSessionDep, refuse
from seneschal_core.db import OrganizationSession, WriteSession

router = APIRouter(prefix="/adjustments", tags=["adjustments"])

#: A quantity or cost as the API takes it: exact, to six places.
Amount = Annotated[Decimal, Field(max_digits=20, decimal_places=6)]
#: A quantity or cost that is not negative.
NonNegative = Annotated[Decimal, Field(ge=0, max_digits=20, decimal_places=6)]
NOTES = Field(default=None, max_length=2000)
#: The OpenAPI ``responses`` of a route.
Responses = dict[int | str, dict[str, Any]]
NOT_FOUND: Responses = {404: {"description": "NOT_FOUND"}}


class AdjustmentError(StrEnum):
    """Why an adjustment was refused."""

    #: A cost was sent by a member who does not see costs.
    COSTS_HIDDEN = "COSTS_HIDDEN"
    #: A purchase, a stocktake or an order made it, and is where it changes.
    ADJUSTMENT_FROM_RECORD = "ADJUSTMENT_FROM_RECORD"
    #: Only a starting adjustment's unit cost is typed in.
    ADJUSTMENT_COST_DERIVED = "ADJUSTMENT_COST_DERIVED"
    #: A change of nothing.
    ADJUSTMENT_CHANGE_ZERO = "ADJUSTMENT_CHANGE_ZERO"


class AdjustmentItem(BaseModel):
    """An item, as a movement names it."""

    id: int
    kind: ItemKind
    #: The material's or the base's name, or the variation's product's.
    name: str
    #: A variation's attributes, such as ``{"size": "large"}``; none for a
    #: material or a base.
    attributes: dict[str, str]


async def describe_items(
    db: OrganizationSession, item_ids: Collection[int]
) -> dict[int, AdjustmentItem]:
    """Each of ``item_ids`` that names an item, described, by id."""
    ids = sorted(set(item_ids))
    if not ids:
        return {}
    materials = await db.exec(select(Material.id, Material.name).where(col(Material.id).in_(ids)))
    bases = await db.exec(select(Base.id, Base.name).where(col(Base.id).in_(ids)))
    variations = await db.exec(
        select(Variation.id, Product.name, Variation.attributes)
        .join(Product, col(Product.id) == col(Variation.product_id))
        .where(col(Variation.id).in_(ids))
    )
    described = {
        id_: AdjustmentItem(id=id_, kind=ItemKind.MATERIAL, name=name, attributes={})
        for id_, name in materials.all()
    }
    for id_, name in bases.all():
        described[id_] = AdjustmentItem(id=id_, kind=ItemKind.BASE, name=name, attributes={})
    for id_, name, attributes in variations.all():
        described[id_] = AdjustmentItem(
            id=id_, kind=ItemKind.VARIATION, name=name, attributes=attributes
        )
    return described


async def known[T](db: OrganizationSession, model: type[T], id_: int) -> T:
    """The ``model`` record ``id_`` names, or 404 ``NOT_FOUND``."""
    found = await db.get(model, id_)
    if found is None:
        raise refuse(status.HTTP_404_NOT_FOUND, HoardError.NOT_FOUND)
    return found


async def location_or_main(db: WriteSession, location_id: int | None) -> int:
    """``location_id`` once it names a location, or the main location's."""
    if location_id is None:
        return (await main_location(db)).id
    return (await known(db, Location, location_id)).id


def changes(body: BaseModel, *, nullable: Collection[str] = ()) -> dict[str, Any]:
    """What a change sent: the fields it gave. Only those in ``nullable`` may
    be given as null."""
    sent = body.model_dump(exclude_unset=True)
    if any(value is None and name not in nullable for name, value in sent.items()):
        raise refuse(status.HTTP_422_UNPROCESSABLE_CONTENT, ErrorCode.REQUEST_INVALID)
    return sent


class AdjustmentRead(BaseModel):
    id: int
    item: AdjustmentItem
    location_id: int
    lot_id: int | None
    kind: AdjustmentKind
    dated_on: date
    change: Decimal
    #: What one unit cost at the time; sent only to a member who sees costs.
    unit_cost: Decimal | None = None
    reason_id: int | None
    notes: str | None
    #: The purchase whose received line made it.
    purchase_id: int | None
    #: The stocktake whose row made it.
    stocktake_id: int | None
    #: The order whose shipment, or what came back of it, made it.
    order_id: int | None
    created_at: datetime
    created_by: str | None
    updated_at: datetime
    updated_by: str | None


def _adjustments() -> Any:
    return (
        select(Adjustment, PurchaseLine.purchase_id, StocktakeRow.stocktake_id, Shipment.order_id)
        .outerjoin(PurchaseLine, col(PurchaseLine.id) == col(Adjustment.purchase_line_id))
        .outerjoin(StocktakeRow, col(StocktakeRow.id) == col(Adjustment.stocktake_row_id))
        .outerjoin(ReturnLine, col(ReturnLine.id) == col(Adjustment.return_line_id))
        .outerjoin(
            ShipmentLine,
            col(ShipmentLine.id)
            == func.coalesce(Adjustment.shipment_line_id, ReturnLine.shipment_line_id),
        )
        .outerjoin(Shipment, col(Shipment.id) == col(ShipmentLine.shipment_id))
    )


async def _read(db: OrganizationSession, rows: list[Any], sees_costs: bool) -> list[AdjustmentRead]:
    items = await describe_items(db, [adjustment.item_id for adjustment, *_ in rows])
    return [
        AdjustmentRead(
            id=adjustment.id,
            item=items[adjustment.item_id],
            location_id=adjustment.location_id,
            lot_id=adjustment.lot_id,
            kind=adjustment.kind,
            dated_on=adjustment.dated_on,
            change=adjustment.change,
            reason_id=adjustment.reason_id,
            notes=adjustment.notes,
            purchase_id=purchase_id,
            stocktake_id=stocktake_id,
            order_id=order_id,
            created_at=adjustment.created_at,
            created_by=adjustment.created_by,
            updated_at=adjustment.updated_at,
            updated_by=adjustment.updated_by,
            **({"unit_cost": adjustment.unit_cost} if sees_costs else {}),
        )
        for adjustment, purchase_id, stocktake_id, order_id in rows
    ]


async def _read_one(
    db: OrganizationSession, adjustment_id: int, sees_costs: bool
) -> AdjustmentRead:
    rows = await db.exec(
        _adjustments()
        .where(Adjustment.id == adjustment_id)
        .execution_options(populate_existing=True)
    )
    return (await _read(db, list(rows.all()), sees_costs))[0]


@router.get("", response_model_exclude_unset=True)
async def list_adjustments(
    db: OrganizationSessionDep,
    sees_costs: SeesCostsDep,
    page: PageDep,
    item_id: Annotated[int | None, Query()] = None,
    kind: Annotated[AdjustmentKind | None, Query()] = None,
) -> Page[AdjustmentRead]:
    """Adjustments, the latest first: of one item, or of one kind, when asked."""
    conditions = []
    if item_id is not None:
        conditions.append(Adjustment.item_id == item_id)
    if kind is not None:
        conditions.append(Adjustment.kind == kind)
    total = (await db.exec(select(func.count()).select_from(Adjustment).where(*conditions))).one()
    rows = await db.exec(
        _adjustments()
        .where(*conditions)
        .order_by(col(Adjustment.dated_on).desc(), col(Adjustment.id).desc())
        .offset(page.offset)
        .limit(page.limit)
    )
    return Page(items=await _read(db, list(rows.all()), sees_costs), total=total)


class AdjustmentCreate(BaseModel):
    """A change made by hand."""

    item_id: int
    #: The main location when not given.
    location_id: int | None = None
    #: Today when not given.
    dated_on: date | None = None
    #: What it adds to stock, or takes from it when negative.
    change: Amount
    reason_id: int | None = None
    notes: str | None = NOTES


async def _reason(db: OrganizationSession, reason_id: int | None) -> int | None:
    return None if reason_id is None else (await known(db, AdjustmentReason, reason_id)).id


def _not_zero(change: Decimal | None) -> None:
    if change == 0:
        raise refuse(status.HTTP_422_UNPROCESSABLE_CONTENT, AdjustmentError.ADJUSTMENT_CHANGE_ZERO)


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model_exclude_unset=True,
    responses={
        **NOT_FOUND,
        409: {"description": "NOT_INVENTORIABLE"},
        422: {"description": "ADJUSTMENT_CHANGE_ZERO"},
    },
)
async def create_adjustment(
    db: WriteSessionDep, sees_costs: SeesCostsDep, body: AdjustmentCreate
) -> AdjustmentRead:
    """Change an item's stock by hand, and re-derive its cost."""
    _not_zero(body.change)
    if not await describe_items(db, [body.item_id]):
        raise refuse(status.HTTP_404_NOT_FOUND, HoardError.NOT_FOUND)
    adjustment = Adjustment(
        item_id=body.item_id,
        location_id=await location_or_main(db, body.location_id),
        kind=AdjustmentKind.MANUAL,
        dated_on=body.dated_on or date.today(),
        change=body.change,
        reason_id=await _reason(db, body.reason_id),
        notes=body.notes,
    )
    try:
        await adjust(db, adjustment)
    except StockRefused as refused:
        raise refuse(status.HTTP_409_CONFLICT, refused.code) from None
    await db.commit()
    return await _read_one(db, adjustment.id, sees_costs)


class AdjustmentUpdate(BaseModel):
    """A change to a manual or starting adjustment: only what is sent."""

    location_id: int | None = None
    dated_on: date | None = None
    change: Amount | None = None
    #: A starting adjustment's unit cost; only a member who sees costs sends it.
    unit_cost: NonNegative | None = None
    reason_id: int | None = None
    notes: str | None = NOTES


async def _editable(db: WriteSession, adjustment_id: int) -> Adjustment:
    adjustment = await known(db, Adjustment, adjustment_id)
    by_hand = adjustment.kind in (AdjustmentKind.MANUAL, AdjustmentKind.STARTING)
    # A damaged return's write-off is manual, but its return made it.
    if not by_hand or adjustment.return_line_id is not None:
        raise refuse(status.HTTP_409_CONFLICT, AdjustmentError.ADJUSTMENT_FROM_RECORD)
    return adjustment


@router.patch(
    "/{adjustment_id}",
    response_model_exclude_unset=True,
    responses={
        **NOT_FOUND,
        403: {"description": "COSTS_HIDDEN"},
        409: {"description": "ADJUSTMENT_FROM_RECORD, ADJUSTMENT_COST_DERIVED"},
        422: {"description": "ADJUSTMENT_CHANGE_ZERO"},
    },
)
async def update_adjustment(
    db: WriteSessionDep, sees_costs: SeesCostsDep, adjustment_id: int, body: AdjustmentUpdate
) -> AdjustmentRead:
    """Change a manual or starting adjustment, and re-derive its item's cost."""
    sent = changes(body, nullable={"reason_id", "notes"})
    if "unit_cost" in sent and not sees_costs:
        raise refuse(status.HTTP_403_FORBIDDEN, AdjustmentError.COSTS_HIDDEN)
    adjustment = await _editable(db, adjustment_id)
    if "unit_cost" in sent and adjustment.kind != AdjustmentKind.STARTING:
        raise refuse(status.HTTP_409_CONFLICT, AdjustmentError.ADJUSTMENT_COST_DERIVED)
    _not_zero(sent.get("change"))
    if "location_id" in sent:
        sent["location_id"] = await location_or_main(db, sent["location_id"])
    if "reason_id" in sent:
        sent["reason_id"] = await _reason(db, sent["reason_id"])
    for name, value in sent.items():
        setattr(adjustment, name, value)
    await costing.rederive(db, adjustment.item_id)
    await db.commit()
    return await _read_one(db, adjustment.id, sees_costs)


@router.delete(
    "/{adjustment_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={**NOT_FOUND, 409: {"description": "ADJUSTMENT_FROM_RECORD"}},
)
async def delete_adjustment(db: WriteSessionDep, adjustment_id: int) -> None:
    """Delete a manual or starting adjustment, and re-derive its item's cost."""
    adjustment = await _editable(db, adjustment_id)
    await db.delete(adjustment)
    await costing.rederive(db, adjustment.item_id)
    await db.commit()
