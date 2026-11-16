"""Tracing a lot: back to the lots and suppliers that went into it, and
forward to what was made from it and where it went.

**Back**, a production run's lines lead to the lots it consumed, a bought lot
to its purchase line and supplier, and a lot moved in to the lot it was moved
from, at every level. **Forward** is two lists: what went out with the lot as
someone recorded it (the runs, shipments and transfers that name it, not
assumed, at every level), and what may have (the lines that assume it, and the
draws naming no lot of its item at its location while it held stock). A
transfer leads on to the lot it moved into, at the location it moved to,
which it names by that location's contact, a consignee's included. A
**recall** traces forward from a lot, or from every lot of an item dated
within a range.
"""

from __future__ import annotations

import heapq
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from datetime import date
from decimal import Decimal
from enum import StrEnum

from sqlalchemy.orm import aliased
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from hoard.records import (
    Adjustment,
    Location,
    Lot,
    Order,
    ProductionRun,
    ProductionRunLine,
    Purchase,
    PurchaseLine,
    RunStatus,
    Shipment,
    ShipmentLine,
    ShipmentStatus,
)


class StepKind(StrEnum):
    """What a step of a trace is."""

    #: A production run that consumed one lot and made another.
    RUN = "run"
    #: A shipment that sent a lot out.
    SHIPMENT = "shipment"
    #: A purchase that received a lot.
    PURCHASE = "purchase"
    #: A transfer that moved a lot to another location.
    TRANSFER = "transfer"


@dataclass(frozen=True)
class TraceStep:
    """One movement a trace follows: a lot consumed by a run, shipped,
    received, or moved to another location."""

    kind: StepKind
    #: The run, shipment, purchase or transfer.
    record_id: int
    #: The lot that moved; None for a draw that named none.
    lot_id: int | None
    #: The lot a run made of it, or the lot a transfer moved it into.
    made_lot_id: int | None
    item_id: int
    quantity: Decimal
    dated_on: date
    #: The other party: a shipment's customer, a purchase's supplier, or the
    #: contact of the location a transfer moved it to, as an Initiative
    #: contact's id.
    contact_id: int | None
    #: Whether the lot only may have moved: an assumed or unnamed draw.
    assumed: bool
    #: How many steps from the traced lot: 1 for its own.
    level: int
    #: The lot of the trace this step goes on from: the lot it moved, or, for
    #: a draw that named none, the lot that held stock when it was made.
    from_lot_id: int | None = None
    #: The location a transfer moved it to.
    location_id: int | None = None
    #: The shipment's line it went out on, for a shipment.
    line_id: int | None = None


@dataclass
class Trace:
    """Every step a trace from one lot reaches."""

    lot_id: int
    steps: list[TraceStep] = field(default_factory=list)

    def lot_ids(self, *, assumed: bool = True) -> set[int]:
        """The lots the trace reaches beside its own: made from it, forward,
        or gone into it, back; only those recorded unless ``assumed``."""
        return {
            lot_id
            for step in self.steps
            if assumed or not step.assumed
            for lot_id in (step.made_lot_id, step.lot_id)
            if lot_id is not None and lot_id != self.lot_id
        }


#: What a trace reaches from one lot: each step, its own lot unset, and the
#: lot it leads on to.
Moves = list[tuple[TraceStep, int | None]]


def _day(day: date | None) -> date:
    """The day a completed run or a shipped shipment moved stock, which each keeps."""
    if day is None:
        raise ValueError("a completed run or a shipped shipment has its day")
    return day


def _step(
    kind: StepKind,
    record_id: int,
    *,
    lot_id: int | None,
    made_lot_id: int | None,
    item_id: int,
    quantity: Decimal,
    dated_on: date,
    contact_id: int | None = None,
    assumed: bool,
    location_id: int | None = None,
    line_id: int | None = None,
) -> TraceStep:
    return TraceStep(
        kind=kind,
        record_id=record_id,
        lot_id=lot_id,
        made_lot_id=made_lot_id,
        item_id=item_id,
        quantity=quantity,
        dated_on=dated_on,
        contact_id=contact_id,
        assumed=assumed,
        level=0,
        location_id=location_id,
        line_id=line_id,
    )


async def _windows(db: AsyncSession, lot_id: int) -> list[tuple[date, date | None]]:
    """The spans of days a lot held stock, from the day its balance rose above
    nothing to the day it fell back, that day included; the last open while
    it holds stock still."""
    changes = await db.exec(
        select(col(Adjustment.dated_on), col(Adjustment.change))
        .where(Adjustment.lot_id == lot_id)
        .order_by(col(Adjustment.dated_on), col(Adjustment.id))
    )
    spans: list[tuple[date, date | None]] = []
    balance = Decimal(0)
    opened: date | None = None
    for dated_on, change in changes.all():
        balance += change
        if opened is None and balance > 0:
            opened = dated_on
        elif opened is not None and balance <= 0:
            spans.append((opened, dated_on))
            opened = None
    if opened is not None:
        spans.append((opened, None))
    return spans


