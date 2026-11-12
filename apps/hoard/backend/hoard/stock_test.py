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
    Category,
    CategoryKind,
    Location,
    Lot,
    Material,
    Picking,
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
    draw_lots,
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


async def test_lots_are_drawn_by_earliest_expiry_then_first_in_and_no_further_than_they_hold(
    db: WriteSession, main: Location, material, lot
):
    flour = await material("Flour")
    late = await lot(flour.id, "A", "3", date(2026, 11, 1), expires_on=date(2026, 12, 10))
    soon = await lot(flour.id, "B", "2", date(2026, 11, 2), expires_on=date(2026, 12, 5))
    lasting = await lot(flour.id, "C", "2", date(2026, 10, 1))
    drawn: dict[int, Decimal] = {}

    first = await draw_lots(db, flour.id, main.id, Decimal(4), drawn)
    rest = await draw_lots(db, flour.id, main.id, Decimal(5), drawn)

    assert first == [(soon.id, 2), (late.id, 2)]
    # What was drawn already is not drawn again; what no lot holds draws on none.
    assert rest == [(late.id, 1), (lasting.id, 2), (None, 2)]
    assert drawn == {soon.id: 2, late.id: 3, lasting.id: 2}


async def test_lots_on_hold_are_never_drawn(db: WriteSession, main: Location, material, lot, hold):
    flour = await material("Flour")
    held = await lot(flour.id, "A", "3", expires_on=date(2026, 12, 1))
    free = await lot(flour.id, "B", "2", expires_on=date(2026, 12, 9))
    await hold(held)

    assert await draw_lots(db, flour.id, main.id, Decimal(4), {}) == [(free.id, 2), (None, 2)]


@pytest.mark.parametrize(
    ("rule", "order"),
    [
        (Picking.EARLIEST_EXPIRY, ["soon", "late", "lasting"]),
        (Picking.FIRST_IN, ["lasting", "late", "soon"]),
        (Picking.LAST_IN, ["soon", "late", "lasting"]),
        (Picking.RECORDED, []),
    ],
)
async def test_lots_are_drawn_as_the_category_picks_them(
    db: WriteSession, main: Location, material, lot, rule: Picking, order: list[str]
):
    category = Category(
        organization_id=db.organization_id, kind=CategoryKind.MATERIAL, name="Bulk", picking=rule
    )
    db.add(category)
    await db.flush()
    flour = await material("Flour", category_id=category.id)
    lots = {
        "late": await lot(flour.id, "A", "1", date(2026, 11, 1), expires_on=date(2026, 12, 10)),
        "soon": await lot(flour.id, "B", "1", date(2026, 11, 2), expires_on=date(2026, 12, 5)),
        "lasting": await lot(flour.id, "C", "1", date(2026, 10, 1)),
    }

    draws = await draw_lots(db, flour.id, main.id, Decimal(3), {})

    # Where every draw is recorded, none is assumed.
    expected = [(lots[name].id, 1) for name in order] or [(None, 3)]
    assert draws == expected


async def test_a_variation_is_drawn_as_its_products_category_picks(
    db: WriteSession, main: Location, lot
):
    category = Category(
        organization_id=db.organization_id,
        kind=CategoryKind.PRODUCT,
        name="Soap",
        picking=Picking.LAST_IN,
    )
    db.add(category)
    await db.flush()
    _, soap = await add_product(db, Product(name="Soap", category_id=category.id))
    first = await lot(soap.id, "S-1", "1", date(2026, 11, 1))
    last = await lot(soap.id, "S-2", "1", date(2026, 11, 5))

    assert await draw_lots(db, soap.id, main.id, Decimal(2), {}) == [(last.id, 1), (first.id, 1)]
