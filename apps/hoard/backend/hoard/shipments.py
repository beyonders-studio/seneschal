"""Shipments: some of an order's lines sent from a location, and what comes
back of them.

A shipment's lines name the lot each draws on: the lot someone recorded, for
instance by scanning it, or else the one Hoard assumes when it ships, as a
production run does (``hoard.stock.draw_lots``), the line split over as many
lots as it takes and marked assumed. Only shipping it moves stock: each line
out of its lot, costed at the item's average on the day it shipped
(``hoard.costing``). Unshipping it takes those adjustments back and returns
its assumed lines to naming no lot. A lot on hold is never shipped, and a
shipment by hand of an item whose category records every lot ships only from
a lot each line names (``hoard.holds``); a store's fulfillment, which names no
lot, ships what it sent.

A shipment is planned while pending: its lines, no more of each order line
than the order's other shipments leave, and the lot each draws. Shipping or
unshipping one marks its order shipped or open again (``hoard.orders``).

What comes back of a shipped line comes in to the lot it left, at what it
shipped at; what comes back damaged is then written off at once with the
``returned_damaged`` reason, so it is recorded without being restocked.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import delete, func
from sqlalchemy.dialects.postgresql import insert
from sqlmodel import col, select

from hoard import costing, holds, orders
from hoard.holds import HoldError
from hoard.orders import OrderError
from hoard.records import (
    Adjustment,
    AdjustmentKind,
    AdjustmentReason,
    DefaultReason,
    Lot,
    LotScan,
    LotSource,
    Order,
    OrderLine,
    OrderStatus,
    ReturnLine,
    Shipment,
    ShipmentLine,
    ShipmentStatus,
)
from hoard.stock import draw_lots, lot_scan
from seneschal_core.db import WriteSession


class ShipmentError(StrEnum):
    """Why a change to a shipment was refused: codes pages translate."""

    #: It has shipped: what it moved changes only once it is unshipped.
    SHIPMENT_SHIPPED = "SHIPMENT_SHIPPED"
    #: It has not shipped, so there is nothing to take back.
    SHIPMENT_NOT_SHIPPED = "SHIPMENT_NOT_SHIPPED"
    #: Something came back of it.
    SHIPMENT_RETURNED = "SHIPMENT_RETURNED"
    #: It is cancelled.
    SHIPMENT_CANCELLED = "SHIPMENT_CANCELLED"
    #: It has no lines, or the order has nothing left to ship.
    SHIPMENT_EMPTY = "SHIPMENT_EMPTY"
    #: The line is not one of the order's that ships: a bundle ships as its parts.
    SHIPMENT_LINE_NOT_SHIPPABLE = "SHIPMENT_LINE_NOT_SHIPPABLE"
    #: More than the order line has left once its other shipments are counted.
    SHIPMENT_OVER_ORDER = "SHIPMENT_OVER_ORDER"
    #: The lot is not one of the line's item at the shipment's location.
    SHIPMENT_LOT_MISMATCH = "SHIPMENT_LOT_MISMATCH"
    #: More came back than the line shipped.
    RETURN_OVER_SHIPPED = "RETURN_OVER_SHIPPED"
    #: It was copied from the store's history and moved no stock, so nothing
    #: comes back of it either.
    SHIPMENT_FROM_HISTORY = "SHIPMENT_FROM_HISTORY"
    #: A line's item's category ships only lots scanned, or chosen by an
    #: admin, and the line's lot was neither.
    LOT_SCAN_REQUIRED = "LOT_SCAN_REQUIRED"
    #: The scanned lot is of no item the shipment sends.
    SCAN_NOT_ON_SHIPMENT = "SCAN_NOT_ON_SHIPMENT"
    #: The scanned lot holds nothing more for the shipment to take.
    SCAN_LOT_SHORT = "SCAN_LOT_SHORT"
    #: Every line of the scanned lot's item already has a scanned lot.
    SCAN_ITEM_DONE = "SCAN_ITEM_DONE"


class ShipmentRefused(Exception):
    def __init__(self, code: ShipmentError | OrderError | HoldError) -> None:
        super().__init__(code.value)
        self.code = code


async def lines(db: WriteSession, shipment_id: int) -> list[ShipmentLine]:
    """A shipment's lines, in the order they were added."""
    found = await db.exec(
        select(ShipmentLine)
        .where(ShipmentLine.shipment_id == shipment_id)
        .order_by(col(ShipmentLine.id))
        .execution_options(populate_existing=True)
    )
    return list(found.all())


