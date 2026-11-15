"""The hooks a store's connection calls, each naming its channel's connection
in ``body["connection"]``:

* ``after_connect``: an admin saved the connection. Its connector checks the
  typed values at the store, and the permissions its app was granted; the
  answer is the store's own address and the secret its webhooks are signed
  with, as managed values. Nothing is written to the store: the store is
  pending until someone syncs it (``hoard.store_sync``), which subscribes to
  its webhooks. A store that refuses is refused, and its reason kept on the
  store (``last_check_error``).
* ``revoke``: the connection was cleared or replaced. The subscriptions made
  with the values it held are removed, and the store is no longer connected
  if those values were its own.
* ``webhook``: a delivery Initiative checked and routed here, for a synced
  store. What it says changed is left as a nudge
  (:class:`~hoard.records.StoreNudge`), which ``hoard.sync`` works. One nudge
  waits per record until the worker takes it: an order's keeps the newest
  body delivered for it. The hook answers Initiative's delivery, so it waits
  on no row the worker holds.

and the schedules, by ``body["schedule"]``, for each synced store:

* ``reconcile``: what changed since the last reconcile copied in
  (``hoard.sync``) and what is available pushed where the store holds
  something else (``hoard.push``);
* ``upkeep``: each synced store's subscriptions finished, the push log
  pruned (``hoard.push``), the holds whose ready date has come noted
  (``hoard.holds``), and the jobs finished a week ago pruned
  (``seneschal_core.jobs``).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert
from sqlmodel import col, select

from hoard import channels, holds, plugin_manifest, push, sync
from hoard.channels import Connected, ConnectRefused
from hoard.records import Channel, Store, StoreNudge
from seneschal_core import jobs
from seneschal_core.db import OrganizationSession, WriteSession
from seneschal_core.platform import (
    HookBodyInvalid,
    HookCall,
    HookHandler,
    organization_session,
    write_session,
)

logger = logging.getLogger(__name__)

#: How many times a webhook tries to leave its nudge while the worker takes
#: the one waiting meanwhile.
LEAVE_TRIES = 3


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


async def _store(db: OrganizationSession, channel: Channel, *, lock: bool = True) -> Store | None:
    query = select(Store).where(Store.channel == channel)
    found = await db.exec(query.with_for_update() if lock else query)
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


def _newer(
    connector: channels.Connector, body: Mapping[str, Any], held: Mapping[str, Any] | None
) -> bool:
    """Whether an order's webhook ``body`` describes it as the store changed
    it no earlier than ``held`` does."""
    if held is None:
        return True
    found, kept = connector.webhook_order(body), connector.webhook_order(held)
    if found is None or kept is None:
        return found is not None
    return (
        found.updated_at is None or kept.updated_at is None or found.updated_at >= kept.updated_at
    )


async def _leave(
    db: WriteSession, connector: channels.Connector, store: Store, nudge: channels.Nudge
) -> None:
    """Leave ``nudge`` for the worker: a new one, or the one waiting for its
    record, given the body when it is the newer. One the worker has taken is
    left alone, and a new one waits after it: the waiting one is locked only
    by another delivery or for the moment the worker takes it, never while
    the worker refetches its record."""
    for _ in range(LEAVE_TRIES):
        made = await db.execute(
            insert(StoreNudge)
            .values(
                organization_id=db.organization_id,
                store_id=store.id,
                topic=nudge.topic.value,
                external_id=nudge.external_id,
                body=None if nudge.body is None else dict(nudge.body),
            )
            .on_conflict_do_nothing(
                index_elements=["organization_id", "store_id", "topic", "external_id"],
                index_where=text("done_at IS NULL AND taken_at IS NULL"),
            )
            .returning(col(StoreNudge.id))
        )
        if made.first() is not None:
            return
        waiting = (
            await db.exec(
                select(StoreNudge)
                .where(
                    StoreNudge.store_id == store.id,
                    StoreNudge.topic == nudge.topic.value,
                    StoreNudge.external_id == nudge.external_id,
                    col(StoreNudge.done_at).is_(None),
                    col(StoreNudge.taken_at).is_(None),
                )
                .with_for_update()
            )
        ).first()
        if waiting is not None:
            if nudge.body is not None and _newer(connector, nudge.body, waiting.body):
                waiting.body = dict(nudge.body)
                await db.flush()
            return
    # The worker kept taking the waiting nudge: the reconcile reads the record.
    logger.info("left no nudge for a %s record", nudge.topic.value)


def hooks(address: str) -> dict[str, HookHandler]:
    """The hooks and schedules of Hoard's store connections, with
    Initiative's webhook ``address`` for Hoard."""

    async def after_connect(call: HookCall) -> dict[str, Any]:
        channel = _channel(call.body)
        connector = channels.connector_for(channel, _values(call.body))
        try:
            connected = await connector.check()
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
        connector = channels.connector_for(channel, {})
        nudge = connector.parse_webhook(
            {str(name).lower(): str(value) for name, value in headers.items()}, body
        )
        if nudge is None:
            return
        async with write_session(call) as db:
            store = await _store(db, channel, lock=False)
            if store is None or not store.connected or store.imported_at is None:
                return
            await _leave(db, connector, store, nudge)
            await db.commit()

    async def schedule(call: HookCall) -> None:
        name = call.body.get("schedule")
        if name not in ("reconcile", "upkeep"):
            raise HookBodyInvalid("schedule is not one Hoard declares")
        since = _since(call.body.get("since"))
        async with organization_session(call) as db:
            organization_id = db.organization_id
            found = await db.exec(select(Store).where(push.synced()).order_by(col(Store.id)))
            stores = list(found.all())
        if organization_id is None:
            return
        if name == "upkeep":
            async with write_session(call) as db:
                await push.prune(db)
                await holds.upkeep(db)
                await jobs.prune(db)
                await db.commit()
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
