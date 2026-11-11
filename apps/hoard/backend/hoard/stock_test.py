"""Stock levels and lot balances follow adjustments, at each location."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlmodel import select

from hoard import costing
from hoard.records import (
    Adjustment,
    AdjustmentKind,
    Location,
    Lot,
    Material,
    Product,
    Unit,
    Variation,
)
from hoard.stock import (
    StockError,
    StockRefused,
    add_material,
    add_product,
    adjust,
    main_location,
    on_hand,
)
from seneschal_core.db import WriteSession

DAY = date(2026, 11, 1)


async def _manual(
    db: WriteSession, material: Material, location: Location, change: str, lot: Lot | None = None
) -> Adjustment:
    return await adjust(
        db,
        Adjustment(
            item_id=material.id,
            location_id=location.id,
            lot_id=lot.id if lot else None,
            kind=AdjustmentKind.MANUAL,
            dated_on=DAY,
            change=Decimal(change),
        ),
    )


async def test_the_main_location_is_made_once(db: WriteSession):
    first = await main_location(db)
    assert (await main_location(db)).id == first.id
    assert first.main and first.name is None


async def test_stock_and_lot_balances_follow_every_adjustment(
    db: WriteSession, main: Location, unit: Unit
):
    flour = await add_material(db, Material(name="Flour", tracking_unit_id=unit.id))
    shed = Location(organization_id=db.organization_id, name="Shed")
    lot = Lot(
        organization_id=db.organization_id,
        item_id=flour.id,
        location_id=main.id,
        number="F-1",
        dated_on=DAY,
    )
    db.add_all([shed, lot])
    await db.flush()

    into_lot = await _manual(db, flour, main, "10", lot)
    await _manual(db, flour, main, "5")
    await _manual(db, flour, shed, "3")
    await db.refresh(lot)
    assert (await on_hand(db, flour.id), await on_hand(db, flour.id, main.id)) == (18, 15)
    assert lot.balance == 10

    into_lot.change = Decimal(4)
    await costing.rederive(db, flour.id)
    await db.refresh(lot)
    assert await on_hand(db, flour.id, main.id) == 9
    assert lot.balance == 4

    await db.delete(into_lot)
    await costing.rederive(db, flour.id)
    await db.refresh(lot)
    assert await on_hand(db, flour.id) == 8
    assert lot.balance == 0


async def test_a_material_that_is_not_inventoriable_carries_no_stock(
    db: WriteSession, main: Location, unit: Unit
):
    whisk = await add_material(
        db, Material(name="Whisk", tracking_unit_id=unit.id, inventoriable=False)
    )
    with pytest.raises(StockRefused) as refused:
        await _manual(db, whisk, main, "1")
    assert refused.value.code == StockError.NOT_INVENTORIABLE


async def test_a_simple_product_has_its_one_default_variation(db: WriteSession):
    product, variation = await add_product(db, Product(name="Chai"))
    variations = (await db.exec(select(Variation).where(Variation.product_id == product.id))).all()
    assert [(v.id, v.is_default, v.attributes) for v in variations] == [(variation.id, True, {})]