async def _assume_lots(db: WriteSession, shipment: Shipment) -> list[ShipmentLine]:
    """Assume a lot for each line that names none, splitting a line over as
    many lots as it takes. Answers the lines."""
    found = await lines(db, shipment.id)
    drawn: dict[int, Decimal] = defaultdict(Decimal)
    for line in found:
        if line.lot_id is not None:
            drawn[line.lot_id] += line.quantity
    picked: list[ShipmentLine] = []
    for line in found:
        if line.lot_id is not None:
            picked.append(line)
            continue
        current: ShipmentLine | None = line
        for lot_id, take in await draw_lots(
            db, line.item_id, shipment.location_id, line.quantity, drawn
        ):
            if current is None:
                current = ShipmentLine(
                    organization_id=db.organization_id,
                    shipment_id=shipment.id,
                    order_line_id=line.order_line_id,
                    item_id=line.item_id,
                )
                db.add(current)
            current.lot_id, current.quantity, current.assumed = lot_id, take, lot_id is not None
            picked.append(current)
            current = None
    await db.flush()
    return picked


async def post(db: WriteSession, shipment: Shipment, shipped_on: date) -> None:
    """Ship ``shipment`` on ``shipped_on``: each line out of its lot, assuming
    a lot for each that names none, and each item's cost re-derived. Refused
    while a line names a lot on hold."""
    if shipment.status is ShipmentStatus.SHIPPED:
        raise ShipmentRefused(ShipmentError.SHIPMENT_SHIPPED)
    draws = [(line.item_id, line.lot_id) for line in await lines(db, shipment.id)]
    refused = await holds.refusal(db, draws, named=False)
    if refused is not None:
        raise ShipmentRefused(refused)
    shipped = await _assume_lots(db, shipment)
    for line in shipped:
        db.add(
            Adjustment(
                organization_id=db.organization_id,
                item_id=line.item_id,
                location_id=shipment.location_id,
                lot_id=line.lot_id,
                kind=AdjustmentKind.SHIPMENT,
                dated_on=shipped_on,
                change=-line.quantity,
                shipment_line_id=line.id,
            )
        )
    shipment.status = ShipmentStatus.SHIPPED
    shipment.shipped_on = shipped_on
    await db.flush()
    for item_id in sorted({line.item_id for line in shipped}):
        await costing.rederive(db, item_id)


async def unpost(db: WriteSession, shipment: Shipment) -> None:
    """Take back what a shipped shipment moved: its adjustments go, its
    assumed lines name no lot again, and it is pending. Refused once something
    has come back of it."""
    if shipment.status is not ShipmentStatus.SHIPPED:
        raise ShipmentRefused(ShipmentError.SHIPMENT_NOT_SHIPPED)
    shipped = await lines(db, shipment.id)
    returned = await db.exec(
        select(func.count())
        .select_from(ReturnLine)
        .where(col(ReturnLine.shipment_line_id).in_([line.id for line in shipped]))
    )
    if returned.one():
        raise ShipmentRefused(ShipmentError.SHIPMENT_RETURNED)
    await db.execute(
        delete(Adjustment).where(
            col(Adjustment.shipment_line_id).in_([line.id for line in shipped])
        )
    )
    # Each order line's assumed lines become one line again, naming no lot.
    kept: dict[int, ShipmentLine] = {}
    for line in shipped:
        if line.lot_id is not None and not line.assumed:
            continue
        if line.order_line_id in kept:
            kept[line.order_line_id].quantity += line.quantity
            await db.delete(line)
        else:
            line.lot_id, line.assumed = None, False
            kept[line.order_line_id] = line
    shipment.status = ShipmentStatus.PENDING
    shipment.shipped_on = None
    await db.flush()
    for item_id in sorted({line.item_id for line in shipped}):
        await costing.rederive(db, item_id)


@dataclass(frozen=True)
class Pick:
    """An order line to ship: so much of it, or all it has left, from a lot,
    or from the one assumed when it ships; a lot chosen as ``source`` says."""

    order_line_id: int
    quantity: Decimal | None = None
    lot_id: int | None = None
    source: LotSource = LotSource.CHOSEN


