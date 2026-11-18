"""Shipments of an order's lines, the lots they draw, and what comes back
(``hoard.shipments``).

A shipment is planned pending: its lines, each no more than its order line
has left, and the lot each draws, recorded or left for Hoard to assume when it
ships. Only shipping it moves stock. What comes back of a shipped line is
restocked, or written off when damaged. Every route answers the shipment's
order as :func:`hoard.api.orders.describe_order` reads it.

A synced store's order ships as the store's fulfillments say
(:func:`hoard.orders.store_owned`): its shipments are neither made, changed,
shipped, unshipped nor deleted here, and only the lot each line draws and
what comes back of it are recorded.
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum

from fastapi import APIRouter, status
from pydantic import BaseModel, Field

from hoard import shipments
from hoard.api import scans
from hoard.api.adjustments import NOT_FOUND, Responses, changes, known, location_or_main
from hoard.api.deps import HoardError
from hoard.api.orders import OrderRead, Positive, Text, describe_order, refuse_store_owned
from hoard.records import Lot, LotSource, Order, ReturnLine, Shipment, ShipmentLine
from hoard.shipments import Pick, ShipmentError, ShipmentRefused
from seneschal_core.api import SessionDep, WriteSessionDep, refuse
from seneschal_core.db import WriteSession
from seneschal_core.sessions import SessionClaims

router = APIRouter(prefix="/shipments", tags=["shipments"])

REFUSED: Responses = {
    409: {
        "description": "SHIPMENT_SHIPPED, SHIPMENT_NOT_SHIPPED, SHIPMENT_RETURNED, "
        "SHIPMENT_CANCELLED, SHIPMENT_EMPTY, SHIPMENT_FROM_HISTORY, ORDER_CANCELLED, "
        "MANAGED_BY_STORE, LOT_SCAN_REQUIRED, LOT_ON_HOLD, LOT_REQUIRED"
    },
    422: {
        "description": "SHIPMENT_LINE_NOT_SHIPPABLE, SHIPMENT_OVER_ORDER, "
        "SHIPMENT_LOT_MISMATCH, RETURN_OVER_SHIPPED"
    },
}
#: What a scan onto a shipment may be refused for besides.
SCAN_REFUSED: Responses = {
    409: {
        "description": "SHIPMENT_SHIPPED, SHIPMENT_CANCELLED, LOT_ON_HOLD, SCAN_LOT_SHORT, "
        "SCAN_ITEM_DONE"
    },
    422: {
        "description": "SCAN_AMBIGUOUS, SCAN_NAMES_NO_LOT, SCAN_LOT_NOT_FOUND, "
        "SCAN_NOT_ON_SHIPMENT, SHIPMENT_LOT_MISMATCH"
    },
}
#: What was asked of a shipment that no state of it allows.
_INVALID = frozenset(
    {
        ShipmentError.SHIPMENT_LINE_NOT_SHIPPABLE,
        ShipmentError.SHIPMENT_OVER_ORDER,
        ShipmentError.SHIPMENT_LOT_MISMATCH,
        ShipmentError.RETURN_OVER_SHIPPED,
        ShipmentError.SCAN_NOT_ON_SHIPMENT,
    }
)


class ScanError(StrEnum):
    """Why a scan placed no lot on a shipment, before the shipment was asked."""

    #: A scanner may have dropped a separator: the label's lot isn't certain.
    SCAN_AMBIGUOUS = "SCAN_AMBIGUOUS"
    #: The scan names an item but no lot: scan the lot's label.
    SCAN_NAMES_NO_LOT = "SCAN_NAMES_NO_LOT"
    #: The label's lot isn't one Hoard holds at the shipment's location.
    SCAN_LOT_NOT_FOUND = "SCAN_LOT_NOT_FOUND"


def _refused(refused: ShipmentRefused) -> Exception:
    """The answer to a change :mod:`hoard.shipments` refused."""
    if refused.code in _INVALID:
        return refuse(status.HTTP_422_UNPROCESSABLE_CONTENT, refused.code)
    return refuse(status.HTTP_409_CONFLICT, refused.code)


async def _order_of(db: WriteSession, shipment: Shipment) -> OrderRead:
    return await describe_order(db, await db.get_one(Order, shipment.order_id))


async def _changeable(db: WriteSession, shipment_id: int) -> Shipment:
    """The shipment, refused while its order's store manages it."""
    shipment = await known(db, Shipment, shipment_id)
    await _refuse_store_owned(db, shipment)
    return shipment


async def _refuse_store_owned(db: WriteSession, shipment: Shipment) -> None:
    await refuse_store_owned(db, await db.get_one(Order, shipment.order_id))


class ShipmentLineCreate(BaseModel):
    order_line_id: int
    #: All it has left when not given.
    quantity: Positive | None = None
    #: The lot it draws; one is assumed when it ships when not given.
    lot_id: int | None = None


