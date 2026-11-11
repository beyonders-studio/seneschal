"""What the goods cost: each item's moving weighted average unit cost.

An item's average is derived from its adjustments, in date order:

* stock coming in at a cost, a purchase's landed cost or a starting
  adjustment's, moves the average toward that cost by quantity;
* stock coming in by any other adjustment, a stocktake's included, comes in at
  zero cost and lowers the average;
* stock going out leaves the average as it is, and records it as what went out
  cost at the time;
* stock reaching zero starts the average again from zero cost.

A cost is never typed in except on a starting adjustment. Editing a purchase or
a starting adjustment re-derives everything after it, so any service that
changes an item's adjustments ends with :func:`rederive`.
"""

from __future__ import annotations

from decimal import Decimal

from sqlmodel import col, select

from hoard.records import PLACES, Adjustment, AdjustmentKind, Item
from seneschal_core.db import WriteSession

#: The kinds whose incoming stock brings its own unit cost.
COSTED_KINDS = frozenset({AdjustmentKind.STARTING, AdjustmentKind.PURCHASE})


async def rederive(db: WriteSession, item_id: int) -> Decimal:
    """Re-derive an item's average unit cost, and the unit cost at the time on
    each adjustment that does not bring its own, from all its adjustments.
    Answers the average."""
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
            adjustment.unit_cost = average
        quantity += adjustment.change
        if quantity <= 0:
            average = Decimal(0)
    item.average_cost = average
    await db.flush()
    return average