def _pending(shipment: Shipment) -> None:
    if shipment.status is ShipmentStatus.SHIPPED:
        raise ShipmentRefused(ShipmentError.SHIPMENT_SHIPPED)
    if shipment.status is ShipmentStatus.CANCELLED:
        raise ShipmentRefused(ShipmentError.SHIPMENT_CANCELLED)


async def _check_lot(
    db: WriteSession, shipment: Shipment, item_id: int, lot_id: int | None
) -> None:
    if lot_id is None:
        return
    lot = await db.get(Lot, lot_id)
    if lot is None or (lot.item_id, lot.location_id) != (item_id, shipment.location_id):
        raise ShipmentRefused(ShipmentError.SHIPMENT_LOT_MISMATCH)
    if await holds.refusal(db, [(item_id, lot_id)]):
        raise ShipmentRefused(HoldError.LOT_ON_HOLD)


async def _left(db: WriteSession, shipment: Shipment, line: ShipmentLine) -> Decimal:
    """What ``line``'s order line has left to ship besides ``line`` itself."""
    order_line = await db.get_one(OrderLine, line.order_line_id)
    holding = await orders.held(db, shipment.order_id)
    return orders.selling(order_line) - holding[order_line.id] + line.quantity


async def add(db: WriteSession, order: Order, location_id: int, picks: Sequence[Pick]) -> Shipment:
    """Add a pending shipment of ``order`` from a location, with ``picks``, or
    with all the order has left to ship when there are none."""
    if order.status is OrderStatus.CANCELLED:
        raise ShipmentRefused(OrderError.ORDER_CANCELLED)
    to_ship = {line.id: line for line in orders.shippable(await orders.lines(db, order.id))}
    holding = await orders.held(db, order.id)
    if not picks:
        picks = [Pick(id_) for id_, line in to_ship.items() if orders.selling(line) > holding[id_]]
    if not picks:
        raise ShipmentRefused(ShipmentError.SHIPMENT_EMPTY)
    shipment = Shipment(
        organization_id=db.organization_id, order_id=order.id, location_id=location_id
    )
    planned: list[tuple[OrderLine, Decimal, int | None, LotSource | None]] = []
    for pick in picks:
        line = to_ship.get(pick.order_line_id)
        if line is None:
            raise ShipmentRefused(ShipmentError.SHIPMENT_LINE_NOT_SHIPPABLE)
        left = orders.selling(line) - holding[line.id]
        quantity = left if pick.quantity is None else pick.quantity
        if quantity <= 0 or quantity > left:
            raise ShipmentRefused(ShipmentError.SHIPMENT_OVER_ORDER)
        await _check_lot(db, shipment, line.item_id, pick.lot_id)
        holding[line.id] += quantity
        planned.append((line, quantity, pick.lot_id, pick.source if pick.lot_id else None))
    db.add(shipment)
    await db.flush()
    db.add_all(
        ShipmentLine(
            organization_id=db.organization_id,
            shipment_id=shipment.id,
            order_line_id=line.id,
            item_id=line.item_id,
            lot_id=lot_id,
            quantity=quantity,
            lot_source=source,
        )
        for line, quantity, lot_id, source in planned
    )
    await db.flush()
    return shipment


async def move(db: WriteSession, shipment: Shipment, location_id: int) -> None:
    """Send a pending shipment from another location; the lots its lines
    recorded are another location's, so they name none again."""
    _pending(shipment)
    if location_id == shipment.location_id:
        return
    shipment.location_id = location_id
    for line in await lines(db, shipment.id):
        line.lot_id, line.assumed, line.lot_source = None, False, None
    await db.flush()


async def set_quantity(
    db: WriteSession, shipment: Shipment, line: ShipmentLine, quantity: Decimal
) -> None:
    """Send ``quantity`` of a pending shipment's line: no more than its order
    line has left."""
    _pending(shipment)
    if quantity > await _left(db, shipment, line):
        raise ShipmentRefused(ShipmentError.SHIPMENT_OVER_ORDER)
    line.quantity = quantity
    await db.flush()


