"""A lot on its own page: what it is a lot of, and its open hold."""

from __future__ import annotations

from hoard.records import Product
from hoard.stock import add_product
from seneschal_core.db import WriteSession


async def test_a_lot_reads_with_its_item_and_its_open_hold(
    client, sign_in, db: WriteSession, material, lot, hold
):
    flour = await material("Flour")
    plain = await lot(flour.id, "F-1", "3")
    _, chai = await add_product(db, Product(name="Chai"))
    curing = await lot(chai.id, "C-1", "5")
    held = await hold(curing)
    await db.commit()
    await sign_in(client)

    bought = (await client.get(f"/api/v1/lots/{plain.id}")).json()
    made = (await client.get(f"/api/v1/lots/{curing.id}")).json()
    missing = await client.get("/api/v1/lots/999999")

    assert (bought["number"], bought["item"]["name"], bought["product_id"]) == (
        "F-1",
        "Flour",
        None,
    )
    assert (bought["on_hold"], bought["hold"], bought["created_by"]) == (False, None, "member-ref")
    assert (made["item"]["kind"], made["product_id"], made["on_hold"]) == (
        "variation",
        chai.product_id,
        True,
    )
    assert (made["hold"]["id"], made["hold"]["reason_id"]) == (held.id, held.reason_id)
    assert (missing.status_code, missing.json()) == (404, {"detail": "NOT_FOUND"})
