"""Store orders in: the nudges a store's webhooks leave, worked by a worker
the app's lifespan runs, each refetching its record from the store; orders
copied in, bundles split into their parts, claiming stock once paid; the
store's fulfillments recorded as shipments, their lots assumed; cancellations;
the import of the store's recent orders, which a store's sync runs
(``hoard.store_sync``); and the reconcile on Initiative's schedule, for
whatever a webhook missed. A store that is not synced is left alone.

A product's nudge, and the reconcile, keep each product's listings and copy
(``hoard.listings``), and take a product the store made since into Hoard; a
product the store no longer has loses its listings and copy.

A store's order is copied in whole each time (:func:`copy_order`): its lines
by the store's line ids, each sold item found through its listing, with how
many each sells now and how many were refunded; its refunded total, its
currency and its customer's number; and its status from the store's:
cancelled, shipped once the store fulfilled every line, pending until it is
paid, and open, committing what its lines sell, once it is. A copy older than
the one Hoard holds, by the store's ``updated_at``, is skipped. An order that
first claims its stock makes what it sells where auto-manufacture says so
(``hoard.automanufacture``).

A store's order is **wholesale** once the store says a company bought it
(its B2B), and Hoard keeps nothing else about the buyer.

A nudge refetches its order from the store. An order the store no longer
returns, one older than the window it keeps, is copied from the webhook's own
body instead, kept on the nudge, when Hoard holds it.

Each fulfillment the store has sent becomes a shipment from the location the
store sent it from, shipped on its date, each line drawing on the lot Hoard
assumes. A fulfillment sent before the store's sync read its orders is
history: it is copied as shipped, and moves no stock. A fulfillment the store cancels is
unshipped, and an order the store cancels takes back, as returned, whatever
its shipments moved; the store's own restock is not read.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import event, func, update
from sqlalchemy.orm import ORMExecuteState, Session
from sqlmodel import col, select

from hoard import (
    automanufacture,
    channels,
    listings,
    orders,
    recipes,
    shipments,
    store_fields,
    units,
)
from hoard.channels import (
    Connector,
    NudgeTopic,
    StoreFulfillment,
    StoreLine,
    StoreOrder,
    StoreProduct,
)
from hoard.records import (
    Adjustment,
    AdjustmentKind,
    Channel,
    Listing,
    Order,
    OrderLine,
    OrderStatus,
    Product,
    Recipe,
    ReturnLine,
    Shipment,
    ShipmentLine,
    ShipmentStatus,
    Store,
    StoreLocation,
    StoreNudge,
    Variation,
)
from hoard.shipments import ShipmentRefused
from hoard.stock import main_location
from seneschal_core import organizations
from seneschal_core.api import Plugin
from seneschal_core.db import WriteSession

logger = logging.getLogger(__name__)

#: How often every organization's nudges are looked at, besides after a webhook.
SWEEP_SECONDS = 60.0
#: The wait before a nudge is tried again, doubling with each attempt, up to
#: :data:`LONGEST_WAIT`.
FIRST_WAIT = timedelta(seconds=30)
LONGEST_WAIT = timedelta(hours=1)
#: The attempts after which a nudge is let go: the reconcile copies its record in.
MOST_ATTEMPTS = 8
#: How long a nudge the worker took waits before another takes it, its worker lost.
TAKEN_FOR = timedelta(minutes=10)
#: How many of a store's products are matched to listings at a time.
PRODUCTS_AT_ONCE = 50
#: What the codes of each channel's orders start with.
CODE_PREFIXES: dict[Channel, str] = {Channel.SHOPIFY: "SH", Channel.WOOCOMMERCE: "WC"}


def _now() -> datetime:
    return datetime.now(UTC)


async def _code(db: WriteSession, store: Store, found: StoreOrder) -> str:
    """The code of a store's order: its channel's prefix and the store's number
    for it, then its id where another order has that."""
    number = found.number.lstrip("#").strip() or found.id
    code = f"{CODE_PREFIXES[store.channel]}-{number}"
    taken = await db.exec(select(func.count()).select_from(Order).where(Order.code == code))
    return code if not taken.one() else f"{code}-{found.id}"


async def _mapped(db: WriteSession, store: Store) -> dict[str, int]:
    """The Hoard location each of the store's mapped locations sells from, by
    the store's id, in the order they were first seen."""
    found = await db.exec(
        select(StoreLocation.external_id, StoreLocation.location_id)
        .where(StoreLocation.store_id == store.id, col(StoreLocation.location_id).is_not(None))
        .order_by(col(StoreLocation.id))
    )
    return {external_id: location_id for external_id, location_id in found.all() if location_id}


