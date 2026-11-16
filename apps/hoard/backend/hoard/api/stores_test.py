"""The community's stores, their sync, locations, push log and products,
against a store kept in memory."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlmodel import select

from hoard import channels, listings, plugin_manifest
from hoard.app import JOB_HANDLERS
from hoard.channels import ConnectError, StoreImage, StoreProduct, StoreVariant
from hoard.channels.fake import FakeConnector
from hoard.channels.hooks import webhook_address
from hoard.records import (
    Channel,
    Listing,
    Location,
    MatchedBy,
    Order,
    Product,
    PushStatus,
    StockPush,
    Store,
    StoreLocation,
    Variation,
    WholesaleBuyer,
)
from hoard.stock import add_product, add_variation
from seneschal_core import jobs
from seneschal_core.db import WriteSession
from seneschal_core.testing import InitiativeStandIn

COSTS = (plugin_manifest.SEE_COSTS,)
ADDRESS = webhook_address(InitiativeStandIn.API_URL)


@pytest.fixture
def shop(
    monkeypatch: pytest.MonkeyPatch, initiative_stand_in: InitiativeStandIn, community_ref: str
) -> FakeConnector:
    """The store the community's Shopify connection reaches."""
    fake = FakeConnector()
    monkeypatch.setitem(channels.CONNECTORS, Channel.SHOPIFY, lambda values: fake)
    initiative_stand_in.connections[community_ref] = {"shopify": {"shop": fake.shop_domain}}
    fake.shop_products["p1"] = StoreProduct(
        id="p1",
        title="Chai",
        description="<p>Spiced</p>",
        status="active",
        options=["Size"],
        variants=[
            StoreVariant(
                id="v1",
                title="Large",
                sku="CHAI-L",
                price=Decimal("12.50"),
                options={"Size": "Large"},
                inventory_item_id="i1",
            ),
            StoreVariant(
                id="v2",
                title="Small",
                sku="CHAI-S",
                price=Decimal("8"),
                options={"Size": "Small"},
                inventory_item_id="i2",
            ),
        ],
        images=[StoreImage(url="https://cdn.test/chai.png", alt="A tin", id="img-1")],
        published_on=["online-store"],
    )
    return fake


@pytest.fixture
async def store(db: WriteSession) -> Store:
    """A synced store."""
    made = Store(
        organization_id=db.organization_id,
        channel=Channel.SHOPIFY,
        shop_domain="tea.myshopify.com",
        connected=True,
        imported_at=datetime(2026, 11, 14, tzinfo=UTC),
    )
    db.add(made)
    await db.commit()
    return made


@pytest.fixture
async def pending(db: WriteSession, store: Store) -> Store:
    """The store, connected and not synced."""
    store.imported_at = None
    db.add(store)
    await db.commit()
    return store


@pytest.fixture
async def chai(db: WriteSession) -> tuple[Product, Variation, Variation]:
    product, large = await add_product(db, Product(name="Chai", default_price=Decimal("10")))
    large.attributes, large.sku = {"size": "Large"}, "CHAI-L"
    db.add(large)
    small = await add_variation(
        db, Variation(product_id=product.id, attributes={"size": "Small"}, price=Decimal("7"))
    )
    await db.commit()
    return product, large, small


