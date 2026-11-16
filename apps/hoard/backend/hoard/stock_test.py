"""Stock levels and lot balances follow adjustments, at each location."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlmodel import col, select

from hoard import costing, orders
from hoard.holds import HoldError
from hoard.records import (
    Adjustment,
    AdjustmentKind,
    Category,
    CategoryKind,
    DefaultHoldReason,
    DocumentLink,
    Hold,
    Item,
    Location,
    Lot,
    Material,
    Picking,
    Product,
    PropertyDefinition,
    PropertyType,
    PropertyValue,
    RecordKind,
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
    transfer,
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


async def _location(db: WriteSession, name: str) -> Location:
    made = Location(organization_id=db.organization_id, name=name)
    db.add(made)
    await db.flush()
    return made


async def _lot_of(db: WriteSession, item_id: int, location: Location) -> Lot:
    return (
        await db.exec(
            select(Lot)
            .where(Lot.item_id == item_id, Lot.location_id == location.id)
            .execution_options(populate_existing=True)
        )
    ).one()


async def test_a_transfer_moves_a_lot_at_its_cost_and_moving_all_of_it_keeps_the_average(
    db: WriteSession, main: Location, material, buy
):
    flour = await material("Flour")
    await buy((flour, "10", "2"))
    bought = await _lot_of(db, flour.id, main)
    organic = PropertyDefinition(
        organization_id=db.organization_id,
        kind=RecordKind.LOT,
        name="Organic",
        type=PropertyType.TEXT,
    )
    db.add(organic)
    await db.flush()
    db.add_all(
        [
            PropertyValue(
                organization_id=db.organization_id,
                definition_id=organic.id,
                lot_id=bought.id,
                value="yes",
            ),
            DocumentLink(organization_id=db.organization_id, file_id=41, lot_id=bought.id),
        ]
    )
    shed = await _location(db, "Shed")

    moved = await transfer(
        db, flour.id, main.id, shed.id, Decimal(10), lot_id=bought.id, dated_on=DAY
    )

    # All of it moved: the average is the item's, and stays as it was.
    item = await db.get_one(Item, flour.id, populate_existing=True)
    assert item.average_cost == 2
    halves = (
        await db.exec(
            select(Adjustment)
            .where(Adjustment.transfer_id == moved.id)
            .order_by(col(Adjustment.change))
        )
    ).all()
    copy = await _lot_of(db, flour.id, shed)
    assert [(half.location_id, half.lot_id, half.change, half.unit_cost) for half in halves] == [
        (main.id, bought.id, -10, 2),
        (shed.id, copy.id, 10, 2),
    ]
    assert {half.kind for half in halves} == {AdjustmentKind.TRANSFER}
    assert (await on_hand(db, flour.id, main.id), await on_hand(db, flour.id, shed.id)) == (0, 10)
    assert (copy.number, copy.dated_on, copy.balance) == (bought.number, bought.dated_on, 10)
    assert (
        await db.exec(select(PropertyValue.value).where(PropertyValue.lot_id == copy.id))
    ).all() == ["yes"]
    assert (
        await db.exec(select(DocumentLink.file_id).where(DocumentLink.lot_id == copy.id))
    ).all() == [41]

    # Moved back, it goes into the lot it came from.
    await transfer(db, flour.id, shed.id, main.id, Decimal(4), lot_id=copy.id, dated_on=DAY)
    assert (await _lot_of(db, flour.id, main)).balance == 4
    assert (await db.get_one(Item, flour.id, populate_existing=True)).average_cost == 2


async def test_a_transfer_moves_no_more_than_is_there_and_goes_somewhere_else(
    db: WriteSession, main: Location, material, lot
):
    flour = await material("Flour")
    sugar = await material("Sugar")
    bought = await lot(flour.id, "F-1", "10")
    await _manual(db, flour, main, "5")
    shed = await _location(db, "Shed")

    async def refused(**fields) -> StockError | HoldError:
        move = {"item_id": flour.id, "from_location_id": main.id, "to_location_id": shed.id}
        move |= fields
        with pytest.raises(StockRefused) as refusal:
            await transfer(
                db,
                move.pop("item_id"),
                move.pop("from_location_id"),
                move.pop("to_location_id"),
                move.pop("quantity", Decimal(1)),
                dated_on=DAY,
                **move,
            )
        return refusal.value.code

    assert await refused(to_location_id=main.id) == StockError.TRANSFER_SAME_LOCATION
    assert await refused(lot_id=bought.id, quantity=Decimal(11)) == StockError.TRANSFER_OVER_BALANCE
    assert await refused(item_id=sugar.id, lot_id=bought.id) == StockError.TRANSFER_LOT_ELSEWHERE
    assert await refused(from_location_id=shed.id, to_location_id=main.id, lot_id=bought.id) == (
        StockError.TRANSFER_LOT_ELSEWHERE
    )
    # Five are in no lot: no more of them moves.
    assert await refused(quantity=Decimal(6)) == StockError.TRANSFER_OVER_BALANCE
    await transfer(db, flour.id, main.id, shed.id, Decimal(5), dated_on=DAY)
    assert (await on_hand(db, flour.id, shed.id), (await _lot_of(db, flour.id, main)).balance) == (
        5,
        10,
    )

    # Where every lot drawn is recorded, stock in no lot does not move.
    category = Category(
        organization_id=db.organization_id,
        kind=CategoryKind.MATERIAL,
        name="Bulk",
        picking=Picking.RECORDED,
    )
    db.add(category)
    await db.flush()
    flour.category_id = category.id
    db.add(flour)
    await db.flush()
    assert await refused(from_location_id=shed.id, to_location_id=main.id) == HoldError.LOT_REQUIRED


async def test_a_hold_moves_with_its_lot(db: WriteSession, main: Location, material, lot, hold):
    flour = await material("Flour")
    curing = await hold(
        await lot(flour.id, "F-1", "10"), DefaultHoldReason.CURING, ready_on=date(2026, 12, 1)
    )
    suspect = await hold(await lot(flour.id, "F-2", "4"))
    shed = await _location(db, "Shed")

    await transfer(db, flour.id, main.id, shed.id, Decimal(3), lot_id=curing.lot_id, dated_on=DAY)
    await transfer(db, flour.id, main.id, shed.id, Decimal(4), lot_id=suspect.lot_id, dated_on=DAY)

    # Part moved: both lots are held, until the cure's day.
    holds = (
        await db.exec(
            select(Lot.number, Lot.location_id, Hold.reason_id, Hold.ready_on, Hold.id)
            .join(Hold, col(Hold.lot_id) == Lot.id)
            .where(col(Hold.ended).is_(None))
            .order_by(col(Lot.number), col(Lot.location_id))
        )
    ).all()
    assert [row[:4] for row in holds] == [
        ("F-1", main.id, curing.reason_id, date(2026, 12, 1)),
        ("F-1", shed.id, curing.reason_id, date(2026, 12, 1)),
        # All of it moved: its hold went with it.
        ("F-2", shed.id, suspect.reason_id, None),
    ]
    assert holds[2][4] == suspect.id


async def test_moving_what_open_orders_commit_leaves_them_short(
    db: WriteSession, main: Location, lot, sell
):
    _, chai = await add_product(db, Product(name="Chai"))
    stocked = await lot(chai.id, "C-1", "10")
    order = await sell((chai.id, "8", "4"))
    shop = await _location(db, "Shop")

    await transfer(db, chai.id, main.id, shop.id, Decimal(5), lot_id=stocked.id, dated_on=DAY)

    assert list((await orders.short(db, order)).values()) == [3]
