"""Pushing what is available to each store: a push queued for each listing
whose item's stock changed, once the change commits, sent by a worker the
app's lifespan runs, retried with the store's compare-and-set where it has
one, and reconciled on Initiative's schedule. Each push is kept as the push
log (:class:`hoard.records.StockPush`).

* **Noting**: a change to an item's stock notes the item on its session
  (:func:`note`, through ``hoard.listings.note_changed``). Once the session
  commits, the running :func:`push_forever` queues a push for each of the
  item's listings at each of its store's mapped locations (:func:`queue`), in
  whole units of what is available there (``hoard.orders.available``), unless
  the store was last pushed that already. One push waits per listing and
  location at a time: a newer change sets the waiting one's quantity.
* **Sending** (:func:`send`): each due push is sent with what is available
  when it is sent, and what the store was last pushed as the quantity it
  compares first. A store holding something else answers what it holds,
  which the push compares next time, after a wait in which Hoard may copy in
  the order that changed it. A push the store does not take is tried again
  later, waiting longer each time, and given up on after
  :data:`MAX_ATTEMPTS`; its error says why.
* **Reconciling** (:func:`reconcile`), on Initiative's schedule: the store's
  locations are read again, and each listing pushed where what is available
  is not what the store was last pushed.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import time
from collections import defaultdict
from collections.abc import Iterable, Mapping
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from decimal import ROUND_FLOOR, Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import event, text
from sqlalchemy.orm import Session
from sqlmodel import col, select

from hoard import channels, orders
from hoard.channels import ConnectError, Connector, ConnectRefused, StockSet
from hoard.records import Listing, PushStatus, StockPush, Store, StoreLocation
from seneschal_core import organizations
from seneschal_core.api import Plugin
from seneschal_core.db import WriteSession
from seneschal_core.initiative_api import InitiativeRefused, InitiativeUnavailable

logger = logging.getLogger(__name__)


class PushError(StrEnum):
    """Why a push was not taken, beside the store's own refusals
    (:class:`hoard.channels.ConnectError`): codes pages translate."""

    #: The store's connection was cleared.
    STORE_NOT_CONNECTED = "STORE_NOT_CONNECTED"
    #: The listing no longer sells an item.
    LISTING_UNLINKED = "LISTING_UNLINKED"
    #: The store's location no longer sells a Hoard location's stock.
    LOCATION_UNMAPPED = "LOCATION_UNMAPPED"
    #: The store held something else than Hoard last pushed it.
    STORE_HOLDS_OTHER = "STORE_HOLDS_OTHER"
    #: Initiative could not hand over the store's connection.
    INITIATIVE_UNAVAILABLE = "INITIATIVE_UNAVAILABLE"


#: How often every organization's due pushes are sent, besides after a commit.
SWEEP_SECONDS = 60.0
#: The wait before a push is tried again, doubling with each attempt, up to
#: :data:`LONGEST_WAIT`.
FIRST_WAIT = timedelta(seconds=30)
LONGEST_WAIT = timedelta(hours=6)
#: The attempts a push is given before it is given up on.
MAX_ATTEMPTS = 10
#: The lock one process holds while it sends an organization's pushes.
_PUSH_LOCK = "SELECT pg_try_advisory_xact_lock(hashtext('hoard.stock_pushes'), :organization)"
#: Where a session notes the items whose stock it changed, by organization.
_NOTED = "hoard.noted_items"


def note(db: WriteSession, item_id: int) -> None:
    """Note that ``db`` changed an item's stock, for its listings to be pushed
    once it commits."""
    noted: dict[int, set[int]] = db.info.setdefault(_NOTED, {})
    noted.setdefault(db.organization_id, set()).add(item_id)


def whole(quantity: Decimal) -> int:
    """What a store is told it may sell of ``quantity``: whole units, none below nothing."""
    return max(0, int(quantity.to_integral_value(ROUND_FLOOR)))


def _key() -> str:
    return secrets.token_hex(16)


def _wait(attempts: int) -> timedelta:
    return min(FIRST_WAIT * 2 ** min(attempts, 20), LONGEST_WAIT)


async def _last_pushed(
    db: WriteSession, listing_id: int, store_location_id: int
) -> StockPush | None:
    found = await db.exec(
        select(StockPush)
        .where(
            StockPush.listing_id == listing_id,
            StockPush.store_location_id == store_location_id,
            StockPush.status == PushStatus.PUSHED,
        )
        .order_by(col(StockPush.pushed_at).desc(), col(StockPush.id).desc())
        .limit(1)
    )
    return found.first()


async def _queue_one(
    db: WriteSession, listing_id: int, store_location_id: int, quantity: int
) -> StockPush | None:
    waiting = (
        await db.exec(
            select(StockPush)
            .where(
                StockPush.listing_id == listing_id,
                StockPush.store_location_id == store_location_id,
                StockPush.status == PushStatus.QUEUED,
            )
            .order_by(col(StockPush.id))
            .limit(1)
        )
    ).first()
    if waiting is not None:
        if waiting.quantity != quantity:
            _set_quantity(waiting, quantity)
            db.add(waiting)
        return waiting
    last = await _last_pushed(db, listing_id, store_location_id)
    if last is not None and last.quantity == quantity:
        return None
    queued = StockPush(
        organization_id=db.organization_id,
        listing_id=listing_id,
        store_location_id=store_location_id,
        quantity=quantity,
        compare_quantity=None if last is None else last.quantity,
        idempotency_key=_key(),
    )
    db.add(queued)
    return queued


def _set_quantity(stock_push: StockPush, quantity: int) -> None:
    """Change what a waiting push sets; one already tried is a new push to the store."""
    stock_push.quantity = quantity
    if stock_push.attempts:
        stock_push.idempotency_key = _key()


async def queue(
    db: WriteSession, item_ids: Iterable[int], *, store_id: int | None = None
) -> list[StockPush]:
    """Queue a push for each listing of ``item_ids`` in a connected store, or
    in ``store_id``'s alone, at each of the store's mapped locations, where
    what is available is not what the store was last pushed. Answers the
    pushes queued or changed."""
    ids = sorted(set(item_ids))
    if not ids:
        return []
    query = (
        select(Listing)
        .join(Store, col(Store.id) == Listing.store_id)
        .where(col(Listing.item_id).in_(ids), col(Store.connected))
        .order_by(col(Listing.id))
    )
    if store_id is not None:
        query = query.where(Listing.store_id == store_id)
    found = list((await db.exec(query)).all())
    mapped: dict[int, list[StoreLocation]] = defaultdict(list)
    located = await db.exec(
        select(StoreLocation)
        .where(
            col(StoreLocation.store_id).in_({listing.store_id for listing in found}),
            col(StoreLocation.location_id).is_not(None),
        )
        .order_by(col(StoreLocation.id))
    )
    for store_location in located.all():
        mapped[store_location.store_id].append(store_location)
    available: dict[tuple[int, int], int] = {}
    queued: list[StockPush] = []
    for listing in found:
        assert listing.item_id is not None
        for store_location in mapped[listing.store_id]:
            assert store_location.location_id is not None
            where = (listing.item_id, store_location.location_id)
            if where not in available:
                available[where] = whole(await orders.available(db, *where))
            made = await _queue_one(db, listing.id, store_location.id, available[where])
            if made is not None:
                queued.append(made)
    await db.flush()
    return queued


def _pushed(stock_push: StockPush, now: datetime) -> None:
    stock_push.status = PushStatus.PUSHED
    stock_push.attempts += 1
    stock_push.pushed_at = now
    stock_push.error = None


def _later(stock_push: StockPush, error: str, now: datetime) -> None:
    stock_push.attempts += 1
    stock_push.error = error
    if stock_push.attempts >= MAX_ATTEMPTS:
        stock_push.status = PushStatus.FAILED
    else:
        stock_push.next_attempt_at = now + _wait(stock_push.attempts - 1)


def _give_up(stock_push: StockPush, error: PushError) -> None:
    stock_push.status = PushStatus.FAILED
    stock_push.error = error.value


#: A due push, the listing it sets and the store location it sets it at.
_Due = tuple[StockPush, Listing, StoreLocation]


async def _connector(
    plugin: Plugin, community_ref: str, store: Store, due: list[_Due], now: datetime
) -> Connector | None:
    """The store's connector, or None once each push is put off or given up on."""
    if store.connected:
        try:
            connector = await channels.connect(plugin, community_ref, store.channel)
        except ConnectRefused as refused:
            for stock_push, _, _ in due:
                _later(stock_push, refused.reason, now)
            return None
        except (InitiativeRefused, InitiativeUnavailable):
            for stock_push, _, _ in due:
                _later(stock_push, PushError.INITIATIVE_UNAVAILABLE.value, now)
            return None
        if connector is not None:
            return connector
    for stock_push, _, _ in due:
        _give_up(stock_push, PushError.STORE_NOT_CONNECTED)
    return None