def _within(day: date, spans: list[tuple[date, date | None]]) -> bool:
    return any(start <= day and (end is None or day <= end) for start, end in spans)


async def _transfers(db: AsyncSession, lot: Lot, *, into: bool) -> Moves:
    """The transfers out of ``lot``, and out of no lot of its item at its
    location, or, ``into``, those into it: each a step from the lot moved out
    to the lot moved into, at the location it went to."""
    this, other = aliased(Adjustment), aliased(Adjustment)
    query = (
        select(this, other, Location.contact_id)
        .join(other, (col(other.transfer_id) == this.transfer_id) & (col(other.id) != this.id))
        .join(Location, col(Location.id) == other.location_id)
        .order_by(col(this.dated_on), col(this.id))
    )
    if into:
        query = query.where(this.lot_id == lot.id, col(this.change) > 0)
    else:
        query = query.where(
            this.item_id == lot.item_id,
            this.location_id == lot.location_id,
            col(this.change) < 0,
            (col(this.lot_id) == lot.id) | col(this.lot_id).is_(None),
        )
    moves: Moves = []
    for half, pair, contact_id in (await db.exec(query)).all():
        out, went = (pair, half) if into else (half, pair)
        step = _step(
            StepKind.TRANSFER,
            half.transfer_id or 0,
            lot_id=out.lot_id,
            made_lot_id=went.lot_id,
            item_id=half.item_id,
            quantity=went.change,
            dated_on=half.dated_on,
            contact_id=None if into else contact_id,
            assumed=out.lot_id is None,
            location_id=went.location_id,
        )
        moves.append((step, pair.lot_id))
    return moves


async def _drawn_from(db: AsyncSession, lot: Lot) -> Moves:
    """Where ``lot`` went: the completed runs, the shipped shipments and the
    transfers that draw on it, and those that draw on no lot of its item at
    its location on a day it held stock."""
    moves: Moves = []
    runs = await db.exec(
        select(ProductionRunLine, ProductionRun)
        .join(ProductionRun, col(ProductionRun.id) == ProductionRunLine.run_id)
        .where(
            ProductionRun.status == RunStatus.COMPLETED,
            ProductionRunLine.item_id == lot.item_id,
            ProductionRun.location_id == lot.location_id,
            (col(ProductionRunLine.lot_id) == lot.id) | col(ProductionRunLine.lot_id).is_(None),
        )
        .order_by(col(ProductionRun.completed_on), col(ProductionRunLine.id))
    )
    shipments = await db.exec(
        select(ShipmentLine, Shipment, Order)
        .join(Shipment, col(Shipment.id) == ShipmentLine.shipment_id)
        .join(Order, col(Order.id) == Shipment.order_id)
        .where(
            Shipment.status == ShipmentStatus.SHIPPED,
            ShipmentLine.item_id == lot.item_id,
            Shipment.location_id == lot.location_id,
            (col(ShipmentLine.lot_id) == lot.id) | col(ShipmentLine.lot_id).is_(None),
        )
        .order_by(col(Shipment.shipped_on), col(ShipmentLine.id))
    )
    run_rows, shipment_rows = runs.all(), shipments.all()
    transfers = await _transfers(db, lot, into=False)
    spans = (
        await _windows(db, lot.id)
        if any(line.lot_id is None for line, _ in run_rows)
        or any(line.lot_id is None for line, _, _ in shipment_rows)
        or any(step.lot_id is None for step, _ in transfers)
        else []
    )
    for line, run in run_rows:
        day = _day(run.completed_on)
        if line.lot_id is None and not _within(day, spans):
            continue
        step = _step(
            StepKind.RUN,
            run.id,
            lot_id=line.lot_id,
            made_lot_id=run.output_lot_id,
            item_id=line.item_id,
            quantity=line.quantity,
            dated_on=day,
            assumed=line.assumed or line.lot_id is None,
        )
        moves.append((step, run.output_lot_id))
    for line, shipment, order in shipment_rows:
        day = _day(shipment.shipped_on)
        if line.lot_id is None and not _within(day, spans):
            continue
        step = _step(
            StepKind.SHIPMENT,
            shipment.id,
            lot_id=line.lot_id,
            made_lot_id=None,
            item_id=line.item_id,
            quantity=line.quantity,
            dated_on=day,
            contact_id=order.customer_contact_id,
            assumed=line.assumed or line.lot_id is None,
            line_id=line.id,
        )
        moves.append((step, None))
    moves += [
        (step, onward)
        for step, onward in transfers
        if step.lot_id is not None or _within(step.dated_on, spans)
    ]
    return moves


