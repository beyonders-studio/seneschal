"""A store's sync, against a store kept in memory: everything read into Hoard
and nothing written to the store, a lost worker resumed at its step, and a
failed sync leaving the store pending."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import update
from sqlmodel import col, select

from hoard import channels, push, store_fields, store_sync
from hoard.app import JOB_HANDLERS
from hoard.channels import (
    ConnectError,
    StoreField,
    StoreLine,
    StoreOrder,
    StoreProduct,
    StoreVariant,
)
from hoard.channels.fake import FakeConnector
from hoard.channels.hooks import webhook_address
from hoard.orders import available, committed
from hoard.records import (
    Adjustment,
    AdjustmentKind,
    AdjustmentReason,
    Channel,
    FieldOwner,
    FieldTarget,
    ItemStatus,
    Listing,
    Location,
    Product,
    PropertyValue,
    PushStatus,
    StockPush,
    Store,
    StoreFieldValue,
    StoreLocation,
    StoreProductCopy,
    Variation,
)
from hoard.stock import add_product
from seneschal_core import jobs
from seneschal_core.api import Plugin
from seneschal_core.db import WriteSession
from seneschal_core.jobs import Job, JobState
from seneschal_core.testing import InitiativeStandIn

ADDRESS = webhook_address(InitiativeStandIn.API_URL)


@pytest.fixture
def shop(
    monkeypatch: pytest.MonkeyPatch, initiative_stand_in: InitiativeStandIn, community_ref: str
) -> FakeConnector:
    """A store with three locations, a product Hoard sells already, one it
    does not, and an archived one; stock and costs; an order still to send;
    and fields for the products' origin and their variants' allergens."""
    fake = FakeConnector()
    monkeypatch.setitem(channels.CONNECTORS, Channel.SHOPIFY, lambda values: fake)
    initiative_stand_in.connections[community_ref] = {"shopify": {"shop": fake.shop_domain}}
    fake.shop_locations = {"loc-1": "Shop", "loc-2": "Warehouse", "loc-3": "Pop-up"}
    fake.shop_products = {
        "p-chai": StoreProduct(
            id="p-chai",
            title="Masala chai",
            options=["Size"],
            variants=[
                StoreVariant(
                    id="v-small",
                    title="Small",
                    sku="CHAI-S",
                    price=Decimal(8),
                    options={"Size": "Small"},
                    inventory_item_id="i-small",
                ),
                StoreVariant(
                    id="v-large",
                    title="Large",
                    sku="CHAI-L",
                    price=Decimal(12),
                    options={"Size": "Large"},
                    inventory_item_id="i-large",
                ),
            ],
        ),
        "p-mug": StoreProduct(
            id="p-mug",
            title="Mug",
            variants=[StoreVariant(id="v-mug", title="Mug", sku="MUG", inventory_item_id="i-mug")],
        ),
        "p-old": StoreProduct(
            id="p-old",
            title="Old tin",
            status="archived",
            variants=[StoreVariant(id="v-old", title="Old tin", inventory_item_id="i-old")],
        ),
    }
    fake.stock = {
        ("i-small", "loc-1"): 5,
        ("i-large", "loc-1"): 3,
        ("i-mug", "loc-1"): 7,
        ("i-mug", "loc-2"): 2,
        ("i-mug", "loc-3"): 40,
    }
    fake.unit_costs = {"i-mug": Decimal("2.5")}
    fake.shop_fields = [
        StoreField(FieldOwner.PRODUCT, "custom", "origin", "Origin", "single_line_text_field"),
        StoreField(FieldOwner.VARIANT, "custom", "allergens", "Allergens", "multi_line_text_field"),
    ]
    fake.field_store = {
        "p-chai": {"custom.origin": "Assam"},
        "v-small": {"custom.allergens": "Milk, Peanuts"},
    }
    fake.history = [
        StoreOrder(
            id="o-1",
            number="#1001",
            ordered_at=datetime(2026, 11, 10, tzinfo=UTC),
            paid=True,
            lines=[
                StoreLine(
                    id="l-1",
                    title="Mug",
                    quantity=Decimal(2),
                    price=Decimal(9),
                    product_id="p-mug",
                    variant_id="v-mug",
                )
            ],
        )
    ]
    return fake


@pytest.fixture
async def store(db: WriteSession) -> Store:
    """A store connected, and not yet synced."""
    made = Store(
        organization_id=db.organization_id,
        channel=Channel.SHOPIFY,
        shop_domain="tea.myshopify.com",
        connected=True,
    )
    db.add(made)
    await db.flush()
    return made


@pytest.fixture
async def chai(db: WriteSession, main: Location, lot) -> Variation:
    """Hoard's chai, sold already, with ten in stock."""
    _, small = await add_product(db, Product(name="Chai"))
    small.sku = "chai-s"
    db.add(small)
    await lot(small.id, "C-1", "10")
    return small


