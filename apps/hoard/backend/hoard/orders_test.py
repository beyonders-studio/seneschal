"""What open orders commit, and what is available beside it; a hand order's
lines, bundles split into their parts; and an order shipped, cancelled and
reopened."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlmodel import select

from hoard import orders, recipes, shipments
from hoard.orders import OrderError, OrderRefused
from hoard.records import (
    Channel,
    Location,
    Order,
    OrderLine,
    OrderStatus,
    Product,
    Recipe,
    Shipment,
    ShipmentLine,
    ShipmentStatus,
    Store,
    Variation,
)
from hoard.stock import add_product, add_variation
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


async def _refused(making) -> OrderError:
    with pytest.raises(OrderRefused) as refused:
        await making
    return refused.value.code


async def test_a_hand_order_sells_variations_and_splits_bundles(
    db: WriteSession, main: Location, material, sell
):
    product, chai = await add_product(db, Product(name="Chai", default_price=Decimal(4)))
    large = await add_variation(
        db, Variation(product_id=product.id, attributes={"size": "large"}, price=Decimal(6))
    )
    _, mug = await add_product(db, Product(name="Mug"))
    box, _ = await add_product(db, Product(name="Gift box", default_price=Decimal(20)))
    recipe = Recipe(product_id=box.id, batch_quantity=Decimal(2))
    await recipes.save(
        db, recipe, [recipes.Line(chai.id, Decimal(4)), recipes.Line(mug.id, Decimal(2))]
    )
    boxed = (await db.exec(select(Variation).where(Variation.product_id == box.id))).one()
    order = await sell()

    await orders.add_line(db, order, chai.id, Decimal(1))
    await orders.add_line(db, order, large.id, Decimal(1))
    await orders.add_line(db, order, large.id, Decimal(1), Decimal(5))
    bundle = await orders.add_line(db, order, boxed.id, Decimal(3))
    assert await _refused(orders.add_line(db, order, (await material("Sugar")).id, Decimal(1))) is (
        OrderError.ORDER_ITEM_NOT_SOLD
    )

    def sold(lines: list[OrderLine]) -> list[tuple[int, Decimal, Decimal, bool]]:
        return [
            (line.item_id, line.quantity, line.price, line.bundle_line_id is not None)
            for line in lines
        ]

    # Each bundle holds 2 of chai and 1 mug; its parts sell at no price.
    assert sold(await orders.lines(db, order.id)) == [
        (chai.id, 1, 4, False),
        (large.id, 1, 6, False),
        (large.id, 1, 5, False),
        (boxed.id, 3, 20, False),
        (chai.id, 6, 0, True),
        (mug.id, 3, 0, True),
    ]
    assert await orders.committed(db, chai.id) == 7
    assert await orders.committed(db, boxed.id) == 0

    await orders.change_line(db, order, bundle, quantity=Decimal(1))
    parts = (await orders.lines(db, order.id))[4:]
    assert sold(parts) == [(chai.id, 2, 0, True), (mug.id, 1, 0, True)]
    assert await _refused(orders.change_line(db, order, parts[0], quantity=Decimal(1))) is (
        OrderError.ORDER_LINE_PART
    )

    # Once a shipment holds a part, its bundle stays as it is.
    shipment = await shipments.add(db, order, main.id, [shipments.Pick(parts[1].id)])
    assert await _refused(orders.change_line(db, order, bundle, quantity=Decimal(2))) is (
        OrderError.ORDER_LINE_SHIPPED
    )
    assert await _refused(orders.remove_line(db, order, bundle)) is OrderError.ORDER_LINE_SHIPPED
    await shipments.remove(db, shipment)
    await orders.remove_line(db, order, bundle)
    assert [line.item_id for line in await orders.lines(db, order.id)] == [
        chai.id,
        large.id,
        large.id,
    ]

    # A store's order changes in the store.
    store = Store(organization_id=db.organization_id, channel=Channel.SHOPIFY)
    db.add(store)
    await db.flush()
    copied = await sell(store_id=store.id, external_id="gid://1", customer_contact_id=None)
    assert await _refused(orders.add_line(db, copied, chai.id, Decimal(1))) is (
        OrderError.ORDER_FROM_STORE
    )
    assert await _refused(orders.delete(db, copied)) is OrderError.ORDER_FROM_STORE


async def test_an_order_ships_cancels_and_reopens(db: WriteSession, main: Location, lot, sell):
    _, chai = await add_product(db, Product(name="Chai"))
    await lot(chai.id, "C-1", "10")
    order = await sell((chai.id, "4", "4"))
    [line] = await orders.lines(db, order.id)
    first = await shipments.add(db, order, main.id, [shipments.Pick(line.id, Decimal(1))])
    second = await shipments.add(db, order, main.id, [])
    assert [held.quantity for held in await shipments.lines(db, second.id)] == [3]

    await shipments.ship(db, first, date(2026, 11, 4))
    assert order.status is OrderStatus.OPEN
    await shipments.ship(db, second, date(2026, 11, 5))
    assert order.status is OrderStatus.SHIPPED
    assert await orders.committed(db, chai.id) == 0
    await shipments.unship(db, second)
    assert order.status is OrderStatus.OPEN
    assert await orders.committed(db, chai.id) == 3

    # Cancelling cancels what is pending and keeps what shipped.
    await orders.cancel(db, order)
    assert (order.status, second.status, first.status) == (
        OrderStatus.CANCELLED,
        ShipmentStatus.CANCELLED,
        ShipmentStatus.SHIPPED,
    )
    assert await orders.committed(db, chai.id) == 0
    assert await _refused(orders.cancel(db, order)) is OrderError.ORDER_CANCELLED
    assert await _refused(orders.add_line(db, order, chai.id, Decimal(1))) is (
        OrderError.ORDER_CANCELLED
    )
    with pytest.raises(shipments.ShipmentRefused) as refused:
        await shipments.add(db, order, main.id, [])
    assert refused.value.code is OrderError.ORDER_CANCELLED

    await orders.reopen(db, order)
    assert order.status is OrderStatus.OPEN
    assert await orders.committed(db, chai.id) == 3
    assert await _refused(orders.reopen(db, order)) is OrderError.ORDER_NOT_CANCELLED
    assert await _refused(orders.delete(db, order)) is OrderError.ORDER_SHIPPED
    await shipments.unship(db, first)
    await orders.delete(db, order)
    assert await db.get(Order, order.id) is None


async def test_what_is_on_hold_is_not_available(db: WriteSession, main: Location, lot, sell, hold):
    _, chai = await add_product(db, Product(name="Chai"))
    suspect = await lot(chai.id, "C-1", "6")
    await lot(chai.id, "C-2", "10")
    await sell((chai.id, "4", "4"))

    await hold(suspect)

    assert await orders.available(db, chai.id, main.id) == 6
    assert await orders.available(db, chai.id) == 6


async def test_an_open_order_is_short_of_what_its_location_lacks(
    db: WriteSession, main: Location, lot, sell, hold
):
    _, chai = await add_product(db, Product(name="Chai"))
    _, mug = await add_product(db, Product(name="Mug"))
    suspect = await lot(chai.id, "C-1", "4")
    await lot(chai.id, "C-2", "3")
    await lot(mug.id, "M-1", "9")
    await sell((chai.id, "2", "4"))
    order = await sell((chai.id, "4", "4"), (mug.id, "1", "8"))
    chai_line, mug_line = await orders.lines(db, order.id)
    assert await orders.short(db, order) == {}

    await hold(suspect)
    # 3 on hand that is not held, less 6 committed: chai lacks 3.
    assert await orders.short(db, order) == {chai_line.id: 3}
    order.status = OrderStatus.CANCELLED
    assert await orders.short(db, order) == {}


async def test_a_deleted_order_leaves_its_holds_naming_none(
    db: WriteSession, main: Location, lot, sell, hold
):
    _, chai = await add_product(db, Product(name="Chai"))
    picked = await lot(chai.id, "C-1", "4")
    order = await sell((chai.id, "1", "4"))
    [line] = await orders.lines(db, order.id)
    await shipments.add(db, order, main.id, [shipments.Pick(line.id, lot_id=picked.id)])
    kept = await hold(picked, order_id=order.id)

    await orders.delete(db, order)

    await db.refresh(kept)
    assert (kept.order_id, kept.ended) == (None, None)
