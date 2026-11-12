"""A store kept in memory, for tests: a :class:`~hoard.channels.Connector`
whose products, locations, stock and orders a test sets, and which records
what Hoard asked of it.

Its webhooks name what changed in an ``x-topic`` header (a
:class:`~hoard.channels.NudgeTopic`) and the record's id as the body's ``id``.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime

from hoard.channels import (
    Capabilities,
    Connected,
    ConnectError,
    ConnectRefused,
    Nudge,
    NudgeTopic,
    PushResult,
    StockSet,
    StoreFulfillment,
    StoreOrder,
    StoreProduct,
)

CAPABILITIES = Capabilities(
    multi_location=True,
    compare_and_set=True,
    idempotency=True,
    batch_size=250,
    history_days=60,
    bundles=True,
    scheduled_fulfillments=True,
)


@dataclass
class FakeConnector:
    shop_domain: str = "tea.myshopify.com"
    account_label: str | None = "Tea House"
    signing_secret: str = "signing-secret"
    capabilities: Capabilities = CAPABILITIES
    #: Refuse every call with this, once set.
    refuse: ConnectError | None = None
    #: The addresses webhooks are aimed at.
    subscriptions: set[str] = field(default_factory=set)
    shop_products: dict[str, StoreProduct] = field(default_factory=dict)
    shop_locations: dict[str, str] = field(default_factory=lambda: {"loc-1": "Shop"})
    #: What is available, by inventory item (else variant, else product) and location.
    stock: dict[tuple[str, str | None], int] = field(default_factory=dict)
    shop_orders: dict[str, StoreOrder] = field(default_factory=dict)
    #: What :meth:`import_history` reads.
    history: list[StoreOrder] = field(default_factory=list)
    #: Each push asked for, and each fulfillment pushed with its order's id.
    pushes: list[StockSet] = field(default_factory=list)
    fulfillments: list[tuple[str, StoreFulfillment]] = field(default_factory=list)

    def _checked(self) -> None:
        if self.refuse is not None:
            raise ConnectRefused(self.refuse)

    async def check(self) -> Connected:
        self._checked()
        return Connected(self.shop_domain, self.account_label, self.signing_secret)

    async def subscribe(self, address: str) -> None:
        self._checked()
        self.subscriptions.add(address)

    async def unsubscribe(self, address: str) -> None:
        self._checked()
        self.subscriptions.discard(address)

    async def products(self, since: datetime | None = None) -> AsyncIterator[StoreProduct]:
        self._checked()
        for product in list(self.shop_products.values()):
            if since is None or product.updated_at is None or product.updated_at > since:
                yield product

    async def product(self, product_id: str) -> StoreProduct | None:
        self._checked()
        return self.shop_products.get(product_id)

    async def save_product(self, product: StoreProduct) -> StoreProduct:
        self._checked()
        assert product.id in self.shop_products
        self.shop_products[product.id] = product
        return product

    async def create_product(self, product: StoreProduct) -> StoreProduct:
        self._checked()
        number = len(self.shop_products) + 1
        made = replace(
            product,
            id=f"product-{number}",
            variants=[
                replace(variant, id=f"variant-{number}-{n}", inventory_item_id=f"item-{number}-{n}")
                for n, variant in enumerate(product.variants, 1)
            ],
        )
        self.shop_products[f"product-{number}"] = made
        return made

    async def locations(self) -> dict[str, str]:
        self._checked()
        return dict(self.shop_locations)

    async def push_available(self, sets: Sequence[StockSet]) -> list[PushResult]:
        self._checked()
        results = []
        for stock_set in sets:
            self.pushes.append(stock_set)
            key = (
                stock_set.inventory_item_id or stock_set.variant_id or stock_set.product_id,
                stock_set.location_id,
            )
            held = self.stock.get(key, 0)
            if stock_set.compare_quantity is not None and stock_set.compare_quantity != held:
                results.append(PushResult(stock_set.idempotency_key, False, store_quantity=held))
                continue
            self.stock[key] = stock_set.quantity
            results.append(PushResult(stock_set.idempotency_key, True))
        return results

    async def orders(self, since: datetime | None = None) -> AsyncIterator[StoreOrder]:
        self._checked()
        for order in list(self.shop_orders.values()):
            if since is None or order.updated_at is None or order.updated_at > since:
                yield order

    async def order(self, order_id: str) -> StoreOrder | None:
        self._checked()
        return self.shop_orders.get(order_id)

    async def push_fulfillment(self, order_id: str, fulfillment: StoreFulfillment) -> str:
        self._checked()
        made = replace(fulfillment, id=f"fulfillment-{len(self.fulfillments) + 1}")
        self.fulfillments.append((order_id, made))
        return str(made.id)

    def parse_webhook(self, headers: Mapping[str, str], body: str) -> Nudge | None:
        try:
            topic = NudgeTopic(headers.get("x-topic", ""))
            record = json.loads(body).get("id")
        except (ValueError, AttributeError):
            return None
        return Nudge(topic, str(record)) if record is not None else None

    async def import_history(self) -> AsyncIterator[StoreOrder]:
        self._checked()
        for order in list(self.history):
            yield order
