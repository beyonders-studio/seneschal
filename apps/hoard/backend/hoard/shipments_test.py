"""Shipping moves stock out of the lots recorded or assumed, at the average
cost on the day, and unshipping takes it back; a shipment is planned within
what its order has left; and what comes back is restocked, or written off
when damaged."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlmodel import col, select

from hoard import costing, orders, shipments
from hoard.records import (
    Adjustment,
    AdjustmentKind,
    AdjustmentReason,
    DefaultReason,
    Location,
    OrderLine,
    Product,
    ReturnLine,
    Shipment,
    ShipmentLine,
    ShipmentStatus,
)
from hoard.stock import add_product, on_hand
from seneschal_core.db import WriteSession

SHIPPED_ON = date(2026, 11, 5)


async def test_shipping_draws_lots_and_unshipping_takes_it_back(
    db: WriteSession, main: Location, lot, sell
):
    _, chai = await add_product(db, Product(name="Chai"))
    older = await lot(chai.id, "C-1", "2", date(2026, 11, 1), unit_cost="2")
    newer = await lot(chai.id, "C-2", "5", date(2026, 11, 3), unit_cost="4")
    picked = await lot(chai.id, "C-3", "4", date(2026, 11, 4), unit_cost="4")
    order = await sell((chai.id, "6", "9"))
    line = (await db.exec(select(OrderLine).where(OrderLine.order_id == order.id))).one()
    shipment = Shipment(organization_id=db.organization_id, order_id=order.id, location_id=main.id)
    db.add(shipment)
    await db.flush()
    for quantity, lot_id in (("5", None), ("1", picked.id)):
        db.add(
            ShipmentLine(
                organization_id=db.organization_id,
                shipment_id=shipment.id,
                order_line_id=line.id,
                item_id=chai.id,
                lot_id=lot_id,
                quantity=Decimal(quantity),
            )
        )
    await db.flush()

    await shipments.post(db, shipment, SHIPPED_ON)

    drawn = [
        (line.lot_id, line.quantity, line.assumed)
        for line in await shipments.lines(db, shipment.id)
    ]
    assert sorted(drawn) == sorted(
        [(older.id, 2, True), (newer.id, 3, True), (picked.id, 1, False)]
    )
    moved = (
        await db.exec(select(Adjustment).where(col(Adjustment.shipment_line_id).is_not(None)))
    ).all()
    assert {adjustment.kind for adjustment in moved} == {AdjustmentKind.SHIPMENT}
    assert sum(adjustment.change for adjustment in moved) == -6
    # Each goes out at the average on the day, (2×2 + 9×4) ÷ 11, as each lot moved it.
    assert {adjustment.unit_cost for adjustment in moved} == {Decimal("3.636363")}
    assert (shipment.status, await on_hand(db, chai.id)) == (ShipmentStatus.SHIPPED, 5)
    with pytest.raises(shipments.ShipmentRefused) as refused:
        await shipments.post(db, shipment, SHIPPED_ON)
    assert refused.value.code is shipments.ShipmentError.SHIPMENT_SHIPPED

    await shipments.unpost(db, shipment)

    assert sorted(
        (line.lot_id or 0, line.quantity, line.assumed)
        for line in await shipments.lines(db, shipment.id)
    ) == [(0, 5, False), (picked.id, 1, False)]
    assert (shipment.status, shipment.shipped_on, await on_hand(db, chai.id)) == (
        ShipmentStatus.PENDING,
        None,
        11,
    )
    with pytest.raises(shipments.ShipmentRefused) as refused:
        await shipments.unpost(db, shipment)
    assert refused.value.code is shipments.ShipmentError.SHIPMENT_NOT_SHIPPED

    # Once something came back of it, it stays shipped.
    await shipments.post(db, shipment, SHIPPED_ON)
    returned = ReturnLine(
        organization_id=db.organization_id,
        shipment_line_id=(await shipments.lines(db, shipment.id))[0].id,
        quantity=Decimal(1),
        dated_on=date(2026, 11, 6),
    )
    db.add(returned)
    await db.flush()
    with pytest.raises(shipments.ShipmentRefused) as refused:
        await shipments.unpost(db, shipment)
    assert refused.value.code is shipments.ShipmentError.SHIPMENT_RETURNED

    # What comes back comes in at its own cost, not at none.
    db.add(
        Adjustment(
            organization_id=db.organization_id,
            item_id=chai.id,
            location_id=main.id,
            kind=AdjustmentKind.RETURN,
            dated_on=returned.dated_on,
            change=Decimal(1),
            unit_cost=Decimal(10),
            return_line_id=returned.id,
        )
    )
    assert await costing.rederive(db, chai.id) == Decimal("4.696969")


async def _refused(making) -> shipments.ShipmentError | orders.OrderError:
    with pytest.raises(shipments.ShipmentRefused) as refused:
        await making
    return refused.value.code


async def test_a_shipment_is_planned_within_what_its_order_has_left(
    db: WriteSession, main: Location, lot, sell
):
    _, chai = await add_product(db, Product(name="Chai"))
    _, mug = await add_product(db, Product(name="Mug"))
    picked = await lot(chai.id, "C-1", "10")
    elsewhere = Location(organization_id=db.organization_id, name="Market")
    db.add(elsewhere)
    await db.flush()
    other = await lot(chai.id, "C-2", "10", location=elsewhere)
    order = await sell((chai.id, "4", "4"), (mug.id, "1", "8"))
    chai_line, mug_line = await orders.lines(db, order.id)
    Error = shipments.ShipmentError

    def plan(*picks: shipments.Pick):
        return shipments.add(db, order, main.id, list(picks))

    assert (
        await _refused(plan(shipments.Pick(chai_line.id, Decimal(5)))) is Error.SHIPMENT_OVER_ORDER
    )
    assert await _refused(plan(shipments.Pick(chai_line.id, lot_id=other.id))) is (
        Error.SHIPMENT_LOT_MISMATCH
    )
    first = await plan(shipments.Pick(chai_line.id, Decimal(3), picked.id))
    [line] = await shipments.lines(db, first.id)
    assert (line.quantity, line.lot_id) == (3, picked.id)

    # With no lines named, it takes what is left of each.
    rest = await plan()
    assert sorted(
        (line.order_line_id, line.quantity) for line in await shipments.lines(db, rest.id)
    ) == [(chai_line.id, 1), (mug_line.id, 1)]
    assert await _refused(plan()) is Error.SHIPMENT_EMPTY
    rest_chai = next(
        line for line in await shipments.lines(db, rest.id) if line.order_line_id == chai_line.id
    )
    assert await _refused(shipments.set_quantity(db, rest, rest_chai, Decimal(2))) is (
        Error.SHIPMENT_OVER_ORDER
    )
    await shipments.set_quantity(db, first, line, Decimal(2))
    await shipments.set_quantity(db, rest, rest_chai, Decimal(2))
    await shipments.set_lot(db, rest, rest_chai, picked.id)

    # Moving it to another location forgets the lots it recorded.
    await shipments.move(db, rest, elsewhere.id)
    assert {line.lot_id for line in await shipments.lines(db, rest.id)} == {None}
    await shipments.set_lot(db, rest, rest_chai, other.id)

    await shipments.ship(db, first, SHIPPED_ON)
    assert await _refused(shipments.set_lot(db, first, line, None)) is Error.SHIPMENT_SHIPPED
    assert await _refused(shipments.remove(db, first)) is Error.SHIPMENT_SHIPPED
    await shipments.remove_line(db, rest, rest_chai)
    await shipments.remove(db, rest)
    assert [s.id for s in (await db.exec(select(Shipment))).all()] == [first.id]


async def test_what_comes_back_is_restocked_or_written_off(
    db: WriteSession, main: Location, lot, sell
):
    _, chai = await add_product(db, Product(name="Chai"))
    picked = await lot(chai.id, "C-1", "10", unit_cost="3")
    order = await sell((chai.id, "4", "9"))
    shipment = await shipments.add(db, order, main.id, [])
    [line] = await shipments.lines(db, shipment.id)
    assert (
        await _refused(shipments.record_return(db, line, Decimal(1), SHIPPED_ON, damaged=False))
        is shipments.ShipmentError.SHIPMENT_NOT_SHIPPED
    )
    await shipments.ship(db, shipment, SHIPPED_ON)
    [line] = await shipments.lines(db, shipment.id)
    # The lot is assumed, and something more comes in after, at another cost.
    assert (line.lot_id, line.assumed) == (picked.id, True)
    await lot(chai.id, "C-2", "6", date(2026, 11, 6), unit_cost="7")

    back = await shipments.record_return(db, line, Decimal(1), date(2026, 11, 7), damaged=False)
    # The unit comes back to its lot at what it shipped at, 3: (12×5 + 3) ÷ 13.
    await db.refresh(picked)
    assert (picked.balance, await on_hand(db, chai.id)) == (7, 13)
    assert await costing.rederive(db, chai.id) == Decimal("4.846154")

    # Someone removed the reason: it is made again for what comes back damaged.
    reason = (
        await db.exec(
            select(AdjustmentReason).where(
                AdjustmentReason.key == DefaultReason.RETURNED_DAMAGED.value
            )
        )
    ).first()
    if reason is not None:
        await db.delete(reason)
        await db.flush()
    damaged = await shipments.record_return(db, line, Decimal(2), date(2026, 11, 7), damaged=True)
    moved = (
        await db.exec(
            select(Adjustment)
            .where(Adjustment.return_line_id == damaged.id)
            .order_by(col(Adjustment.id))
        )
    ).all()
    assert [(a.kind, a.change, a.lot_id) for a in moved] == [
        (AdjustmentKind.RETURN, 2, picked.id),
        (AdjustmentKind.MANUAL, -2, picked.id),
    ]
    written_off = await db.get_one(AdjustmentReason, moved[1].reason_id)
    assert written_off.key == DefaultReason.RETURNED_DAMAGED
    assert await on_hand(db, chai.id) == 13
    assert (
        await _refused(shipments.record_return(db, line, Decimal(2), SHIPPED_ON, damaged=False))
        is shipments.ShipmentError.RETURN_OVER_SHIPPED
    )
    assert (await shipments.returned(db, [line.id]))[line.id] == 3

    await shipments.remove_return(db, back)
    await shipments.remove_return(db, damaged)
    assert await on_hand(db, chai.id) == 12
    await shipments.unship(db, shipment)
    assert await on_hand(db, chai.id) == 16