async def _location(db: WriteSession, mapped: dict[str, int], found: StoreOrder) -> int:
    """Where a store's order ships from: its first fulfillment's location, the
    store's first mapped one, or the main location."""
    for fulfillment in found.fulfillments:
        if fulfillment.location_id in mapped:
            return mapped[fulfillment.location_id]
    if mapped:
        return next(iter(mapped.values()))
    return (await main_location(db)).id


class _Items:
    """The item each of a store's lines sells, through its listing: a product
    with no listing linked yet is read from the store and matched, once."""

    def __init__(self, db: WriteSession, store: Store, connector: Connector) -> None:
        self.db, self.store, self.connector = db, store, connector
        self.read: set[str] = set()

    async def _linked(self, line: StoreLine) -> int | None:
        found = await self.db.exec(
            select(Listing.item_id).where(
                Listing.store_id == self.store.id,
                Listing.product_id == line.product_id,
                col(Listing.variant_id).is_not_distinct_from(line.variant_id),
            )
        )
        return found.first()

    async def item(self, line: StoreLine) -> int | None:
        if line.product_id is None:
            return None
        item_id = await self._linked(line)
        if item_id is None and line.product_id not in self.read:
            self.read.add(line.product_id)
            product = await self.connector.product(line.product_id)
            if product is not None:
                await listings.adopt(self.db, self.store, [product])
                item_id = await self._linked(line)
        return item_id


def _status(found: StoreOrder) -> OrderStatus:
    if found.cancelled:
        return OrderStatus.CANCELLED
    if found.fulfilled:
        return OrderStatus.SHIPPED
    return OrderStatus.OPEN if found.paid else OrderStatus.PENDING


def _sent(fulfillment: StoreFulfillment) -> bool:
    return fulfillment.id is not None and fulfillment.shipped_at is not None


def _history(store: Store, fulfillment: StoreFulfillment) -> bool:
    """Whether the store sent a fulfillment before its sync read its orders:
    every one it sent, while the sync runs."""
    return fulfillment.shipped_at is not None and (
        store.imported_at is None or fulfillment.shipped_at < store.imported_at
    )


async def _order_lines(db: WriteSession, order: Order) -> list[OrderLine]:
    found = await db.exec(
        select(OrderLine)
        .where(OrderLine.order_id == order.id)
        .order_by(col(OrderLine.id))
        .execution_options(populate_existing=True)
    )
    return list(found.all())


def _follow(part: OrderLine, line: OrderLine) -> None:
    """Keep a part Hoard split from a bundle's line selling, and refunded, in
    step with the line."""
    part.current_quantity = units.scaled(part.quantity, orders.selling(line) / line.quantity)
    part.refunded_quantity = units.scaled(part.quantity, line.refunded_quantity / line.quantity)


async def _split(db: WriteSession, line: OrderLine) -> None:
    """Add the parts of ``line``, when it sells a bundle, as its recipe holds
    them, each a line at no price."""
    variation = await db.get(Variation, line.item_id)
    if variation is None or not (await db.get_one(Product, variation.product_id)).bundle:
        return
    recipe = (
        await db.exec(select(Recipe).where(Recipe.product_id == variation.product_id))
    ).first()
    if recipe is None:
        return
    for part in (await recipes.lines_of(db, [recipe.id]))[recipe.id]:
        made = OrderLine(
            organization_id=db.organization_id,
            order_id=line.order_id,
            item_id=part.item_id,
            quantity=units.scaled(line.quantity, units.each(part.quantity, recipe.batch_quantity)),
            price=Decimal(0),
            bundle_line_id=line.id,
        )
        _follow(made, line)
        db.add(made)
    await db.flush()