async def test_the_stores_are_listed_with_how_their_listings_and_pushes_stand(
    client, sign_in, db: WriteSession, store: Store, main: Location
):
    db.add(
        StoreLocation(
            organization_id=db.organization_id,
            store_id=store.id,
            external_id="loc-1",
            name="Shop",
            location_id=main.id,
        )
    )
    _, chai = await add_product(db, Product(name="Chai"))
    listing = Listing(
        organization_id=db.organization_id,
        store_id=store.id,
        product_id="p1",
        item_id=chai.id,
        matched_by=MatchedBy.HAND,
    )
    db.add_all(
        [listing, Listing(organization_id=db.organization_id, store_id=store.id, product_id="p2")]
    )
    await db.flush()
    db.add(
        StockPush(
            organization_id=db.organization_id,
            listing_id=listing.id,
            quantity=1,
            idempotency_key="k",
            status=PushStatus.FAILED,
        )
    )
    await db.commit()
    await sign_in(client)

    [read] = (await client.get("/api/v1/stores")).json()

    assert {
        key: read[key]
        for key in (
            "channel",
            "shop_domain",
            "connected",
            "state",
            "sync_job_id",
            "listings",
            "linked",
            "pushes_failed",
        )
    } == {
        "channel": "shopify",
        "shop_domain": "tea.myshopify.com",
        "connected": True,
        "state": "synced",
        "sync_job_id": None,
        "listings": 2,
        "linked": 1,
        "pushes_failed": 1,
    }
    assert [(found["name"], found["location_id"]) for found in read["locations"]] == [
        ("Shop", main.id)
    ]


async def test_an_admin_reads_the_store_again_and_maps_its_locations(
    client, sign_in, db: WriteSession, store: Store, shop: FakeConnector, chai, main: Location
):
    _, large, _ = chai
    await db.commit()
    shop.shop_locations = {"loc-1": "Shop", "loc-2": "Warehouse"}
    await sign_in(client)
    refused = await client.post(f"/api/v1/stores/{store.id}/refresh")
    assert (refused.status_code, refused.json()) == (403, {"detail": "ADMIN_ONLY"})

    await sign_in(client, admin=True)
    read = (await client.post(f"/api/v1/stores/{store.id}/refresh")).json()

    assert (read["listings"], read["linked"]) == (2, 2)
    assert [(found["external_id"], found["location_id"]) for found in read["locations"]] == [
        ("loc-1", None),
        ("loc-2", None),
    ]
    warehouse = read["locations"][1]
    mapped = await client.put(
        f"/api/v1/stores/{store.id}/locations/{warehouse['id']}", json={"location_id": main.id}
    )
    assert mapped.json()["location_id"] == main.id
    unknown = await client.put(
        f"/api/v1/stores/{store.id}/locations/{warehouse['id']}", json={"location_id": 999999}
    )
    assert (unknown.status_code, unknown.json()) == (422, {"detail": "LOCATION_UNKNOWN"})
    sold = (await db.exec(select(Listing.item_id).where(Listing.variant_id == "v1"))).one()
    assert sold == large.id


async def test_an_admin_chooses_the_wholesale_segment_and_the_orders_are_marked_again(
    app, client, sign_in, db: WriteSession, store: Store, shop: FakeConnector, sell
):
    shop.shop_segments = {"seg-b": "wholesale", "seg-a": "VIP"}
    shop.members = {"seg-b": {"7"}}
    for external_id, customer, wholesale in (
        ("o-1", "8", WholesaleBuyer.COMPANY),
        ("o-2", "7", None),
        ("o-3", "9", WholesaleBuyer.SEGMENT),
        ("o-4", None, None),
    ):
        await sell(
            store_id=store.id,
            external_id=external_id,
            customer_contact_id=None,
            store_customer=customer,
            wholesale=wholesale,
        )
    await db.commit()

    async def marked() -> dict[str | None, WholesaleBuyer | None]:
        await jobs.work(app.state.plugin, db.organization_id, JOB_HANDLERS)
        found = await db.exec(
            select(Order.external_id, Order.wholesale).execution_options(populate_existing=True)
        )
        return dict(found.all())

    await sign_in(client)
    refused = await client.get(f"/api/v1/stores/{store.id}/segments")
    assert (refused.status_code, refused.json()) == (403, {"detail": "ADMIN_ONLY"})
    await sign_in(client, admin=True)
    assert (await client.get(f"/api/v1/stores/{store.id}/segments")).json() == [
        {"id": "seg-a", "name": "VIP"},
        {"id": "seg-b", "name": "wholesale"},
    ]
    path = f"/api/v1/stores/{store.id}/wholesale"
    unknown = await client.put(path, json={"segment_id": "seg-x"})
    assert (unknown.status_code, unknown.json()) == (422, {"detail": "SEGMENT_UNKNOWN"})

    chosen = await client.put(path, json={"segment_id": "seg-b"})

    assert chosen.json()["wholesale_segment"] == "seg-b"
    # A company's order stays wholesale; every other is as the segment stands now.
    assert await marked() == {
        "o-1": WholesaleBuyer.COMPANY,
        "o-2": WholesaleBuyer.SEGMENT,
        "o-3": None,
        "o-4": None,
    }
    assert shop.member_reads == ["seg-b"]

    await client.put(path, json={"segment_id": None})

    assert await marked() == {"o-1": WholesaleBuyer.COMPANY, "o-2": None, "o-3": None, "o-4": None}
    assert shop.member_reads == ["seg-b"]