class ShipmentCreate(BaseModel):
    order_id: int
    #: The order's location when not given.
    location_id: int | None = None
    #: All the order has left to ship when none are given.
    lines: list[ShipmentLineCreate] = Field(default_factory=list, max_length=500)
    #: The carrier: an Initiative contact's id.
    carrier_contact_id: int | None = None
    tracking: Text | None = None
    #: The order's when not given.
    delivery_address: Text | None = None
    reference: Text | None = None


@router.post("", status_code=status.HTTP_201_CREATED, responses={**NOT_FOUND, **REFUSED})
async def create_shipment(
    db: WriteSessionDep, session: SessionDep, body: ShipmentCreate
) -> OrderRead:
    """Plan a shipment of an order, pending."""
    order = await known(db, Order, body.order_id)
    await refuse_store_owned(db, order)
    location_id = (
        order.location_id
        if body.location_id is None
        else await location_or_main(db, body.location_id)
    )
    source = _by_hand(session)
    picks = [Pick(line.order_line_id, line.quantity, line.lot_id, source) for line in body.lines]
    try:
        shipment = await shipments.add(db, order, location_id, picks)
    except ShipmentRefused as refused:
        raise _refused(refused) from None
    shipment.carrier_contact_id = body.carrier_contact_id
    shipment.tracking = body.tracking or None
    shipment.delivery_address = body.delivery_address or order.delivery_address
    shipment.reference = body.reference or None
    await db.commit()
    return await describe_order(db, order)


class ShipmentUpdate(BaseModel):
    """A change to a shipment: only what is sent. Its location changes only
    while it is pending; the rest, whenever."""

    location_id: int | None = None
    #: Null takes it off, as for the others below.
    carrier_contact_id: int | None = None
    tracking: Text | None = None
    delivery_address: Text | None = None
    reference: Text | None = None


@router.patch("/{shipment_id}", responses={**NOT_FOUND, **REFUSED})
async def update_shipment(db: WriteSessionDep, shipment_id: int, body: ShipmentUpdate) -> OrderRead:
    """Change a shipment."""
    sent = changes(
        body, nullable={"carrier_contact_id", "tracking", "delivery_address", "reference"}
    )
    shipment = await _changeable(db, shipment_id)
    if "location_id" in sent:
        try:
            await shipments.move(db, shipment, await location_or_main(db, sent.pop("location_id")))
        except ShipmentRefused as refused:
            raise _refused(refused) from None
    for name, value in sent.items():
        setattr(shipment, name, value or None)
    await db.commit()
    return await _order_of(db, shipment)


@router.delete("/{shipment_id}", responses={**NOT_FOUND, **REFUSED})
async def delete_shipment(db: WriteSessionDep, shipment_id: int) -> OrderRead:
    """Delete a shipment that has not shipped."""
    shipment = await _changeable(db, shipment_id)
    order = await db.get_one(Order, shipment.order_id)
    try:
        await shipments.remove(db, shipment)
    except ShipmentRefused as refused:
        raise _refused(refused) from None
    await db.commit()
    return await describe_order(db, order)


class ShipmentShip(BaseModel):
    #: Today when not given.
    shipped_on: date | None = None


@router.post("/{shipment_id}/ship", responses={**NOT_FOUND, **REFUSED})
async def ship_shipment(db: WriteSessionDep, shipment_id: int, body: ShipmentShip) -> OrderRead:
    """Ship a pending shipment: each line out of its lot, a lot assumed for
    each that names none."""
    shipment = await _changeable(db, shipment_id)
    try:
        await shipments.ship(db, shipment, body.shipped_on or date.today())
    except ShipmentRefused as refused:
        raise _refused(refused) from None
    await db.commit()
    return await _order_of(db, shipment)


@router.post("/{shipment_id}/unship", responses={**NOT_FOUND, **REFUSED})
async def unship_shipment(db: WriteSessionDep, shipment_id: int) -> OrderRead:
    """Take back what a shipment moved, while nothing has come back of it."""
    shipment = await _changeable(db, shipment_id)
    try:
        await shipments.unship(db, shipment)
    except ShipmentRefused as refused:
        raise _refused(refused) from None
    await db.commit()
    return await _order_of(db, shipment)


def _by_hand(session: SessionClaims) -> LotSource:
    """How a lot chosen by hand was chosen: by an admin, who may where a scan
    is required, or by a member."""
    return LotSource.ADMIN if session.admin else LotSource.CHOSEN


async def _line(db: WriteSession, shipment_id: int, line_id: int) -> tuple[Shipment, ShipmentLine]:
    shipment = await known(db, Shipment, shipment_id)
    line = await known(db, ShipmentLine, line_id)
    if line.shipment_id != shipment.id:
        raise refuse(status.HTTP_404_NOT_FOUND, HoardError.NOT_FOUND)
    return shipment, line


class ShipmentLineUpdate(BaseModel):
    """A change to a pending shipment's line: only what is sent."""

    quantity: Positive | None = None
    #: Null leaves the lot for Hoard to assume when it ships.
    lot_id: int | None = None