async def _copy_lines(db: WriteSession, items: _Items, order: Order, found: StoreOrder) -> bool:
    """Keep a line for each of the store's lines whose item is known, selling
    what the store says it sells now. A bundle whose parts the store sends has
    them as its parts; one whose parts it does not send is split as its recipe
    holds them. Answers whether what any line sells changed."""
    held = await _order_lines(db, order)
    before = {line.id: orders.selling(line) for line in held}
    by_store = {line.external_line_id: line for line in held if line.external_line_id}
    bundles = {line.bundle_id for line in found.lines if line.bundle_id}
    changed = False
    # A bundle's own line first, so that its parts can name it.
    for sold in sorted(found.lines, key=lambda sold: sold.id not in bundles):
        current = sold.quantity if sold.current_quantity is None else sold.current_quantity
        line = by_store.get(sold.id)
        if line is not None:
            line.price, line.selling_plan = sold.price, sold.selling_plan
            parts = [
                part
                for part in held
                if part.bundle_line_id == line.id and part.external_line_id is None
            ]
            if sold.quantity > 0 and sold.quantity != line.quantity:
                for part in parts:
                    part.quantity = units.scaled(part.quantity, sold.quantity / line.quantity)
                line.quantity = sold.quantity
            line.current_quantity, line.refunded_quantity = current, sold.refunded_quantity
            for part in parts:
                _follow(part, line)
            continue
        item_id = await items.item(sold)
        if item_id is None or sold.quantity <= 0:
            continue
        bundle = by_store.get(sold.bundle_id) if sold.bundle_id else None
        line = OrderLine(
            organization_id=db.organization_id,
            order_id=order.id,
            item_id=item_id,
            quantity=sold.quantity,
            current_quantity=current,
            refunded_quantity=sold.refunded_quantity,
            price=sold.price,
            external_line_id=sold.id,
            bundle_line_id=None if bundle is None else bundle.id,
            selling_plan=sold.selling_plan,
        )
        db.add(line)
        await db.flush()
        by_store[sold.id] = line
        changed = True
        if sold.id not in bundles:
            await _split(db, line)
    await db.flush()
    return changed or any(orders.selling(line) != before[line.id] for line in held)


async def _ship(
    db: WriteSession,
    store: Store,
    order: Order,
    mapped: dict[str, int],
    fulfillment: StoreFulfillment,
) -> None:
    """Keep the shipment recording one of the store's fulfillments: made and
    shipped once the store sent it, its tracking kept, and unshipped once the
    store cancels it, unless something has come back of it."""
    shipment = (
        await db.exec(
            select(Shipment).where(
                Shipment.order_id == order.id, Shipment.external_id == fulfillment.id
            )
        )
    ).first()
    if shipment is not None:
        shipment.tracking = fulfillment.tracking_number or shipment.tracking
        if fulfillment.cancelled and shipment.status is not ShipmentStatus.CANCELLED:
            if shipment.status is ShipmentStatus.SHIPPED:
                try:
                    await shipments.unpost(db, shipment)
                except ShipmentRefused:
                    return
            shipment.status = ShipmentStatus.CANCELLED
        await db.flush()
        return
    if fulfillment.cancelled or fulfillment.id is None or fulfillment.shipped_at is None:
        return
    held = await _order_lines(db, order)
    by_store = {line.external_line_id: line for line in held if line.external_line_id}
    shipment = Shipment(
        organization_id=db.organization_id,
        order_id=order.id,
        location_id=mapped.get(fulfillment.location_id or "", order.location_id),
        external_id=fulfillment.id,
        tracking=fulfillment.tracking_number,
    )
    db.add(shipment)
    await db.flush()
    sent: list[ShipmentLine] = []
    for line_id, quantity in fulfillment.lines.items():
        line = by_store.get(line_id)
        if line is None or quantity <= 0:
            continue
        parts = [part for part in held if part.bundle_line_id == line.id]
        each = [(part, units.scaled(part.quantity, quantity / line.quantity)) for part in parts]
        for sold, how_many in each or [(line, quantity)]:
            sent.append(
                ShipmentLine(
                    organization_id=db.organization_id,
                    shipment_id=shipment.id,
                    order_line_id=sold.id,
                    item_id=sold.item_id,
                    quantity=how_many,
                )
            )
    if not sent:
        await db.delete(shipment)
        await db.flush()
        return
    db.add_all(sent)
    await db.flush()
    shipped_on = fulfillment.shipped_at.date()
    if _history(store, fulfillment):
        shipment.status, shipment.shipped_on = ShipmentStatus.SHIPPED, shipped_on
        await db.flush()
    else:
        await shipments.post(db, shipment, shipped_on)


async def _take_back(db: WriteSession, order: Order, dated_on: date) -> None:
    """Record as returned whatever the order's shipments moved and has not yet
    come back. Shipments copied as history moved nothing."""
    moved = await db.exec(
        select(ShipmentLine)
        .join(Shipment, col(Shipment.id) == ShipmentLine.shipment_id)
        .join(Adjustment, col(Adjustment.shipment_line_id) == ShipmentLine.id)
        .where(
            Shipment.order_id == order.id,
            Shipment.status == ShipmentStatus.SHIPPED,
            Adjustment.kind == AdjustmentKind.SHIPMENT,
        )
        .order_by(col(ShipmentLine.id))
    )
    for line in moved.all():
        back = await db.exec(
            select(func.coalesce(func.sum(ReturnLine.quantity), 0)).where(
                ReturnLine.shipment_line_id == line.id
            )
        )
        left = line.quantity - Decimal(back.one())
        if left > 0:
            await shipments.record_return(db, line, left, dated_on, damaged=False)