async def test_a_consignees_location_is_never_mapped_to_a_stores(
    client, sign_in, db: WriteSession, store: Store, pending: Store
):
    corner = Location(organization_id=db.organization_id, name="Corner", contact_id=7)
    consignee = Location(
        organization_id=db.organization_id, name="Gift shop", contact_id=8, consignee=True
    )
    store_location = StoreLocation(
        organization_id=db.organization_id, store_id=store.id, external_id="loc-1"
    )
    db.add_all([corner, consignee, store_location])
    await db.commit()
    await sign_in(client, admin=True)

    refused = await client.put(
        f"/api/v1/stores/{store.id}/locations/{store_location.id}",
        json={"location_id": consignee.id},
    )
    assert (refused.status_code, refused.json()) == (422, {"detail": "LOCATION_CONSIGNEE"})
    chosen = await client.post(
        f"/api/v1/stores/{pending.id}/sync",
        json={"locations": [{"external_id": "loc-1", "location_id": consignee.id}]},
    )
    assert (chosen.status_code, chosen.json()) == (422, {"detail": "LOCATION_CONSIGNEE"})

    mapped = await client.put(
        f"/api/v1/stores/{store.id}/locations/{store_location.id}",
        json={"location_id": corner.id},
    )
    assert mapped.status_code == 200
    marked = await client.patch(
        f"/api/v1/settings/locations/{corner.id}", json={"name": "Corner", "consignee": True}
    )
    assert (marked.status_code, marked.json()) == (409, {"detail": "CONSIGNEE_MAPPED"})


async def test_mapping_a_location_queues_what_is_available_there(
    client, sign_in, db: WriteSession, store: Store, main: Location, lot
):
    _, chai = await add_product(db, Product(name="Chai"))
    await lot(chai.id, "L1", "4")
    shop_location = StoreLocation(
        organization_id=db.organization_id, store_id=store.id, external_id="loc-1"
    )
    db.add_all(
        [
            shop_location,
            Listing(
                organization_id=db.organization_id,
                store_id=store.id,
                product_id="p1",
                item_id=chai.id,
                matched_by=MatchedBy.HAND,
            ),
        ]
    )
    await db.commit()
    await sign_in(client, admin=True)

    mapped = await client.put(
        f"/api/v1/stores/{store.id}/locations/{shop_location.id}", json={"location_id": main.id}
    )

    assert mapped.status_code == 200
    log = (await client.get(f"/api/v1/stores/{store.id}/pushes")).json()
    assert [(row["quantity"], row["status"], row["location"]) for row in log["items"]] == [
        (4, "queued", None)
    ]
    assert log["total"] == 1
    assert (await client.get(f"/api/v1/stores/{store.id}/pushes?status=failed")).json()[
        "total"
    ] == 0


async def test_a_pending_store_is_neither_read_again_nor_written_to(
    client, sign_in, pending: Store, shop: FakeConnector, chai
):
    product, _, _ = chai
    await sign_in(client, admin=True)

    for refused in (
        await client.post(f"/api/v1/stores/{pending.id}/refresh"),
        await client.put(
            f"/api/v1/stores/{pending.id}/products/p1", json={"title": "Chai", "updated_at": None}
        ),
        await client.post(f"/api/v1/stores/{pending.id}/products", json={"product_id": product.id}),
    ):
        assert (refused.status_code, refused.json()) == (409, {"detail": "STORE_NOT_SYNCED"})
    assert shop.shop_products["p1"].title == "Chai"