@router.patch("/{shipment_id}/lines/{line_id}", responses={**NOT_FOUND, **REFUSED})
async def update_shipment_line(
    db: WriteSessionDep,
    session: SessionDep,
    shipment_id: int,
    line_id: int,
    body: ShipmentLineUpdate,
) -> OrderRead:
    """Change how much a pending shipment's line sends, or the lot it draws.
    Only its lot changes while the order's store manages the order."""
    sent = changes(body, nullable={"lot_id"})
    shipment, line = await _line(db, shipment_id, line_id)
    if "quantity" in sent:
        await _refuse_store_owned(db, shipment)
    try:
        if "quantity" in sent:
            await shipments.set_quantity(db, shipment, line, sent["quantity"])
        if "lot_id" in sent:
            await shipments.set_lot(db, shipment, line, sent["lot_id"], _by_hand(session))
    except ShipmentRefused as refused:
        raise _refused(refused) from None
    await db.commit()
    return await _order_of(db, shipment)


@router.delete("/{shipment_id}/lines/{line_id}", responses={**NOT_FOUND, **REFUSED})
async def delete_shipment_line(db: WriteSessionDep, shipment_id: int, line_id: int) -> OrderRead:
    """Take a line off a pending shipment."""
    shipment, line = await _line(db, shipment_id, line_id)
    await _refuse_store_owned(db, shipment)
    try:
        await shipments.remove_line(db, shipment, line)
    except ShipmentRefused as refused:
        raise _refused(refused) from None
    await db.commit()
    return await _order_of(db, shipment)


class ShipmentScan(BaseModel):
    """A scan of a lot's label, onto a pending shipment."""

    text: str = Field(min_length=1, max_length=2048)
    #: What the scanner knew of the symbol (``POST /scans``).
    gs1: bool | None = None


class ShipmentScanRead(BaseModel):
    """The order, with the line the scanned lot was placed on."""

    order: OrderRead
    line_id: int
    lot_id: int
    #: The line sent more than the lot holds, so the rest went to a new line
    #: that waits for another scan.
    split: bool
    #: Another lot is the one the item's picking rule takes first.
    not_first: bool


@router.post("/{shipment_id}/scans", responses={**NOT_FOUND, **SCAN_REFUSED})
async def scan_onto_shipment(
    db: WriteSessionDep, shipment_id: int, body: ShipmentScan
) -> ShipmentScanRead:
    """Place the lot a scanned label names on the pending shipment's line of
    its item, as scanned (``hoard.shipments.scan_lot``)."""
    shipment = await known(db, Shipment, shipment_id)
    read = await scans.resolve(
        db, scans.ScanAsk(text=body.text, gs1=body.gs1, location_id=shipment.location_id)
    )
    if "ambiguous" in read.problems:
        raise refuse(status.HTTP_422_UNPROCESSABLE_CONTENT, ScanError.SCAN_AMBIGUOUS)
    if not read.lots:
        code = ScanError.SCAN_LOT_NOT_FOUND if read.lot else ScanError.SCAN_NAMES_NO_LOT
        raise refuse(status.HTTP_422_UNPROCESSABLE_CONTENT, code)
    lots = [await db.get_one(Lot, each.id) for each in read.lots]
    try:
        scanned = await shipments.scan_lot(db, shipment, lots)
    except ShipmentRefused as refused:
        raise _refused(refused) from None
    line_id, lot_id = scanned.line.id, scanned.line.lot_id
    assert lot_id is not None
    await db.commit()
    return ShipmentScanRead(
        order=await _order_of(db, shipment),
        line_id=line_id,
        lot_id=lot_id,
        split=scanned.split,
        not_first=scanned.not_first,
    )


class ShipmentReturnCreate(BaseModel):
    quantity: Positive
    #: Today when not given.
    dated_on: date | None = None
    #: Written off as returned damaged rather than restocked.
    damaged: bool = False


@router.post(
    "/{shipment_id}/lines/{line_id}/returns",
    status_code=status.HTTP_201_CREATED,
    responses={**NOT_FOUND, **REFUSED},
)
async def create_shipment_return(
    db: WriteSessionDep, shipment_id: int, line_id: int, body: ShipmentReturnCreate
) -> OrderRead:
    """Record what came back of a shipped line: restocked into the lot it
    left, or written off as returned damaged."""
    shipment, line = await _line(db, shipment_id, line_id)
    try:
        await shipments.record_return(
            db, line, body.quantity, body.dated_on or date.today(), damaged=body.damaged
        )
    except ShipmentRefused as refused:
        raise _refused(refused) from None
    await db.commit()
    return await _order_of(db, shipment)


@router.delete("/{shipment_id}/returns/{return_id}", responses=NOT_FOUND)
async def delete_shipment_return(
    db: WriteSessionDep, shipment_id: int, return_id: int
) -> OrderRead:
    """Take back a return, and what it restocked or wrote off."""
    shipment = await known(db, Shipment, shipment_id)
    back = await known(db, ReturnLine, return_id)
    line = await known(db, ShipmentLine, back.shipment_line_id)
    if line.shipment_id != shipment.id:
        raise refuse(status.HTTP_404_NOT_FOUND, HoardError.NOT_FOUND)
    await shipments.remove_return(db, back)
    await db.commit()
    return await _order_of(db, shipment)