async def copy_order(
    db: WriteSession, store: Store, connector: Connector, found: StoreOrder
) -> Order:
    """Copy in one of the store's orders as the store has it now, as its own
    order, from the location it ships from."""
    return await _copy(db, _Items(db, store, connector), found)


async def _copy(db: WriteSession, items: _Items, found: StoreOrder) -> Order:
    store = items.store
    order = (
        await db.exec(
            select(Order)
            .where(Order.store_id == store.id, Order.external_id == found.id)
            .with_for_update()
        )
    ).first()
    if (
        order is not None
        and order.store_updated_at is not None
        and found.updated_at is not None
        and found.updated_at < order.store_updated_at
    ):
        # An older copy than the one held: what it says has since changed.
        return order
    mapped = await _mapped(db, store)
    before = None if order is None else order.status
    if order is None:
        order = Order(
            organization_id=db.organization_id,
            code=await _code(db, store, found),
            store_id=store.id,
            external_id=found.id,
            location_id=await _location(db, mapped, found),
            ordered_on=found.ordered_at.date(),
            status=OrderStatus.PENDING,
        )
        db.add(order)
    order.external_number = found.number
    order.paid = found.paid
    order.shipping, order.tax, order.discount = found.shipping, found.tax, found.discount
    order.refunded, order.store_customer = found.refunded, found.customer
    currency = (found.currency or "").upper()
    order.currency = (
        currency if len(currency) == 3 and currency.isascii() and currency.isalpha() else None
    )
    order.store_updated_at = found.updated_at or order.store_updated_at
    order.wholesale = order.wholesale or found.company
    await db.flush()
    selling_changed = await _copy_lines(db, items, order, found)

    status = _status(found)
    live = any(_sent(sent) and not _history(store, sent) for sent in found.fulfillments)
    claims = before in (None, OrderStatus.PENDING) and (
        status is OrderStatus.OPEN or (status is OrderStatus.SHIPPED and live)
    )
    order.status = status
    await db.flush()
    if claims:
        await automanufacture.on_claimed(db, order)
    for fulfillment in found.fulfillments:
        if fulfillment.id is not None:
            await _ship(db, store, order, mapped, fulfillment)
    if status is OrderStatus.CANCELLED and before is not OrderStatus.CANCELLED:
        await db.execute(
            update(Shipment)
            .where(
                col(Shipment.order_id) == order.id,
                col(Shipment.status) == ShipmentStatus.PENDING,
            )
            .values(status=ShipmentStatus.CANCELLED)
        )
        await _take_back(db, order, (found.updated_at or _now()).date())
    if status is not before or selling_changed:
        # What the order commits changed, for its items' listings.
        for item_id in sorted({line.item_id for line in await _order_lines(db, order)}):
            await listings.note_changed(db, item_id)
    await db.flush()
    return order


async def _held(db: WriteSession, store: Store, external_id: str) -> bool:
    found = await db.exec(
        select(func.count())
        .select_from(Order)
        .where(Order.store_id == store.id, Order.external_id == external_id)
    )
    return bool(found.one())


async def _refetch(db: WriteSession, store: Store, connector: Connector, nudge: StoreNudge) -> None:
    """Read again from the store the record a nudge names, and keep what it
    says. An order the store no longer returns is kept as the webhook's body
    describes it, when Hoard holds it."""
    if nudge.topic == NudgeTopic.ORDER:
        found = await connector.order(nudge.external_id)
        if found is None and nudge.body is not None and await _held(db, store, nudge.external_id):
            found = connector.webhook_order(nudge.body)
        if found is not None and found.id == nudge.external_id:
            await copy_order(db, store, connector, found)
    elif nudge.topic == NudgeTopic.PRODUCT:
        product = await connector.product(nudge.external_id)
        if product is None:
            await listings.forget(db, store, nudge.external_id)
        else:
            await listings.adopt(db, store, [product])
            await store_fields.refresh(db, store, connector, [nudge.external_id])
    elif nudge.topic == NudgeTopic.INVENTORY:
        # The store changed its own count: each listing is pushed Hoard's again.
        linked = await db.exec(
            select(Listing.item_id)
            .where(
                Listing.store_id == store.id,
                Listing.inventory_item_id == nudge.external_id,
                col(Listing.item_id).is_not(None),
            )
            .distinct()
        )
        for item_id in linked.all():
            if item_id is not None:
                await listings.note_changed(db, item_id)


