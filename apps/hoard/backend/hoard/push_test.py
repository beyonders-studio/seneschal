"""Queueing, sending and reconciling stock pushes, against a store kept in memory."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import update
from sqlmodel import col, select

from hoard import channels, push
from hoard.channels import ConnectError
from hoard.channels.fake import FakeConnector
from hoard.records import (
    Channel,
    Listing,
    Location,
    MatchedBy,
    Product,
    PushStatus,
    StockPush,
    Store,
    StoreLocation,
    Variation,
)
from hoard.stock import add_product
from seneschal_core.api import Plugin
from seneschal_core.db import Database, WriteSession
from seneschal_core.testing import InitiativeStandIn


@pytest.fixture
def shop(
    monkeypatch: pytest.MonkeyPatch, initiative_stand_in: InitiativeStandIn, community_ref: str
) -> FakeConnector:
    """The store the community's Shopify connection reaches."""
    fake = FakeConnector()
    monkeypatch.setitem(channels.CONNECTORS, Channel.SHOPIFY, lambda values: fake)
    initiative_stand_in.connections[community_ref] = {"shopify": {"shop": fake.shop_domain}}
    return fake


@pytest.fixture
async def store(db: WriteSession) -> Store:
    made = Store(organization_id=db.organization_id, channel=Channel.SHOPIFY, connected=True)
    db.add(made)
    await db.flush()
    return made


@pytest.fixture
async def shop_location(db: WriteSession, store: Store, main: Location) -> StoreLocation:
    made = StoreLocation(
        organization_id=db.organization_id,
        store_id=store.id,
        external_id="loc-1",
        name="Shop",
        location_id=main.id,
    )
    db.add(made)
    await db.flush()
    return made


@pytest.fixture
async def chai(db: WriteSession) -> Variation:
    _, variation = await add_product(db, Product(name="Chai"))
    return variation


@pytest.fixture
async def listing(db: WriteSession, store: Store, chai: Variation) -> Listing:
    made = Listing(
        organization_id=db.organization_id,
        store_id=store.id,
        product_id="p1",
        variant_id="v1",
        inventory_item_id="i1",
        item_id=chai.id,
        matched_by=MatchedBy.SKU,
    )
    db.add(made)
    await db.flush()
    return made


@pytest.fixture
def plugin(app) -> Plugin:
    return app.state.plugin


async def _pushes(served: Database, organization_id: int) -> list[StockPush]:
    async with served.organization_session(organization_id) as db:
        return list((await db.exec(select(StockPush).order_by(col(StockPush.id)))).all())


async def _due_now(db: WriteSession) -> None:
    await db.execute(
        update(StockPush).values(next_attempt_at=datetime.now(UTC) - timedelta(seconds=1))
    )
    await db.commit()


async def _until(check: Callable[[], Awaitable[bool]]) -> None:
    async with asyncio.timeout(10):
        while not await check():
            await asyncio.sleep(0.05)


def test_a_store_is_told_whole_units_and_never_less_than_none():
    assert push.whole(Decimal("3.9")) == 3
    assert push.whole(Decimal("-2")) == 0


async def test_a_push_is_queued_with_what_is_available(
    db: WriteSession, listing: Listing, shop_location: StoreLocation, chai, lot, sell
):
    await lot(chai.id, "L1", "5")
    await sell((chai.id, "2", "4"))

    [queued] = await push.queue(db, [chai.id])

    assert (queued.listing_id, queued.store_location_id) == (listing.id, shop_location.id)
    assert (queued.quantity, queued.compare_quantity, queued.status) == (3, None, PushStatus.QUEUED)

    await sell((chai.id, "1", "4"))
    [again] = await push.queue(db, [chai.id])

    assert (again.id, again.quantity, again.idempotency_key) == (
        queued.id,
        2,
        queued.idempotency_key,
    )
    assert len((await db.exec(select(StockPush))).all()) == 1


async def test_nothing_is_queued_where_the_store_was_last_pushed_as_much(
    db: WriteSession, listing: Listing, shop_location: StoreLocation, chai, lot
):
    await lot(chai.id, "L1", "4")
    db.add(
        StockPush(
            organization_id=db.organization_id,
            listing_id=listing.id,
            store_location_id=shop_location.id,
            quantity=4,
            idempotency_key="pushed",
            status=PushStatus.PUSHED,
            pushed_at=datetime.now(UTC),
        )
    )
    await db.flush()

    assert await push.queue(db, [chai.id]) == []

    await lot(chai.id, "L2", "1")
    [queued] = await push.queue(db, [chai.id])

    assert (queued.quantity, queued.compare_quantity) == (5, 4)


