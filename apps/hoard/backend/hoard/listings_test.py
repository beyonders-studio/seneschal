"""Matching a store's products to the items they sell, keeping their copy,
taking them into Hoard, linking by hand, and keeping the store's locations."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlmodel import col, select

from hoard import listings
from hoard.channels import StoreImage, StoreProduct, StoreVariant
from hoard.records import (
    Channel,
    Listing,
    Location,
    MatchedBy,
    Product,
    ProductStatus,
    Store,
    StoreLocation,
    StoreProductCopy,
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


async def test_forgetting_a_product_removes_its_listings_and_copy_only(
    db: WriteSession, store: Store
):
    _, tea = await add_product(db, Product(name="Tea"))
    await listings.match(db, store, [_product("p1", "Tea", _variant("v1")), _product("p2", "Mug")])

    await listings.forget(db, store, "p1")

    assert (await db.exec(select(Listing.product_id))).all() == ["p2"]
    assert (await db.exec(select(StoreProductCopy.product_id))).all() == ["p2"]
    assert await db.get(Variation, tea.id) is not None


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


async def test_the_stores_locations_are_kept_unmapped(
    db: WriteSession, store: Store, main: Location
):
    kept = await listings.keep_locations(db, store, {"l1": "Shop", "l2": "Warehouse"})
    assert [(found.external_id, found.name, found.location_id) for found in kept] == [
        ("l1", "Shop", None),
        ("l2", "Warehouse", None),
    ]
    kept[0].location_id = main.id
    await db.flush()

    again = await listings.keep_locations(db, store, {"l1": "Front shop", "l3": "Pop-up"})

    assert [(found.external_id, found.name, found.location_id) for found in again] == [
        ("l1", "Front shop", main.id),
        ("l3", "Pop-up", None),
    ]
    assert len((await db.exec(select(StoreLocation))).all()) == 2


async def _copy(db: WriteSession) -> StoreProductCopy | None:
    return (
        await db.exec(select(StoreProductCopy).execution_options(populate_existing=True))
    ).first()


async def test_matching_keeps_the_products_copy_and_prices_and_the_newest_wins(
    db: WriteSession, store: Store
):
    first = datetime(2026, 11, 1, tzinfo=UTC)
    product = StoreProduct(
        id="p1",
        title="Tea",
        description="<p>Leaf</p>",
        status="active",
        options=["Size"],
        variants=[
            _variant("v1", "Small", sku="T-S", price=Decimal("4.50"), options={"Size": "Small"}),
            _variant("v2", "Large", sku="T-L", price=Decimal("8"), options={"Size": "Large"}),
        ],
        images=[StoreImage("https://i/1", "Tin", "m1")],
        published_on=["pub-1"],
        updated_at=first,
        product_type="Loose leaf",
        vendor="Hills",
        tags=["green", "organic"],
    )

    kept = await listings.match(db, store, [product])

    assert [(listing.variant_id, listing.price) for listing in kept] == [
        ("v1", Decimal("4.50")),
        ("v2", Decimal("8")),
    ]
    copy = await _copy(db)
    assert copy is not None
    assert (copy.title, copy.status, copy.product_type, copy.vendor, copy.tags) == (
        "Tea",
        "active",
        "Loose leaf",
        "Hills",
        ["green", "organic"],
    )
    assert copy.variants == [
        {"id": "v1", "title": "Small", "options": {"Size": "Small"}},
        {"id": "v2", "title": "Large", "options": {"Size": "Large"}},
    ]
    assert copy.images == [{"id": "m1", "url": "https://i/1", "alt": "Tin"}]
    assert (copy.published_on, copy.store_updated_at) == (["pub-1"], first)

    # A read older than the copy changes nothing; a newer one replaces it.
    older = replace(product, title="Old tea", variants=[], updated_at=first - timedelta(hours=1))
    await listings.match(db, store, [older])
    assert (copy := await _copy(db)) is not None and copy.title == "Tea"
    assert len((await db.exec(select(Listing))).all()) == 2
    later = replace(product, title="Green tea", updated_at=first + timedelta(hours=1))
    await listings.match(db, store, [later])
    assert (copy := await _copy(db)) is not None and copy.title == "Green tea"


async def test_adopting_overwrites_matched_products_and_makes_the_others(
    db: WriteSession, store: Store
):
    chai, small = await add_product(db, Product(name="Chai"))
    small.sku, small.attributes = "CHAI-S", {"size": "small"}
    sizes = [
        _variant("v1", "Small", sku="chai-s", options={"Size": "S"}),
        _variant("v2", "Large", sku="CHAI-L", options={"Size": "L"}),
    ]
    store_chai = StoreProduct(id="p1", title="Masala chai", options=["Size"], variants=sizes)
    mugs = StoreProduct(id="p2", title="Mug", status="archived", variants=[_variant("v3", sku="M")])

    await listings.adopt(db, store, [store_chai, mugs], every=True)

    await db.refresh(chai)
    assert chai.name == "Masala chai"
    found = await db.exec(
        select(Variation).where(Variation.product_id == chai.id).order_by(col(Variation.id))
    )
    assert [(variation.sku, variation.attributes) for variation in found.all()] == [
        ("chai-s", {"Size": "S"}),
        ("CHAI-L", {"Size": "L"}),
    ]
    [mug] = (await db.exec(select(Product).where(Product.name == "Mug"))).all()
    assert mug.status is ProductStatus.ARCHIVED
    [mug_variation] = (await db.exec(select(Variation).where(Variation.product_id == mug.id))).all()
    assert (mug_variation.sku, mug_variation.attributes, mug_variation.is_default) == (
        "M",
        {},
        True,
    )
    linked = await db.exec(select(Listing.variant_id, Listing.matched_by).order_by(col(Listing.id)))
    assert linked.all() == [
        ("v1", MatchedBy.SKU),
        ("v2", MatchedBy.EXTERNAL_ID),
        ("v3", MatchedBy.EXTERNAL_ID),
    ]


async def test_adopting_takes_only_products_not_listed_before_unless_asked(
    db: WriteSession, store: Store
):
    _, chai = await add_product(db, Product(name="Chai"))
    chai.sku = "C"
    await listings.match(db, store, [_product("p1", "Masala chai", _variant("v1", sku="C"))])

    await listings.adopt(
        db,
        store,
        [
            _product("p1", "Masala chai", _variant("v1", sku="C")),
            _product("p2", "Mug", _variant("v2")),
        ],
    )

    names = await db.exec(select(Product.name).order_by(col(Product.id)))
    assert names.all() == ["Chai", "Mug"]


def test_a_listing_is_titled_by_its_variant_where_it_has_several():
    one = _product("p1", "Tea", _variant("v1", "Default Title"))
    two = _product("p1", "Tea", _variant("v1", "Large"), _variant("v2", "Small"))

    assert listings.title_of(one, one.variants[0]) == "Tea"
    assert listings.title_of(two, two.variants[0]) == "Tea - Large"
    assert listings.title_of(two, None) == "Tea"