def _wait(attempts: int) -> timedelta:
    return min(FIRST_WAIT * 2 ** min(attempts, 20), LONGEST_WAIT)


class _Connectors:
    """The connector of each of an organization's stores, built once per pass."""

    def __init__(self, plugin: Plugin, community_ref: str) -> None:
        self.plugin, self.community_ref = plugin, community_ref
        self.built: dict[Channel, Connector | None] = {}

    async def get(self, channel: Channel) -> Connector | None:
        if channel not in self.built:
            self.built[channel] = await channels.connect(self.plugin, self.community_ref, channel)
        return self.built[channel]


async def _take(plugin: Plugin, organization_id: int) -> int | None:
    """Take the organization's next due nudge, so that a webhook arriving
    while it is worked leaves a new one rather than waiting; answers its id."""
    async with plugin.database.write_session(organization_id, actor=None) as db:
        nudge = (
            await db.exec(
                select(StoreNudge)
                .where(col(StoreNudge.done_at).is_(None), StoreNudge.next_attempt_at <= _now())
                .order_by(col(StoreNudge.next_attempt_at), col(StoreNudge.id))
                .limit(1)
                .with_for_update(skip_locked=True)
            )
        ).first()
        if nudge is None:
            return None
        nudge_id = nudge.id
        nudge.taken_at, nudge.next_attempt_at = _now(), _now() + TAKEN_FOR
        await db.commit()
        return nudge_id


async def _work_next(plugin: Plugin, organization_id: int, connectors: _Connectors) -> bool:
    """Work the organization's next due nudge; answers whether there was one.
    A store that is not synced has its nudges let go."""
    nudge_id = await _take(plugin, organization_id)
    if nudge_id is None:
        return False
    async with plugin.database.write_session(organization_id, actor=None) as db:
        nudge = await db.get_one(StoreNudge, nudge_id)
        try:
            store = await db.get(Store, nudge.store_id)
            connector = None
            if store is not None and store.connected and store.imported_at is not None:
                connector = await connectors.get(store.channel)
            if store is not None and connector is not None:
                await _refetch(db, store, connector, nudge)
            nudge.done_at = _now()
            await db.commit()
            return True
        except Exception:
            logger.warning("could not work store nudge %s", nudge_id, exc_info=True)
            await db.rollback()
        nudge = await db.get_one(StoreNudge, nudge_id, populate_existing=True)
        nudge.next_attempt_at = _now() + _wait(nudge.attempts)
        nudge.attempts += 1
        if nudge.attempts >= MOST_ATTEMPTS:
            nudge.done_at = _now()
        await db.commit()
        return True


async def work(plugin: Plugin, organization_id: int) -> None:
    """Work the organization's due nudges, oldest first. A community that may
    not change is left alone."""
    async with plugin.database.session() as db:
        organization = await organizations.get(db, organization_id)
    if organization is None or not organization.available or organization.read_only:
        return
    connectors = _Connectors(plugin, organization.community_ref)
    while await _work_next(plugin, organization_id, connectors):
        pass


class _Waker:
    """The organizations whose commits left nudges, and the signal that one did."""

    def __init__(self) -> None:
        self.due: set[int] = set()
        self.woken = asyncio.Event()


#: The running :func:`work_forever`'s waker, if one runs.
_waker: _Waker | None = None
#: Where a session notes the organization it left nudges for.
_NUDGED = "hoard.store_nudges"


@event.listens_for(WriteSession.sync_session_class, "do_orm_execute")
def _note_nudges(state: ORMExecuteState) -> None:
    table = getattr(state.statement, "table", None)
    if state.is_insert and getattr(table, "name", None) == StoreNudge.__tablename__:
        # The organization the write session was opened for.
        state.session.info[_NUDGED] = state.session.info.get("organization_id")


@event.listens_for(WriteSession.sync_session_class, "after_commit")
def _wake(session: Session) -> None:
    organization_id = session.info.pop(_NUDGED, None)
    if organization_id is not None and _waker is not None:
        _waker.due.add(organization_id)
        _waker.woken.set()