async def test_nothing_is_queued_for_unmapped_locations_or_cleared_stores(
    db: WriteSession,
    listing: Listing,
    shop_location: StoreLocation,
    store: Store,
    main: Location,
    chai,
    lot,
):
    await lot(chai.id, "L1", "4")
    shop_location.location_id = None
    db.add(shop_location)
    await db.flush()

    assert await push.queue(db, [chai.id]) == []

    shop_location.location_id = main.id
    store.connected = False
    db.add_all([shop_location, store])
    await db.flush()

    assert await push.queue(db, [chai.id]) == []


async def test_a_queued_push_is_sent_and_logged(
    db: WriteSession,
    plugin,
    serving_database,
    shop: FakeConnector,
    listing,
    shop_location,
    chai,
    lot,
):
    await lot(chai.id, "L1", "6")
    await push.queue(db, [chai.id])
    await db.commit()

    await push.send(plugin, db.organization_id)

    [sent] = await _pushes(serving_database, db.organization_id)
    assert (sent.status, sent.quantity, sent.attempts, sent.error) == (
        PushStatus.PUSHED,
        6,
        1,
        None,
    )
    assert sent.pushed_at is not None
    assert shop.stock == {("i1", "loc-1"): 6}
    [asked] = shop.pushes
    assert (asked.product_id, asked.variant_id, asked.location_id, asked.compare_quantity) == (
        "p1",
        "v1",
        "loc-1",
        None,
    )


async def test_the_log_keeps_recent_pushes_and_the_last_one_taken(
    db: WriteSession, listing: Listing, shop_location: StoreLocation
):
    now = datetime(2026, 11, 12, tzinfo=UTC)
    long_ago = now - push.KEEP_FINISHED - timedelta(days=1)

    def logged(key: str, status: PushStatus, created_at: datetime) -> StockPush:
        return StockPush(
            organization_id=db.organization_id,
            listing_id=listing.id,
            store_location_id=shop_location.id,
            quantity=1,
            idempotency_key=key,
            status=status,
            created_at=created_at,
            pushed_at=created_at if status is PushStatus.PUSHED else None,
        )

    db.add_all(
        [
            logged("taken-before", PushStatus.PUSHED, long_ago - timedelta(days=1)),
            logged("taken-last", PushStatus.PUSHED, long_ago),
            logged("given-up", PushStatus.FAILED, long_ago),
            logged("waiting", PushStatus.QUEUED, long_ago),
            logged("given-up-lately", PushStatus.FAILED, now - timedelta(days=1)),
        ]
    )
    await db.flush()

    await push.prune(db, now)

    kept = await db.exec(select(StockPush.idempotency_key).order_by(col(StockPush.id)))
    assert kept.all() == ["taken-last", "waiting", "given-up-lately"]


async def test_a_push_sends_what_is_available_when_it_is_sent(
    db: WriteSession,
    plugin,
    serving_database,
    shop: FakeConnector,
    listing,
    shop_location,
    chai,
    lot,
    sell,
):
    await lot(chai.id, "L1", "6")
    await push.queue(db, [chai.id])
    await sell((chai.id, "4", "1"))
    await db.commit()

    await push.send(plugin, db.organization_id)

    [sent] = await _pushes(serving_database, db.organization_id)
    assert (sent.status, sent.quantity) == (PushStatus.PUSHED, 2)
    assert shop.stock == {("i1", "loc-1"): 2}


async def test_a_store_holding_something_else_is_compared_with_that_next(
    db: WriteSession,
    plugin,
    serving_database,
    shop: FakeConnector,
    listing,
    shop_location,
    chai,
    lot,
):
    shop.stock[("i1", "loc-1")] = 9
    await lot(chai.id, "L1", "6")
    db.add(
        StockPush(
            organization_id=db.organization_id,
            listing_id=listing.id,
            store_location_id=shop_location.id,
            quantity=5,
            idempotency_key="before",
            status=PushStatus.PUSHED,
            pushed_at=datetime.now(UTC),
        )
    )
    await db.flush()
    await push.queue(db, [chai.id])
    await db.commit()

    await push.send(plugin, db.organization_id)

    first = (await _pushes(serving_database, db.organization_id))[-1]
    assert (first.status, first.compare_quantity, first.attempts, first.error) == (
        PushStatus.QUEUED,
        9,
        1,
        "STORE_HOLDS_OTHER",
    )
    assert first.idempotency_key != shop.pushes[0].idempotency_key
    assert shop.stock == {("i1", "loc-1"): 9}

    await _due_now(db)
    await push.send(plugin, db.organization_id)

    again = (await _pushes(serving_database, db.organization_id))[-1]
    assert (again.status, again.attempts, again.error) == (PushStatus.PUSHED, 2, None)
    assert shop.stock == {("i1", "loc-1"): 6}


