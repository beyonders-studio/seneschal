"""A material's moving average, through purchases, adjustments and zero stock."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from hoard import costing
from hoard.records import Adjustment, AdjustmentKind, Item, Location
from hoard.stock import adjust
from seneschal_core.db import WriteSession


async def _average(db: WriteSession, item_id: int) -> Decimal:
    item = await db.get_one(Item, item_id)
    return item.average_cost


async def _adjust(
    db: WriteSession,
    item_id: int,
    location: Location,
    change: str,
    *,
    day: int,
    kind: AdjustmentKind = AdjustmentKind.MANUAL,
    unit_cost: str = "0",
) -> Adjustment:
    return await adjust(
        db,
        Adjustment(
            item_id=item_id,
            location_id=location.id,
            kind=kind,
            dated_on=date(2026, 11, day),
            change=Decimal(change),
            unit_cost=Decimal(unit_cost),
        ),
    )


async def test_the_average_moves_with_purchases_adjustments_and_zero_stock(
    db: WriteSession, main: Location, material, buy
):
    flour = await material("Flour")
    await buy((flour, "10", "2"), dated_on=date(2026, 11, 1))
    assert await _average(db, flour.id) == 2

    await buy((flour, "10", "4"), dated_on=date(2026, 11, 2))
    assert await _average(db, flour.id) == 3

    # Found stock comes in at zero cost, whatever is typed, and lowers the average.
    found = await _adjust(db, flour.id, main, "10", day=3, unit_cost="9")
    assert found.unit_cost == 0
    assert await _average(db, flour.id) == 2

    used = await _adjust(db, flour.id, main, "-30", day=4)
    assert used.unit_cost == 2
    assert await _average(db, flour.id) == 0

    await buy((flour, "5", "6"), dated_on=date(2026, 11, 5))
    assert await _average(db, flour.id) == 6


async def test_a_starting_cost_is_corrected_by_editing_it(
    db: WriteSession, main: Location, material
):
    sugar = await material("Sugar")
    starting = await _adjust(
        db, sugar.id, main, "10", day=1, kind=AdjustmentKind.STARTING, unit_cost="3"
    )
    used = await _adjust(db, sugar.id, main, "-4", day=2)
    assert (await _average(db, sugar.id), used.unit_cost) == (3, 3)

    starting.unit_cost = Decimal(5)
    await costing.rederive(db, sugar.id)
    assert (await _average(db, sugar.id), used.unit_cost) == (5, 5)
