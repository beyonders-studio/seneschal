"""Orders through the API: a hand order for a customer, its lines and what
they commit, cancelling and reopening it, and a store's order kept as the
store has it, with its customer's other orders, managed by the store once it
is synced."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from hoard import orders, summary
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
    assert (await summary.stock_at(db, chai.id)).committed == 3

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
    assert (await summary.stock_at(db, chai.id)).committed == 4
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
    assert (await summary.stock_at(db, chai.id)).committed == 0
    refused = await client.delete(f"/api/v1/orders/{order['id']}/lines/{line}")
    assert (refused.status_code, refused.json()["detail"]) == (409, "ORDER_CANCELLED")
    assert (await client.get("/api/v1/orders", params={"status": "open"})).json()["total"] == 0
    reopened = await client.post(f"/api/v1/orders/{order['id']}/reopen")
    assert reopened.json()["status"] == "open"

    gone = await client.delete(f"/api/v1/orders/{order['id']}")
    assert gone.status_code == 204
    assert (await client.get(f"/api/v1/orders/{order['id']}")).status_code == 404


async def test_a_stores_order_is_read_with_its_customers_other_orders(
    client, sign_in, db: WriteSession, sell
):
    _, chai = await add_product(db, Product(name="Chai"))
    store = Store(organization_id=db.organization_id, channel=Channel.SHOPIFY)
    db.add(store)
    await db.flush()

    async def store_order(number: int, customer: str | None):
        return await sell(
            (chai.id, "1", "4"),
            store_id=store.id,
            external_id=f"gid://order/{number}",
            external_number=f"#{number}",
            store_customer=customer,
            customer_contact_id=None,
        )

    order = await store_order(1001, "99")
    again = await store_order(1002, "99")
    await store_order(1003, "12")
    alone = await store_order(1004, None)
    [line] = await orders.lines(db, order.id)
    line.current_quantity, line.refunded_quantity = Decimal(0), Decimal(1)
    await db.commit()
    await sign_in(client)

    read = (await client.get(f"/api/v1/orders/{order.id}")).json()
    assert (read["store_id"], read["external_number"], read["customer_contact_id"]) == (
        store.id,
        "#1001",
        None,
    )
    assert (read["store_customer"], read["customer_orders"]) == (
        "99",
        [{"id": again.id, "code": again.code}],
    )
    [sold] = read["lines"]
    assert (sold["quantity"], sold["current_quantity"], sold["refunded_quantity"]) == (
        "1.000000",
        "0.000000",
        "1.000000",
    )
    listed = (await client.get("/api/v1/orders", params={"store_customer": "99"})).json()
    assert (listed["total"], [one["id"] for one in listed["items"]]) == (2, [again.id, order.id])
    read = (await client.get(f"/api/v1/orders/{alone.id}")).json()
    assert (read["store_customer"], read["customer_orders"]) == (None, [])


async def test_a_synced_stores_order_changes_only_in_the_store(
    client, sign_in, db: WriteSession, sell
):
    _, chai = await add_product(db, Product(name="Chai"))
    store = Store(
        organization_id=db.organization_id,
        channel=Channel.SHOPIFY,
        shop_domain="tea.myshopify.com",
        connected=True,
        imported_at=datetime(2026, 11, 14, tzinfo=UTC),
    )
    db.add(store)
    await db.flush()
    order = await sell(
        (chai.id, "2", "4"),
        store_id=store.id,
        external_id="gid://shopify/Order/1001",
        external_number="#1001",
        customer_contact_id=None,
    )
    await db.commit()
    [line] = await orders.lines(db, order.id)
    await sign_in(client)
    path = f"/api/v1/orders/{order.id}"

    read = (await client.get(path)).json()
    assert (read["managed_by_store"], read["store_url"]) == (
        "shopify",
        "https://tea.myshopify.com/admin/orders/1001",
    )

    def changes():
        return (
            client.patch(path, json={"paid": True}),
            client.post(f"{path}/lines", json={"item_id": chai.id, "quantity": "1"}),
            client.patch(f"{path}/lines/{line.id}", json={"quantity": "3"}),
            client.post(f"{path}/cancel"),
            client.post(f"{path}/reopen"),
            client.delete(f"{path}/lines/{line.id}"),
            client.delete(path),
        )

    for change in changes():
        refused = await change
        assert (refused.status_code, refused.json()) == (409, {"detail": "MANAGED_BY_STORE"})
    assert (await summary.stock_at(db, chai.id)).committed == 2

    # Pending, as a store is before its sync and after its sync is removed,
    # its orders are Hoard's to change.
    store.imported_at = None
    db.add(store)
    await db.commit()
    read = (await client.get(path)).json()
    assert (read["managed_by_store"], read["store_url"]) == (None, None)
    for change in changes():
        answer = await change
        assert answer.status_code < 300, answer.text
    assert (await client.get(path)).status_code == 404