async def test_a_store_product_is_read_from_its_copy_with_prices_only_for_costs(
    client, sign_in, db: WriteSession, store: Store, shop: FakeConnector
):
    await listings.match(db, store, [shop.shop_products["p1"]])
    await db.commit()
    # Reading the copy asks nothing of the store.
    shop.refuse = ConnectError.STORE_UNREACHABLE
    await sign_in(client)

    read = (await client.get(f"/api/v1/stores/{store.id}/products/p1")).json()

    assert (read["title"], read["description"], read["status"]) == (
        "Chai",
        "<p>Spiced</p>",
        "active",
    )
    assert [(v["sku"], v["price"]) for v in read["variants"]] == [
        ("CHAI-L", None),
        ("CHAI-S", None),
    ]
    assert read["images"] == [{"id": "img-1", "url": "https://cdn.test/chai.png", "alt": "A tin"}]

    await sign_in(client, COSTS)
    priced = (await client.get(f"/api/v1/stores/{store.id}/products/p1")).json()
    assert [v["price"] for v in priced["variants"]] == ["12.500000", "8.000000"]

    missing = await client.get(f"/api/v1/stores/{store.id}/products/nope")
    assert (missing.status_code, missing.json()) == (404, {"detail": "NOT_FOUND"})


async def test_a_store_product_is_edited_live_and_its_answer_kept(
    client, sign_in, store: Store, shop: FakeConnector, chai
):
    _, large, _ = chai
    await sign_in(client)
    body = {
        "updated_at": None,
        "title": "Masala chai",
        "description": "<p>Warm</p>",
        "status": "draft",
        "variants": [{"id": "v1", "sku": "CHAI-L", "price": "99"}],
        "images": [{"url": "https://cdn.test/new.png", "alt": None}],
        "published_on": [],
    }

    saved = (await client.put(f"/api/v1/stores/{store.id}/products/p1", json=body)).json()

    held = shop.shop_products["p1"]
    assert (held.title, held.description, held.status, held.published_on) == (
        "Masala chai",
        "<p>Warm</p>",
        "draft",
        [],
    )
    # Without costs, the price is left as the store has it.
    assert [variant.price for variant in held.variants] == [Decimal("12.50"), Decimal("8")]
    assert held.images == [StoreImage(url="https://cdn.test/new.png", alt=None, id=None)]
    assert [(row["variant_id"], row["item_id"]) for row in saved["listings"]] == [
        ("v1", large.id),
        ("v2", None),
    ]
    read = (await client.get(f"/api/v1/stores/{store.id}/products/p1")).json()
    assert (read["title"], read["status"]) == ("Masala chai", "draft")

    assert read["updated_at"] == saved["updated_at"] is not None

    await sign_in(client, COSTS)
    await client.put(
        f"/api/v1/stores/{store.id}/products/p1", json={**body, "updated_at": read["updated_at"]}
    )
    assert shop.shop_products["p1"].variants[0].price == Decimal("99")


async def test_a_store_product_changed_in_the_store_is_not_written_over(
    client, sign_in, db: WriteSession, store: Store, shop: FakeConnector
):
    await listings.match(db, store, [shop.shop_products["p1"]])
    await db.commit()
    await sign_in(client)
    opened = (await client.get(f"/api/v1/stores/{store.id}/products/p1")).json()
    changed = datetime(2026, 11, 15, 9, tzinfo=UTC)
    shop.shop_products["p1"] = replace(
        shop.shop_products["p1"], title="Chai, renamed in the store", updated_at=changed
    )

    refused = await client.put(
        f"/api/v1/stores/{store.id}/products/p1",
        json={"updated_at": opened["updated_at"], "title": "Masala chai"},
    )

    assert (refused.status_code, refused.json()) == (409, {"detail": "STORE_PRODUCT_CHANGED"})
    assert shop.shop_products["p1"].title == "Chai, renamed in the store"
    # Hoard's copy now holds the store's version, for the page to show.
    read = (await client.get(f"/api/v1/stores/{store.id}/products/p1")).json()
    assert (read["title"], read["updated_at"]) == (
        "Chai, renamed in the store",
        "2026-11-15T09:00:00Z",
    )


