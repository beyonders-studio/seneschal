"""Receiving purchases: landed costs, lots, and edits that re-derive cost."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlmodel import select

from hoard.purchases import rederive_purchase, unreceive_line
from hoard.records import Adjustment, AdjustmentKind, Item, Location, Lot, PurchaseLine
from hoard.stock import StockError, StockRefused, adjust, on_hand
from seneschal_core.db import WriteSession


async def _average(db: WriteSession, item_id: int) -> Decimal:
    return (await db.get_one(Item, item_id)).average_cost


async def _lines(db: WriteSession, purchase_id: int) -> list[PurchaseLine]:
    lines = await db.exec(select(PurchaseLine).where(PurchaseLine.purchase_id == purchase_id))
    return list(lines.all())


async def test_discount_and_shipping_spread_by_value_and_tax_stays_out(
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
    flour_line = next(
        line for line in await _lines(db, purchase.id) if line.material_id == flour.id
    )
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
    first.dated_on = date(2026, 11, 4)
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
    assert not line.received
    assert (await db.exec(select(Lot).where(Lot.item_id == flour.id))).all() == []
    assert (await on_hand(db, flour.id), await _average(db, flour.id)) == (0, 0)
