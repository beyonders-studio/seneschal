"""Receiving purchases: landed costs, lots dated the day they arrived,
receiving part of a line, products bought as materials are, and edits that
re-derive cost."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlmodel import col, select

from hoard.purchases import (
    PurchaseError,
    PurchaseRefused,
    bought,
    landed_costs,
    receive_line,
    rederive_purchase,
    unreceive_line,
)
from hoard.records import (
    Adjustment,
    AdjustmentKind,
    Item,
    Location,
    Lot,
    Material,
    Product,
    Purchase,
    PurchaseLine,
    Variation,
)
from hoard.stock import StockError, StockRefused, add_product, adjust, on_hand
from seneschal_core.db import WriteSession


async def _average(db: WriteSession, item_id: int) -> Decimal:
    return (await db.get_one(Item, item_id)).average_cost


async def _lines(db: WriteSession, purchase_id: int) -> list[PurchaseLine]:
    lines = await db.exec(
        select(PurchaseLine)
        .where(PurchaseLine.purchase_id == purchase_id)
        .order_by(col(PurchaseLine.id))
        .execution_options(populate_existing=True)
    )
    return list(lines.all())


async def _order(
    db: WriteSession, main: Location, *lines: tuple[Material | Variation, str, str, str], **fields
) -> tuple[Purchase, list[PurchaseLine]]:
    """A purchase dated 8 November of ``lines``, none received: each a
    material or a variation, its purchase and tracking quantities, and its
    unit cost."""
    purchase = Purchase(
        organization_id=db.organization_id,
        code="P-1",
        dated_on=date(2026, 11, 8),
        location_id=main.id,
        **fields,
    )
    db.add(purchase)
    await db.flush()
    for item, quantity, tracking, unit_cost in lines:
        db.add(
            PurchaseLine(
                organization_id=db.organization_id,
                purchase_id=purchase.id,
                item_id=item.id,
                purchase_quantity=Decimal(quantity),
                tracking_quantity=Decimal(tracking),
                unit_cost=Decimal(unit_cost),
            )
        )
    await db.flush()
    return purchase, await _lines(db, purchase.id)


async def _received(db: WriteSession, line: PurchaseLine) -> tuple[Lot, Adjustment]:
    lot = (await db.exec(select(Lot).where(Lot.purchase_line_id == line.id))).one()
    adjustment = (
        await db.exec(select(Adjustment).where(Adjustment.purchase_line_id == line.id))
    ).one()
    return lot, adjustment


async def test_discount_and_shipping_spread_by_value_and_tax_only_where_it_counts(
    db: WriteSession, material, buy
):
    flour, sugar = await material("Flour"), await material("Sugar")
    purchase = await buy(
        (flour, "10", "2"),
        (sugar, "5", "6"),
        shipping=Decimal(10),
        discount=Decimal(5),
        tax=Decimal("3.5"),
    )

    # Of the net 5 added, flour's 20 of value takes 2 and sugar's 30 takes 3.
    assert (await _average(db, flour.id), await _average(db, sugar.id)) == (
        Decimal("2.2"),
        Decimal("6.6"),
    )
    flour_line = next(line for line in await _lines(db, purchase.id) if line.item_id == flour.id)
    lot = (await db.exec(select(Lot).where(Lot.purchase_line_id == flour_line.id))).one()
    await db.refresh(lot)
    assert (lot.number, lot.balance) == (f"{purchase.code}-{flour_line.id}", 10)
    received = (
        await db.exec(select(Adjustment).where(Adjustment.purchase_line_id == flour_line.id))
    ).one()
    assert (received.kind, received.lot_id, received.unit_cost) == (
        AdjustmentKind.PURCHASE,
        lot.id,
        Decimal("2.2"),
    )

    # Counted in cost, the tax spreads with them: 8.5 of 50, so flour's 20 takes 3.4.
    lines = await _lines(db, purchase.id)
    assert [
        landed_costs(purchase, lines, tax_in_cost=taxed)[flour_line.id] for taxed in (False, True)
    ] == [Decimal("2.2"), Decimal("2.34")]


async def test_editing_a_purchase_rederives_the_cost_of_what_followed(
    db: WriteSession, main: Location, material, buy
):
    flour = await material("Flour")
    first = await buy((flour, "10", "2"), dated_on=date(2026, 11, 1))
    await buy((flour, "10", "4"), dated_on=date(2026, 11, 2))
    used = await adjust(
        db,
        Adjustment(
            item_id=flour.id,
            location_id=main.id,
            kind=AdjustmentKind.MANUAL,
            dated_on=date(2026, 11, 3),
            change=Decimal(-5),
        ),
    )
    assert (await _average(db, flour.id), used.unit_cost) == (3, 3)

    [line] = await _lines(db, first.id)
    line.unit_cost = Decimal(5)
    await rederive_purchase(db, first)
    assert (await _average(db, flour.id), used.unit_cost) == (Decimal("4.5"), Decimal("4.5"))

    # Received after the use instead, with shipping: 5 left at 4, then 10 at 6.
    line.received_on = date(2026, 11, 4)
    first.shipping = Decimal(10)
    await rederive_purchase(db, first)
    assert (await _average(db, flour.id), used.unit_cost) == (Decimal("5.333333"), 4)
    assert await on_hand(db, flour.id) == 15


async def test_unreceiving_a_line_takes_back_its_lot_unless_it_is_drawn_on(
    db: WriteSession, main: Location, material, buy
):
    flour = await material("Flour")
    purchase = await buy((flour, "10", "2"))
    [line] = await _lines(db, purchase.id)
    lot = (await db.exec(select(Lot).where(Lot.purchase_line_id == line.id))).one()
    drawn = await adjust(
        db,
        Adjustment(
            item_id=flour.id,
            location_id=main.id,
            lot_id=lot.id,
            kind=AdjustmentKind.MANUAL,
            dated_on=purchase.dated_on,
            change=Decimal(-2),
        ),
    )
    with pytest.raises(StockRefused) as refused:
        await unreceive_line(db, line)
    assert refused.value.code == StockError.LOT_IN_USE

    await db.delete(drawn)
    await unreceive_line(db, line)
    assert line.received_on is None
    assert (await db.exec(select(Lot).where(Lot.item_id == flour.id))).all() == []
    assert (await on_hand(db, flour.id), await _average(db, flour.id)) == (0, 0)


async def test_a_receipt_is_dated_the_day_it_arrived(db: WriteSession, main: Location, material):
    flour = await material("Flour")
    purchase, [line] = await _order(db, main, (flour, "10", "10", "2"))

    with pytest.raises(PurchaseRefused) as refused:
        await receive_line(db, line, received_on=date(2026, 11, 7))
    assert refused.value.code == PurchaseError.RECEIVED_BEFORE_PURCHASE

    await receive_line(db, line, received_on=date(2026, 11, 15))
    lot, adjustment = await _received(db, line)
    assert (lot.dated_on, adjustment.dated_on) == (date(2026, 11, 15), date(2026, 11, 15))

    line.received_on = date(2026, 11, 12)
    await rederive_purchase(db, purchase)
    assert (lot.dated_on, adjustment.dated_on) == (date(2026, 11, 12), date(2026, 11, 12))

    purchase.dated_on = date(2026, 11, 13)
    with pytest.raises(PurchaseRefused) as refused:
        await rederive_purchase(db, purchase)
    assert refused.value.code == PurchaseError.RECEIVED_BEFORE_PURCHASE


async def test_receiving_part_of_a_line_splits_it_and_taking_it_back_merges_them(
    db: WriteSession, main: Location, material
):
    flour, sugar = await material("Flour"), await material("Sugar")
    # A pound of flour, counted in grams, beside a sugar, with shipping to spread.
    purchase, [pound, other] = await _order(
        db, main, (flour, "1", "453.592", "9"), (sugar, "1", "1", "1"), shipping=Decimal(10)
    )
    whole = landed_costs(purchase, [pound, other], tax_in_cost=False)

    with pytest.raises(PurchaseRefused) as refused:
        await receive_line(db, pound, quantity=Decimal("453.593"))
    assert refused.value.code == PurchaseError.RECEIVED_MORE_THAN_LINE

    part = await receive_line(db, pound, received_on=date(2026, 11, 15), quantity=Decimal(200))
    assert part.id not in (pound.id, other.id)
    assert [
        (line.purchase_quantity, line.tracking_quantity, line.unit_cost, line.received_on)
        for line in (pound, part)
    ] == [
        (Decimal("0.559075"), Decimal("253.592"), 9, None),
        (Decimal("0.440925"), Decimal(200), 9, date(2026, 11, 15)),
    ]
    # Their values add up to the line's, so the spread is as it was.
    split = landed_costs(purchase, await _lines(db, purchase.id), tax_in_cost=False)
    assert (split[pound.id], split[part.id], split[other.id]) == (
        whole[pound.id],
        whole[pound.id],
        whole[other.id],
    )
    lot, adjustment = await _received(db, part)
    await db.refresh(lot)
    assert (lot.number, lot.balance, adjustment.change) == (f"P-1-{part.id}", 200, 200)
    assert await on_hand(db, flour.id) == 200

    await unreceive_line(db, part)
    assert [line.id for line in await _lines(db, purchase.id)] == [pound.id, other.id]
    assert (pound.purchase_quantity, pound.tracking_quantity) == (1, Decimal("453.592"))
    assert await on_hand(db, flour.id) == 0

    # Once the rest has come too, a part taken back stays a line of its own.
    part = await receive_line(db, pound, quantity=Decimal(200))
    await receive_line(db, pound)
    await unreceive_line(db, part)
    assert [(line.id, line.received_on) for line in await _lines(db, purchase.id)] == [
        (pound.id, date.today()),
        (other.id, None),
        (part.id, None),
    ]
    assert await on_hand(db, flour.id) == Decimal("253.592")


async def test_a_product_s_variation_is_bought_into_a_lot_at_its_landed_cost(
    db: WriteSession, main: Location, base
):
    _, infuser = await add_product(db, Product(name="Infuser"))
    # Twelve at 2 each, with 6 of shipping: 2.50 each landed.
    _, [line] = await _order(db, main, (infuser, "12", "12", "2"), shipping=Decimal(6))

    part = await receive_line(db, line, quantity=Decimal(5))
    lot, adjustment = await _received(db, part)
    await db.refresh(lot)
    assert (lot.item_id, lot.balance, adjustment.unit_cost) == (infuser.id, 5, Decimal("2.5"))
    assert (line.tracking_quantity, line.received_on) == (7, None)
    assert (await on_hand(db, infuser.id), await _average(db, infuser.id)) == (5, Decimal("2.5"))

    # A bundle has no stock of its own, and a base is made, so neither is bought.
    _, box = await add_product(db, Product(name="Gift box", bundle=True))
    for unbought in (box.id, (await base("Syrup")).id):
        with pytest.raises(PurchaseRefused) as refused:
            await bought(db, unbought)
        assert refused.value.code == PurchaseError.PURCHASE_LINE_KIND