async def test_a_hoard_product_is_made_in_the_store_with_its_listings(
    client, sign_in, db: WriteSession, store: Store, shop: FakeConnector, chai
):
    product, large, small = chai
    await sign_in(client, COSTS)

    made = await client.post(f"/api/v1/stores/{store.id}/products", json={"product_id": product.id})

    assert made.status_code == 201
    read = made.json()
    assert (read["title"], read["status"], read["options"]) == ("Chai", "draft", ["size"])
    assert [(v["title"], v["sku"], v["price"]) for v in read["variants"]] == [
        ("Large", "CHAI-L", "10.000000"),
        ("Small", None, "7.000000"),
    ]
    assert [(row["item_id"], row["matched_by"]) for row in read["listings"]] == [
        (large.id, "external_id"),
        (small.id, "external_id"),
    ]


async def test_a_store_that_is_not_connected_or_refuses_is_refused(
    client, sign_in, db: WriteSession, store: Store, shop: FakeConnector
):
    await sign_in(client, admin=True)
    shop.refuse = ConnectError.STORE_UNREACHABLE
    edit = {"title": "Chai", "updated_at": None}

    refused = await client.put(f"/api/v1/stores/{store.id}/products/p1", json=edit)
    assert (refused.status_code, refused.json()) == (502, {"detail": "STORE_UNREACHABLE"})
    locations = await client.get(f"/api/v1/stores/{store.id}/store-locations")
    assert (locations.status_code, locations.json()) == (502, {"detail": "STORE_UNREACHABLE"})

    store.connected = False
    db.add(store)
    await db.commit()
    cleared = await client.put(f"/api/v1/stores/{store.id}/products/p1", json=edit)
    assert (cleared.status_code, cleared.json()) == (409, {"detail": "STORE_NOT_CONNECTED"})


async def test_the_push_log_is_newest_first(client, sign_in, db: WriteSession, store: Store):
    listing = Listing(organization_id=db.organization_id, store_id=store.id, product_id="p1")
    db.add(listing)
    await db.flush()
    for key, day in (("newer", 2), ("older", 1)):
        db.add(
            StockPush(
                organization_id=db.organization_id,
                listing_id=listing.id,
                quantity=day,
                idempotency_key=key,
                created_at=datetime(2026, 11, day, tzinfo=UTC),
            )
        )
        await db.flush()
    await db.commit()
    await sign_in(client)

    log = (await client.get(f"/api/v1/stores/{store.id}/pushes")).json()

    assert [row["quantity"] for row in log["items"]] == [2, 1]


