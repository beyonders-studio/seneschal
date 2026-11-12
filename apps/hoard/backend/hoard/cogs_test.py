"""Cost of goods over a period, valued at moving average cost, and the mass
balance, split by a lot property or not."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlmodel import col, select

from hoard import cogs, shipments
from hoard.records import (
    Adjustment,
    AdjustmentKind,
    AdjustmentReason,
    DefaultReason,
    ItemKind,
    Location,
    Lot,
    OrderLine,
    Product,
    ProductionRun,
    PropertyDefinition,
    PropertyType,
    PropertyValue,
    RecordKind,
    RunStatus,
    Shipment,
    ShipmentLine,
)
from hoard.stock import add_default_reasons, add_product, adjust
from seneschal_core.db import WriteSession

START, END = date(2026, 1, 1), date(2026, 12, 31)


async def _reason(db: WriteSession, key: DefaultReason) -> int:
    await add_default_reasons(db)
    found = await db.exec(select(AdjustmentReason.id).where(AdjustmentReason.key == key.value))
    return found.one()


async def _take(
    db: WriteSession, item_id: int, main: Location, change: str, on: date, reason: int
) -> None:
    await adjust(
        db,
        Adjustment(
            item_id=item_id,
            location_id=main.id,
            kind=AdjustmentKind.MANUAL,
            dated_on=on,
            change=Decimal(change),
            reason_id=reason,
        ),
    )


async def _ship(db: WriteSession, main: Location, sell, item_id: int, quantity: str, on: date):
    order = await sell((item_id, quantity, "9"))
    line = (await db.exec(select(OrderLine).where(OrderLine.order_id == order.id))).one()
    shipment = Shipment(organization_id=db.organization_id, order_id=order.id, location_id=main.id)
    db.add(shipment)
    await db.flush()
    db.add(
        ShipmentLine(
            organization_id=db.organization_id,
            shipment_id=shipment.id,
            order_line_id=line.id,
            item_id=item_id,
            quantity=Decimal(quantity),
        )
    )
    await db.flush()
    await shipments.post(db, shipment, on)


async def test_cost_of_goods_is_opening_plus_purchases_less_personal_use_and_closing(
    db: WriteSession, main: Location, material, buy, lot, sell
):
    flour = await material("Flour")
    sugar = await material("Sugar")
    _, chai = await add_product(db, Product(name="Chai"))
    await buy((flour, "10", "2"), dated_on=date(2025, 12, 1))
    await buy((flour, "10", "4"), dated_on=date(2026, 3, 1))
    await _take(
        db, flour.id, main, "-2", date(2026, 4, 1), await _reason(db, DefaultReason.PERSONAL_USE)
    )
    await _take(
        db, flour.id, main, "-3", date(2026, 6, 1), await _reason(db, DefaultReason.DAMAGED)
    )
    await lot(sugar.id, "S-1", "5", date(2026, 5, 1), unit_cost="1")
    await lot(chai.id, "C-1", "4", date(2026, 5, 1), unit_cost="2")
    await _ship(db, main, sell, chai.id, "3", date(2026, 7, 1))
    await buy((flour, "100", "1"), dated_on=date(2027, 1, 5))
    db.add(
        ProductionRun(
            organization_id=db.organization_id,
            code="R-1",
            item_id=chai.id,
            location_id=main.id,
            status=RunStatus.COMPLETED,
            completed_on=date(2026, 8, 1),
            actual_quantity=Decimal(0),
            uncosted=True,
        )
    )
    await db.flush()

    found = await cogs.cost_of_goods(db, START, END)

    # Flour: 10 at 2 open the year; 10 at 4 bought make the average 3, so the
    # 2 taken for personal use cost 6, and 15 close the year at 45. Sugar's 5
    # at 1 and chai's 4 at 2 start in the year; 3 chai ship at 6, and 1 closes.
    assert found == cogs.CostOfGoods(
        currency="USD",
        opening=Decimal("20.000000"),
        starting=Decimal("13.000000"),
        purchases=Decimal("40.000000"),
        personal_use=Decimal("6.000000"),
        closing=Decimal("52.000000"),
        cost_of_goods=Decimal("15.000000"),
        shipped=Decimal("6.000000"),
        uncosted_runs=1,
    )


async def test_stock_out_at_the_start_opens_at_nothing(db: WriteSession, material, buy, main):
    flour = await material("Flour")
    await buy((flour, "4", "2"), dated_on=date(2025, 6, 1))
    await _take(db, flour.id, main, "-4", date(2025, 7, 1), await _reason(db, DefaultReason.LOST))
    await buy((flour, "4", "5"), dated_on=date(2026, 2, 1))

    found = await cogs.cost_of_goods(db, START, END)

    assert (found.opening, found.purchases, found.closing, found.cost_of_goods) == (
        Decimal(0),
        Decimal("20.000000"),
        Decimal("20.000000"),
        Decimal(0),
    )


async def test_the_mass_balance_accounts_for_every_change(
    db: WriteSession, main: Location, material, buy, lot, sell
):
    flour = await material("Flour")
    _, chai = await add_product(db, Product(name="Chai"))
    await buy((flour, "10", "2"), dated_on=date(2025, 12, 1))
    await buy((flour, "6", "2"), dated_on=date(2026, 3, 1))
    await _take(
        db, flour.id, main, "-1", date(2026, 4, 1), await _reason(db, DefaultReason.DAMAGED)
    )
    await lot(chai.id, "C-1", "4", date(2026, 5, 1))
    await _ship(db, main, sell, chai.id, "3", date(2026, 7, 1))
    await material("Salt")

    found = await cogs.mass_balance(db, START, END)

    assert [
        (one.name, one.kind, one.unit, one.option, one.opening, one.received, one.sold, one.lost)
        for one in found
    ] == [
        ("Chai", ItemKind.VARIATION, None, None, 0, 4, 3, 0),
        ("Flour", ItemKind.MATERIAL, "g", None, 10, 6, 0, 1),
    ]
    assert [one.closing for one in found] == [1, 15]


async def test_the_mass_balance_splits_by_what_each_lot_holds(
    db: WriteSession, main: Location, material, buy
):
    tea = await material("Tea")
    status = PropertyDefinition(
        organization_id=db.organization_id,
        kind=RecordKind.LOT,
        name="Fair trade",
        type=PropertyType.SELECT,
        options=[{"value": "certified", "label": "Certified"}],
    )
    db.add(status)
    await db.flush()
    await buy((tea, "8", "2"), dated_on=date(2026, 2, 1))
    await buy((tea, "3", "2"), dated_on=date(2026, 3, 1))
    certified = (
        await db.exec(select(Lot).where(Lot.item_id == tea.id).order_by(col(Lot.id)))
    ).first()
    assert certified is not None
    db.add(
        PropertyValue(
            organization_id=db.organization_id,
            definition_id=status.id,
            lot_id=certified.id,
            value="certified",
        )
    )
    await db.flush()
    await adjust(
        db,
        Adjustment(
            item_id=tea.id,
            location_id=main.id,
            lot_id=certified.id,
            kind=AdjustmentKind.MANUAL,
            dated_on=date(2026, 4, 1),
            change=Decimal(-2),
            reason_id=await _reason(db, DefaultReason.LOST),
        ),
    )

    split = await cogs.mass_balance(db, START, END, status.id)
    whole = await cogs.mass_balance(db, START, END)

    assert [(one.option, one.received, one.lost, one.closing) for one in split] == [
        ("certified", 8, 2, 6),
        (None, 3, 0, 3),
    ]
    assert [(one.option, one.received, one.closing) for one in whole] == [(None, 11, 9)]
