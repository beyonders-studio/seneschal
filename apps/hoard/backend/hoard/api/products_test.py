"""Products, their variations and images, and their stock; a product a
synced store lists, edited through the store, against a store kept in memory."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlmodel import select

from hoard import channels, listings, plugin_manifest
from hoard.channels import ConnectError, StoreProduct, StoreVariant
from hoard.channels.fake import FakeConnector
from hoard.records import (
    MAX_IMAGE_BYTES,
    Adjustment,
    AdjustmentKind,
    Channel,
    ItemStatus,
    Location,
    PricePoint,
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
        {
            "location_id": main.id,
            "quantity": "4.000000",
            "bin": None,
            "could_make": None,
            "last_stocktake": None,
        }
    ]
    hidden = [page["default_price"], page["totals"]["profit"]] + [
        v[field]
        for v in page["variations"]
        for field in ("price", "average_cost", "profit", "pricing")
    ]
    assert hidden == [None] * 10

    await sign_in(client, (plugin_manifest.SEE_COSTS,))
    page = (await client.get(f"/api/v1/products/{product.id}")).json()
    assert page["default_price"] == "12.000000"
    assert [(v["price"], v["average_cost"]) for v in page["variations"]] == [
        (None, "3.000000"),
        (None, "0.000000"),
    ]
    assert page["variations"][0]["pricing"] == {
        "cost": {
            "goods": "3.000000",
            "last_known": False,
            "labour": None,
            "overhead": None,
            "total": "3.000000",
        },
        "points": [],
    }
    listed = (await client.get("/api/v1/products")).json()
    assert [(p["name"], p["on_hand"]) for p in listed["items"]] == [("Chai", "4.000000")]


async def test_a_product_adds_up_its_variations_and_is_priced_at_price_points(
    client, sign_in, db: WriteSession, main: Location, lot, sell, buy
):
    product = await _product(db, main, "4")
    [chai] = (await db.exec(select(Variation).where(Variation.product_id == product.id))).all()
    large = await add_variation(
        db, Variation(product_id=product.id, attributes={"size": "large"}, sku="CHAI-L")
    )
    await lot(large.id, "L1", "6", unit_cost="5")
    sold = await sell((chai.id, "1", "12"), (large.id, "2", "20"))
    bought = await buy((large, "3", "5"))
    await sell((large.id, "1", "20"))
    wholesale = PricePoint(organization_id=db.organization_id, name="Wholesale", markup=Decimal(20))
    db.add(wholesale)
    await db.commit()

    await sign_in(client)
    page = (await client.get(f"/api/v1/products/{product.id}")).json()
    assert page["totals"] == {
        "committed": "4.000000",
        "available": "9.000000",
        "potential": None,
        "made": "0",
        "sold": "4.000000",
        "shipped": "0",
        "on_order": "0",
        "overdue": False,
        "profit": None,
    }
    path = f"/api/v1/products/{product.id}/variations/{large.id}/prices/{wholesale.id}"
    refused = await client.put(path, json={"price": "9"})
    assert (refused.status_code, refused.json()) == (403, {"detail": "COSTS_HIDDEN"})
    refused = await client.put("/api/v1/contacts/5/price-point", json={"price_point_id": 1})
    assert (refused.status_code, refused.json()) == (403, {"detail": "COSTS_HIDDEN"})

    # Its orders and purchases, by item.
    orders = (await client.get("/api/v1/orders", params={"item_id": chai.id})).json()
    assert [order["id"] for order in orders["items"]] == [sold.id]
    purchases = (await client.get("/api/v1/purchases", params={"item_id": large.id})).json()
    assert [purchase["id"] for purchase in purchases["items"]] == [bought.id]
    assert (await client.get("/api/v1/purchases", params={"item_id": chai.id})).json()["total"] == 0

    await sign_in(client, (plugin_manifest.SEE_COSTS,))
    # Typed at a price point, and back to the suggested price: 5 marked up 20%.
    typed = (await client.put(path, json={"price": "9"})).json()
    [_, priced] = typed["variations"]
    assert [(at["typed"], at["price"]) for at in priced["pricing"]["points"]] == [
        ("9.000000", "9.000000")
    ]
    cleared = (await client.put(path, json={"price": None})).json()
    [_, priced] = cleared["variations"]
    assert [(at["typed"], at["suggested"]) for at in priced["pricing"]["points"]] == [
        (None, "6.00")
    ]
    assert cleared["totals"]["profit"] == {
        "revenue": "0.000000",
        "cost": "0.000000",
        "profit": "0.000000",
        "share": None,
        "uncosted": 0,
        "left_out": 0,
    }


async def test_archived_products_are_listed_only_when_asked(
    client, sign_in, db: WriteSession, product
):
    await product("Chai")
    await product("Old chai", status=ItemStatus.ARCHIVED)
    await db.commit()
    await sign_in(client)

    def names(listed) -> list[str]:
        return [product["name"] for product in listed.json()["items"]]

    assert names(await client.get("/api/v1/products")) == ["Chai"]
    assert names(await client.get("/api/v1/products", params={"archived": True})) == [
        "Chai",
        "Old chai",
    ]


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
    await sign_in(client, admin=True)
    bought = await client.post(
        "/api/v1/settings/types", json={"kind": "product", "name": "Bought", "shape": "bought"}
    )
    # Hoard's type is its own, and never reaches the store.
    typed = await client.put(
        f"/api/v1/products/{product.id}", json={"name": "Chai", "type_id": bought.json()["id"]}
    )

    assert (kept.status_code, kept.json()["cure_days"]) == (200, 3)
    assert (typed.status_code, typed.json()["type_id"]) == (200, bought.json()["id"])


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


async def test_a_new_product_takes_its_categorys_default_type_and_keeps_its_type(
    client, sign_in, db: WriteSession
):
    await sign_in(client, admin=True)
    types = "/api/v1/settings/types"
    made = next(
        each["id"]
        for each in (await client.get(types, params={"kind": "product"})).json()
        if each["is_default"]
    )
    bought = (
        await client.post(types, json={"kind": "product", "name": "Bought", "shape": "bought"})
    ).json()["id"]
    tins = (await client.post(types, json={"kind": "material", "name": "Tins"})).json()["id"]
    drinkware = (
        await client.post(
            "/api/v1/settings/categories",
            json={"kind": "product", "name": "Drinkware", "default_type_id": bought},
        )
    ).json()["id"]

    async def add(name: str, **fields) -> dict:
        return (await client.post("/api/v1/products", json={"name": name, **fields})).json()

    mug = await add("Mug", category_id=drinkware)
    tumbler = await add("Tumbler", category_id=drinkware, type_id=made)
    chai = await add("Chai")
    assert [each["type_id"] for each in (mug, tumbler, chai)] == [bought, made, made]
    refused = await client.post("/api/v1/products", json={"name": "Tin", "type_id": tins})
    assert (refused.status_code, refused.json()) == (422, {"detail": "TYPE_UNKNOWN"})

    # A category chosen later, or a category's new default, changes no record's type.
    moved = await client.put(
        f"/api/v1/products/{chai['id']}", json={"name": "Chai", "category_id": drinkware}
    )
    assert moved.json()["type_id"] == made
    await client.patch(f"/api/v1/settings/categories/{drinkware}", json={"default_type_id": made})
    assert (await client.get(f"/api/v1/products/{mug['id']}")).json()["type_id"] == bought

    # A type left out of an edit is kept; a type changed changes only the page.
    await client.put(
        f"/api/v1/declarations/product/{mug['id']}",
        json={"ingredient_name": None, "allergens": ["milk"], "fragrance_allergens": None},
    )
    kept = await client.put(f"/api/v1/products/{mug['id']}", json={"name": "Mug"})
    assert kept.json()["type_id"] == bought
    await client.put(f"/api/v1/products/{mug['id']}", json={"name": "Mug", "type_id": made})
    declared = (await client.get(f"/api/v1/declarations/product/{mug['id']}")).json()
    assert (declared["recorded_allergens"], declared["allergens"]) == (["milk"], ["milk"])

    listed = (await client.get("/api/v1/products", params={"type_id": made})).json()
    assert [each["name"] for each in listed["items"]] == ["Chai", "Mug", "Tumbler"]
    assert (await client.get("/api/v1/products", params={"type_id": bought})).json()["total"] == 0
