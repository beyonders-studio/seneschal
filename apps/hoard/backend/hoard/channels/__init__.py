"""Sales channels: what Hoard asks of a store, whichever channel it is on.

Each channel has a **connector** (:class:`Connector`), built by
:func:`connector_for` from the values the community's connection holds: what
an admin typed, and the managed values Hoard's ``after_connect`` answered
(``hoard.channels.hooks``). Initiative hands them over with the install's
configuration, the secrets decrypted, and :func:`connect` reads them there. A
connector holds no database session: callers record what it answers.

A connector:

* checks the connection (:meth:`Connector.check`) and keeps the store's
  webhook subscriptions, aimed at Initiative's address for Hoard;
* reads and edits the store's products live, and creates one, keeping no
  copy: the store's titles, descriptions, images, options, prices and
  visibility stay the store's;
* lists the store's locations, and sets what is available at each;
* reads the store's orders, with their fulfillments, and pushes a shipment
  back as a fulfillment;
* reads a webhook delivery Initiative checked and forwarded as a
  :class:`Nudge`: which record changed, which Hoard refetches; it reads none
  of the connection's values, so a connector built with none parses it;
* imports the orders of the window the store keeps (``history_days``).

A store refusing what Hoard asks raises :class:`ConnectRefused`, with a code
pages translate.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Protocol

from hoard.records import Channel
from seneschal_core.api import Plugin


class ConnectError(StrEnum):
    """Why a store refused what Hoard asked: codes pages translate."""

    #: The address is not the store's own, such as its ``myshopify.com`` one.
    STORE_ADDRESS_INVALID = "STORE_ADDRESS_INVALID"
    #: The store refused the credentials.
    STORE_CREDENTIALS_REFUSED = "STORE_CREDENTIALS_REFUSED"
    #: The store's app was not granted every permission Hoard needs; the
    #: refusal's detail names those missing.
    STORE_SCOPES_MISSING = "STORE_SCOPES_MISSING"
    #: The store did not answer, or answered with an error.
    STORE_UNREACHABLE = "STORE_UNREACHABLE"
    #: The store refused a webhook subscription.
    STORE_SUBSCRIPTION_REFUSED = "STORE_SUBSCRIPTION_REFUSED"
    #: Hoard has no connector for the channel yet.
    CHANNEL_UNSUPPORTED = "CHANNEL_UNSUPPORTED"


class ConnectRefused(Exception):
    """The store refused: ``code`` says why, and ``detail`` what, where a code
    names something, such as the permissions missing."""

    def __init__(self, code: ConnectError, detail: str | None = None) -> None:
        super().__init__(code.value if detail is None else f"{code.value}: {detail}")
        self.code = code
        self.detail = detail

    @property
    def reason(self) -> str:
        """The code, and its detail after ``": "``, as a store keeps it
        (:attr:`hoard.records.Store.last_check_error`)."""
        return str(self)


class NudgeTopic(StrEnum):
    """What a webhook says changed at the store."""

    #: An order, its payment, fulfillment, cancellation or refund.
    ORDER = "order"
    #: A product or one of its variants.
    PRODUCT = "product"
    #: The stock of an inventory item, changed in the store.
    INVENTORY = "inventory"


@dataclass(frozen=True)
class Capabilities:
    """What a channel's stores can do."""

    #: Stock is kept per location.
    multi_location: bool
    #: Setting stock can check what the store holds first.
    compare_and_set: bool
    #: A retried push is recognised as the same push.
    idempotency: bool
    #: The most stock settings one push carries.
    batch_size: int
    #: How many days back the store's orders can be read; None for all.
    history_days: int | None
    #: A bundle's parts arrive as lines of their own.
    bundles: bool
    #: An order can be fulfilled in parts on dates of their own, as a prepaid
    #: subscription is.
    scheduled_fulfillments: bool


@dataclass(frozen=True)
class Connected:
    """A connection the store accepted: the store's own address, what it calls
    itself, and the secret its webhooks are signed with."""

    shop_domain: str
    account_label: str | None
    signing_secret: str


@dataclass(frozen=True)
class StoreImage:
    url: str
    alt: str | None = None
    #: The store's id for it; None for one not yet in the store.
    id: str | None = None


@dataclass(frozen=True)
class StoreVariant:
    """One form of a store's product, by its options."""

    #: None for one not yet in the store.
    id: str | None
    title: str
    sku: str | None = None
    price: Decimal | None = None
    #: Each option's value, such as ``{"Size": "Large"}``.
    options: Mapping[str, str] = field(default_factory=dict)
    #: What the store keeps its stock under, where that is not the variant.
    inventory_item_id: str | None = None


@dataclass(frozen=True)
class StoreProduct:
    """A store's product as the store keeps it."""

    #: None for one not yet in the store.
    id: str | None
    title: str
    #: As the store keeps it: HTML.
    description: str | None = None
    #: The store's word for whether it sells it, such as ``active`` or ``draft``.
    status: str | None = None
    #: The names of its options, in order.
    options: Sequence[str] = ()
    variants: Sequence[StoreVariant] = ()
    images: Sequence[StoreImage] = ()
    #: The store's sales channels showing it, by the store's ids.
    published_on: Sequence[str] = ()
    updated_at: datetime | None = None


@dataclass(frozen=True)
class StoreLine:
    """A line of a store's order."""

    id: str
    title: str
    quantity: Decimal
    #: What one sold for.
    price: Decimal
    product_id: str | None = None
    variant_id: str | None = None
    sku: str | None = None
    #: The store's id for the bundle this line is a part of.
    bundle_id: str | None = None
    #: The selling plan of a subscription's line.
    selling_plan: str | None = None


