"""A store's sync: everything the store holds read into Hoard once, writing
nothing to the store, after which the store keeps syncing.

A store is in one of three states. **Pending**: connected, and never synced,
or its sync removed; Hoard reads and writes nothing of it but checking its
connection. **Syncing**: its sync is a job (``seneschal_core.jobs``) of kind
:data:`STORE_SYNC_JOB`, one per store at a time. **Synced**: its
``imported_at`` is set, and its webhooks, the reconcile and the pushes keep
it and Hoard in step.

The sync runs in six steps (:class:`SyncStep`). Each commits as it goes and
records that it is done in the job's checkpoint, so a sync whose worker is
lost resumes at the step it was on; each can run twice to the same result.

1. **Locations**: the store's locations are kept and each mapped as asked
   (:class:`LocationChoice`): into a Hoard location, into a new one named as
   the store names it, or nowhere.
2. **Products**: every store product is read in bulk and taken into Hoard
   (``hoard.listings.adopt``): a matched product takes the store's name, its
   variations the store's SKUs and options, and an unmatched one is made.
   Each product's copy is kept.
3. **Fields**: the store's fields are mapped as asked
   (:class:`hoard.store_fields.FieldChoice`), the new properties asked for
   made, and every product's values of them read in bulk and kept
   (``hoard.store_fields.take``): a property filled, and a field Hoard works
   out recorded as what the store holds, not sent.
4. **Orders**: the orders of the window the store keeps are copied in
   (``hoard.sync.import_history``). What the store already sent moves no
   stock.
5. **Stock**: each linked item is set, at each Hoard location a store
   location is mapped into, so that what Hoard has available there is what
   the store has available: an item Hoard holds no stock of there starts with
   it at the store's cost, and any other gets a manual adjustment for the
   difference, for the "synced from store" reason.
6. **Finish**: the pushes the earlier steps left are dropped, each listing
   recorded as already pushed what is available (``hoard.push.baseline``),
   the store's webhooks subscribed to, and ``imported_at`` stamped with when
   step 4 began.

Nothing is sent to the store but the webhook subscriptions: Hoard pushes only
where what is available differs from what it last pushed, and step 5 records
the two as equal.

**Removing the sync** (:func:`unsync`) unsubscribes from the store's webhooks,
drops its waiting pushes and clears ``imported_at``; whatever the sync
brought in stays.

**A fields job** (:data:`hoard.store_fields.STORE_FIELDS_JOB`, run by
:func:`run_fields`) reads in a field mapped after the sync, as the sync does,
and sends what Hoard works out where it differs from the store
(``hoard.store_fields.send``).
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from hoard import channels, listings, push, stock, store_fields, summary, sync
from hoard.channels import Connector, ConnectRefused, StoreStock
from hoard.push import PushError
from hoard.records import (
    Adjustment,
    AdjustmentKind,
    AdjustmentReason,
    DefaultReason,
    Item,
    Listing,
    Location,
    Store,
    StoreLocation,
)
from hoard.stock import StockRefused
from seneschal_core import organizations
from seneschal_core.api import ErrorCode
from seneschal_core.db import WriteSession
from seneschal_core.initiative_api import InitiativeRefused, InitiativeUnavailable
from seneschal_core.jobs import Job, JobContext, JobError, JobState

logger = logging.getLogger(__name__)

#: The kind of job a store's sync is.
STORE_SYNC_JOB = "store_sync"
#: How many products, or items' stock, one commit takes.
AT_ONCE = 50


class SyncStep(StrEnum):
    """The sync's steps, in order: codes pages translate."""

    LOCATIONS = "store_locations"
    PRODUCTS = "store_products"
    FIELDS = "store_fields"
    ORDERS = "store_orders"
    STOCK = "store_stock"
    FINISH = "store_finish"


class SyncError(StrEnum):
    """Why a sync failed, beside the store's own refusals
    (:class:`hoard.channels.ConnectError`): codes pages translate."""

    #: The store's connection was cleared.
    STORE_NOT_CONNECTED = "STORE_NOT_CONNECTED"


@dataclass(frozen=True)
class LocationChoice:
    """Where one of the store's locations, by the store's id, takes its
    stock from: a Hoard location, a new one named as the store names it
    (``new``), or none."""

    external_id: str
    location_id: int | None = None
    new: bool = False


