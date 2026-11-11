"""Purchases through the API: receiving lines, and costs only for who sees them."""

from __future__ import annotations

from decimal import Decimal

from sqlmodel import select

from hoard import plugin_manifest
from hoard.records import Item, Lot
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
    assert (first["code"], first["total"], first["lines"][0]["received"]) == (
        "P-1",
        "34.000000",
        False,
    )
    assert await _average(db, flour.id) == 0

    received = await _receive(client, first)
    line = received["lines"][0]
    assert (line["received"], line["lot"]["number"], line["landed_cost"]) == (
        True,
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
