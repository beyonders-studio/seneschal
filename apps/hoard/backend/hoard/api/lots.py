"""Lots, each on a page of its own: what it is a lot of, what is left of it,
and its open hold. Its trace (``hoard.api.trace``), holds
(``hoard.api.holds``) and documents (``hoard.api.documents``) are read from
their own areas."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel
from sqlmodel import col, select

from hoard.api import items
from hoard.api.adjustments import AdjustmentItem, describe_items
from hoard.api.properties import values_of
from hoard.api.settings import found
from hoard.records import Hold, Lot, RecordKind, Variation
from seneschal_core.api import OrganizationSessionDep

router = APIRouter(prefix="/lots", tags=["lots"])

_READ: dict[int | str, dict[str, Any]] = {
    401: {"description": "NOT_AUTHENTICATED"},
    404: {"description": "NOT_FOUND"},
}


class LotHoldRead(BaseModel):
    """A lot's open hold."""

    id: int
    reason_id: int
    #: The day before which it is not released.
    ready_on: date | None
    notes: str | None
    #: The production run that put it on hold, curing.
    run_id: int | None
    #: The order it was placed from.
    order_id: int | None
    #: The hold whose lot this one's was made from.
    from_hold_id: int | None
    created_at: datetime
    created_by: str | None


class LotRead(items.ItemLotRead, items.ItemStamps):
    """A lot, with what it is a lot of and its open hold."""

    item: AdjustmentItem
    #: The product a variation's lot is of, whose page lists it.
    product_id: int | None
    #: The purchase line that received it.
    purchase_line_id: int | None
    hold: LotHoldRead | None


@router.get("/{lot_id}", responses=_READ)
async def read_lot(lot_id: int, db: OrganizationSessionDep) -> LotRead:
    """A lot, with what it is a lot of and its open hold."""
    lot = await found(db, Lot, lot_id)
    hold = (
        await db.exec(select(Hold).where(Hold.lot_id == lot_id, col(Hold.ended).is_(None)))
    ).first()
    variation = await db.get(Variation, lot.item_id)
    [item] = (await describe_items(db, [lot.item_id])).values()
    properties = (await values_of(db, RecordKind.LOT, [lot_id]))[lot_id]
    return LotRead.model_validate(
        {
            **items.lot_read(lot, hold is not None, properties).model_dump(),
            **lot.model_dump(include={"created_at", "created_by", "updated_at", "updated_by"}),
            "item": item,
            "product_id": variation.product_id if variation is not None else None,
            "purchase_line_id": lot.purchase_line_id,
            "hold": None if hold is None else hold.model_dump(),
        }
    )