async def _ready(db: WriteSession, due: list[_Due]) -> list[_Due]:
    """The pushes that may be sent, each setting what is available now."""
    ready = []
    for stock_push, listing, store_location in due:
        if listing.item_id is None:
            _give_up(stock_push, PushError.LISTING_UNLINKED)
        elif store_location.location_id is None:
            _give_up(stock_push, PushError.LOCATION_UNMAPPED)
        else:
            quantity = whole(
                await orders.available(db, listing.item_id, store_location.location_id)
            )
            if quantity != stock_push.quantity:
                _set_quantity(stock_push, quantity)
            ready.append((stock_push, listing, store_location))
    return ready


async def _send_batch(connector: Connector, batch: list[_Due], now: datetime) -> None:
    compares = connector.capabilities.compare_and_set
    sets = [
        StockSet(
            product_id=listing.product_id,
            variant_id=listing.variant_id,
            inventory_item_id=listing.inventory_item_id,
            location_id=store_location.external_id,
            quantity=stock_push.quantity,
            compare_quantity=stock_push.compare_quantity if compares else None,
            idempotency_key=stock_push.idempotency_key,
        )
        for stock_push, listing, store_location in batch
    ]
    try:
        results = await connector.push_available(sets)
    except ConnectRefused as refused:
        for stock_push, _, _ in batch:
            _later(stock_push, refused.reason, now)
        return
    answered = {result.idempotency_key: result for result in results}
    for stock_push, _, _ in batch:
        result = answered.get(stock_push.idempotency_key)
        if result is None:
            _later(stock_push, ConnectError.STORE_UNREACHABLE.value, now)
        elif result.pushed or result.store_quantity == stock_push.quantity:
            _pushed(stock_push, now)
        elif result.store_quantity is not None:
            stock_push.compare_quantity = result.store_quantity
            stock_push.idempotency_key = _key()
            _later(stock_push, PushError.STORE_HOLDS_OTHER.value, now)
        else:
            _later(stock_push, result.error or ConnectError.STORE_UNREACHABLE.value, now)