@event.listens_for(WriteSession.sync_session_class, "after_soft_rollback")
def _forget(session: Session, _transaction: Any) -> None:
    session.info.pop(_NUDGED, None)


async def _organizations(plugin: Plugin) -> set[int]:
    async with plugin.database.session() as db:
        found = await db.exec(select(organizations.Organization.id))
        return set(found.all())


async def work_forever(plugin: Plugin, *, sweep_seconds: float = SWEEP_SECONDS) -> None:
    """Work every organization's waiting nudges, again and again, until
    cancelled: an organization's as soon as a webhook leaves one there, and
    every organization's each ``sweep_seconds``."""
    global _waker
    waker = _waker = _Waker()
    swept: float | None = None
    try:
        while True:
            waker.woken.clear()
            due, waker.due = waker.due, set()
            if swept is None or time.monotonic() - swept >= sweep_seconds:
                swept = time.monotonic()
                try:
                    due |= await _organizations(plugin)
                except Exception:
                    logger.exception("could not list the organizations to work nudges for")
            for organization_id in sorted(due):
                try:
                    await work(plugin, organization_id)
                except Exception:
                    logger.exception("could not work organization %s's nudges", organization_id)
            with suppress(TimeoutError):
                async with asyncio.timeout(sweep_seconds):
                    await waker.woken.wait()
    finally:
        if _waker is waker:
            _waker = None


#: Told how many orders were copied in, out of how many.
Progress = Callable[[int, int], Awaitable[None]]


async def _copy_all(
    db: WriteSession,
    store: Store,
    connector: Connector,
    found: list[StoreOrder],
    progress: Progress | None = None,
) -> tuple[Store, bool]:
    """Copy in each order of ``found``, each committed on its own, telling
    ``progress`` every :data:`PRODUCTS_AT_ONCE`. One that fails is left for
    the next reconcile. Answers the store, as read again after a failure, and
    whether every order was copied."""
    items = _Items(db, store, connector)
    copied = True
    for done, order in enumerate(found, 1):
        try:
            await _copy(db, items, order)
            await db.commit()
        except Exception:
            logger.warning("could not copy in store order %s", order.id, exc_info=True)
            await db.rollback()
            copied = False
            items.store = await db.get_one(Store, store.id, populate_existing=True)
        if progress is not None and (done % PRODUCTS_AT_ONCE == 0 or done == len(found)):
            await progress(done, len(found))
    return items.store, copied


async def _adopt(
    db: WriteSession, store: Store, connector: Connector, products: list[StoreProduct]
) -> None:
    """Take the store's changed ``products`` in, with their mapped fields."""
    await listings.adopt(db, store, products)
    await store_fields.refresh(
        db, store, connector, [product.id for product in products if product.id is not None]
    )


async def reconcile(
    plugin: Plugin,
    organization_id: int,
    store: Store,
    connector: Connector,
    since: datetime | None,
) -> None:
    """Copy in the store's orders and products changed since ``since``, when
    the reconcile last succeeded, or since the store's own stamps. A store
    whose orders were never stamped as read has every order it lets Hoard read
    copied in again."""
    started = _now()
    async with plugin.database.write_session(organization_id, actor=None) as db:
        kept = await db.get_one(Store, store.id, populate_existing=True)
        products_since = since or kept.products_since
        orders_since = None if kept.orders_since is None else since or kept.orders_since
        batch = []
        async for product in connector.products(products_since):
            batch.append(product)
            if len(batch) >= PRODUCTS_AT_ONCE:
                await _adopt(db, kept, connector, batch)
                await db.commit()
                batch = []
        if batch:
            await _adopt(db, kept, connector, batch)
        kept.products_since = started
        await db.commit()
        changed = [order async for order in connector.orders(orders_since)]
        kept, copied = await _copy_all(db, kept, connector, changed)
        if copied:
            kept.orders_since = started
        await db.commit()


async def import_history(
    plugin: Plugin,
    organization_id: int,
    store: Store,
    connector: Connector,
    progress: Progress | None = None,
) -> None:
    """Copy in the orders of the window the store keeps, moving no stock but
    for what was sent once the store was synced, telling ``progress`` how
    many are copied. The reconcile reads on from when this began."""
    started = _now()
    found = [order async for order in connector.import_history()]
    async with plugin.database.write_session(organization_id, actor=None) as db:
        kept = await db.get_one(Store, store.id, populate_existing=True)
        kept, _ = await _copy_all(db, kept, connector, found, progress)
        kept.orders_since = kept.orders_since or started
        await db.commit()