def job_args(
    store: Store,
    address: str,
    choices: Sequence[LocationChoice],
    fields: Sequence[store_fields.FieldChoice] = (),
) -> dict[str, Any]:
    """A sync's arguments: the store, Initiative's webhook ``address`` for
    Hoard, where each store location's stock goes, and what each of the
    store's fields fills."""
    return {
        "store_id": store.id,
        "address": address,
        "fields": [choice.args() for choice in fields],
        "locations": [
            {
                "external_id": choice.external_id,
                "location_id": choice.location_id,
                "new": choice.new,
            }
            for choice in choices
        ],
    }


def _now() -> datetime:
    return datetime.now(UTC)


async def sync_job(db: AsyncSession, store: Store) -> Job | None:
    """The store's sync now running or waiting, else the last one it had."""
    found = await db.exec(
        select(Job)
        .where(Job.kind == STORE_SYNC_JOB, Job.unique_key == str(store.id))
        .order_by(col(Job.id).desc())
        .limit(1)
    )
    return found.first()


def syncing(job: Job | None) -> bool:
    return job is not None and job.state in (JobState.QUEUED, JobState.RUNNING)


@dataclass
class _Sync:
    """One run of a store's sync."""

    job: JobContext
    store_id: int
    connector: Connector
    #: The checkpoint saved as each step ends: the next step, and when the
    #: orders began to be read.
    state: dict[str, Any]

    def session(self) -> WriteSession:
        return self.job.plugin.database.write_session(
            self.job.organization_id, actor=self.job.started_by
        )

    async def progress(self, step: SyncStep, done: int | None, total: int | None) -> None:
        await self.job.progress(step.value, done, total)


async def _chosen(db: WriteSession, choice: Mapping[str, Any] | None, name: str) -> int | None:
    """The Hoard location a store location is mapped into, as chosen."""
    if choice is None:
        return None
    if choice.get("new"):
        await db.execute(
            insert(Location)
            .values(organization_id=db.organization_id, name=name)
            .on_conflict_do_nothing(index_elements=["organization_id", "name"])
        )
        return (await db.exec(select(Location.id).where(Location.name == name))).one()
    location_id = choice.get("location_id")
    if location_id is None or await db.get(Location, int(location_id)) is None:
        return None
    return int(location_id)


async def _locations(run: _Sync) -> None:
    found = await run.connector.locations()
    await run.progress(SyncStep.LOCATIONS, 0, len(found))
    choices = {str(choice["external_id"]): choice for choice in run.job.args.get("locations", [])}
    async with run.session() as db:
        store = await db.get_one(Store, run.store_id)
        for kept in await listings.keep_locations(db, store, found):
            name = kept.name or kept.external_id
            kept.location_id = await _chosen(db, choices.get(kept.external_id), name)
            db.add(kept)
        await db.commit()
    await run.progress(SyncStep.LOCATIONS, len(found), len(found))


async def _products(run: _Sync) -> None:
    started = _now()
    await run.progress(SyncStep.PRODUCTS, None, None)
    found = [product async for product in run.connector.import_products()]
    await run.progress(SyncStep.PRODUCTS, 0, len(found))
    async with run.session() as db:
        store = await db.get_one(Store, run.store_id)
        for start in range(0, len(found), AT_ONCE):
            await listings.adopt(db, store, found[start : start + AT_ONCE], every=True)
            push.forget_noted(db)
            await db.commit()
            await run.progress(SyncStep.PRODUCTS, min(start + AT_ONCE, len(found)), len(found))
        # The reconcile reads on from here.
        store.products_since = started
        await db.commit()


async def _fields(run: _Sync) -> None:
    await run.progress(SyncStep.FIELDS, None, None)
    choices = [store_fields.FieldChoice.from_args(each) for each in run.job.args.get("fields", [])]
    if not choices:
        return
    found = await run.connector.fields()
    kept = []
    async with run.session() as db:
        store = await db.get_one(Store, run.store_id)
        for choice in choices:
            with suppress(store_fields.StoreFieldRefused):
                # A field the store changed since it was chosen is left out.
                mapping = await store_fields.keep(db, store, found, choice)
                if mapping is not None:
                    kept.append(mapping.id)
        await db.commit()

    async def progress(done: int, total: int) -> None:
        await run.progress(SyncStep.FIELDS, done, total)

    await store_fields.read_in(run.session, run.store_id, run.connector, kept, progress)


