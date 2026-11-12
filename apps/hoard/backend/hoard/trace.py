"""Tracing a lot: back to the lots and suppliers that went into it, and
forward to what was made from it and where it went.

**Back**, a production run's lines lead to the lots it consumed, a bought lot
to its purchase line and supplier, at every level. **Forward** is two lists:
what went out with the lot as someone recorded it (the runs and shipments
that name it, not assumed, at every level), and what may have (the lines that
assume it, and the draws naming no lot of its item at its location while it
held stock). A **recall** traces forward from a lot, or from every lot of an
item dated within a range.
"""

from __future__ import annotations

import heapq
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from datetime import date
from decimal import Decimal
from enum import StrEnum

from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from hoard.records import (
    Adjustment,
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


@dataclass(frozen=True)
class TraceStep:
    """One movement a trace follows: a lot consumed by a run, shipped, or
    received."""

    kind: StepKind
    #: The run, shipment or purchase.
    record_id: int
    #: The lot that moved; None for a draw that named none.
    lot_id: int | None
    #: The lot a run made of it.
    made_lot_id: int | None
    item_id: int
    quantity: Decimal
    dated_on: date
    #: The other party: a shipment's customer, a purchase's supplier, as an
    #: Initiative contact's id.
    contact_id: int | None
    #: Whether the lot only may have moved: an assumed or unnamed draw.
    assumed: bool
    #: How many steps from the traced lot: 1 for its own.
    level: int
    #: The lot of the trace this step goes on from: the lot it moved, or, for
    #: a draw that named none, the lot that held stock when it was made.
    from_lot_id: int | None = None


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


async def _drawn_from(db: AsyncSession, lot: Lot) -> Moves:
    """Where ``lot`` went: the completed runs and the shipped shipments whose
    lines draw on it, and those whose lines draw on no lot of its item at its
    location on a day it held stock."""
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
    spans = (
        await _windows(db, lot.id)
        if any(line.lot_id is None for line, _ in run_rows)
        or any(line.lot_id is None for line, _, _ in shipment_rows)
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
        )
        moves.append((step, None))
    return moves


async def _gone_into(db: AsyncSession, lot: Lot) -> Moves:
    """What went into ``lot``: the purchase that received it, or the lines
    of the completed run that made it, each leading on to the lot it drew."""
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
            dated_on=purchase.dated_on,
            contact_id=purchase.supplier_contact_id,
            assumed=False,
        )
        return [(step, None)]
    found = await db.exec(
        select(ProductionRunLine, ProductionRun)
        .join(ProductionRun, col(ProductionRun.id) == ProductionRunLine.run_id)
        .where(ProductionRun.output_lot_id == lot.id, ProductionRun.status == RunStatus.COMPLETED)
        .order_by(col(ProductionRunLine.id))
    )
    return [
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
    """Where a lot went: the runs and shipments that drew on it, recorded
    and assumed, and on from each lot those runs made, at every level."""
    return await _walk(db, lot_id, _drawn_from)


async def back(db: AsyncSession, lot_id: int) -> Trace:
    """What went into a lot: the lots its run consumed, at every level, and
    the purchases that received each bought lot."""
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
