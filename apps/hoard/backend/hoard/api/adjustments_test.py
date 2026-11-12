"""Adjustments through the API: changes made by hand, which re-derive cost."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlmodel import select

from hoard import plugin_manifest, shipments
from hoard.records import AdjustmentReason, Item, Product
from hoard.stock import add_product, on_hand
from seneschal_core.db import WriteSession


async def _average(db: WriteSession, item_id: int) -> Decimal:
    return (await db.exec(select(Item.average_cost).where(Item.id == item_id))).one()


async def test_a_manual_adjustment_and_its_edits_rederive_cost(
    client, sign_in, db: WriteSession, material, buy, main
):
    flour = await material("Flour")
    await buy((flour, "10", "2"))
    spilt = AdjustmentReason(organization_id=db.organization_id, name="Spilt")
    db.add(spilt)
    await db.commit()
    await sign_in(client, (plugin_manifest.SEE_COSTS,))

    made = await client.post(
        "/api/v1/adjustments",
        json={"item_id": flour.id, "change": "10", "dated_on": "2026-11-02", "notes": "found"},
    )
    adjustment = made.json()
    assert made.status_code == 201, made.text
    assert (adjustment["kind"], adjustment["item"]["name"], adjustment["unit_cost"]) == (
        "manual",
        "Flour",
        "0.000000",
    )
    assert await _average(db, flour.id) == Decimal(1)

    edited = await client.patch(
        f"/api/v1/adjustments/{adjustment['id']}", json={"change": "-4", "reason_id": spilt.id}
    )
    assert (edited.json()["reason_id"], edited.json()["unit_cost"]) == (spilt.id, "2.000000")
    assert await _average(db, flour.id) == Decimal(2)
    assert await on_hand(db, flour.id, main.id) == Decimal(6)

    derived = await client.patch(f"/api/v1/adjustments/{adjustment['id']}", json={"unit_cost": "9"})
    assert (derived.status_code, derived.json()) == (409, {"detail": "ADJUSTMENT_COST_DERIVED"})
    zero = await client.patch(f"/api/v1/adjustments/{adjustment['id']}", json={"change": "0"})
    assert (zero.status_code, zero.json()) == (422, {"detail": "ADJUSTMENT_CHANGE_ZERO"})

    purchased = (await client.get("/api/v1/adjustments", params={"kind": "purchase"})).json()
    assert purchased["total"] == 1
    assert purchased["items"][0]["purchase_id"] is not None
    from_record = await client.delete(f"/api/v1/adjustments/{purchased['items'][0]['id']}")
    assert (from_record.status_code, from_record.json()) == (
        409,
        {"detail": "ADJUSTMENT_FROM_RECORD"},
    )

    assert (await client.delete(f"/api/v1/adjustments/{adjustment['id']}")).status_code == 204
    assert await on_hand(db, flour.id, main.id) == Decimal(10)
    assert (await client.get("/api/v1/adjustments", params={"item_id": flour.id})).json()[
        "total"
    ] == 1


async def test_a_damaged_returns_write_off_changes_only_through_its_order(
    client, sign_in, db: WriteSession, main, lot, sell
):
    _, chai = await add_product(db, Product(name="Chai"))
    await lot(chai.id, "C-1", "5")
    order = await sell((chai.id, "2", "9"))
    shipment = await shipments.add(db, order, main.id, [])
    await shipments.ship(db, shipment, date(2026, 11, 5))
    [line] = await shipments.lines(db, shipment.id)
    await shipments.record_return(db, line, Decimal(1), date(2026, 11, 7), damaged=True)
    await db.commit()
    await sign_in(client)

    manual = (await client.get("/api/v1/adjustments", params={"kind": "manual"})).json()
    [written_off] = manual["items"]
    assert (written_off["change"], written_off["order_id"]) == ("-1.000000", order.id)
    edited = await client.patch(f"/api/v1/adjustments/{written_off['id']}", json={"notes": "x"})
    deleted = await client.delete(f"/api/v1/adjustments/{written_off['id']}")
    for refused in (edited, deleted):
        assert (refused.status_code, refused.json()) == (409, {"detail": "ADJUSTMENT_FROM_RECORD"})
    shipped = (await client.get("/api/v1/adjustments", params={"kind": "shipment"})).json()
    assert shipped["items"][0]["order_id"] == order.id
    assert await on_hand(db, chai.id, main.id) == Decimal(3)


async def test_a_member_without_costs_adjusts_but_sees_and_types_no_cost(
    client, sign_in, db: WriteSession, material, buy
):
    flour = await material("Flour")
    await buy((flour, "10", "2"))
    await db.commit()
    await sign_in(client)

    made = await client.post("/api/v1/adjustments", json={"item_id": flour.id, "change": "-1"})
    assert made.status_code == 201
    assert "unit_cost" not in made.json()
    assert all(
        "unit_cost" not in item
        for item in (await client.get("/api/v1/adjustments")).json()["items"]
    )

    refused = await client.patch(
        f"/api/v1/adjustments/{made.json()['id']}", json={"unit_cost": "1"}
    )
    assert (refused.status_code, refused.json()) == (403, {"detail": "COSTS_HIDDEN"})