async def _orders(run: _Sync) -> None:
    if "orders_from" not in run.state:
        # Saved before the orders are read, so that a resumed step keeps it.
        run.state["orders_from"] = _now().isoformat()
        await run.job.save_checkpoint(run.state)
    await run.progress(SyncStep.ORDERS, None, None)
    async with run.session() as db:
        store = await db.get_one(Store, run.store_id)

    async def progress(done: int, total: int) -> None:
        await run.progress(SyncStep.ORDERS, done, total)

    await sync.import_history(
        run.job.plugin, run.job.organization_id, store, run.connector, progress
    )


def _store_key(listing: Listing) -> str:
    """What the store keeps a listing's stock under."""
    return listing.inventory_item_id or listing.variant_id or listing.product_id


async def _targets(
    db: WriteSession, store: Store, levels: Mapping[str, StoreStock]
) -> dict[tuple[int, int], tuple[int, Decimal | None]]:
    """What the store has available of each linked item, by item and the
    Hoard location its store locations are mapped into, with what one cost
    in the store: the sum over those store locations, and, for an item
    several listings sell, the most any of them has."""
    located = await db.exec(
        select(StoreLocation).where(
            StoreLocation.store_id == store.id, col(StoreLocation.location_id).is_not(None)
        )
    )
    into: dict[int, list[str]] = defaultdict(list)
    for store_location in located.all():
        assert store_location.location_id is not None
        into[store_location.location_id].append(store_location.external_id)
    sold = await db.exec(
        select(Listing)
        .where(Listing.store_id == store.id, col(Listing.item_id).is_not(None))
        .order_by(col(Listing.id))
    )
    targets: dict[tuple[int, int], tuple[int, Decimal | None]] = {}
    for listing in sold.all():
        level = levels.get(_store_key(listing))
        if level is None or listing.item_id is None:
            continue
        for location_id, external_ids in into.items():
            available = sum(level.available.get(external_id, 0) for external_id in external_ids)
            held = targets.get((listing.item_id, location_id))
            if held is None or available > held[0]:
                targets[(listing.item_id, location_id)] = (available, level.unit_cost)
    return targets


async def _synced_reason(db: WriteSession) -> int:
    """The ``synced_from_store`` reason's id, made again if it was removed."""
    await db.execute(
        insert(AdjustmentReason)
        .values(organization_id=db.organization_id, key=DefaultReason.SYNCED_FROM_STORE)
        .on_conflict_do_nothing()
    )
    reason = await db.exec(
        select(AdjustmentReason.id).where(
            AdjustmentReason.key == DefaultReason.SYNCED_FROM_STORE.value
        )
    )
    return reason.one()


async def _set(
    db: WriteSession, item_id: int, location_id: int, target: int, unit_cost: Decimal | None
) -> None:
    """Set an item's stock at a location so that what is available there is
    ``target``."""
    change = Decimal(target) - (await summary.stock_at(db, item_id, location_id)).available
    if change == 0:
        return
    held = await db.exec(
        select(func.count())
        .select_from(Adjustment)
        .where(Adjustment.item_id == item_id, Adjustment.location_id == location_id)
    )
    starting = change > 0 and not held.one()
    adjustment = Adjustment(
        item_id=item_id,
        location_id=location_id,
        kind=AdjustmentKind.STARTING if starting else AdjustmentKind.MANUAL,
        dated_on=_now().date(),
        change=change,
        unit_cost=(unit_cost or Decimal(0)) if starting else Decimal(0),
        reason_id=None if starting else await _synced_reason(db),
    )
    with suppress(StockRefused):
        # Refused for a material that carries no stock, which is left alone.
        await stock.adjust(db, adjustment)


async def _stock(run: _Sync) -> None:
    await run.progress(SyncStep.STOCK, None, None)
    levels = {level.inventory_item_id: level for level in await run.connector.stock_levels()}
    async with run.session() as db:
        store = await db.get_one(Store, run.store_id)
        targets = sorted((await _targets(db, store, levels)).items())
        await run.progress(SyncStep.STOCK, 0, len(targets))
        for done, ((item_id, location_id), (target, unit_cost)) in enumerate(targets, 1):
            if await db.get(Item, item_id) is not None:
                await _set(db, item_id, location_id, target, unit_cost)
            if done % AT_ONCE == 0 or done == len(targets):
                push.forget_noted(db)
                await db.commit()
                await run.progress(SyncStep.STOCK, done, len(targets))