@pytest.fixture
def plugin(app) -> Plugin:
    return app.state.plugin


Sync = Callable[..., Awaitable[Job]]


@pytest.fixture
def sync_store(db: WriteSession, store: Store, main: Location, plugin: Plugin) -> Sync:
    """Queue the store's sync, the shop going into the main location, the
    warehouse into a new one and the pop-up nowhere, the origin into a new
    property and the allergens compared, and work it until it is
    done or failed, each retry due at once, and, where its worker was
    ``lost``, its tries not counted."""

    async def sync_store(*, lost: bool = False) -> Job:
        choices = [
            store_sync.LocationChoice("loc-1", main.id),
            store_sync.LocationChoice("loc-2", new=True),
        ]
        job = await jobs.enqueue(
            db,
            store_sync.STORE_SYNC_JOB,
            store_sync.job_args(
                store,
                ADDRESS,
                choices,
                [
                    store_fields.FieldChoice(
                        FieldOwner.PRODUCT, "custom.origin", FieldTarget.PROPERTY
                    ),
                    store_fields.FieldChoice(
                        FieldOwner.VARIANT, "custom.allergens", FieldTarget.ALLERGENS
                    ),
                ],
            ),
            started_by="member-ref",
            unique_key=str(store.id),
        )
        await db.commit()
        while True:
            await jobs.work(plugin, db.organization_id, JOB_HANDLERS)
            found = await db.get_one(Job, job.id, populate_existing=True)
            if found.state in (JobState.DONE, JobState.FAILED):
                return found
            again: dict[str, Any] = {"next_attempt_at": datetime.now(UTC)}
            if lost:
                again["attempts"] = 0
            await db.execute(update(Job).where(col(Job.id) == job.id).values(again))
            await db.commit()

    return sync_store


async def _outcome(db: WriteSession, store: Store, main: Location) -> dict[str, Any]:
    """What a sync left in Hoard."""
    warehouse = (await db.exec(select(Location).where(Location.name == "Warehouse"))).one()
    located = await db.exec(
        select(StoreLocation.external_id, StoreLocation.location_id).order_by(
            col(StoreLocation.external_id)
        )
    )
    products = await db.exec(
        select(Product.name, Product.status, Variation.sku, Variation.attributes)
        .join(Variation, col(Variation.product_id) == Product.id)
        .order_by(col(Variation.id))
    )
    sold = {
        listing.variant_id: listing.item_id
        for listing in (
            await db.exec(select(Listing).execution_options(populate_existing=True))
        ).all()
        if listing.item_id is not None
    }
    stock = {
        (variant, where.name or "main"): await available(db, item_id, where.id)
        for variant, item_id in sold.items()
        for where in (main, warehouse)
    }
    adjustments = await db.exec(
        select(Adjustment.kind, Adjustment.change, Adjustment.unit_cost, AdjustmentReason.key)
        .outerjoin(AdjustmentReason, col(AdjustmentReason.id) == Adjustment.reason_id)
        .order_by(col(Adjustment.id))
    )
    pushes = await db.exec(select(StockPush.status, StockPush.quantity).order_by(col(StockPush.id)))
    filled = await db.exec(select(PropertyValue.value))
    compared = await db.exec(
        select(StoreFieldValue.owner_id, StoreFieldValue.store_value).order_by(
            col(StoreFieldValue.owner_id)
        )
    )
    kept = await db.get_one(Store, store.id, populate_existing=True)
    return {
        "locations": [
            (external_id, {None: None, main.id: "main", warehouse.id: "Warehouse"}[location_id])
            for external_id, location_id in located.all()
        ],
        "products": [tuple(row) for row in products.all()],
        "copies": sorted((await db.exec(select(StoreProductCopy.product_id))).all()),
        "stock": stock,
        "adjustments": [tuple(row) for row in adjustments.all()],
        "pushes": [tuple(row) for row in pushes.all()],
        "fields": (list(filled.all()), [tuple(row) for row in compared.all()]),
        "synced": kept.imported_at is not None,
    }


