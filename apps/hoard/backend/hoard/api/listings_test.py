"""The stores' listings, and linking one by hand."""

from __future__ import annotations

import pytest

from hoard.records import Channel, Listing, MatchedBy, Product, Store, Variation
from hoard.stock import add_product, add_variation
from seneschal_core.db import WriteSession


@pytest.fixture
async def store(db: WriteSession) -> Store:
    made = Store(organization_id=db.organization_id, channel=Channel.SHOPIFY, connected=True)
    db.add(made)
    await db.flush()
    return made


async def test_listings_are_listed_with_the_items_they_sell(
    client, sign_in, db: WriteSession, store: Store, material
):
    product, _ = await add_product(db, Product(name="Chai"))
    large = await add_variation(db, Variation(product_id=product.id, attributes={"size": "Large"}))
    salt = await material("Salt")
    for product_id, title, sku, item_id in (
        ("p1", "Chai tin", "CHAI", large.id),
        ("p2", "Salt jar", None, salt.id),
        ("p3", "Mug", "MUG", None),
    ):
        db.add(
            Listing(
                organization_id=db.organization_id,
                store_id=store.id,
                product_id=product_id,
                title=title,
                sku=sku,
                item_id=item_id,
                matched_by=None if item_id is None else MatchedBy.SKU,
            )
        )
    await db.commit()
    await sign_in(client)

    every = (await client.get("/api/v1/listings")).json()

    assert every["total"] == 3
    assert [
        (row["title"], row["item_name"], row["item_kind"], row["item_product_id"])
        for row in every["items"]
    ] == [
        ("Chai tin", "Chai Large", "variation", product.id),
        ("Mug", None, None, None),
        ("Salt jar", "Salt", "material", None),
    ]
    assert every["items"][0]["channel"] == "shopify"

    def titles(answer) -> list[str]:
        return [row["title"] for row in answer.json()["items"]]

    assert titles(await client.get("/api/v1/listings?linked=false")) == ["Mug"]
    assert titles(await client.get("/api/v1/listings?q=mug")) == ["Mug"]
    assert titles(await client.get(f"/api/v1/listings?product_id={product.id}")) == ["Chai tin"]
    assert titles(await client.get(f"/api/v1/listings?store_id={store.id + 1}")) == []


async def test_a_listing_is_linked_by_hand(client, sign_in, db: WriteSession, store: Store, base):
    _, chai = await add_product(db, Product(name="Chai"))
    syrup = await base("Syrup")
    listing = Listing(organization_id=db.organization_id, store_id=store.id, product_id="p1")
    db.add(listing)
    await db.commit()
    await sign_in(client)

    linked = await client.put(f"/api/v1/listings/{listing.id}", json={"item_id": chai.id})
    assert (linked.json()["item_id"], linked.json()["matched_by"]) == (chai.id, "hand")

    refused = await client.put(f"/api/v1/listings/{listing.id}", json={"item_id": syrup.id})
    assert (refused.status_code, refused.json()) == (422, {"detail": "ITEM_NOT_SOLD"})

    unlinked = await client.put(f"/api/v1/listings/{listing.id}", json={"item_id": None})
    assert (unlinked.json()["item_id"], unlinked.json()["matched_by"]) == (None, None)

    missing = await client.put("/api/v1/listings/999999", json={"item_id": None})
    assert (missing.status_code, missing.json()) == (404, {"detail": "NOT_FOUND"})