async def send(
    plugin: Plugin, organization_id: int, connectors: Mapping[int, Connector] | None = None
) -> None:
    """Send the organization's due pushes, each store's in batches as large as
    it takes, through ``connectors``' connector for a store where it holds one,
    else the one its connection holds now. A community that reaches nothing
    is sent nothing."""
    async with plugin.database.session() as db:
        organization = await organizations.get(db, organization_id)
    if organization is None or not organization.available:
        return
    async with plugin.database.write_session(organization_id, actor=None) as db:
        locked = await db.execute(text(_PUSH_LOCK), {"organization": organization_id})
        if not locked.scalar_one():
            return
        now = datetime.now(UTC)
        found = await db.exec(
            select(StockPush, Listing, StoreLocation, Store)
            .join(Listing, col(Listing.id) == StockPush.listing_id)
            .join(StoreLocation, col(StoreLocation.id) == StockPush.store_location_id)
            .join(Store, col(Store.id) == Listing.store_id)
            .where(StockPush.status == PushStatus.QUEUED, StockPush.next_attempt_at <= now)
            .order_by(col(StockPush.id))
        )
        stores: dict[int, Store] = {}
        due: dict[int, list[_Due]] = defaultdict(list)
        for stock_push, listing, store_location, store in found.all():
            stores[store.id] = store
            due[store.id].append((stock_push, listing, store_location))
        for store_id, pushes in due.items():
            connector = (connectors or {}).get(store_id) or await _connector(
                plugin, organization.community_ref, stores[store_id], pushes, now
            )
            if connector is None:
                continue
            ready = await _ready(db, pushes)
            size = max(1, connector.capabilities.batch_size)
            for start in range(0, len(ready), size):
                await _send_batch(connector, ready[start : start + size], now)
        await db.commit()