#: What the sync leaves, against the store the ``shop`` fixture holds.
EXPECTED = {
    "locations": [("loc-1", "main"), ("loc-2", "Warehouse"), ("loc-3", None)],
    "products": [
        # Hoard's chai takes the store's name, SKU and options; its large
        # variant, matched by nothing, is made on it.
        ("Masala chai", ItemStatus.ACTIVE, "CHAI-S", {"Size": "Small"}),
        ("Masala chai", ItemStatus.ACTIVE, "CHAI-L", {"Size": "Large"}),
        ("Mug", ItemStatus.ACTIVE, "MUG", {}),
        ("Old tin", ItemStatus.ARCHIVED, None, {}),
    ],
    "copies": ["p-chai", "p-mug", "p-old"],
    "stock": {
        ("v-small", "main"): 5,
        ("v-small", "Warehouse"): 0,
        ("v-large", "main"): 3,
        ("v-large", "Warehouse"): 0,
        ("v-mug", "main"): 7,
        ("v-mug", "Warehouse"): 2,
        ("v-old", "main"): 0,
        ("v-old", "Warehouse"): 0,
    },
    "adjustments": [
        # The chai's starting lot, then set down to the store's count.
        (AdjustmentKind.STARTING, 10, 1, None),
        (AdjustmentKind.MANUAL, -5, 1, "synced_from_store"),
        (AdjustmentKind.STARTING, 3, 0, None),
        # The mug's open order holds two at the shop.
        (AdjustmentKind.STARTING, 9, Decimal("2.5"), None),
        (AdjustmentKind.STARTING, 2, Decimal("2.5"), None),
    ],
    "pushes": [(PushStatus.PUSHED, quantity) for quantity in (5, 0, 3, 0, 7, 2, 0, 0)],
    # The chai's origin fills a property; each variant's allergens are
    # recorded as the store holds them.
    "fields": (
        ["Assam"],
        [
            ("v-large", None),
            ("v-mug", None),
            ("v-old", None),
            ("v-small", "Milk, Peanuts"),
        ],
    ),
    "synced": True,
}


async def test_a_sync_reads_the_store_into_hoard_and_sends_it_nothing(
    db: WriteSession,
    store: Store,
    main: Location,
    chai: Variation,
    shop: FakeConnector,
    plugin: Plugin,
    sync_store: Sync,
):
    job = await sync_store()

    assert (job.state, job.error, job.step) == (JobState.DONE, None, "store_finish")
    assert await _outcome(db, store, main) == EXPECTED
    mug = (await db.exec(select(Listing.item_id).where(Listing.variant_id == "v-mug"))).one()
    assert mug is not None and await committed(db, mug, main.id) == 2
    # The webhook subscriptions are the one thing the store is told.
    assert (shop.pushes, shop.field_writes, shop.subscriptions) == ([], [], {ADDRESS})

    await push.reconcile(plugin, db.organization_id, store, shop)

    assert shop.pushes == []
    queued = await db.exec(select(StockPush).where(StockPush.status == PushStatus.QUEUED))
    assert queued.all() == []


async def test_a_sync_whose_worker_is_lost_resumes_at_its_step(
    db: WriteSession,
    store: Store,
    main: Location,
    chai: Variation,
    shop: FakeConnector,
    sync_store: Sync,
    monkeypatch: pytest.MonkeyPatch,
):
    ran: list[store_sync.SyncStep] = []

    def losing(step: store_sync.SyncStep, work: Callable[[Any], Awaitable[None]]):
        async def run(current: Any) -> None:
            ran.append(step)
            await work(current)
            if ran.count(step) == 1:
                # Lost once each step's work is done, before it is recorded.
                raise RuntimeError("worker lost")

        return step, run

    monkeypatch.setattr(
        store_sync, "_STEPS", [losing(step, work) for step, work in store_sync._STEPS]
    )

    job = await sync_store(lost=True)

    assert job.state is JobState.DONE
    assert ran == [step for step in store_sync.SyncStep for _ in range(2)]
    assert await _outcome(db, store, main) == EXPECTED
    assert (shop.pushes, shop.field_writes) == ([], [])


async def test_a_failed_sync_leaves_the_store_pending_with_its_error(
    db: WriteSession,
    store: Store,
    shop: FakeConnector,
    sync_store: Sync,
    client,
    sign_in,
):
    shop.refuse = ConnectError.STORE_UNREACHABLE

    job = await sync_store()

    assert (job.state, job.error) == (JobState.FAILED, "STORE_UNREACHABLE")
    await sign_in(client)
    [read] = (await client.get("/api/v1/stores")).json()
    assert (read["state"], read["imported_at"], read["sync_job_id"]) == ("pending", None, job.id)
    assert shop.subscriptions == set()
