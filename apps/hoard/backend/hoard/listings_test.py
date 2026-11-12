"""Matching a store's products to the items they sell, linking by hand, and
keeping the store's locations."""

from __future__ import annotations

import pytest
from sqlmodel import select

from hoard import listings
from hoard.channels import StoreProduct, StoreVariant
from hoard.records import (
    Channel,
    Listing,
    Location,
    MatchedBy,
    Product,
    ProductStatus,
    Store,
    StoreLocation,
    Variation,
)
from hoard.stock import add_product, add_variation
from seneschal_core.db import WriteSession


@pytest.fixture
async def store(db: WriteSession) -> Store:
    made = Store(organization_id=db.organization_id, channel=Channel.SHOPIFY, connected=True)
    db.add(made)
    await db.flush()
    return made


def _product(product_id: str, title: str, *variants: StoreVariant) -> StoreProduct:
    return StoreProduct(id=product_id, title=title, variants=variants)


def _variant(variant_id: str, title: str = "Default Title", **fields) -> StoreVariant:
    return StoreVariant(id=variant_id, title=title, **fields)


async def test_a_listing_is_matched_by_sku_first(db: WriteSession, store: Store):
    _, chai = await add_product(db, Product(name="Chai"))
    await add_product(db, Product(name="Masala"))
    chai.sku = " CHAI-1 "
    db.add(chai)

    [listing] = await listings.match(
        db, store, [_product("p1", "Masala", _variant("v1", sku="chai-1", inventory_item_id="i1"))]
    )

    assert (listing.item_id, listing.matched_by) == (chai.id, MatchedBy.SKU)
    assert (listing.product_id, listing.variant_id, listing.inventory_item_id) == ("p1", "v1", "i1")
    assert listing.title == "Masala"


async def test_a_sku_two_variations_share_matches_neither(db: WriteSession, store: Store):
    _, one = await add_product(db, Product(name="One"))
    _, two = await add_product(db, Product(name="Two"))
    one.sku = two.sku = "SAME"

    [listing] = await listings.match(
        db, store, [_product("p1", "Three", _variant("v1", sku="SAME"))]
    )

    assert (listing.item_id, listing.matched_by) == (None, None)


async def test_a_listing_is_matched_by_title_and_options(db: WriteSession, store: Store):
    product, _ = await add_product(db, Product(name="Chai"))
    large = await add_variation(db, Variation(product_id=product.id, attributes={"size": "Large"}))
    small = await add_variation(db, Variation(product_id=product.id, attributes={"size": "Small"}))

    kept = await listings.match(
        db,
        store,
        [
            _product(
                "p1",
                "chai",
                _variant("v1", "Large", options={"Size": "large"}),
                _variant("v2", "Small", options={"Size": "SMALL"}),
                _variant("v3", "Huge", options={"Size": "Huge"}),
            )
        ],
    )

    assert [(listing.item_id, listing.matched_by, listing.title) for listing in kept] == [
        (large.id, MatchedBy.TITLE, "chai - Large"),
        (small.id, MatchedBy.TITLE, "chai - Small"),
        (None, None, "chai - Huge"),
    ]


async def test_a_simple_product_matches_its_one_variation_by_title(db: WriteSession, store: Store):
    _, chai = await add_product(db, Product(name="Chai"))
    await add_product(db, Product(name="Old chai", status=ProductStatus.ARCHIVED))

    [listing] = await listings.match(db, store, [_product("p1", "Chai", _variant("v1"))])

    assert (listing.item_id, listing.matched_by) == (chai.id, MatchedBy.TITLE)


async def test_a_listing_is_matched_by_the_stores_inventory_item_last(
    db: WriteSession, store: Store
):
    _, chai = await add_product(db, Product(name="Chai"))
    db.add(
        Listing(
            organization_id=db.organization_id,
            store_id=store.id,
            product_id="p0",
            variant_id="v0",
            inventory_item_id="i1",
            item_id=chai.id,
            matched_by=MatchedBy.HAND,
        )
    )
    await db.flush()

    [listing] = await listings.match(
        db, store, [_product("p1", "Renamed", _variant("v1", inventory_item_id="i1"))]
    )

    assert (listing.item_id, listing.matched_by) == (chai.id, MatchedBy.EXTERNAL_ID)


