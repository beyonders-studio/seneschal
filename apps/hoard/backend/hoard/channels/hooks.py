"""The hooks a store's connection calls, each naming its channel's connection
in ``body["connection"]``:

* ``after_connect``: an admin saved the connection. Its connector checks the
  typed values at the store and subscribes to the store's webhooks, aimed at
  Initiative's address for Hoard; the answer is the store's own address and
  the secret its webhooks are signed with, as managed values. A store that
  refuses is refused, and its reason kept on the store
  (``last_check_error``). Subscriptions not made within
  :data:`SUBSCRIBE_SECONDS` are finished by ``upkeep``.
* ``revoke``: the connection was cleared or replaced. The subscriptions made
  with the values it held are removed, and the store is no longer connected
  if those values were its own.
* ``webhook``: a delivery Initiative checked and routed here. What it says
  changed is left as a nudge (:class:`~hoard.records.StoreNudge`), which
  ``hoard.sync`` works.

and the schedules, by ``body["schedule"]``:

* ``reconcile``: each connected store's recent orders imported once, then
  what changed since the last reconcile copied in (``hoard.sync``) and what
  is available pushed where the store holds something else (``hoard.push``);
* ``upkeep``: each connected store's subscriptions finished.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from contextlib import suppress
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert
from sqlmodel import col, select

from hoard import channels, plugin_manifest, push, sync
from hoard.channels import Connected, ConnectRefused
from hoard.records import Channel, Store, StoreNudge
from seneschal_core.db import OrganizationSession, WriteSession
from seneschal_core.platform import (
    HookBodyInvalid,
    HookCall,
    HookHandler,
    organization_session,
    write_session,
)

logger = logging.getLogger(__name__)

#: How long ``after_connect`` waits on the store's subscriptions before
#: leaving the rest to ``upkeep``.
SUBSCRIBE_SECONDS = 5.0


def webhook_address(public_api_url: str) -> str:
    """Initiative's address for Hoard's webhooks, under its public API."""
    return f"{public_api_url.rstrip('/')}/plugin-hooks/{plugin_manifest.PUBLIC_ID}"


def _channel(body: Mapping[str, Any]) -> Channel:
    try:
        return Channel(body.get("connection"))
    except ValueError:
        raise HookBodyInvalid("connection is not a channel Hoard sells on") from None


def _values(body: Mapping[str, Any]) -> Mapping[str, Any]:
    values = body.get("values") or {}
    if not isinstance(values, dict):
        raise HookBodyInvalid("values is not an object")
    return values


async def _store(db: OrganizationSession, channel: Channel) -> Store | None:
    found = await db.exec(select(Store).where(Store.channel == channel).with_for_update())
    return found.first()


async def _keep(
    db: WriteSession,
    channel: Channel,
    connected: Connected | None = None,
    refused: ConnectRefused | None = None,
) -> Store:
    """The organization's store on ``channel``, made on first sight, as its
    connection was just saved or refused."""
    store = await _store(db, channel)
    if store is None:
        store = Store(organization_id=db.organization_id, channel=channel)
        db.add(store)
    if connected is not None:
        store.shop_domain = connected.shop_domain
        store.account_label = connected.account_label
        store.connected = True
    store.last_check_error = refused.reason if refused is not None else None
    await db.flush()
    return store


async def _subscribe(connector: channels.Connector, address: str) -> None:
    with suppress(TimeoutError):
        async with asyncio.timeout(SUBSCRIBE_SECONDS):
            await connector.subscribe(address)


def hooks(address: str) -> dict[str, HookHandler]:
    """The hooks and schedules of Hoard's store connections, with
    Initiative's webhook ``address`` for Hoard."""

    async def after_connect(call: HookCall) -> dict[str, Any]:
        channel = _channel(call.body)
        connector = channels.connector_for(channel, _values(call.body))
        try:
            connected = await connector.check()
            await _subscribe(connector, address)
        except ConnectRefused as refused:
            async with write_session(call) as db:
                await _keep(db, channel, refused=refused)
                await db.commit()
            return {"refuse": True}
        async with write_session(call) as db:
            await _keep(db, channel, connected=connected)
            await db.commit()
        return {
            "values": {
                "shop_domain": connected.shop_domain,
                "signing_secret": connected.signing_secret,
            },
            "account_label": connected.account_label,
        }

    async def revoke(call: HookCall) -> None:
        channel = _channel(call.body)
        values = _values(call.body)
        connector = channels.connector_for(channel, values)
        try:
            await connector.unsubscribe(address)
        except ConnectRefused as refused:
            # The store no longer takes these values: nothing there is left to remove.
            logger.info("could not unsubscribe from a %s store: %s", channel.value, refused)
        async with write_session(call) as db:
            store = await _store(db, channel)
            if store is not None and store.shop_domain == values.get("shop_domain"):
                store.connected = False
            await db.commit()

    async def webhook(call: HookCall) -> None:
        channel = _channel(call.body)
        headers, body = call.body.get("headers"), call.body.get("body")
        if not isinstance(headers, dict) or not isinstance(body, str):
            raise HookBodyInvalid("a webhook carries its headers and body")
        nudge = channels.connector_for(channel, {}).parse_webhook(
            {str(name).lower(): str(value) for name, value in headers.items()}, body
        )
        if nudge is None:
            return
        async with write_session(call) as db:
            store = await _store(db, channel)
            if store is None or not store.connected:
                return
            await db.execute(
                insert(StoreNudge)
                .values(
                    organization_id=db.organization_id,
                    store_id=store.id,
                    topic=nudge.topic.value,
                    external_id=nudge.external_id,
                )
                .on_conflict_do_nothing(
                    index_elements=["organization_id", "store_id", "topic", "external_id"],
                    index_where=text("done_at IS NULL"),
                )
            )
            await db.commit()

    async def schedule(call: HookCall) -> None:
        name = call.body.get("schedule")
        if name not in ("reconcile", "upkeep"):
            raise HookBodyInvalid("schedule is not one Hoard declares")
        since = _since(call.body.get("since"))
        async with organization_session(call) as db:
            organization_id = db.organization_id
            found = await db.exec(select(Store).where(col(Store.connected)).order_by(col(Store.id)))
            stores = list(found.all())
        if organization_id is None:
            return
        for store in stores:
            connector = await channels.connect(
                call.plugin, call.claims.community_ref, store.channel
            )
            if connector is None:
                continue
            if name == "upkeep":
                try:
                    await connector.subscribe(address)
                except ConnectRefused as refused:
                    async with write_session(call) as db:
                        await _keep(db, store.channel, refused=refused)
                        await db.commit()
                continue
            if store.imported_at is None:
                await sync.import_history(call.plugin, organization_id, store, connector)
            await sync.reconcile(call.plugin, organization_id, store, connector, since)
            await push.reconcile(call.plugin, organization_id, store, connector)

    return {
        "after_connect": after_connect,
        "revoke": revoke,
        "webhook": webhook,
        "schedule": schedule,
    }


def _since(value: object) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        raise HookBodyInvalid("since is not a moment") from None
