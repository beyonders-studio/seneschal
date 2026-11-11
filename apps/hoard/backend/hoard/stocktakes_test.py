"""Stocktakes: what they count, their variances, and their accuracy."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlmodel import select

from hoard.records import (
    Adjustment,
    AdjustmentKind,
    Category,
    CategoryKind,
    Item,
    Location,
    Product,
    Stocktake,
)
from hoard.stock import add_product, on_hand
from hoard.stocktakes import accuracy, confirm_row, start_stocktake
from seneschal_core.db import WriteSession

DAY = date(2026, 11, 10)


async def _category(db: WriteSession, kind: CategoryKind, name: str) -> Category:
    category = Category(organization_id=db.organization_id, kind=kind, name=name)
    db.add(category)
    await db.flush()
    return category


async def test_variances_become_adjustments_and_accuracy_is_the_share_that_matched(
    db: WriteSession, main: Location, material, buy
):
    baking = await _category(db, CategoryKind.MATERIAL, "Baking")
    flour, sugar, salt = [
        await material(name, category_id=baking.id) for name in ("Flour", "Sugar", "Salt")
    ]
    await material("Whisk", category_id=baking.id, inventoriable=False)
    await material("Tea")
    await buy((flour, "10", "2"), (sugar, "5", "2"))

    stocktake = Stocktake(
        kind=CategoryKind.MATERIAL, location_id=main.id, category_id=baking.id, dated_on=DAY
    )
    rows = {row.item_id: row for row in await start_stocktake(db, stocktake)}
    assert set(rows) == {flour.id, sugar.id, salt.id}
    assert await accuracy(db, stocktake.id) is None

    await confirm_row(db, rows[flour.id], Decimal(12))
    await confirm_row(db, rows[sugar.id], Decimal(3))
    await confirm_row(db, rows[salt.id], Decimal(0))

    found = await db.exec(select(Adjustment).where(Adjustment.kind == AdjustmentKind.STOCKTAKE))
    changes = {
        adjustment.item_id: (adjustment.change, adjustment.unit_cost, adjustment.dated_on)
        for adjustment in found.all()
    }
    # Stock found comes in at zero cost; stock missing goes out at the average.
    assert changes == {flour.id: (2, 0, DAY), sugar.id: (-2, 2, DAY)}
    assert (await db.get_one(Item, flour.id)).average_cost == Decimal("1.666667")
    assert (await on_hand(db, flour.id), await on_hand(db, sugar.id)) == (12, 3)
    assert await accuracy(db, stocktake.id) == Decimal("0.3333")

    # Counted again, sugar matches what was expected, and its variance goes.
    await confirm_row(db, rows[sugar.id], Decimal(5))
    assert await on_hand(db, sugar.id) == 5
    assert await accuracy(db, stocktake.id) == Decimal("0.6667")


async def test_a_random_sample_counts_that_many_of_the_kind(db: WriteSession, main: Location):
    variations = {
        (await add_product(db, Product(name=name)))[1].id for name in ("Chai", "Matcha", "Hojicha")
    }

    stocktake = Stocktake(
        kind=CategoryKind.PRODUCT, location_id=main.id, sample_size=2, dated_on=DAY
    )
    rows = await start_stocktake(db, stocktake)
    assert len(rows) == 2
    assert {row.item_id for row in rows} < variations
