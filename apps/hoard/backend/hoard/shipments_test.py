"""Shipping moves stock out of the lots recorded or assumed, at the average
cost on the day, and unshipping takes it back."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlmodel import col, select

from hoard import costing, shipments
from hoard.records import (
    Adjustment,
    AdjustmentKind,
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