async def reconcile(
    plugin: Plugin, organization_id: int, store: Store, connector: Connector
) -> None:
    """Read the store's locations again, then push each of its listings what
    is available wherever it is not what the store was last pushed, or the
    last push was given up on."""
    # Imported here: listings notes changes through this module.
    from hoard import listings

    try:
        locations = await connector.locations()
    except ConnectRefused as refused:
        logger.info("could not read a %s store's locations: %s", store.channel.value, refused)
        return
    async with plugin.database.write_session(organization_id, actor=None) as db:
        await listings.keep_locations(db, store, locations)
        sold = await db.exec(
            select(Listing.item_id)
            .where(Listing.store_id == store.id, col(Listing.item_id).is_not(None))
            .distinct()
        )
        await queue(
            db, [item_id for item_id in sold.all() if item_id is not None], store_id=store.id
        )
        await db.commit()
    await send(plugin, organization_id, {store.id: connector})


async def queue_noted(plugin: Plugin, organization_id: int, item_ids: Iterable[int]) -> None:
    """Queue the pushes for items whose stock a commit changed."""
    async with plugin.database.write_session(organization_id, actor=None) as db:
        await queue(db, item_ids)
        await db.commit()


class _Pusher:
    """The items whose stock commits changed, by organization, and the signal that one did."""

    def __init__(self) -> None:
        self.noted: dict[int, set[int]] = {}
        self.woken = asyncio.Event()


#: The running :func:`push_forever`'s pusher, if one runs.
_pusher: _Pusher | None = None


@event.listens_for(WriteSession.sync_session_class, "after_commit")
def _wake(session: Session) -> None:
    noted: dict[int, set[int]] | None = session.info.pop(_NOTED, None)
    if noted and _pusher is not None:
        for organization_id, item_ids in noted.items():
            _pusher.noted.setdefault(organization_id, set()).update(item_ids)
        _pusher.woken.set()


@event.listens_for(WriteSession.sync_session_class, "after_soft_rollback")
def _forget(session: Session, _transaction: Any) -> None:
    session.info.pop(_NOTED, None)


async def _available(plugin: Plugin) -> set[int]:
    unavailable = [community_status.value for community_status in organizations.UNAVAILABLE]
    async with plugin.database.session() as db:
        found = await db.exec(
            select(organizations.Organization.id).where(
                col(organizations.Organization.community_status).not_in(unavailable)
            )
        )
        return set(found.all())


async def push_forever(plugin: Plugin, *, sweep_seconds: float = SWEEP_SECONDS) -> None:
    """Send every organization's queued pushes, again and again, until
    cancelled: an organization's as soon as a commit changes stock there,
    once its pushes are queued, and every organization's each ``sweep_seconds``."""
    global _pusher
    pusher = _pusher = _Pusher()
    swept: float | None = None
    try:
        while True:
            pusher.woken.clear()
            noted, pusher.noted = pusher.noted, {}
            due = set(noted)
            if swept is None or time.monotonic() - swept >= sweep_seconds:
                swept = time.monotonic()
                try:
                    due |= await _available(plugin)
                except Exception:
                    logger.exception("could not list the organizations to push stock for")
            for organization_id in sorted(due):
                try:
                    if organization_id in noted:
                        await queue_noted(plugin, organization_id, noted[organization_id])
                    await send(plugin, organization_id)
                except Exception:
                    logger.exception("could not push organization %s's stock", organization_id)
            with suppress(TimeoutError):
                async with asyncio.timeout(sweep_seconds):
                    await pusher.woken.wait()
    finally:
        if _pusher is pusher:
            _pusher = None
