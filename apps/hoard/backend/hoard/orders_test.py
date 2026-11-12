"""What open orders commit, and what is available beside it."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlmodel import select

from hoard import orders, shipments
from hoard.records import (
    Location,
    Order,
    OrderLine,
    OrderStatus,
    Product,
    Shipment,
    ShipmentLine,
)
from hoard.stock import add_product
from seneschal_core.db import WriteSession


async def _line(db: WriteSession, order: Order) -> OrderLine:
    return (await db.exec(select(OrderLine).where(OrderLine.order_id == order.id))).one()


async def test_open_orders_commit_what_they_have_not_shipped(
    db: WriteSession, main: Location, lot, sell
):
    _, chai = await add_product(db, Product(name="Chai"))
    _, box = await add_product(db, Product(name="Gift box", bundle=True))
    await lot(chai.id, "C-1", "20")
    market = Location(organization_id=db.organization_id, name="Market")
    db.add(market)
    await db.flush()

    order = await sell((chai.id, "5", "4"))
    await sell((chai.id, "3", "4"), location_id=market.id)
    for status in (OrderStatus.PENDING, OrderStatus.SHIPPED, OrderStatus.CANCELLED):
        await sell((chai.id, "100", "4"), status=status)
    # A bundle's own line commits nothing; its part does.
    boxed = await sell((box.id, "1", "12"))
    db.add(
        OrderLine(
            organization_id=db.organization_id,
            order_id=boxed.id,
            item_id=chai.id,
            quantity=Decimal(2),
            bundle_line_id=(await _line(db, boxed)).id,
        )
    )
    await db.flush()

    assert await orders.committed(db, chai.id) == 10
    assert await orders.committed(db, chai.id, main.id) == 7
    assert await orders.committed(db, box.id) == 0
    assert await orders.available(db, chai.id, main.id) == 13

    # What a pending shipment holds is still committed; once shipped, it is
    # neither committed nor on hand.
    shipment = Shipment(organization_id=db.organization_id, order_id=order.id, location_id=main.id)
    db.add(shipment)
    await db.flush()
    db.add(
        ShipmentLine(
            organization_id=db.organization_id,
            shipment_id=shipment.id,
            order_line_id=(await _line(db, order)).id,
            item_id=chai.id,
            quantity=Decimal(4),
        )
    )
    await db.flush()
    assert await orders.committed(db, chai.id, main.id) == 7

    await shipments.post(db, shipment, date(2026, 11, 3))
    assert await orders.committed(db, chai.id, main.id) == 3
    assert await orders.available(db, chai.id, main.id) == 13