async def set_lot(
    db: WriteSession,
    shipment: Shipment,
    line: ShipmentLine,
    lot_id: int | None,
    source: LotSource = LotSource.CHOSEN,
) -> None:
    """Record the lot a pending shipment's line draws, chosen as ``source``
    says, or none, to assume one when it ships."""
    _pending(shipment)
    await _check_lot(db, shipment, line.item_id, lot_id)
    line.lot_id, line.assumed = lot_id, False
    line.lot_source = source if lot_id is not None else None
    await db.flush()


@dataclass(frozen=True)
class Scanned:
    """A scanned lot, placed on a shipment's line."""

    line: ShipmentLine
    #: The line sent more than the lot holds, so the rest went to a new line
    #: that names no lot yet.
    split: bool
    #: Another lot is the one the item's picking rule takes first.
    not_first: bool


async def scan_lot(db: WriteSession, shipment: Shipment, lots: Sequence[Lot]) -> Scanned:
    """Place the first of ``lots``, those a scan named at the shipment's
    location, whose item the shipment sends, on that item's line: one whose
    lot wasn't scanned, or the one that already scanned it. The line takes
    no more than the lot holds besides what the shipment's other lines take
    from it; the rest goes to a new line that names no lot. Refused for a
    lot on hold, for one that holds nothing more, and once every line of its
    item has a scanned lot. A lot scanned again is the line that has it."""
    _pending(shipment)
    sending = await lines(db, shipment.id)
    items = {line.item_id for line in sending}
    lot = next((each for each in lots if each.item_id in items), None)
    if lot is None:
        raise ShipmentRefused(ShipmentError.SCAN_NOT_ON_SHIPMENT)
    await _check_lot(db, shipment, lot.item_id, lot.id)
    mine = [line for line in sending if line.item_id == lot.item_id]
    again = next(
        (line for line in mine if line.lot_id == lot.id and line.lot_source is LotSource.SCANNED),
        None,
    )
    if again is not None:
        return Scanned(line=again, split=False, not_first=False)
    line = next((each for each in mine if each.lot_source is not LotSource.SCANNED), None)
    if line is None:
        raise ShipmentRefused(ShipmentError.SCAN_ITEM_DONE)
    taken = sum(
        (each.quantity for each in sending if each.lot_id == lot.id and each is not line),
        Decimal(0),
    )
    holds_more = lot.balance - taken
    if holds_more <= 0:
        raise ShipmentRefused(ShipmentError.SCAN_LOT_SHORT)
    drawn: dict[int, Decimal] = defaultdict(Decimal)
    for each in sending:
        if each.lot_id is not None and each is not line and each.lot_source is not None:
            drawn[each.lot_id] += each.quantity
    first = next(
        (
            lot_id
            for lot_id, _ in await draw_lots(
                db, lot.item_id, shipment.location_id, line.quantity, drawn
            )
        ),
        None,
    )
    split = holds_more < line.quantity
    if split:
        db.add(
            ShipmentLine(
                organization_id=db.organization_id,
                shipment_id=shipment.id,
                order_line_id=line.order_line_id,
                item_id=line.item_id,
                quantity=line.quantity - holds_more,
            )
        )
        line.quantity = holds_more
    line.lot_id, line.assumed, line.lot_source = lot.id, False, LotSource.SCANNED
    await db.flush()
    return Scanned(line=line, split=split, not_first=first is not None and first != lot.id)


async def remove_line(db: WriteSession, shipment: Shipment, line: ShipmentLine) -> None:
    """Take a line off a pending shipment."""
    _pending(shipment)
    await db.delete(line)
    await db.flush()


async def remove(db: WriteSession, shipment: Shipment) -> None:
    """Delete a shipment that has not shipped."""
    if shipment.status is ShipmentStatus.SHIPPED:
        raise ShipmentRefused(ShipmentError.SHIPMENT_SHIPPED)
    await db.delete(shipment)
    await db.flush()


