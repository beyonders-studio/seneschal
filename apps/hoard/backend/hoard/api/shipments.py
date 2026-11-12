"""Shipments of an order's lines, the lots they draw, and what comes back
(``hoard.shipments``).

A shipment is planned pending: its lines, each no more than its order line
has left, and the lot each draws, recorded or left for Hoard to assume when it
ships. Only shipping it moves stock. What comes back of a shipped line is
restocked, or written off when damaged. Every route answers the shipment's
order as :func:`hoard.api.orders.describe_order` reads it.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, status
from pydantic import BaseModel, Field

from hoard import shipments
from hoard.api.adjustments import NOT_FOUND, Responses, changes, known, location_or_main
from hoard.api.deps import HoardError
from hoard.api.orders import OrderRead, Positive, Text, describe_order
from hoard.records import Order, ReturnLine, Shipment, ShipmentLine
from hoard.shipments import Pick, ShipmentError, ShipmentRefused
from seneschal_core.api import WriteSessionDep, refuse
from seneschal_core.db import WriteSession

router = APIRouter(prefix="/shipments", tags=["shipments"])

REFUSED: Responses = {
    409: {
        "description": "SHIPMENT_SHIPPED, SHIPMENT_NOT_SHIPPED, SHIPMENT_RETURNED, "
        "SHIPMENT_CANCELLED, SHIPMENT_EMPTY, SHIPMENT_FROM_HISTORY, ORDER_CANCELLED"
    },
    422: {
        "description": "SHIPMENT_LINE_NOT_SHIPPABLE, SHIPMENT_OVER_ORDER, "
        "SHIPMENT_LOT_MISMATCH, RETURN_OVER_SHIPPED"
    },
}
#: What was asked of a shipment that no state of it allows.
_INVALID = frozenset(
    {
        ShipmentError.SHIPMENT_LINE_NOT_SHIPPABLE,
        ShipmentError.SHIPMENT_OVER_ORDER,
        ShipmentError.SHIPMENT_LOT_MISMATCH,
        ShipmentError.RETURN_OVER_SHIPPED,
    }
)


def _refused(refused: ShipmentRefused) -> Exception:
    """The answer to a change :mod:`hoard.shipments` refused."""
    if refused.code in _INVALID:
        return refuse(status.HTTP_422_UNPROCESSABLE_CONTENT, refused.code)
    return refuse(status.HTTP_409_CONFLICT, refused.code)


async def _order_of(db: WriteSession, shipment: Shipment) -> OrderRead:
    return await describe_order(db, await db.get_one(Order, shipment.order_id))


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
async def create_shipment(db: WriteSessionDep, body: ShipmentCreate) -> OrderRead:
    """Plan a shipment of an order, pending."""
    order = await known(db, Order, body.order_id)
    location_id = (
        order.location_id
        if body.location_id is None
        else await location_or_main(db, body.location_id)
    )
    picks = [Pick(line.order_line_id, line.quantity, line.lot_id) for line in body.lines]
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
    shipment = await known(db, Shipment, shipment_id)
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
    shipment = await known(db, Shipment, shipment_id)
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
    shipment = await known(db, Shipment, shipment_id)
    try:
        await shipments.ship(db, shipment, body.shipped_on or date.today())
    except ShipmentRefused as refused:
        raise _refused(refused) from None
    await db.commit()
    return await _order_of(db, shipment)


@router.post("/{shipment_id}/unship", responses={**NOT_FOUND, **REFUSED})
async def unship_shipment(db: WriteSessionDep, shipment_id: int) -> OrderRead:
    """Take back what a shipment moved, while nothing has come back of it."""
    shipment = await known(db, Shipment, shipment_id)
    try:
        await shipments.unship(db, shipment)
    except ShipmentRefused as refused:
        raise _refused(refused) from None
    await db.commit()
    return await _order_of(db, shipment)


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
    db: WriteSessionDep, shipment_id: int, line_id: int, body: ShipmentLineUpdate
) -> OrderRead:
    """Change how much a pending shipment's line sends, or the lot it draws."""
    sent = changes(body, nullable={"lot_id"})
    shipment, line = await _line(db, shipment_id, line_id)
    try:
        if "quantity" in sent:
            await shipments.set_quantity(db, shipment, line, sent["quantity"])
        if "lot_id" in sent:
            await shipments.set_lot(db, shipment, line, sent["lot_id"])
    except ShipmentRefused as refused:
        raise _refused(refused) from None
    await db.commit()
    return await _order_of(db, shipment)


@router.delete("/{shipment_id}/lines/{line_id}", responses={**NOT_FOUND, **REFUSED})
async def delete_shipment_line(db: WriteSessionDep, shipment_id: int, line_id: int) -> OrderRead:
    """Take a line off a pending shipment."""
    shipment, line = await _line(db, shipment_id, line_id)
    try:
        await shipments.remove_line(db, shipment, line)
    except ShipmentRefused as refused:
        raise _refused(refused) from None
    await db.commit()
    return await _order_of(db, shipment)


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