@dataclass(frozen=True)
class StoreFulfillment:
    """Lines of a store's order sent from one of its locations, or due to be."""

    #: None for one not yet in the store.
    id: str | None
    #: The store's location it is sent from.
    location_id: str | None
    #: Each line sent, by the store's line id, and how many.
    lines: Mapping[str, Decimal]
    shipped_at: datetime | None = None
    #: When a scheduled part is due to be sent.
    due_on: date | None = None
    cancelled: bool = False
    tracking_number: str | None = None
    tracking_company: str | None = None
    tracking_url: str | None = None


@dataclass(frozen=True)
class StoreOrder:
    """A store's order. Its customer stays in the store: Hoard keeps its number."""

    id: str
    number: str
    ordered_at: datetime
    lines: Sequence[StoreLine]
    paid: bool = False
    cancelled: bool = False
    #: Whether every line was fulfilled.
    fulfilled: bool = False
    currency: str | None = None
    shipping: Decimal = Decimal(0)
    tax: Decimal = Decimal(0)
    discount: Decimal = Decimal(0)
    fulfillments: Sequence[StoreFulfillment] = ()
    updated_at: datetime | None = None


@dataclass(frozen=True)
class StockSet:
    """Setting what is available of a listing at one of the store's locations."""

    product_id: str
    variant_id: str | None
    inventory_item_id: str | None
    #: The store's location; None where the store has one.
    location_id: str | None
    quantity: int
    #: What Hoard last knew the store held, for a store that compares first.
    compare_quantity: int | None
    #: Makes a retry the same push.
    idempotency_key: str


@dataclass(frozen=True)
class PushResult:
    """What the store did with one :class:`StockSet`, by its key."""

    idempotency_key: str
    pushed: bool
    #: What the store holds, when it refused because that was not
    #: ``compare_quantity``.
    store_quantity: int | None = None
    #: Why it refused, otherwise.
    error: str | None = None


@dataclass(frozen=True)
class Nudge:
    """A webhook's word that a record changed at the store."""

    topic: NudgeTopic
    #: The store's id for the record.
    external_id: str


class Connector(Protocol):
    """A store, as Hoard asks things of it."""

    capabilities: Capabilities

    async def check(self) -> Connected:
        """Check the connection's values at the store, and the permissions the
        store's app was granted."""
        ...

    async def subscribe(self, address: str) -> None:
        """Make the webhook subscriptions Hoard needs that the store lacks,
        aimed at ``address``."""
        ...

    async def unsubscribe(self, address: str) -> None:
        """Remove the subscriptions aimed at ``address``."""
        ...

    def products(self, since: datetime | None = None) -> AsyncIterator[StoreProduct]:
        """Every product, or those changed since ``since``."""
        ...

    async def product(self, product_id: str) -> StoreProduct | None:
        """One product, or None when the store has none by that id."""
        ...

    async def save_product(self, product: StoreProduct) -> StoreProduct:
        """Write ``product``'s edits to the store, and answer it as the store
        keeps it now."""
        ...

    async def create_product(self, product: StoreProduct) -> StoreProduct:
        """Make ``product`` in the store, and answer it with the store's ids."""
        ...

    async def locations(self) -> dict[str, str]:
        """The store's locations: each one's name, by the store's id."""
        ...

    async def push_available(self, sets: Sequence[StockSet]) -> list[PushResult]:
        """Set what is available, at most ``capabilities.batch_size`` at a time."""
        ...

    def orders(self, since: datetime | None = None) -> AsyncIterator[StoreOrder]:
        """Every order the store lets Hoard read, or those changed since ``since``."""
        ...

    async def order(self, order_id: str) -> StoreOrder | None:
        """One order, or None when the store has none by that id."""
        ...

    async def push_fulfillment(self, order_id: str, fulfillment: StoreFulfillment) -> str:
        """Record a shipment at the store as a fulfillment of the order's lines,
        and answer the store's id for it."""
        ...

    def parse_webhook(self, headers: Mapping[str, str], body: str) -> Nudge | None:
        """What a delivery Initiative checked says changed, or None for one
        Hoard does not act on."""
        ...

    def import_history(self) -> AsyncIterator[StoreOrder]:
        """The orders of the window the store keeps, read in bulk."""
        ...


def _shopify(values: Mapping[str, Any]) -> Connector:
    from hoard.channels import shopify

    return shopify.connector(values)


#: How each channel's connector is built from its connection's values.
CONNECTORS: dict[Channel, Callable[[Mapping[str, Any]], Connector]] = {
    Channel.SHOPIFY: _shopify,
}


def connector_for(channel: Channel, values: Mapping[str, Any]) -> Connector:
    """The connector for ``channel``'s store, from its connection's values."""
    build = CONNECTORS.get(channel)
    if build is None:
        raise ConnectRefused(ConnectError.CHANNEL_UNSUPPORTED)
    return build(values)


async def connect(plugin: Plugin, community_ref: str, channel: Channel) -> Connector | None:
    """The connector for the community's store on ``channel``, from the values
    its connection holds now; None while it holds none."""
    config = await plugin.api.installation_config(community_ref)
    connections = config.get("connections")
    values = connections.get(channel.value) if isinstance(connections, dict) else None
    if not isinstance(values, dict) or not values:
        return None
    return connector_for(channel, values)
