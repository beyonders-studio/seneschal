"""Store orders in, against a store kept in memory: orders copied, claiming
stock once paid; bundles split; fulfillments shipped; cancellations; the
history import; the nudge worker; and the reconcile."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy.dialects.postgresql import insert
from sqlmodel import col, select

from hoard import channels, listings, shipments, sync
from hoard.channels import StoreFulfillment, StoreLine, StoreOrder, StoreProduct, StoreVariant
from hoard.channels.fake import FakeConnector
from hoard.orders import committed
from hoard.records import (
    Channel,
    Listing,
    Location,
    MatchedBy,
    Order,
    OrderLine,
    OrderStatus,
    Product,
    Recipe,
    RecipeLine,
    Shipment,
    ShipmentLine,
    ShipmentStatus,
    Store,
    StoreNudge,
    Variation,
)
from hoard.stock import add_product, on_hand
from seneschal_core.api import Plugin
from seneschal_core.db import Database, WriteSession
from seneschal_core.sessions import Sessions
from seneschal_core.testing import InitiativeStandIn, new_key_pem

#: Before any store in these tests was connected.
LONG_AGO = datetime(2026, 1, 5, 9, tzinfo=UTC)


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeConnector:
    """The store every Shopify connection reaches."""
    store = FakeConnector()
    monkeypatch.setitem(channels.CONNECTORS, Channel.SHOPIFY, lambda values: store)
    return store


@pytest.fixture
async def plugin(
    serving_database: Database, initiative_stand_in: InitiativeStandIn
) -> AsyncIterator[Plugin]:
    plugin = Plugin(
        database=serving_database,
        initiative=initiative_stand_in.initiative(),
        api=initiative_stand_in.api(),
        sessions=Sessions(app="hoard", private_key_pem=new_key_pem(), cookie_secure=False),
    )
    yield plugin
    await plugin.initiative.aclose()
    await plugin.api.aclose()


@pytest.fixture
async def store(db: WriteSession) -> Store:
    store = Store(
        organization_id=db.organization_id,
        channel=Channel.SHOPIFY,
        shop_domain="tea.myshopify.com",
        connected=True,
    )
    db.add(store)
    await db.flush()
    return store


Sold = Callable[..., Awaitable[Variation]]


@pytest.fixture
def sold(db: WriteSession, store: Store) -> Sold:
    """Add a product whose one variation the store lists as ``variant``."""

    async def sold(name: str, variant: str, **fields: Any) -> Variation:
        _, variation = await add_product(db, Product(name=name, **fields))
        db.add(
            Listing(
                organization_id=db.organization_id,
                store_id=store.id,
                product_id=f"p-{variant}",
                variant_id=variant,
                item_id=variation.id,
                matched_by=MatchedBy.SKU,
            )
        )
        await db.flush()
        return variation

    return sold


def _line(line_id: str, variant: str, quantity: str, price: str = "5", **fields: Any) -> StoreLine:
    return StoreLine(
        id=line_id,
        title=variant,
        quantity=Decimal(quantity),
        price=Decimal(price),
        product_id=f"p-{variant}",
        variant_id=variant,
        **fields,
    )


def _order(*lines: StoreLine, order_id: str = "o-1", **fields: Any) -> StoreOrder:
    return StoreOrder(
        id=order_id,
        number=fields.pop("number", "#1001"),
        ordered_at=datetime(2026, 11, 10, 9, tzinfo=UTC),
        lines=lines,
        **fields,
    )


def _sent(fulfillment_id: str, lines: dict[str, str], **fields: Any) -> StoreFulfillment:
    return StoreFulfillment(
        id=fulfillment_id,
        location_id="loc-1",
        lines={line_id: Decimal(quantity) for line_id, quantity in lines.items()},
        shipped_at=fields.pop("shipped_at", datetime.now(UTC) + timedelta(minutes=1)),
        **fields,
    )


async def _shipments(db: WriteSession, order: Order) -> list[Shipment]:
    found = await db.exec(
        select(Shipment)
        .where(Shipment.order_id == order.id)
        .order_by(col(Shipment.id))
        .execution_options(populate_existing=True)
    )
    return list(found.all())


async def _sent_lines(db: WriteSession, shipment: Shipment) -> list[tuple[int, Decimal]]:
    found = await db.exec(
        select(ShipmentLine.item_id, col(ShipmentLine.quantity))
        .where(ShipmentLine.shipment_id == shipment.id)
        .order_by(col(ShipmentLine.id))
    )
    return [(item_id, quantity) for item_id, quantity in found.all()]


async def _lines(db: WriteSession, order: Order) -> list[OrderLine]:
    found = await db.exec(
        select(OrderLine).where(OrderLine.order_id == order.id).order_by(col(OrderLine.id))
    )
    return list(found.all())


async def test_a_store_order_commits_once_paid_and_ships_with_its_fulfillment(
    db: WriteSession, main: Location, store: Store, fake: FakeConnector, sold: Sold, lot
):
    tea = await sold("Tea", "v-tea")
    await lot(tea.id, "T-1", "10")

    order = await sync.copy_order(db, store, fake, _order(_line("l-1", "v-tea", "2")))
    assert (order.code, order.external_number, order.status, order.location_id) == (
        "SH-1001",
        "#1001",
        OrderStatus.PENDING,
        main.id,
    )
    [line] = await _lines(db, order)
    assert (line.item_id, line.quantity, line.price, line.external_line_id) == (
        tea.id,
        2,
        5,
        "l-1",
    )
    assert await committed(db, tea.id) == 0

    paid = _order(_line("l-1", "v-tea", "2"), paid=True, shipping=Decimal(4))
    order = await sync.copy_order(db, store, fake, paid)
    assert (order.status, order.paid, order.shipping) == (OrderStatus.OPEN, True, 4)
    assert await committed(db, tea.id) == 2

    sent = _sent("f-1", {"l-1": "2"}, tracking_number="1Z99")
    fulfilled = replace(paid, fulfilled=True, fulfillments=[sent])
    for _ in range(2):
        order = await sync.copy_order(db, store, fake, fulfilled)
    assert order.status is OrderStatus.SHIPPED
    [shipment] = await _shipments(db, order)
    assert (shipment.status, shipment.external_id, shipment.tracking, shipment.shipped_on) == (
        ShipmentStatus.SHIPPED,
        "f-1",
        "1Z99",
        sent.shipped_at.date() if sent.shipped_at else None,
    )
    assert (await on_hand(db, tea.id), await committed(db, tea.id)) == (8, 0)
    [shipped] = await shipments.lines(db, shipment.id)
    assert shipped.lot_id is not None and shipped.assumed

    # The store cancels the fulfillment: what it moved comes back, and the
    # order is open again.
    cancelled = replace(paid, fulfillments=[replace(sent, cancelled=True)])
    order = await sync.copy_order(db, store, fake, cancelled)
    [shipment] = await _shipments(db, order)
    assert (order.status, shipment.status) == (OrderStatus.OPEN, ShipmentStatus.CANCELLED)
    assert (await on_hand(db, tea.id), await committed(db, tea.id)) == (10, 2)


async def test_a_line_of_an_unlisted_product_waits_for_its_listing(
    db: WriteSession, store: Store, fake: FakeConnector, sold: Sold
):
    tea = await sold("Tea", "v-tea")
    fake.shop_products["p-v-mug"] = StoreProduct(
        id="p-v-mug", title="Mug", variants=[StoreVariant(id="v-mug", title="Mug", sku="MUG")]
    )
    found = _order(_line("l-1", "v-tea", "1"), _line("l-2", "v-mug", "2"), paid=True)

    # The mug matches nothing: the order is copied without it, and its listing
    # waits to be linked.
    order = await sync.copy_order(db, store, fake, found)
    assert [line.item_id for line in await _lines(db, order)] == [tea.id]
    mug = (await db.exec(select(Listing).where(Listing.variant_id == "v-mug"))).one()
    assert mug.item_id is None

    # Linked, the next copy of the order brings the line in.
    _, made = await add_product(db, Product(name="Mug"))
    await listings.link(db, mug, made.id)
    order = await sync.copy_order(db, store, fake, found)
    assert [line.item_id for line in await _lines(db, order)] == [tea.id, made.id]


async def test_a_bundle_is_split_into_its_parts(
    db: WriteSession, store: Store, fake: FakeConnector, sold: Sold
):
    cup, saucer = await sold("Cup", "v-cup"), await sold("Saucer", "v-saucer")
    box = await sold("Tea set", "v-set", bundle=True)
    recipe = Recipe(organization_id=db.organization_id, product_id=box.product_id)
    db.add(recipe)
    await db.flush()
    for item, quantity in ((cup, "1"), (saucer, "2")):
        db.add(
            RecipeLine(
                organization_id=db.organization_id,
                recipe_id=recipe.id,
                item_id=item.id,
                quantity=Decimal(quantity),
            )
        )
    await db.flush()

    # Sold whole: Hoard splits it as its recipe holds it.
    whole = _order(_line("l-1", "v-set", "3", "30"), paid=True)
    order = await sync.copy_order(db, store, fake, whole)
    [bundle, *parts] = await _lines(db, order)
    assert (bundle.item_id, bundle.bundle_line_id) == (box.id, None)
    assert [(part.item_id, part.quantity, part.price, part.bundle_line_id) for part in parts] == [
        (cup.id, 3, 0, bundle.id),
        (saucer.id, 6, 0, bundle.id),
    ]

    # Sent as its parts: the store's parts are its parts.
    in_parts = _order(
        _line("p-2", "v-saucer", "2", "0", bundle_id="b-1"),
        _line("b-1", "v-set", "1", "10"),
        _line("p-1", "v-cup", "1", "0", bundle_id="b-1"),
        order_id="o-2",
        number="#1002",
        paid=True,
    )
    other = await sync.copy_order(db, store, fake, in_parts)
    [bundle, *parts] = await _lines(db, other)
    assert (bundle.item_id, bundle.external_line_id) == (box.id, "b-1")
    assert sorted((part.external_line_id, part.bundle_line_id) for part in parts) == [
        ("p-1", bundle.id),
        ("p-2", bundle.id),
    ]
    assert (await committed(db, cup.id), await committed(db, box.id)) == (4, 0)

    # A fulfillment of the bundle sends its parts.
    sent = replace(whole, fulfilled=True, fulfillments=[_sent("f-1", {"l-1": "2"})])
    order = await sync.copy_order(db, store, fake, sent)
    [shipment] = await _shipments(db, order)
    assert await _sent_lines(db, shipment) == [(cup.id, 2), (saucer.id, 4)]


async def test_cancelling_a_shipped_order_takes_back_what_it_moved(
    db: WriteSession,
    store: Store,
    fake: FakeConnector,
    sold: Sold,
    lot,
    monkeypatch: pytest.MonkeyPatch,
):
    returned: list[tuple[int, Decimal, date, bool]] = []

    async def record_return(
        _db: WriteSession, line: ShipmentLine, quantity: Decimal, dated_on: date, *, damaged: bool
    ) -> None:
        returned.append((line.item_id, quantity, dated_on, damaged))

    monkeypatch.setattr(shipments, "record_return", record_return)
    tea = await sold("Tea", "v-tea")
    await lot(tea.id, "T-1", "10")
    shipped = _order(
        _line("l-1", "v-tea", "3"),
        paid=True,
        fulfilled=True,
        fulfillments=[_sent("f-1", {"l-1": "3"})],
    )
    await sync.copy_order(db, store, fake, shipped)

    later = datetime(2026, 11, 20, tzinfo=UTC)
    order = await sync.copy_order(
        db, store, fake, replace(shipped, cancelled=True, updated_at=later)
    )

    assert order.status is OrderStatus.CANCELLED
    assert returned == [(tea.id, 3, date(2026, 11, 20), False)]


async def test_the_history_import_copies_orders_moving_no_stock_but_what_is_open(
    db: WriteSession,
    store: Store,
    fake: FakeConnector,
    sold: Sold,
    lot,
    plugin: Plugin,
    organization_id: int,
):
    tea = await sold("Tea", "v-tea")
    await lot(tea.id, "T-1", "10")
    await db.commit()
    before = _sent("f-1", {"l-1": "4"}, shipped_at=LONG_AGO)
    fake.history = [
        _order(_line("l-1", "v-tea", "4"), paid=True, fulfilled=True, fulfillments=[before]),
        _order(_line("l-2", "v-tea", "1"), order_id="o-2", number="#1002", paid=True),
        _order(_line("l-3", "v-tea", "2"), order_id="o-3", number="#1003"),
    ]

    await sync.import_history(plugin, organization_id, store, fake)

    found = await db.exec(
        select(Order).order_by(col(Order.code)).execution_options(populate_existing=True)
    )
    copied = found.all()
    assert [(order.code, order.status) for order in copied] == [
        ("SH-1001", OrderStatus.SHIPPED),
        ("SH-1002", OrderStatus.OPEN),
        ("SH-1003", OrderStatus.PENDING),
    ]
    [shipment] = await _shipments(db, copied[0])
    assert (shipment.status, shipment.shipped_on) == (ShipmentStatus.SHIPPED, LONG_AGO.date())
    # What was sent before the store was connected moved nothing.
    assert (await on_hand(db, tea.id), await committed(db, tea.id)) == (10, 1)
    await db.refresh(store)
    assert store.imported_at is not None
    assert store.orders_since is not None


async def test_the_worker_refetches_what_each_nudge_names(
    db: WriteSession,
    store: Store,
    fake: FakeConnector,
    sold: Sold,
    plugin: Plugin,
    organization_id: int,
    initiative_stand_in: InitiativeStandIn,
    community_ref: str,
    monkeypatch: pytest.MonkeyPatch,
):
    matched: list[list[str | None]] = []

    async def match(_db: WriteSession, _store: Store, products: Iterable[StoreProduct]) -> None:
        matched.append([product.id for product in products])

    monkeypatch.setattr(listings, "match", match)
    initiative_stand_in.connections[community_ref] = {"shopify": {"shop": "tea.myshopify.com"}}
    await sold("Tea", "v-tea")
    fake.shop_orders["o-1"] = _order(_line("l-1", "v-tea", "1"), paid=True)
    fake.shop_products["p-x"] = StoreProduct(id="p-x", title="Mugs")
    for topic, record in (("order", "o-1"), ("product", "p-x"), ("order", "o-gone")):
        db.add(
            StoreNudge(
                organization_id=db.organization_id,
                store_id=store.id,
                topic=topic,
                external_id=record,
            )
        )
    await db.commit()

    await sync.work(plugin, organization_id)

    [order] = (await db.exec(select(Order))).all()
    assert (order.external_id, order.status) == ("o-1", OrderStatus.OPEN)
    assert matched == [["p-x"]]
    found = await db.exec(select(StoreNudge).execution_options(populate_existing=True))
    assert all(nudge.done_at is not None for nudge in found.all())

    # A store that refuses leaves the nudge waiting, to be tried again later.
    fake.refuse = channels.ConnectError.STORE_UNREACHABLE
    db.add(
        StoreNudge(
            organization_id=db.organization_id, store_id=store.id, topic="order", external_id="o-1"
        )
    )
    await db.commit()
    await sync.work(plugin, organization_id)
    waiting = await db.exec(
        select(StoreNudge)
        .where(col(StoreNudge.done_at).is_(None))
        .execution_options(populate_existing=True)
    )
    [nudge] = waiting.all()
    assert nudge.attempts == 1
    assert nudge.next_attempt_at > datetime.now(UTC)


async def test_the_reconcile_copies_in_what_changed(
    db: WriteSession,
    store: Store,
    fake: FakeConnector,
    sold: Sold,
    plugin: Plugin,
    organization_id: int,
    monkeypatch: pytest.MonkeyPatch,
):
    matched: list[str | None] = []

    async def match(_db: WriteSession, _store: Store, products: Iterable[StoreProduct]) -> None:
        matched.extend(product.id for product in products)

    monkeypatch.setattr(listings, "match", match)
    await sold("Tea", "v-tea")
    await db.commit()
    since = datetime(2026, 11, 1, tzinfo=UTC)
    for number in range(3):
        fake.shop_products[f"p-{number}"] = StoreProduct(
            id=f"p-{number}", title="Tea", updated_at=since + timedelta(days=number - 1)
        )
    fake.shop_orders["o-1"] = _order(_line("l-1", "v-tea", "1"), paid=True, updated_at=since)
    fake.shop_orders["o-2"] = _order(
        _line("l-2", "v-tea", "1"),
        order_id="o-2",
        number="#1002",
        updated_at=since + timedelta(days=1),
    )

    await sync.reconcile(plugin, organization_id, store, fake, since)

    assert matched == ["p-2"]
    found = await db.exec(select(Order.external_id))
    assert found.all() == ["o-2"]
    await db.refresh(store)
    assert store.orders_since is not None and store.orders_since > since
    assert store.products_since == store.orders_since


async def test_a_nudge_left_wakes_the_worker_for_its_organization(
    serving_database: Database, store: Store, db: WriteSession, monkeypatch: pytest.MonkeyPatch
):
    await db.commit()
    waker = sync._Waker()
    monkeypatch.setattr(sync, "_waker", waker)
    async with serving_database.write_session(db.organization_id, actor=None) as other:
        await other.execute(
            insert(StoreNudge).values(
                organization_id=other.organization_id,
                store_id=store.id,
                topic="order",
                external_id="o-1",
            )
        )
        assert not waker.woken.is_set()
        await other.commit()

    assert waker.woken.is_set()
    assert waker.due == {db.organization_id}