async def test_an_admin_queues_a_pending_stores_sync_with_its_location_choices(
    client, sign_in, db: WriteSession, pending: Store, shop: FakeConnector, main: Location
):
    await db.commit()
    shop.shop_locations = {"loc-1": "Shop", "loc-2": "Warehouse"}
    choices = {
        "locations": [
            {"external_id": "loc-1", "location_id": main.id},
            {"external_id": "loc-2", "new": True},
        ]
    }
    await sign_in(client)
    refused = await client.post(f"/api/v1/stores/{pending.id}/sync", json=choices)
    assert (refused.status_code, refused.json()) == (403, {"detail": "ADMIN_ONLY"})

    await sign_in(client, admin=True)
    found = (await client.get(f"/api/v1/stores/{pending.id}/store-locations")).json()
    assert found == [
        {"external_id": "loc-1", "name": "Shop"},
        {"external_id": "loc-2", "name": "Warehouse"},
    ]
    [read] = (await client.get("/api/v1/stores")).json()
    assert (read["state"], read["sync_job_id"]) == ("pending", None)
    unknown = await client.post(
        f"/api/v1/stores/{pending.id}/sync",
        json={"locations": [{"external_id": "loc-1", "location_id": 999999}]},
    )
    assert (unknown.status_code, unknown.json()) == (422, {"detail": "LOCATION_UNKNOWN"})

    queued = await client.post(f"/api/v1/stores/{pending.id}/sync", json=choices)

    assert queued.status_code == 202, queued.text
    job = queued.json()
    assert (job["kind"], job["state"]) == ("store_sync", "queued")
    assert job["args"]["store_id"] == pending.id
    assert job["args"]["locations"] == [
        {"external_id": "loc-1", "location_id": main.id, "new": False},
        {"external_id": "loc-2", "location_id": None, "new": True},
    ]
    again = await client.post(f"/api/v1/stores/{pending.id}/sync", json=choices)
    assert again.json()["id"] == job["id"]
    [read] = (await client.get("/api/v1/stores")).json()
    assert (read["state"], read["sync_job_id"]) == ("syncing", job["id"])
    busy = await client.post(f"/api/v1/stores/{pending.id}/unsync")
    assert (busy.status_code, busy.json()) == (409, {"detail": "STORE_SYNCING"})


async def test_a_synced_or_disconnected_store_is_not_synced_again(
    client, sign_in, db: WriteSession, store: Store, shop: FakeConnector
):
    await sign_in(client, admin=True)
    synced = await client.post(f"/api/v1/stores/{store.id}/sync", json={})
    assert (synced.status_code, synced.json()) == (409, {"detail": "STORE_SYNCED"})

    store.connected, store.imported_at = False, None
    db.add(store)
    await db.commit()
    cleared = await client.post(f"/api/v1/stores/{store.id}/sync", json={})
    assert (cleared.status_code, cleared.json()) == (409, {"detail": "STORE_NOT_CONNECTED"})


async def test_a_sync_is_refused_while_hoard_is_read_only(
    client, sign_in, pending: Store, shop: FakeConnector, initiative_stand_in, community_ref
):
    await sign_in(client, admin=True)
    token = initiative_stand_in.lifecycle("community_status", community_ref=community_ref)
    told = await client.post(
        "/v1/hooks/community_status",
        json={"status": "read_only"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert told.status_code == 204

    refused = await client.post(f"/api/v1/stores/{pending.id}/sync", json={})

    assert (refused.status_code, refused.json()) == (403, {"detail": "READ_ONLY"})


async def test_removing_the_sync_keeps_what_it_brought_in(
    client, sign_in, db: WriteSession, store: Store, shop: FakeConnector, chai
):
    _, large, _ = chai
    listing = Listing(
        organization_id=db.organization_id,
        store_id=store.id,
        product_id="p1",
        variant_id="v1",
        item_id=large.id,
        matched_by=MatchedBy.SKU,
    )
    db.add(listing)
    await db.flush()
    db.add_all(
        StockPush(
            organization_id=db.organization_id,
            listing_id=listing.id,
            quantity=1,
            idempotency_key=key,
            status=status,
        )
        for key, status in (("queued", PushStatus.QUEUED), ("pushed", PushStatus.PUSHED))
    )
    await db.commit()
    shop.subscriptions.add(ADDRESS)
    await sign_in(client)
    refused = await client.post(f"/api/v1/stores/{store.id}/unsync")
    assert (refused.status_code, refused.json()) == (403, {"detail": "ADMIN_ONLY"})

    await sign_in(client, admin=True)
    read = (await client.post(f"/api/v1/stores/{store.id}/unsync")).json()

    assert (read["state"], read["imported_at"], read["pushes_queued"]) == ("pending", None, 0)
    assert shop.subscriptions == set()
    statuses = await db.exec(select(StockPush.status).execution_options(populate_existing=True))
    assert statuses.all() == [PushStatus.PUSHED]
    assert (await db.exec(select(Listing.item_id))).all() == [large.id]
