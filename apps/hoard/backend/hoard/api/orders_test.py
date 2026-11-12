"""Orders through the API: a hand order for a customer, its lines and what
they commit, cancelling and reopening it, and a store's order kept as the
store has it."""

from __future__ import annotations

from decimal import Decimal

from hoard import orders
from hoard.records import Channel, Location, Product, Store, Variation
from hoard.stock import add_product, add_variation
from seneschal_core.db import WriteSession


async def test_a_hand_order_is_sold_to_a_customer_and_cancelled(
    client, sign_in, db: WriteSession, main: Location, lot, material
):
    sugar = await material("Sugar")
    product, chai = await add_product(db, Product(name="Chai", default_price=Decimal(4)))
    large = await add_variation(
        db, Variation(product_id=product.id, attributes={"size": "large"}, price=Decimal(6))
    )
    await lot(chai.id, "C-1", "10")
    await db.commit()
    await sign_in(client)

    answer = await client.post("/api/v1/orders", json={"lines": []})
    assert answer.status_code == 422, "an order made by hand names its customer"
    answer = await client.post(
        "/api/v1/orders",
        json={
            "customer_contact_id": 7,
            "ordered_on": "2026-11-02",
            "shipping": "5",
            "discount": "2",
            "delivery_address": "  ",
            "lines": [
                {"item_id": chai.id, "quantity": "3"},
                {"item_id": large.id, "quantity": "1", "price": "5.5"},
            ],
        },
    )
    assert answer.status_code == 201, answer.text
    order = answer.json()
    assert (order["code"], order["status"], order["initiative_id"], order["total"]) == (
        "O-0001",
        "open",
        1,
        "20.500000",
    )
    assert order["delivery_address"] is None
    assert [(line["item"]["name"], line["price"]) for line in order["lines"]] == [
        ("Chai", "4.000000"),
        ("Chai", "5.500000"),
    ]
    assert await orders.committed(db, chai.id) == 3

    again = await client.post(
        "/api/v1/orders", json={"customer_contact_id": 7, "code": order["code"]}
    )
    assert (again.status_code, again.json()["detail"]) == (409, "ORDER_CODE_TAKEN")
    sold = await client.post(
        f"/api/v1/orders/{order['id']}/lines", json={"item_id": sugar.id, "quantity": "1"}
    )
    assert (sold.status_code, sold.json()["detail"]) == (422, "ORDER_ITEM_NOT_SOLD")

    line = order["lines"][0]["id"]
    changed = await client.patch(
        f"/api/v1/orders/{order['id']}/lines/{line}", json={"quantity": "4"}
    )
    assert changed.json()["lines"][0]["quantity"] == "4.000000"
    assert await orders.committed(db, chai.id) == 4
    edited = await client.patch(
        f"/api/v1/orders/{order['id']}", json={"due_on": "2026-11-09", "paid": True}
    )
    assert (edited.json()["due_on"], edited.json()["paid"]) == ("2026-11-09", True)

    listed = (await client.get("/api/v1/orders", params={"status": "open"})).json()
    assert [(one["code"], one["total"]) for one in listed["items"]] == [
        (order["code"], "24.500000")
    ]

    cancelled = await client.post(f"/api/v1/orders/{order['id']}/cancel")
    assert cancelled.json()["status"] == "cancelled"
    assert await orders.committed(db, chai.id) == 0
    refused = await client.delete(f"/api/v1/orders/{order['id']}/lines/{line}")
    assert (refused.status_code, refused.json()["detail"]) == (409, "ORDER_CANCELLED")
    assert (await client.get("/api/v1/orders", params={"status": "open"})).json()["total"] == 0
    reopened = await client.post(f"/api/v1/orders/{order['id']}/reopen")
    assert reopened.json()["status"] == "open"

    gone = await client.delete(f"/api/v1/orders/{order['id']}")
    assert gone.status_code == 204
    assert (await client.get(f"/api/v1/orders/{order['id']}")).status_code == 404


async def test_a_stores_order_changes_its_lines_only_in_the_store(
    client, sign_in, db: WriteSession, sell
):
    _, chai = await add_product(db, Product(name="Chai"))
    store = Store(organization_id=db.organization_id, channel=Channel.SHOPIFY)
    db.add(store)
    await db.flush()
    order = await sell(
        (chai.id, "1", "4"),
        store_id=store.id,
        external_id="gid://order/1",
        external_number="#1001",
        customer_contact_id=None,
    )
    await db.commit()
    await sign_in(client)

    read = (await client.get(f"/api/v1/orders/{order.id}")).json()
    assert (read["store_id"], read["external_number"], read["customer_contact_id"]) == (
        store.id,
        "#1001",
        None,
    )
    for refused in (
        await client.post(
            f"/api/v1/orders/{order.id}/lines", json={"item_id": chai.id, "quantity": "1"}
        ),
        await client.delete(f"/api/v1/orders/{order.id}"),
    ):
        assert (refused.status_code, refused.json()["detail"]) == (409, "ORDER_FROM_STORE")
