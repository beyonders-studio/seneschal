"""Purchases through the API: receiving lines, or part of one, on the day they
arrived; when a purchase is overdue; and costs only for who sees them."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from sqlmodel import select

from hoard import plugin_manifest
from hoard.records import Adjustment, Category, CategoryKind, Item, Lot, OutboxEvent, Purchase
from hoard.stock import on_hand
from seneschal_core.db import WriteSession


async def _average(db: WriteSession, item_id: int) -> Decimal:
    return (await db.exec(select(Item.average_cost).where(Item.id == item_id))).one()


async def _buy(client, material_id: int, quantity: str, unit_cost: str, **fields) -> dict:
    answer = await client.post(
        "/api/v1/purchases",
        json={
            "dated_on": "2026-11-01",
            "lines": [
                {"material_id": material_id, "purchase_quantity": quantity, "unit_cost": unit_cost}
            ],
            **fields,
        },
    )
    assert answer.status_code == 201, answer.text
    return answer.json()


async def _receive(client, purchase: dict) -> dict:
    line = purchase["lines"][0]["id"]
    answer = await client.post(
        f"/api/v1/purchases/{purchase['id']}/lines/{line}/receive", json={"number": "L-1"}
    )
    assert answer.status_code == 200, answer.text
    return answer.json()


async def test_receiving_a_line_makes_its_lot_and_moves_the_average(
    client, sign_in, db: WriteSession, material, main
):
    flour = await material("Flour")
    await db.commit()
    await sign_in(client, (plugin_manifest.SEE_COSTS,))

    first = await _buy(client, flour.id, "10", "2", code="P-1", shipping="10", tax="4")
    assert (first["code"], first["total"], first["lines"][0]["received_on"]) == (
        "P-1",
        "34.000000",
        None,
    )
    assert await _average(db, flour.id) == 0

    received = await _receive(client, first)
    line = received["lines"][0]
    assert (line["received_on"], line["lot"]["number"], line["landed_cost"]) == (
        str(date.today()),
        "L-1",
        "3.000000",
    )
    assert await _average(db, flour.id) == Decimal(3)

    second = await _receive(client, await _buy(client, flour.id, "10", "5"))
    assert second["code"].startswith("P-")
    assert await _average(db, flour.id) == Decimal(4)
    assert await on_hand(db, flour.id, main.id) == Decimal(20)

    again = await client.post(
        f"/api/v1/purchases/{second['id']}/lines/{second['lines'][0]['id']}/receive", json={}
    )
    assert (again.status_code, again.json()) == (409, {"detail": "PURCHASE_LINE_RECEIVED"})

    edited = await client.patch(
        f"/api/v1/purchases/{second['id']}/lines/{second['lines'][0]['id']}",
        json={"unit_cost": "7"},
    )
    assert edited.json()["lines"][0]["landed_cost"] == "7.000000"
    assert await _average(db, flour.id) == Decimal(5)

    unreceived = await client.post(
        f"/api/v1/purchases/{first['id']}/lines/{first['lines'][0]['id']}/unreceive"
    )
    assert unreceived.json()["lines"][0]["lot"] is None
    lot = select(Lot).where(Lot.purchase_line_id == first["lines"][0]["id"])
    assert (await db.exec(lot)).first() is None
    assert await _average(db, flour.id) == Decimal(7)


async def test_costs_are_neither_sent_nor_taken_without_the_permission(
    client, sign_in, db: WriteSession, material
):
    flour = await material("Flour")
    await db.commit()
    await sign_in(client)

    refused = await client.post(
        "/api/v1/purchases",
        json={"shipping": "1", "lines": [{"material_id": flour.id, "purchase_quantity": "2"}]},
    )
    assert (refused.status_code, refused.json()) == (403, {"detail": "COSTS_HIDDEN"})

    made = await client.post(
        "/api/v1/purchases",
        json={"lines": [{"material_id": flour.id, "purchase_quantity": "2"}]},
    )
    purchase = made.json()
    assert made.status_code == 201
    assert not {"discount", "shipping", "tax", "total"} & purchase.keys()
    assert not {"unit_cost", "landed_cost"} & purchase["lines"][0].keys()

    line = f"/api/v1/purchases/{purchase['id']}/lines/{purchase['lines'][0]['id']}"
    refused = await client.patch(line, json={"unit_cost": "3"})
    assert (refused.status_code, refused.json()) == (403, {"detail": "COSTS_HIDDEN"})
    assert (await client.patch(line, json={"purchase_quantity": "4"})).json()["lines"][0][
        "tracking_quantity"
    ] == "4.000000"

    listed = (await client.get("/api/v1/purchases")).json()
    assert listed["total"] == 1
    assert "total" not in listed["items"][0]
    assert (listed["items"][0]["lines"], listed["items"][0]["received"]) == (1, 0)


async def test_a_purchase_is_placed_in_the_pages_initiative_and_its_receipt_recorded(
    client, sign_in, db: WriteSession, material, main
):
    flour = await material("Flour")
    await db.commit()
    await sign_in(client, (plugin_manifest.SEE_COSTS,), initiative_id=7)

    received = await _receive(client, await _buy(client, flour.id, "10", "2"))

    purchase = await db.get_one(Purchase, received["id"])
    assert purchase.initiative_id == 7
    recorded = await db.exec(select(OutboxEvent).order_by(OutboxEvent.id))
    assert [(event.name, event.initiative_id) for event in recorded] == [
        ("purchase-placed", 7),
        ("purchase-line-received", 7),
        ("purchase-received", 7),
    ]


async def test_part_of_a_line_is_received_on_its_day_and_an_open_purchase_is_overdue(
    client, sign_in, db: WriteSession, material
):
    spices = Category(organization_id=db.organization_id, kind=CategoryKind.MATERIAL, name="Spice")
    db.add(spices)
    await db.flush()
    cumin = await material("Cumin", sku="CUM-1", category_id=spices.id)
    await db.commit()
    await sign_in(client, (plugin_manifest.SEE_COSTS,))
    yesterday = date.today() - timedelta(days=1)

    purchase = await _buy(
        client, cumin.id, "453.592", "1", expected_on=str(yesterday), notes="Ring before noon"
    )
    assert (purchase["overdue"], purchase["notes"]) == (True, "Ring before noon")
    assert (purchase["lines"][0]["sku"], purchase["lines"][0]["category_id"]) == (
        "CUM-1",
        spices.id,
    )
    line = f"/api/v1/purchases/{purchase['id']}/lines/{purchase['lines'][0]['id']}"
    early = await client.post(f"{line}/receive", json={"received_on": "2026-10-31"})
    assert (early.status_code, early.json()) == (409, {"detail": "RECEIVED_BEFORE_PURCHASE"})
    more = await client.post(f"{line}/receive", json={"quantity": "500"})
    assert (more.status_code, more.json()) == (409, {"detail": "RECEIVED_MORE_THAN_LINE"})

    answer = await client.post(
        f"{line}/receive", json={"quantity": "200", "received_on": "2026-11-03"}
    )
    assert answer.status_code == 200, answer.text
    rest, part = answer.json()["lines"]
    assert [(each["tracking_quantity"], each["received_on"]) for each in (rest, part)] == [
        ("253.592000", None),
        ("200.000000", "2026-11-03"),
    ]
    assert part["lot"]["number"] == f"{purchase['code']}-{part['id']}"
    listed = (await client.get("/api/v1/purchases")).json()["items"][0]
    assert (listed["overdue"], listed["received"], listed["lines"]) == (True, 1, 2)

    moved = await client.patch(
        f"/api/v1/purchases/{purchase['id']}/lines/{part['id']}", json={"received_on": "2026-11-05"}
    )
    assert moved.status_code == 200, moved.text
    dated = select(Adjustment.dated_on).where(Adjustment.purchase_line_id == part["id"])
    assert (await db.exec(dated)).one() == date(2026, 11, 5)
    late = await client.patch(
        f"/api/v1/purchases/{purchase['id']}", json={"dated_on": "2026-11-06"}
    )
    assert (late.status_code, late.json()) == (409, {"detail": "RECEIVED_BEFORE_PURCHASE"})
    on_order = await client.patch(f"{line}", json={"received_on": "2026-11-05"})
    assert (on_order.status_code, on_order.json()) == (
        409,
        {"detail": "PURCHASE_LINE_NOT_RECEIVED"},
    )

    await client.post(f"{line}/receive", json={})
    received = (await client.get(f"/api/v1/purchases/{purchase['id']}")).json()
    assert received["overdue"] is False
    cleared = await client.patch(
        f"/api/v1/purchases/{purchase['id']}", json={"expected_on": None, "notes": None}
    )
    assert (cleared.json()["expected_on"], cleared.json()["notes"]) == (None, None)
