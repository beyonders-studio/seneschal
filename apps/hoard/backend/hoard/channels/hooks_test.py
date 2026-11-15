"""The hooks a store's connection calls, and the schedules, against a store
kept in memory."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import update
from sqlmodel import col, select

from hoard import channels, push, sync
from hoard.channels import ConnectError, StoreOrder
from hoard.channels.fake import FakeConnector
from hoard.channels.hooks import webhook_address
from hoard.records import Channel, Store, StoreNudge
from seneschal_core import jobs
from seneschal_core.db import Database
from seneschal_core.testing import InitiativeStandIn

ADDRESS = webhook_address(InitiativeStandIn.API_URL)
TYPED = {"shop": "tea.myshopify.com", "client_id": "client", "client_secret": "secret"}


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> FakeConnector:
    """The store every Shopify connection reaches."""
    fake = FakeConnector()
    monkeypatch.setitem(channels.CONNECTORS, Channel.SHOPIFY, lambda values: fake)
    return fake


@pytest.fixture
def call(client, initiative_stand_in, community_ref, organization_id):
    async def call(hook: str, body: Mapping[str, Any]):
        token = initiative_stand_in.lifecycle(hook, community_ref=community_ref)
        return await client.post(
            f"/v1/hooks/{hook}", json=dict(body), headers={"Authorization": f"Bearer {token}"}
        )

    return call


async def _stores(served: Database, organization_id: int) -> list[Store]:
    async with served.organization_session(organization_id) as db:
        return list((await db.exec(select(Store))).all())


async def _synced(served: Database, organization_id: int) -> None:
    async with served.write_session(organization_id, actor=None) as db:
        await db.execute(update(Store).values(imported_at=datetime.now(UTC)))
        await db.commit()


async def _nudges(served: Database, organization_id: int) -> list[tuple[str, str]]:
    async with served.organization_session(organization_id) as db:
        found = await db.exec(select(StoreNudge))
        return [(nudge.topic, nudge.external_id) for nudge in found.all()]


async def test_saving_a_connection_checks_it_and_leaves_it_pending(
    call, store: FakeConnector, serving_database, organization_id
):
    answer = await call("after_connect", {"connection": "shopify", "values": TYPED})

    assert (answer.status_code, answer.json()) == (
        200,
        {
            "values": {"shop_domain": "tea.myshopify.com", "signing_secret": "signing-secret"},
            "account_label": "Tea House",
        },
    )
    # Nothing is written to the store until it is synced.
    assert store.subscriptions == set()
    [kept] = await _stores(serving_database, organization_id)
    assert (
        kept.channel,
        kept.shop_domain,
        kept.connected,
        kept.last_check_error,
        kept.imported_at,
    ) == (Channel.SHOPIFY, "tea.myshopify.com", True, None, None)


async def test_a_connection_the_store_refuses_is_refused_and_why_is_kept(
    call, store: FakeConnector, serving_database, organization_id
):
    store.refuse = ConnectError.STORE_SCOPES_MISSING

    answer = await call("after_connect", {"connection": "shopify", "values": TYPED})

    assert (answer.status_code, answer.json()) == (200, {"refuse": True})
    [kept] = await _stores(serving_database, organization_id)
    assert (kept.connected, kept.last_check_error) == (False, "STORE_SCOPES_MISSING")

    store.refuse = None
    await call("after_connect", {"connection": "shopify", "values": TYPED})
    [kept] = await _stores(serving_database, organization_id)
    assert (kept.connected, kept.last_check_error) == (True, None)


async def test_a_webhook_leaves_one_nudge_per_record_while_it_waits(
    call, store: FakeConnector, serving_database, organization_id
):
    def delivery(topic: str, record: int) -> dict[str, Any]:
        return {
            "connection": "shopify",
            "headers": {"X-Topic": topic},
            "body": json.dumps({"id": record}),
        }

    # Before the store is synced, nothing is kept.
    assert (await call("webhook", delivery("order", 1))).status_code == 204
    await call("after_connect", {"connection": "shopify", "values": TYPED})
    assert (await call("webhook", delivery("order", 1))).status_code == 204
    assert await _nudges(serving_database, organization_id) == []
    await _synced(serving_database, organization_id)
    for topic, record in (("order", 42), ("order", 42), ("product", 7), ("nothing", 9)):
        assert (await call("webhook", delivery(topic, record))).status_code == 204

    assert sorted(await _nudges(serving_database, organization_id)) == [
        ("order", "42"),
        ("product", "7"),
    ]
    refused = await call("webhook", {"connection": "shopify", "headers": [], "body": "{}"})
    assert refused.status_code == 400


async def test_an_orders_waiting_nudge_keeps_the_newest_body_delivered(
    call, store: FakeConnector, serving_database, organization_id
):
    await call("after_connect", {"connection": "shopify", "values": TYPED})
    await _synced(serving_database, organization_id)
    first = datetime(2026, 11, 12, 9, tzinfo=UTC)
    order = StoreOrder(id="o-1", number="#1001", ordered_at=first, lines=[])
    for minutes in (5, 1, 10, 7):
        headers, body = store.order_webhook(
            replace(order, updated_at=first + timedelta(minutes=minutes))
        )
        delivered = {"connection": "shopify", "headers": headers, "body": body}
        assert (await call("webhook", delivered)).status_code == 204

    async with serving_database.organization_session(organization_id) as db:
        [nudge] = (await db.exec(select(StoreNudge))).all()
    assert nudge.body is not None
    kept = store.webhook_order(nudge.body)
    assert kept is not None and kept.updated_at == first + timedelta(minutes=10)


async def test_a_webhook_never_waits_on_the_nudge_the_worker_took(
    call, app, store: FakeConnector, serving_database, organization_id
):
    await call("after_connect", {"connection": "shopify", "values": TYPED})
    await _synced(serving_database, organization_id)
    first = datetime(2026, 11, 12, 9, tzinfo=UTC)
    order = StoreOrder(id="o-1", number="#1001", ordered_at=first, lines=[])

    async def deliver(minutes: int) -> None:
        headers, body = store.order_webhook(
            replace(order, updated_at=first + timedelta(minutes=minutes))
        )
        delivered = {"connection": "shopify", "headers": headers, "body": body}
        async with asyncio.timeout(5):
            assert (await call("webhook", delivered)).status_code == 204

    await deliver(1)
    taken = await sync._take(app.state.plugin, organization_id)
    assert taken is not None
    # The worker holds the nudge it took, and changes the store, while it refetches.
    async with serving_database.write_session(organization_id, actor=None) as worker:
        await worker.exec(select(StoreNudge).where(StoreNudge.id == taken).with_for_update())
        await worker.execute(update(Store).values(products_since=first))

        await deliver(5)
        await deliver(3)

        await worker.rollback()
    async with serving_database.organization_session(organization_id) as db:
        found = await db.exec(select(StoreNudge).order_by(col(StoreNudge.id)))
        nudges = found.all()
    assert [(nudge.id == taken, nudge.taken_at is not None) for nudge in nudges] == [
        (True, True),
        (False, False),
    ]
    waiting = store.webhook_order(nudges[1].body or {})
    assert waiting is not None and waiting.updated_at == first + timedelta(minutes=5)


async def test_clearing_the_connection_unsubscribes_and_disconnects_its_store(
    call, store: FakeConnector, serving_database, organization_id
):
    await call("after_connect", {"connection": "shopify", "values": TYPED})
    held = {**TYPED, "shop_domain": "tea.myshopify.com", "signing_secret": "signing-secret"}

    # Values another store held, replaced, leave this one connected.
    await call("revoke", {"connection": "shopify", "values": {"shop_domain": "old.myshopify.com"}})
    assert [kept.connected for kept in await _stores(serving_database, organization_id)] == [True]

    answer = await call("revoke", {"connection": "shopify", "values": held})

    assert answer.status_code == 204
    assert store.subscriptions == set()
    assert [kept.connected for kept in await _stores(serving_database, organization_id)] == [False]


async def test_the_schedules_reconcile_each_synced_store_and_keep_it_subscribed(
    call,
    store: FakeConnector,
    initiative_stand_in,
    community_ref,
    serving_database,
    organization_id,
    monkeypatch: pytest.MonkeyPatch,
):
    ran: list[tuple[str, int, Any]] = []

    def recording(name: str):
        async def step(_plugin, _organization_id, kept: Store, connector, *since: Any) -> None:
            assert connector is store
            ran.append((name, kept.id, since))

        return step

    for module, name in ((sync, "reconcile"), (push, "reconcile")):
        monkeypatch.setattr(module, name, recording(f"{module.__name__}.{name}"))
    await call("after_connect", {"connection": "shopify", "values": TYPED})
    initiative_stand_in.connections[community_ref] = {"shopify": TYPED}

    # A pending store is left alone by both schedules.
    for name in ("reconcile", "upkeep"):
        assert (await call("schedule", {"schedule": name, "since": None})).status_code == 204
    assert (ran, store.subscriptions) == ([], set())

    # So is a synced one without its values in the install's configuration.
    await _synced(serving_database, organization_id)
    del initiative_stand_in.connections[community_ref]
    assert (await call("schedule", {"schedule": "reconcile", "since": None})).status_code == 204
    assert ran == []

    initiative_stand_in.connections[community_ref] = {"shopify": TYPED}
    since = "2026-11-12T08:00:00+00:00"
    assert (await call("schedule", {"schedule": "reconcile", "since": since})).status_code == 204
    assert [name for name, _, _ in ran] == ["hoard.sync.reconcile", "hoard.push.reconcile"]
    assert ran[0][2][0].isoformat() == since

    pruned: list[tuple[str, int | None]] = []

    def pruning(name: str):
        async def prune(db) -> None:
            pruned.append((name, db.organization_id))

        return prune

    monkeypatch.setattr(push, "prune", pruning("push"))
    monkeypatch.setattr(jobs, "prune", pruning("jobs"))
    assert (await call("schedule", {"schedule": "upkeep", "since": None})).status_code == 204
    assert store.subscriptions == {ADDRESS}
    assert [name for name, _ in pruned] == ["push", "jobs"]
    assert all(organization_id is not None for _, organization_id in pruned)
    assert (await call("schedule", {"schedule": "nightly", "since": None})).status_code == 400