async def test_matching_again_keeps_links_and_drops_gone_variants(db: WriteSession, store: Store):
    _, chai = await add_product(db, Product(name="Chai"))
    _, latte = await add_product(db, Product(name="Latte"))
    latte.sku = "LATTE"
    first = await listings.match(
        db,
        store,
        [_product("p1", "Tea", _variant("v1", "A", sku=None), _variant("v2", "B"))],
    )
    await listings.link(db, first[0], chai.id)

    kept = await listings.match(
        db, store, [_product("p1", "Tea", _variant("v1", "A", sku="LATTE"))]
    )

    assert [(listing.id, listing.item_id, listing.matched_by) for listing in kept] == [
        (first[0].id, chai.id, MatchedBy.HAND)
    ]
    held = (await db.exec(select(Listing.variant_id))).all()
    assert held == ["v1"]


async def test_several_listings_may_sell_one_item(db: WriteSession, store: Store):
    _, chai = await add_product(db, Product(name="Chai"))
    chai.sku = "CHAI"

    kept = await listings.match(
        db,
        store,
        [
            _product("p1", "Chai tin", _variant("v1", sku="CHAI")),
            _product("p2", "Chai bag", _variant("v2", sku="chai")),
        ],
    )

    assert [listing.item_id for listing in kept] == [chai.id, chai.id]


async def test_forgetting_a_product_removes_its_listings(db: WriteSession, store: Store):
    await listings.match(db, store, [_product("p1", "Tea", _variant("v1")), _product("p2", "Mug")])

    await listings.forget(db, store, "p1")

    assert (await db.exec(select(Listing.product_id))).all() == ["p2"]


async def test_linking_by_hand_takes_only_what_is_sold(db: WriteSession, store, base, material):
    [listing] = await listings.match(db, store, [_product("p1", "Salt")])
    salt = await material("Salt")
    syrup = await base("Syrup")

    await listings.link(db, listing, salt.id)
    assert (listing.item_id, listing.matched_by) == (salt.id, MatchedBy.HAND)

    with pytest.raises(LookupError):
        await listings.link(db, listing, syrup.id)

    await listings.link(db, listing, None)
    assert (listing.item_id, listing.matched_by) == (None, None)


async def test_a_change_is_noted_for_pushing(db: WriteSession, store: Store):
    _, chai = await add_product(db, Product(name="Chai"))

    await listings.match(db, store, [_product("p1", "Chai", _variant("v1"))])

    assert db.info["hoard.noted_items"] == {db.organization_id: {chai.id}}


async def test_the_stores_locations_are_kept(db: WriteSession, store: Store, main: Location):
    kept = await listings.keep_locations(db, store, {"l1": "Shop", "l2": "Warehouse"})
    assert [(found.external_id, found.name, found.location_id) for found in kept] == [
        ("l1", "Shop", main.id),
        ("l2", "Warehouse", main.id),
    ]
    db.add(Location(organization_id=db.organization_id, name="Back room"))
    kept[0].location_id = None
    await db.flush()

    again = await listings.keep_locations(db, store, {"l1": "Front shop", "l3": "Pop-up"})

    assert [(found.external_id, found.name, found.location_id) for found in again] == [
        ("l1", "Front shop", None),
        ("l3", "Pop-up", None),
    ]
    assert len((await db.exec(select(StoreLocation))).all()) == 2


def test_a_listing_is_titled_by_its_variant_where_it_has_several():
    one = _product("p1", "Tea", _variant("v1", "Default Title"))
    two = _product("p1", "Tea", _variant("v1", "Large"), _variant("v2", "Small"))

    assert listings.title_of(one, one.variants[0]) == "Tea"
    assert listings.title_of(two, two.variants[0]) == "Tea - Large"
    assert listings.title_of(two, None) == "Tea"