async def ship(db: WriteSession, shipment: Shipment, shipped_on: date) -> None:
    """Ship a pending shipment of an order that is not cancelled, on
    ``shipped_on``, and mark the order shipped once nothing is left. Refused
    while a line names no lot of an item whose every lot is recorded."""
    _pending(shipment)
    order = await db.get_one(Order, shipment.order_id)
    if order.status is OrderStatus.CANCELLED:
        raise ShipmentRefused(OrderError.ORDER_CANCELLED)
    sending = await lines(db, shipment.id)
    if not sending:
        raise ShipmentRefused(ShipmentError.SHIPMENT_EMPTY)
    refused = await holds.refusal(db, [(line.item_id, line.lot_id) for line in sending])
    if refused is not None:
        raise ShipmentRefused(refused)
    for item_id in sorted({line.item_id for line in sending}):
        if await lot_scan(db, item_id) is not LotScan.REQUIRED:
            continue
        if any(
            line.lot_source not in (LotSource.SCANNED, LotSource.ADMIN)
            for line in sending
            if line.item_id == item_id
        ):
            raise ShipmentRefused(ShipmentError.LOT_SCAN_REQUIRED)
    await post(db, shipment, shipped_on)
    await orders.settle(db, order)


async def unship(db: WriteSession, shipment: Shipment) -> None:
    """Take back what a shipment moved, and mark its order open again."""
    await unpost(db, shipment)
    await orders.settle(db, await db.get_one(Order, shipment.order_id))


async def returned(db: WriteSession, line_ids: Sequence[int]) -> dict[int, Decimal]:
    """How much came back of each of a shipment's lines, by line id."""
    found = await db.exec(
        select(ReturnLine.shipment_line_id, func.sum(ReturnLine.quantity))
        .where(col(ReturnLine.shipment_line_id).in_(line_ids))
        .group_by(col(ReturnLine.shipment_line_id))
    )
    return defaultdict(Decimal, {id_: Decimal(quantity) for id_, quantity in found.all()})


async def _returned_damaged(db: WriteSession) -> int:
    """The ``returned_damaged`` reason's id, made again if it was removed."""
    await db.execute(
        insert(AdjustmentReason)
        .values(organization_id=db.organization_id, key=DefaultReason.RETURNED_DAMAGED)
        .on_conflict_do_nothing()
    )
    reason = await db.exec(
        select(AdjustmentReason.id).where(
            AdjustmentReason.key == DefaultReason.RETURNED_DAMAGED.value
        )
    )
    return reason.one()


async def record_return(
    db: WriteSession, line: ShipmentLine, quantity: Decimal, dated_on: date, *, damaged: bool
) -> ReturnLine:
    """Record ``quantity`` of a shipped line coming back on ``dated_on``: in,
    at what it shipped at, or, ``damaged``, written off as returned damaged."""
    shipment = await db.get_one(Shipment, line.shipment_id)
    if shipment.status is not ShipmentStatus.SHIPPED:
        raise ShipmentRefused(ShipmentError.SHIPMENT_NOT_SHIPPED)
    out = (await db.exec(select(Adjustment).where(Adjustment.shipment_line_id == line.id))).first()
    if out is None:
        raise ShipmentRefused(ShipmentError.SHIPMENT_FROM_HISTORY)
    if quantity > line.quantity - (await returned(db, [line.id]))[line.id]:
        raise ShipmentRefused(ShipmentError.RETURN_OVER_SHIPPED)
    back = ReturnLine(
        organization_id=db.organization_id,
        shipment_line_id=line.id,
        quantity=quantity,
        dated_on=dated_on,
        damaged=damaged,
    )
    db.add(back)
    await db.flush()
    changes = [(AdjustmentKind.RETURN, quantity, out.unit_cost, None)]
    if damaged:
        changes.append((AdjustmentKind.MANUAL, -quantity, Decimal(0), await _returned_damaged(db)))
    for kind, change, unit_cost, reason_id in changes:
        db.add(
            Adjustment(
                organization_id=db.organization_id,
                item_id=line.item_id,
                location_id=shipment.location_id,
                lot_id=line.lot_id,
                kind=kind,
                dated_on=dated_on,
                change=change,
                unit_cost=unit_cost,
                reason_id=reason_id,
                return_line_id=back.id,
            )
        )
        # The write-off follows what came in, in the ledger's order.
        await db.flush()
    await costing.rederive(db, line.item_id)
    return back


async def remove_return(db: WriteSession, back: ReturnLine) -> None:
    """Take back a return: what it brought in, and what it wrote off, go."""
    line = await db.get_one(ShipmentLine, back.shipment_line_id)
    await db.delete(back)
    await db.flush()
    await costing.rederive(db, line.item_id)