async def _gone_into(db: AsyncSession, lot: Lot) -> Moves:
    """What went into ``lot``: the purchase that received it, or the lines
    of the completed run that made it, each leading on to the lot it drew;
    and the transfers that moved it in, each leading on to the lot it moved
    out of."""
    moved_in = await _transfers(db, lot, into=True)
    if lot.purchase_line_id is not None:
        line, purchase = (
            await db.exec(
                select(PurchaseLine, Purchase)
                .join(Purchase, col(Purchase.id) == PurchaseLine.purchase_id)
                .where(PurchaseLine.id == lot.purchase_line_id)
            )
        ).one()
        step = _step(
            StepKind.PURCHASE,
            purchase.id,
            lot_id=lot.id,
            made_lot_id=None,
            item_id=lot.item_id,
            quantity=line.tracking_quantity,
            dated_on=lot.dated_on,
            contact_id=purchase.supplier_contact_id,
            assumed=False,
        )
        return [(step, None), *moved_in]
    found = await db.exec(
        select(ProductionRunLine, ProductionRun)
        .join(ProductionRun, col(ProductionRun.id) == ProductionRunLine.run_id)
        .where(ProductionRun.output_lot_id == lot.id, ProductionRun.status == RunStatus.COMPLETED)
        .order_by(col(ProductionRunLine.id))
    )
    return [
        *(
            (
                _step(
                    StepKind.RUN,
                    run.id,
                    lot_id=line.lot_id,
                    made_lot_id=lot.id,
                    item_id=line.item_id,
                    quantity=line.quantity,
                    dated_on=_day(run.completed_on),
                    assumed=line.assumed or line.lot_id is None,
                ),
                line.lot_id,
            )
            for line, run in found.all()
        ),
        *moved_in,
    ]


async def _walk(
    db: AsyncSession, lot_id: int, moves: Callable[[AsyncSession, Lot], Awaitable[Moves]]
) -> Trace:
    """Every step ``moves`` reaches from ``lot_id``, level by level. A lot
    reached by recorded steps is followed from there, before and rather than
    from wherever it is only assumed to have gone, and each lot is followed
    once, so a step is assumed once any step before it is."""
    trace = Trace(lot_id=lot_id)
    best: dict[int, tuple[bool, int]] = {lot_id: (False, 0)}
    queue: list[tuple[bool, int, int]] = [(False, 0, lot_id)]
    while queue:
        assumed, level, current = heapq.heappop(queue)
        if best[current] != (assumed, level):
            continue
        lot = await db.get(Lot, current)
        if lot is None:
            continue
        for step, onward in await moves(db, lot):
            reached = (assumed or step.assumed, level + 1)
            trace.steps.append(
                replace(step, assumed=reached[0], level=reached[1], from_lot_id=current)
            )
            if onward is None or onward == lot_id:
                continue
            if onward not in best or reached < best[onward]:
                best[onward] = reached
                heapq.heappush(queue, (*reached, onward))
    trace.steps.sort(key=lambda s: (s.level, s.dated_on, s.kind, s.record_id, s.lot_id or 0))
    return trace


async def forward(db: AsyncSession, lot_id: int) -> Trace:
    """Where a lot went: the runs, shipments and transfers that drew on it,
    recorded and assumed, and on from each lot those runs made and those
    transfers moved it into, at every level."""
    return await _walk(db, lot_id, _drawn_from)


async def _sent_on(db: AsyncSession, lot: Lot) -> Moves:
    """The shipments and transfers that draw on ``lot``: where it went
    without being made into something else."""
    return [move for move in await _drawn_from(db, lot) if move[0].kind is not StepKind.RUN]


async def shipped(db: AsyncSession, lot_id: int) -> list[TraceStep]:
    """The shipments that sent a lot, or a lot it was moved into, not those
    of what was made from it."""
    walked = await _walk(db, lot_id, _sent_on)
    return [step for step in walked.steps if step.kind is StepKind.SHIPMENT]


async def back(db: AsyncSession, lot_id: int) -> Trace:
    """What went into a lot: the lots its run consumed and the lots moved
    into it, at every level, and the purchases that received each bought lot."""
    return await _walk(db, lot_id, _gone_into)


async def recall(
    db: AsyncSession,
    *,
    lot_id: int | None = None,
    item_id: int | None = None,
    since: date | None = None,
    until: date | None = None,
) -> list[Trace]:
    """The forward traces of a lot, or of each lot of an item dated from
    ``since`` to ``until``, oldest first."""
    if lot_id is not None:
        return [await forward(db, lot_id)]
    if item_id is None:
        raise ValueError("a recall starts from a lot or an item")
    query = select(Lot.id).where(Lot.item_id == item_id)
    if since is not None:
        query = query.where(Lot.dated_on >= since)
    if until is not None:
        query = query.where(Lot.dated_on <= until)
    lots = await db.exec(query.order_by(col(Lot.dated_on), col(Lot.id)))
    return [await forward(db, found) for found in lots.all()]
