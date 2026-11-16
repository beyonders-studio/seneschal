"""Products, their variations and images, and their stock; a product a
synced store lists, edited through the store, against a store kept in memory."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from hoard import channels, listings, plugin_manifest
from hoard.channels import ConnectError, StoreProduct, StoreVariant
from hoard.channels.fake import FakeConnector
from hoard.records import (
    MAX_IMAGE_BYTES,
    Adjustment,
    AdjustmentKind,
    Channel,
    Location,
    Product,
    Store,
    Variation,
)
from hoard.stock import add_product, add_variation, adjust
from seneschal_core.db import WriteSession
from seneschal_core.testing import InitiativeStandIn

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
#: When the store last changed its product, before a test changes it.
OPENED = datetime(2026, 11, 14, 8, tzinfo=UTC)


@pytest.fixture
def shop(
    monkeypatch: pytest.MonkeyPatch, initiative_stand_in: InitiativeStandIn, community_ref: str
) -> FakeConnector:
    """The store the community's Shopify connection reaches, selling chai in
    two sizes."""
    fake = FakeConnector()
    monkeypatch.setitem(channels.CONNECTORS, Channel.SHOPIFY, lambda values: fake)
    initiative_stand_in.connections[community_ref] = {"shopify": {"shop": fake.shop_domain}}
    fake.shop_products["p1"] = StoreProduct(
        id="p1",
        title="Chai",
        status="active",
        options=["Size"],
        variants=[
            StoreVariant(id="v1", title="Large", sku="CHAI-L", options={"Size": "Large"}),
            StoreVariant(id="v2", title="Small", sku="CHAI-S", options={"Size": "Small"}),
        ],
        updated_at=OPENED,
    )
    return fake


@pytest.fixture
async def listed(db: WriteSession, shop: FakeConnector) -> tuple[Store, Product, list[Variation]]:
    """A synced store listing both sizes of a Hoard product, by SKU."""
    store = Store(
        organization_id=db.organization_id,
        channel=Channel.SHOPIFY,
        shop_domain=shop.shop_domain,
        connected=True,
        imported_at=OPENED,
    )
    db.add(store)
    product, large = await add_product(db, Product(name="Chai"))
    large.attributes, large.sku = {"Size": "Large"}, "CHAI-L"
    db.add(large)
    small = await add_variation(
        db, Variation(product_id=product.id, attributes={"Size": "Small"}, sku="CHAI-S")
    )
    await db.flush()
    await listings.match(db, store, [shop.shop_products["p1"]])
    await db.commit()
    return store, product, [large, small]


async def _product(db: WriteSession, main: Location, on_hand: str) -> Product:
    product, default = await add_product(
        db, Product(name="Chai", default_price=Decimal(12), low_stock_limit=Decimal(2))
    )
    await adjust(
        db,
        Adjustment(
            item_id=default.id,
            location_id=main.id,
            kind=AdjustmentKind.STARTING,
            dated_on=date(2026, 11, 1),
            change=Decimal(on_hand),
            unit_cost=Decimal(3),
        ),
    )
    await db.commit()
    return product


async def test_a_product_page_holds_its_variations_stock_and_prices_only_for_costs(
    client, sign_in, db: WriteSession, main: Location
):
    product = await _product(db, main, "4")
    await sign_in(client)

    added = await client.post(
        f"/api/v1/products/{product.id}/variations",
        json={"attributes": {"size": "large"}, "price": "15"},
    )
    page = added.json()
    assert (added.status_code, page["on_hand"], page["low_stock_limit"]) == (
        201,
        "4.000000",
        "2.000000",
    )
    assert [(v["attributes"], v["is_default"], v["on_hand"]) for v in page["variations"]] == [
        ({}, True, "4.000000"),
        ({"size": "large"}, False, "0"),
    ]
    assert page["variations"][0]["stock"] == [
        {"location_id": main.id, "quantity": "4.000000", "bin": None}
    ]
    hidden = [page["default_price"]] + [
        v[field] for v in page["variations"] for field in ("price", "average_cost")
    ]
    assert hidden == [None] * 5

    await sign_in(client, (plugin_manifest.SEE_COSTS,))
    page = (await client.get(f"/api/v1/products/{product.id}")).json()
    assert page["default_price"] == "12.000000"
    assert [(v["price"], v["average_cost"]) for v in page["variations"]] == [
        (None, "3.000000"),
        (None, "0.000000"),
    ]
    listed = (await client.get("/api/v1/products")).json()
    assert [(p["name"], p["on_hand"]) for p in listed["items"]] == [("Chai", "4.000000")]


async def test_a_product_keeps_its_last_variation(client, sign_in, db: WriteSession, main):
    product = await _product(db, main, "1")
    await sign_in(client)
    page = (await client.get(f"/api/v1/products/{product.id}")).json()

    last = await client.delete(
        f"/api/v1/products/{product.id}/variations/{page['variations'][0]['id']}"
    )
    assert (last.status_code, last.json()) == (409, {"detail": "LAST_VARIATION"})
    assert (await client.delete(f"/api/v1/products/{product.id}")).status_code == 204
    assert (await client.get(f"/api/v1/products/{product.id}")).status_code == 404


async def test_auto_manufacture_is_changed_only_when_sent(client, sign_in, db: WriteSession, main):
    product = await _product(db, main, "1")
    await sign_in(client)
    path = f"/api/v1/products/{product.id}"
    variation = (await client.get(path)).json()["variations"][0]["id"]

    always = await client.put(path, json={"name": "Chai", "auto_manufacture": "always"})
    renamed = await client.put(path, json={"name": "Masala chai"})
    made = await client.put(
        f"{path}/variations/{variation}", json={"auto_manufacture": "out_of_stock"}
    )

    assert always.json()["auto_manufacture"] == renamed.json()["auto_manufacture"] == "always"
    assert made.json()["variations"][0]["auto_manufacture"] == "out_of_stock"


async def test_a_product_keeps_its_cure_time_and_its_traceability_flags(
    client, sign_in, db: WriteSession, main
):
    product = await _product(db, main, "1")
    await sign_in(client)
    path = f"/api/v1/products/{product.id}"

    kept = await client.put(
        path,
        json={"name": "Soap", "cure_days": 28, "traceability_list": True, "rinse_off": True},
    )
    refused = await client.put(path, json={"name": "Soap", "cure_days": 0})

    page = kept.json()
    assert (page["cure_days"], page["traceability_list"], page["rinse_off"]) == (28, True, True)
    assert refused.status_code == 422


async def test_product_images_are_kept_by_type_and_size(
    client, sign_in, db: WriteSession, main: Location
):
    product = await _product(db, main, "1")
    images = f"/api/v1/products/{product.id}/images"
    await sign_in(client)

    for content_type, content in (
        ("image/svg+xml", b"<svg/>"),
        ("image/png", b"GIF89a"),
        ("text/html", PNG),
    ):
        refused = await client.post(images, content=content, headers={"content-type": content_type})
        assert (refused.status_code, refused.json()) == (415, {"detail": "IMAGE_TYPE"})
    large = PNG + b"\x00" * MAX_IMAGE_BYTES
    refused = await client.post(images, content=large, headers={"content-type": "image/png"})
    assert (refused.status_code, refused.json()) == (413, {"detail": "IMAGE_TOO_LARGE"})

    added = await client.post(images, content=PNG, headers={"content-type": "image/png"})
    assert added.status_code == 201
    image = added.json()
    assert (image["position"], image["size"], image["created_by"]) == (0, len(PNG), "member-ref")
    assert [listed["id"] for listed in (await client.get(images)).json()] == [image["id"]]
    shown = await client.get(f"{images}/{image['id']}")
    assert (shown.content, shown.headers["content-type"]) == (PNG, "image/png")

    assert (await client.delete(f"{images}/{image['id']}")).status_code == 204
    assert (await client.get(images)).json() == []


def _versions(page: dict) -> list[dict]:
    return [
        {key: held[key] for key in ("store_id", "product_id", "updated_at")}
        for held in page["store_products"]
    ]


async def test_a_listed_product_is_edited_through_its_store(
    client, sign_in, db: WriteSession, shop: FakeConnector, listed
):
    store, product, [large, _] = listed
    await sign_in(client)
    path = f"/api/v1/products/{product.id}"
    page = (await client.get(path)).json()
    assert page["store_products"] == [
        {
            "store_id": store.id,
            "product_id": "p1",
            "updated_at": "2026-11-14T08:00:00Z",
            "channel": "shopify",
        }
    ]

    renamed = await client.put(
        path,
        json={"name": "Masala chai", "status": "archived", "store_versions": _versions(page)},
    )

    assert renamed.status_code == 200, renamed.text
    held = shop.shop_products["p1"]
    assert (held.title, held.status) == ("Masala chai", "archived")
    page = renamed.json()
    assert (page["name"], page["status"]) == ("Masala chai", "archived")
    # The store's answer is kept as Hoard's copy.
    assert page["store_products"][0]["updated_at"] == held.updated_at.isoformat().replace(
        "+00:00", "Z"
    )
    copy = (await client.get(f"/api/v1/stores/{store.id}/products/p1")).json()
    assert (copy["title"], copy["status"]) == ("Masala chai", "archived")

    # Making it active again makes it active in the store.
    active = await client.put(
        path, json={"name": "Masala chai", "status": "active", "store_versions": _versions(page)}
    )
    assert active.status_code == 200, active.text
    assert shop.shop_products["p1"].status == "active"

    variation = await client.put(
        f"{path}/variations/{large.id}",
        json={
            "attributes": {"Size": "Big"},
            "sku": "CHAI-B",
            "store_versions": _versions(active.json()),
        },
    )
    assert variation.status_code == 200, variation.text
    sent = shop.shop_products["p1"].variants
    assert [(v.sku, dict(v.options)) for v in sent] == [
        ("CHAI-B", {"Size": "Big"}),
        ("CHAI-S", {"Size": "Small"}),
    ]
    sold = [(v["sku"], v["attributes"]) for v in variation.json()["variations"]]
    assert sold[0] == ("CHAI-B", {"Size": "Big"})


async def test_an_edit_over_a_change_made_in_the_store_is_refused(
    client, sign_in, db: WriteSession, shop: FakeConnector, listed
):
    store, product, [large, _] = listed
    await sign_in(client)
    path = f"/api/v1/products/{product.id}"
    opened = _versions((await client.get(path)).json())
    changed = replace(
        shop.shop_products["p1"],
        title="Chai tea",
        updated_at=datetime(2026, 11, 15, 9, tzinfo=UTC),
    )
    shop.shop_products["p1"] = changed

    for refused in (
        await client.put(path, json={"name": "Masala chai", "store_versions": opened}),
        await client.put(path, json={"name": "Masala chai"}),
        await client.put(
            f"{path}/variations/{large.id}",
            json={"attributes": {"Size": "Large"}, "sku": "NEW", "store_versions": opened},
        ),
    ):
        assert (refused.status_code, refused.json()) == (409, {"detail": "STORE_PRODUCT_CHANGED"})

    # Nothing was sent, nothing of Hoard's changed, and the copy holds the
    # store's version for the page to show.
    assert shop.shop_products["p1"] == changed
    page = (await client.get(path)).json()
    assert (page["name"], page["variations"][0]["sku"]) == ("Chai", "CHAI-L")
    assert page["store_products"][0]["updated_at"] == "2026-11-15T09:00:00Z"
    copy = (await client.get(f"/api/v1/stores/{store.id}/products/p1")).json()
    assert copy["title"] == "Chai tea"

    # From that version, it goes through.
    saved = await client.put(path, json={"name": "Masala chai", "store_versions": _versions(page)})
    assert saved.status_code == 200, saved.text
    assert shop.shop_products["p1"].title == "Masala chai"


async def test_hoards_own_fields_change_without_the_store(
    client, sign_in, db: WriteSession, shop: FakeConnector, listed
):
    _, product, _ = listed
    shop.refuse = ConnectError.STORE_UNREACHABLE
    await sign_in(client)

    kept = await client.put(f"/api/v1/products/{product.id}", json={"name": "Chai", "cure_days": 3})

    assert (kept.status_code, kept.json()["cure_days"]) == (200, 3)


async def test_a_listed_product_is_archived_rather_than_deleted(
    client, sign_in, db: WriteSession, shop: FakeConnector, listed
):
    store, product, [large, small] = listed
    await sign_in(client)
    path = f"/api/v1/products/{product.id}"

    for refused in (
        await client.delete(path),
        await client.delete(f"{path}/variations/{small.id}"),
    ):
        assert (refused.status_code, refused.json()) == (409, {"detail": "LISTED_IN_STORE"})

    # A pending store's listings are Hoard's alone: it neither reads nor
    # writes the store.
    store.imported_at = None
    db.add(store)
    await db.commit()
    shop.refuse = ConnectError.STORE_UNREACHABLE
    page = (await client.get(path)).json()
    assert page["store_products"] == []
    renamed = await client.put(path, json={"name": "Masala chai"})
    assert renamed.status_code == 200, renamed.text
    assert (await client.delete(f"{path}/variations/{small.id}")).status_code == 204
    assert (await client.delete(path)).status_code == 204
