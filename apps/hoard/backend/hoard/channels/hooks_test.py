"""The hooks a store's connection calls, and the schedules, against a store
kept in memory."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

import pytest
from sqlmodel import select

from hoard import channels, push, sync
from hoard.channels import ConnectError
from hoard.channels.fake import FakeConnector
from hoard.channels.hooks import webhook_address
from hoard.records import Channel, Store, StoreNudge
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


async def _nudges(served: Database, organization_id: int) -> list[tuple[str, str]]:
    async with served.organization_session(organization_id) as db:
        found = await db.exec(select(StoreNudge))
        return [(nudge.topic, nudge.external_id) for nudge in found.all()]


async def test_saving_a_connection_checks_it_and_subscribes(
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
    assert store.subscriptions == {ADDRESS}
    [kept] = await _stores(serving_database, organization_id)
    assert (kept.channel, kept.shop_domain, kept.connected, kept.last_check_error) == (
        Channel.SHOPIFY,
        "tea.myshopify.com",
        True,
        None,
    )


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

    # Before the store is connected, nothing is kept.
    assert (await call("webhook", delivery("order", 1))).status_code == 204
    await call("after_connect", {"connection": "shopify", "values": TYPED})
    for topic, record in (("order", 42), ("order", 42), ("product", 7), ("nothing", 9)):
        assert (await call("webhook", delivery(topic, record))).status_code == 204

    assert sorted(await _nudges(serving_database, organization_id)) == [
        ("order", "42"),
        ("product", "7"),
    ]
    refused = await call("webhook", {"connection": "shopify", "headers": [], "body": "{}"})
    assert refused.status_code == 400


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


async def test_the_schedules_reconcile_each_connected_store_and_keep_it_subscribed(
    call,
    store: FakeConnector,
    initiative_stand_in,
    community_ref,
    monkeypatch: pytest.MonkeyPatch,
):
    ran: list[tuple[str, int, Any]] = []

    def recording(name: str):
        async def step(_plugin, _organization_id, kept: Store, connector, *since: Any) -> None:
            assert connector is store
            ran.append((name, kept.id, since))

        return step

    for module, name in ((sync, "import_history"), (sync, "reconcile"), (push, "reconcile")):
        monkeypatch.setattr(module, name, recording(f"{module.__name__}.{name}"))
    await call("after_connect", {"connection": "shopify", "values": TYPED})

    # Without its values in the install's configuration, the store is left alone.
    assert (await call("schedule", {"schedule": "reconcile", "since": None})).status_code == 204
    assert ran == []

    initiative_stand_in.connections[community_ref] = {"shopify": TYPED}
    since = "2026-11-12T08:00:00+00:00"
    assert (await call("schedule", {"schedule": "reconcile", "since": since})).status_code == 204
    assert [name for name, _, _ in ran] == [
        "hoard.sync.import_history",
        "hoard.sync.reconcile",
        "hoard.push.reconcile",
    ]
    assert ran[1][2][0].isoformat() == since

    store.subscriptions.clear()
    assert (await call("schedule", {"schedule": "upkeep", "since": None})).status_code == 204
    assert store.subscriptions == {ADDRESS}
    assert (await call("schedule", {"schedule": "nightly", "since": None})).status_code == 400
