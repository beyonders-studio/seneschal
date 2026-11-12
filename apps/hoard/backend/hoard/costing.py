"""What the goods cost: each item's moving weighted average unit cost.

An item's average is derived from its adjustments, in date order:

* stock coming in at a cost, a purchase's landed cost, a starting
  adjustment's, a production run's output, or a return at what it shipped
  at, moves the average toward that cost by quantity;
* stock coming in by any other adjustment, a stocktake's included, comes in at
  zero cost and lowers the average;
* stock going out, a shipment's included, leaves the average as it is, and
  records it as what went out cost at the time;
* stock reaching zero starts the average again from zero cost.

A completed production run costs what it consumed at each item's average on
its date, and its output comes in at that cost divided by the quantity made.
A run that consumed an item with no cost on its date, such as one out of stock,
is flagged ``uncosted``.

A cost is never typed in except on a starting adjustment. Editing a purchase or
a starting adjustment re-derives everything after it, and on through the runs
that consumed the item to what they made, so any service that changes an
item's adjustments ends with :func:`rederive`, which also notes that the
item's stock changed, for its listings (``hoard.listings``).
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import update
from sqlmodel import col, select

from hoard.records import (
    PLACES,
    Adjustment,
    AdjustmentKind,
    Item,
    ProductionRun,
    ProductionRunLine,
)
from seneschal_core.db import WriteSession

#: The kinds whose incoming stock brings its own unit cost.
COSTED_KINDS = frozenset(
    {
        AdjustmentKind.STARTING,
        AdjustmentKind.PURCHASE,
        AdjustmentKind.PRODUCTION,
        AdjustmentKind.RETURN,
    }
)


async def _derive(db: WriteSession, item_id: int) -> tuple[Decimal, set[int]]:
    """Derive an item's average and the unit cost on each of its adjustments
    that goes out. Answers the average, and the runs whose consumption of the
    item now costs something else."""
    await db.flush()
    # Locking the item first keeps two changes to it from deriving at once.
    item = (
        await db.exec(
            select(Item)
            .where(Item.id == item_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).one()
    adjustments = await db.exec(
        select(Adjustment)
        .where(Adjustment.item_id == item_id)
        .order_by(col(Adjustment.dated_on), col(Adjustment.id))
        .execution_options(populate_existing=True)
    )
    quantity = average = Decimal(0)
    runs: set[int] = set()
    for adjustment in adjustments.all():
        if adjustment.change > 0:
            if adjustment.kind not in COSTED_KINDS:
                adjustment.unit_cost = Decimal(0)
            held = max(quantity, Decimal(0))
            average = (
                (held * average + adjustment.change * adjustment.unit_cost)
                / (held + adjustment.change)
            ).quantize(PLACES)
        else:
            if adjustment.production_run_id is not None and adjustment.unit_cost != average:
                runs.add(adjustment.production_run_id)
            adjustment.unit_cost = average
        quantity += adjustment.change
        if quantity <= 0:
            average = Decimal(0)
    item.average_cost = average
    await db.flush()
    return average, runs


async def _cost_run(db: WriteSession, run_id: int) -> int | None:
    """Cost a run from what it consumed, as its adjustments hold it, and flag
    it uncosted where something it consumed cost nothing. Answers its output
    item when what one unit of its output cost has changed."""
    consumed = (
        await db.exec(
            select(Adjustment).where(
                Adjustment.production_run_id == run_id,
                col(Adjustment.production_run_line_id).is_not(None),
            )
        )
    ).all()
    uncosted = any(adjustment.unit_cost == 0 for adjustment in consumed)
    # A statement, not a change to the run, so nobody is recorded as changing it.
    await db.execute(
        update(ProductionRun)
        .where(col(ProductionRun.id) == run_id, col(ProductionRun.uncosted) != uncosted)
        .values(uncosted=uncosted)
    )
    output = (
        await db.exec(
            select(Adjustment).where(
                Adjustment.production_run_id == run_id,
                col(Adjustment.production_run_line_id).is_(None),
            )
        )
    ).first()
    if output is None:
        return None
    total = sum((-adjustment.change * adjustment.unit_cost for adjustment in consumed), Decimal(0))
    unit_cost = (total / output.change).quantize(PLACES)
    if unit_cost == output.unit_cost:
        return None
    output.unit_cost = unit_cost
    return output.item_id


async def rederive(
    db: WriteSession, item_id: int, *, _made: frozenset[int] = frozenset()
) -> Decimal:
    """Re-derive an item's average unit cost, and the unit cost at the time on
    each adjustment that does not bring its own, from all its adjustments; then
    each run that consumed it, and what that run made, in turn. Records the
    item's stock falling low, and notes that it changed for the item's
    listings. Answers the average."""
    average, runs = await _derive(db, item_id)
    # The items this cascade is deriving, which a run making one again does not re-enter.
    made = _made | {item_id}
    for run_id in sorted(runs):
        output = await _cost_run(db, run_id)
        if output is not None and output not in made:
            await rederive(db, output, _made=made)
    # Imported here: each reads stock through ``hoard.stock``, which costs through this.
    from hoard import events, listings

    await events.record_if_low(db, item_id)
    await listings.note_changed(db, item_id)
    return average


async def rederive_run(db: WriteSession, run: ProductionRun) -> None:
    """Re-derive what ``run`` consumed, the run's own cost, and what it made:
    after it is completed or reopened."""
    consumed = await db.exec(
        select(ProductionRunLine.item_id).where(ProductionRunLine.run_id == run.id).distinct()
    )
    for item_id in sorted(consumed.all()):
        await rederive(db, item_id, _made=frozenset({run.item_id}))
    await db.flush()
    await _cost_run(db, run.id)
    await rederive(db, run.item_id)