async def test_a_store_already_holding_what_is_pushed_counts_it_pushed(
    db: WriteSession,
    plugin,
    serving_database,
    shop: FakeConnector,
    listing,
    shop_location,
    chai,
    lot,
):
    shop.stock[("i1", "loc-1")] = 6
    await lot(chai.id, "L1", "6")
    [queued] = await push.queue(db, [chai.id])
    queued.compare_quantity = 2
    await db.commit()

    await push.send(plugin, db.organization_id)

    [sent] = await _pushes(serving_database, db.organization_id)
    assert sent.status is PushStatus.PUSHED


async def test_a_refused_push_waits_longer_each_time_then_is_given_up(
    db: WriteSession,
    plugin,
    serving_database,
    shop: FakeConnector,
    listing,
    shop_location,
    chai,
    lot,
):
    await lot(chai.id, "L1", "6")
    await push.queue(db, [chai.id])
    await db.commit()
    shop.refuse = ConnectError.STORE_UNREACHABLE

    await push.send(plugin, db.organization_id)

    [waiting] = await _pushes(serving_database, db.organization_id)
    assert (waiting.status, waiting.attempts, waiting.error) == (
        PushStatus.QUEUED,
        1,
        "STORE_UNREACHABLE",
    )
    assert waiting.next_attempt_at > datetime.now(UTC) + timedelta(seconds=20)

    await db.execute(update(StockPush).values(attempts=push.MAX_ATTEMPTS - 1))
    await _due_now(db)
    await push.send(plugin, db.organization_id)

    [failed] = await _pushes(serving_database, db.organization_id)
    assert (failed.status, failed.attempts) == (PushStatus.FAILED, push.MAX_ATTEMPTS)


async def test_a_push_to_a_cleared_store_is_given_up(
    db: WriteSession,
    plugin,
    serving_database,
    shop: FakeConnector,
    listing,
    shop_location,
    store,
    chai,
    lot,
):
    await lot(chai.id, "L1", "6")
    await push.queue(db, [chai.id])
    store.connected = False
    db.add(store)
    await db.commit()

    await push.send(plugin, db.organization_id)

    [failed] = await _pushes(serving_database, db.organization_id)
    assert (failed.status, failed.error) == (PushStatus.FAILED, "STORE_NOT_CONNECTED")
    assert shop.pushes == []


async def test_a_committed_change_is_pushed_by_the_worker(
    db: WriteSession,
    plugin,
    serving_database,
    shop: FakeConnector,
    listing,
    shop_location,
    chai,
    lot,
):
    await db.commit()
    pushing = asyncio.create_task(push.push_forever(plugin, sweep_seconds=3600))
    try:
        await asyncio.sleep(0.05)
        await lot(chai.id, "L1", "7")
        await db.commit()

        async def pushed() -> bool:
            return shop.stock.get(("i1", "loc-1")) == 7

        await _until(pushed)
    finally:
        pushing.cancel()
    [sent] = await _pushes(serving_database, db.organization_id)
    assert (sent.status, sent.quantity) == (PushStatus.PUSHED, 7)


async def test_a_rolled_back_change_is_not_pushed(db: WriteSession, chai, lot):
    await lot(chai.id, "L1", "7")
    assert db.info["hoard.noted_items"] == {db.organization_id: {chai.id}}

    await db.rollback()

    assert "hoard.noted_items" not in db.info


async def test_reconcile_reads_the_locations_and_pushes_what_differs(
    db: WriteSession, plugin, serving_database, shop: FakeConnector, listing, store, chai, lot
):
    await lot(chai.id, "L1", "3")
    await db.commit()
    shop.shop_locations = {"loc-1": "Shop"}

    await push.reconcile(plugin, db.organization_id, store, shop)

    [sent] = await _pushes(serving_database, db.organization_id)
    assert (sent.status, sent.quantity) == (PushStatus.PUSHED, 3)
    assert shop.stock == {("i1", "loc-1"): 3}

    await push.reconcile(plugin, db.organization_id, store, shop)

    assert len(await _pushes(serving_database, db.organization_id)) == 1