async def _finish(run: _Sync) -> None:
    await run.progress(SyncStep.FINISH, None, None)
    async with run.session() as db:
        store = await db.get_one(Store, run.store_id)
        await push.drop_queued(db, store)
        await push.baseline(db, store)
        push.forget_noted(db)
        await db.commit()
    await run.connector.subscribe(str(run.job.args["address"]))
    async with run.session() as db:
        store = await db.get_one(Store, run.store_id)
        store.imported_at = datetime.fromisoformat(run.state["orders_from"])
        db.add(store)
        await db.commit()


#: Each step and its work, in order.
_STEPS: list[tuple[SyncStep, Callable[[_Sync], Awaitable[None]]]] = [
    (SyncStep.LOCATIONS, _locations),
    (SyncStep.PRODUCTS, _products),
    (SyncStep.FIELDS, _fields),
    (SyncStep.ORDERS, _orders),
    (SyncStep.STOCK, _stock),
    (SyncStep.FINISH, _finish),
]


async def _connector(job: JobContext, store: Store, *, synced: bool = False) -> Connector:
    async with job.plugin.database.session() as db:
        organization = await organizations.get(db, job.organization_id)
    if organization is None or organization.read_only:
        raise JobError(ErrorCode.READ_ONLY.value, retry=False)
    try:
        connector = await channels.connect(job.plugin, organization.community_ref, store.channel)
    except ConnectRefused as refused:
        raise JobError(refused.code.value) from None
    except (InitiativeRefused, InitiativeUnavailable):
        raise JobError(PushError.INITIATIVE_UNAVAILABLE.value) from None
    if connector is None or not store.connected:
        raise JobError(SyncError.STORE_NOT_CONNECTED.value, retry=False)
    if synced and store.imported_at is None:
        raise JobError(store_fields.StoreFieldError.STORE_NOT_SYNCED.value, retry=False)
    return connector


async def run(job: JobContext) -> None:
    """Run a store's sync from the step its checkpoint says is next."""
    store_id = int(job.args["store_id"])
    async with job.plugin.database.organization_session(job.organization_id) as db:
        store = await db.get(Store, store_id)
    if store is None:
        raise JobError(SyncError.STORE_NOT_CONNECTED.value, retry=False)
    state = dict(job.checkpoint or {})
    current = _Sync(job, store_id, await _connector(job, store), state)
    for index, (_, work) in enumerate(_STEPS):
        if index < int(state.get("next", 0)):
            continue
        try:
            await work(current)
        except ConnectRefused as refused:
            raise JobError(refused.code.value) from None
        state["next"] = index + 1
        await job.save_checkpoint(state)


class FieldsStep(StrEnum):
    """A fields job's steps: codes pages translate."""

    READ = "store_fields_read"
    SEND = "store_fields_send"


async def run_fields(job: JobContext) -> None:
    """Read in the fields a fields job names, then send what Hoard works out
    where it differs from the store: every such value where the job asks,
    else those Hoard's own changed."""
    store_id = int(job.args["store_id"])
    async with job.plugin.database.organization_session(job.organization_id) as db:
        store = await db.get(Store, store_id)
    if store is None:
        raise JobError(SyncError.STORE_NOT_CONNECTED.value, retry=False)
    connector = await _connector(job, store, synced=True)

    def session() -> WriteSession:
        return job.plugin.database.write_session(job.organization_id, actor=job.started_by)

    async def progress(done: int, total: int) -> None:
        await job.progress(FieldsStep.READ.value, done, total)

    try:
        if job.args.get("read") and not (job.checkpoint or {}).get("read"):
            await store_fields.read_in(session, store_id, connector, job.args["read"], progress)
            await job.save_checkpoint({"read": True})
        await job.progress(FieldsStep.SEND.value)
        await store_fields.send(session, store_id, connector, bool(job.args.get("send")))
    except ConnectRefused as refused:
        raise JobError(refused.code.value) from None


async def unsync(db: WriteSession, store: Store, connector: Connector | None, address: str) -> None:
    """Remove a store's sync: its webhook subscriptions, through ``connector``
    where its connection holds one, and its waiting pushes go, and it is
    pending again. Everything the sync brought in stays."""
    if connector is not None:
        try:
            await connector.unsubscribe(address)
        except ConnectRefused as refused:
            # Deliveries to a pending store are not acted on.
            logger.info("could not unsubscribe from a %s store: %s", store.channel.value, refused)
    await push.drop_queued(db, store)
    store.imported_at = None
    db.add(store)
    await db.flush()
