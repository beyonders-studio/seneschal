"""Shipments through the API: an order shipped in two parts, from a lot
recorded and one assumed, and what comes back of it."""

from __future__ import annotations

from decimal import Decimal

from hoard.records import Location, Product
from hoard.stock import add_product, on_hand
from seneschal_core.db import WriteSession


async def test_an_order_ships_in_parts_and_takes_returns(
    client, sign_in, db: WriteSession, main: Location, lot
):
    _, chai = await add_product(db, Product(name="Chai", default_price=Decimal(4)))
    await lot(chai.id, "C-1", "3")
    scanned = await lot(chai.id, "C-2", "10")
    await db.commit()
    await sign_in(client)
    order = (
        await client.post(
            "/api/v1/orders",
            json={
                "customer_contact_id": 7,
                "delivery_address": "1 Market St",
                "lines": [{"item_id": chai.id, "quantity": "6"}],
            },
        )
    ).json()
    line = order["lines"][0]["id"]

    over = await client.post(
        "/api/v1/shipments",
        json={"order_id": order["id"], "lines": [{"order_line_id": line, "quantity": "7"}]},
    )
    assert (over.status_code, over.json()["detail"]) == (422, "SHIPMENT_OVER_ORDER")
    planned = await client.post(
        "/api/v1/shipments",
        json={
            "order_id": order["id"],
            "lines": [{"order_line_id": line, "quantity": "2", "lot_id": scanned.id}],
            "tracking": "1Z",
        },
    )
    assert planned.status_code == 201, planned.text
    [first] = planned.json()["shipments"]
    assert (first["status"], first["delivery_address"], first["tracking"]) == (
        "pending",
        "1 Market St",
        "1Z",
    )
    assert [lot["number"] for lot in first["lines"][0]["lots"]] == ["C-1", "C-2"]

    # The rest, with no lines named, from the lot assumed when it ships.
    rest = (await client.post("/api/v1/shipments", json={"order_id": order["id"]})).json()
    second = rest["shipments"][1]
    assert (second["lines"][0]["quantity"], rest["lines"][0]["held"]) == ("4.000000", "6.000000")
    moved = await client.patch(
        f"/api/v1/shipments/{second['id']}/lines/{second['lines'][0]['id']}",
        json={"quantity": "3"},
    )
    assert moved.json()["shipments"][1]["lines"][0]["quantity"] == "3.000000"

    shipped = await client.post(
        f"/api/v1/shipments/{first['id']}/ship", json={"shipped_on": "2026-11-05"}
    )
    assert shipped.json()["shipments"][0]["lines"][0]["lots"] == []
    shipped = await client.post(
        f"/api/v1/shipments/{second['id']}/ship", json={"shipped_on": "2026-11-06"}
    )
    after = shipped.json()
    assert (after["status"], after["lines"][0]["shipped"]) == ("open", "5.000000")
    drawn = [(one["lot"]["number"], one["assumed"]) for one in after["shipments"][1]["lines"]]
    assert drawn == [("C-1", True)]
    assert await on_hand(db, chai.id) == 8

    # A third ships the last one, and the order is shipped.
    last = (await client.post("/api/v1/shipments", json={"order_id": order["id"]})).json()
    done = await client.post(f"/api/v1/shipments/{last['shipments'][2]['id']}/ship", json={})
    assert done.json()["status"] == "shipped"

    shipped_line = after["shipments"][0]["lines"][0]["id"]
    returns = f"/api/v1/shipments/{first['id']}/lines/{shipped_line}/returns"
    back = await client.post(
        returns, json={"quantity": "1", "dated_on": "2026-11-07", "damaged": True}
    )
    assert back.status_code == 201, back.text
    [returned] = back.json()["shipments"][0]["lines"][0]["returns"]
    assert (returned["quantity"], returned["damaged"]) == ("1.000000", True)
    # What came back damaged is not on hand.
    assert await on_hand(db, chai.id) == 7
    over = await client.post(returns, json={"quantity": "2"})
    assert (over.status_code, over.json()["detail"]) == (422, "RETURN_OVER_SHIPPED")
    kept = await client.post(f"/api/v1/shipments/{first['id']}/unship")
    assert (kept.status_code, kept.json()["detail"]) == (409, "SHIPMENT_RETURNED")

    undone = await client.delete(f"/api/v1/shipments/{first['id']}/returns/{returned['id']}")
    assert undone.json()["shipments"][0]["lines"][0]["returns"] == []
    unshipped = await client.post(f"/api/v1/shipments/{first['id']}/unship")
    assert unshipped.json()["status"] == "open"
    gone = await client.delete(f"/api/v1/shipments/{first['id']}")
    assert [one["id"] for one in gone.json()["shipments"]] == [
        second["id"],
        last["shipments"][2]["id"],
    ]
