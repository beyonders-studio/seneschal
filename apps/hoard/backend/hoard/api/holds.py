"""Holds on lots and their reasons (``hoard.holds``).

Any member who changes stock puts a lot on hold, with everything made from it
when they ask, or every lot an order drew when a problem with the order is
reported; releases a hold, or discards what its lot holds. The hold reasons
are every member's to read and a community admin's to change, as the
adjustment reasons are; an organization that has none yet starts with the
default ones. A lot's open hold is read with the lot (``hoard.api.lots``).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated

from fastapi import APIRouter, status
from pydantic import BaseModel, Field
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from hoard import holds, stock
from hoard.api.adjustments import NOTES, NonNegative, Responses, known
from hoard.api.deps import AdminDep
from hoard.api.settings import SettingsError, _made_by_hoard, refused_as
from hoard.holds import HoldError, HoldRefused
from hoard.records import (
    AdjustmentReason,
    DefaultHoldReason,
    Hold,
    HoldEnd,
    HoldReason,
    Lot,
    Order,
)
from seneschal_core.api import (
    OrganizationDep,
    OrganizationSessionDep,
    PluginDep,
    WriteSessionDep,
    refuse,
)
from seneschal_core.db import WriteSession

router = APIRouter(prefix="/holds", tags=["holds"])

_READ: Responses = {401: {"description": "NOT_AUTHENTICATED"}}
_CHANGE: Responses = {
    **_READ,
    403: {"description": "READ_ONLY"},
    404: {"description": "NOT_FOUND"},
    409: {"description": ", ".join(HoldError)},
}
_REASON_CHANGE: Responses = {
    **_READ,
    403: {"description": "ADMIN_ONLY, READ_ONLY"},
    404: {"description": "NOT_FOUND"},
    409: {"description": "NAME_TAKEN, IN_USE"},
}

Name = Annotated[str, Field(min_length=1, max_length=200)]


class HoldRead(BaseModel):
    """A hold on a lot, open or ended."""

    id: int
    lot_id: int
    #: Its lot's number.
    lot: str
    reason_id: int
    #: The day before which it is not released.
    ready_on: date | None
    notes: str | None
    #: The production run that put its lot on hold, curing.
    run_id: int | None
    #: The order it was placed from.
    order_id: int | None
    #: The hold whose lot this one's was made from.
    from_hold_id: int | None
    #: How it ended; null while it is open.
    ended: HoldEnd | None
    ended_on: date | None
    created_at: datetime
    created_by: str | None


class HoldReasonRead(BaseModel):
    id: int
    #: Set on a default reason, or one a starting setup added: until someone
    #: names a default reason, pages call it by the words for its key.
    key: str | None
    #: Null for a default reason until someone names it.
    name: str | None


class HoldReasonWrite(BaseModel):
    name: Name


class HoldCreate(BaseModel):
    lot_id: int
    reason_id: int
    #: The day before which it is not released.
    ready_on: date | None = None
    notes: str | None = NOTES
    #: Whether everything made from the lot goes on hold with it.
    made_from: bool = False


class HoldFromOrder(BaseModel):
    #: The suspect reason when not given.
    reason_id: int | None = None
    notes: str | None = NOTES
    #: Whether everything made from each lot goes on hold with it.
    made_from: bool = False


class HoldRelease(BaseModel):
    #: Today when not given.
    ended_on: date | None = None


class HoldDiscard(BaseModel):
    #: The adjustment reason it is discarded for.
    reason_id: int
    #: How much of the lot is discarded; all of it when not given.
    quantity: NonNegative | None = None
    #: Today when not given.
    ended_on: date | None = None


async def _reads(db: AsyncSession, placed: list[Hold]) -> list[HoldRead]:
    """``placed``, as the database holds them, in the order they were placed."""
    if not placed:
        return []
    found = await db.exec(
        select(Hold, Lot.number)
        .join(Lot, col(Lot.id) == Hold.lot_id)
        .where(col(Hold.id).in_([hold.id for hold in placed]))
        .order_by(col(Hold.id))
        .execution_options(populate_existing=True)
    )
    return [HoldRead.model_validate({**hold.model_dump(), "lot": lot}) for hold, lot in found]


async def _committed(db: WriteSession, placed: list[Hold]) -> list[HoldRead]:
    await db.commit()
    return await _reads(db, placed)


def _refused(refused: HoldRefused) -> Exception:
    return refuse(status.HTTP_409_CONFLICT, refused.code)


@router.post("", status_code=status.HTTP_201_CREATED, responses=_CHANGE)
async def place_hold(body: HoldCreate, db: WriteSessionDep) -> list[HoldRead]:
    """Put a lot on hold for a reason, and, when asked, every lot still
    holding stock that was made from it. Answers the holds placed, the lot's
    own first."""
    lot = await known(db, Lot, body.lot_id)
    await known(db, HoldReason, body.reason_id)
    try:
        hold = await holds.place(
            db, lot, body.reason_id, ready_on=body.ready_on, notes=body.notes or None
        )
    except HoldRefused as refused:
        raise _refused(refused) from None
    placed = [hold, *(await holds.place_made_from(db, hold) if body.made_from else [])]
    return await _committed(db, placed)


@router.post("/orders/{order_id}", status_code=status.HTTP_201_CREATED, responses=_CHANGE)
async def place_order_holds(
    order_id: int, body: HoldFromOrder, db: WriteSessionDep
) -> list[HoldRead]:
    """Put every lot an order's shipments drew on hold, recorded or assumed,
    when a problem with the order is reported, and, when asked, everything
    made from each. Lots already on hold stay as they are. Answers the holds
    placed."""
    order = await known(db, Order, order_id)
    if body.reason_id is None:
        reason_id = await holds.reason_id(db, DefaultHoldReason.SUSPECT)
    else:
        reason_id = (await known(db, HoldReason, body.reason_id)).id
    placed = await holds.place_from_order(
        db, order, reason_id, notes=body.notes or None, made_from=body.made_from
    )
    return await _committed(db, placed)


@router.post("/{hold_id}/release", responses=_CHANGE)
async def release_hold(hold_id: int, body: HoldRelease, db: WriteSessionDep) -> HoldRead:
    """Release a hold, its lot available again: not before its ready date."""
    hold = await known(db, Hold, hold_id)
    try:
        await holds.release(db, hold, body.ended_on or date.today())
    except HoldRefused as refused:
        raise _refused(refused) from None
    [read] = await _committed(db, [hold])
    return read


@router.post("/{hold_id}/discard", responses=_CHANGE)
async def discard_hold(hold_id: int, body: HoldDiscard, db: WriteSessionDep) -> HoldRead:
    """Discard what a held lot holds, or some of it, for an adjustment
    reason, and end its hold: released when some is left, which waits for its
    ready date."""
    hold = await known(db, Hold, hold_id)
    await known(db, AdjustmentReason, body.reason_id)
    try:
        await holds.discard(db, hold, body.reason_id, body.ended_on or date.today(), body.quantity)
    except HoldRefused as refused:
        raise _refused(refused) from None
    [read] = await _committed(db, [hold])
    return read


async def _hold_reasons(db: AsyncSession) -> list[HoldReasonRead]:
    query = select(HoldReason).order_by(col(HoldReason.name), col(HoldReason.id))
    return [
        HoldReasonRead.model_validate(reason, from_attributes=True)
        for reason in await db.exec(query)
    ]


@router.get("/reasons", responses=_READ)
async def list_hold_reasons(
    db: OrganizationSessionDep, organization: OrganizationDep, plugin: PluginDep
) -> list[HoldReasonRead]:
    """Why lots are put on hold: those named, by name, then the default
    reasons nobody has named. An organization that has none yet starts with
    the default reasons."""
    reasons = await _hold_reasons(db)
    if not reasons and await _made_by_hoard(organization, plugin, stock.add_default_hold_reasons):
        reasons = await _hold_reasons(db)
    return reasons


async def _save_reason(db: WriteSession, reason: HoldReason) -> HoldReasonRead:
    db.add(reason)
    with refused_as(SettingsError.NAME_TAKEN):
        await db.commit()
    return HoldReasonRead.model_validate(reason, from_attributes=True)


@router.post("/reasons", status_code=status.HTTP_201_CREATED, responses=_REASON_CHANGE)
async def create_hold_reason(
    body: HoldReasonWrite, _admin: AdminDep, db: WriteSessionDep
) -> HoldReasonRead:
    return await _save_reason(db, HoldReason(organization_id=db.organization_id, name=body.name))


@router.patch("/reasons/{reason_id}", responses=_REASON_CHANGE)
async def update_hold_reason(
    reason_id: int, body: HoldReasonWrite, _admin: AdminDep, db: WriteSessionDep
) -> HoldReasonRead:
    reason = await known(db, HoldReason, reason_id)
    reason.name = body.name
    return await _save_reason(db, reason)


@router.delete(
    "/reasons/{reason_id}", status_code=status.HTTP_204_NO_CONTENT, responses=_REASON_CHANGE
)
async def delete_hold_reason(reason_id: int, _admin: AdminDep, db: WriteSessionDep) -> None:
    """Remove a hold reason no hold names."""
    await db.delete(await known(db, HoldReason, reason_id))
    with refused_as(SettingsError.IN_USE):
        await db.commit()
