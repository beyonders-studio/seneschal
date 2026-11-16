"""A store kept in memory, for tests: a :class:`~hoard.channels.Connector`
whose products, locations, stock and orders a test sets, and which records
what Hoard asked of it.

Its webhooks name what changed in an ``x-topic`` header (a
:class:`~hoard.channels.NudgeTopic`) and the record's id as the body's ``id``.
An order's webhook (:meth:`FakeConnector.order_webhook`) also says which of
the orders it delivered it describes, as the body's ``delivery``.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from hoard.channels import (
    Capabilities,
    Connected,
    ConnectError,
    ConnectRefused,
    FieldOwner,
    FieldValue,
    FieldValues,
    Nudge,
    NudgeTopic,
    PushResult,
    StockSet,
    StoreField,
    StoreFulfillment,
    StoreOrder,
    StoreProduct,
    StoreStock,
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
    #: What one of each inventory item cost, where the store keeps it.
    unit_costs: dict[str, Decimal] = field(default_factory=dict)
    shop_orders: dict[str, StoreOrder] = field(default_factory=dict)
    #: What :meth:`import_history` reads.
    history: list[StoreOrder] = field(default_factory=list)
    #: Each push asked for, and each fulfillment pushed with its order's id.
    pushes: list[StockSet] = field(default_factory=list)
    fulfillments: list[tuple[str, StoreFulfillment]] = field(default_factory=list)
    #: The orders its order webhooks described, by delivery.
    delivered: list[StoreOrder] = field(default_factory=list)
    #: The fields it defines for products and variants.
    shop_fields: list[StoreField] = field(default_factory=list)
    #: Each product's and variant's field values, by its id and ``namespace.key``.
    field_store: dict[str, dict[str, str]] = field(default_factory=dict)
    #: Each field value written to it.
    field_writes: list[FieldValue] = field(default_factory=list)
    #: Its customer segments' names, by id.
    shop_segments: dict[str, str] = field(default_factory=dict)
    #: The customers in each segment, by the segment's id.
    members: dict[str, set[str]] = field(default_factory=dict)
    #: The segments read whole, and the customers asked after one at a time.
    member_reads: list[str] = field(default_factory=list)
    membership_reads: list[tuple[str, str]] = field(default_factory=list)

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
        """Keep ``product`` as the store's, changed now."""
        self._checked()
        assert product.id in self.shop_products
        saved = replace(product, updated_at=datetime.now(UTC))
        self.shop_products[product.id] = saved
        return saved

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

    async def stock_levels(self) -> list[StoreStock]:
        self._checked()
        available: dict[str, dict[str, int]] = defaultdict(dict)
        for (item, location), quantity in self.stock.items():
            if location is not None:
                available[item][location] = quantity
        return [
            StoreStock(item, self.unit_costs.get(item), dict(available.get(item, {})))
            for item in sorted(set(available) | set(self.unit_costs))
        ]

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

    async def segments(self) -> dict[str, str]:
        self._checked()
        return dict(self.shop_segments)

    async def segment_members(self, segment_id: str) -> set[str]:
        self._checked()
        self.member_reads.append(segment_id)
        return set(self.members.get(segment_id, set()))

    async def in_segment(self, segment_id: str, customer: str) -> bool:
        self._checked()
        self.membership_reads.append((segment_id, customer))
        return customer in self.members.get(segment_id, set())

    def order_webhook(self, order: StoreOrder) -> tuple[dict[str, str], str]:
        """The headers and body of the webhook the store sends about
        ``order`` as it is now."""
        self.delivered.append(order)
        body = {"id": order.id, "delivery": len(self.delivered) - 1}
        return {"x-topic": NudgeTopic.ORDER.value}, json.dumps(body)

    def parse_webhook(self, headers: Mapping[str, str], body: str) -> Nudge | None:
        try:
            topic = NudgeTopic(headers.get("x-topic", ""))
            payload = json.loads(body)
            record = payload.get("id")
        except (ValueError, AttributeError):
            return None
        if record is None:
            return None
        kept = topic is NudgeTopic.ORDER and "delivery" in payload
        return Nudge(topic, str(record), payload if kept else None)

    def webhook_order(self, body: Mapping[str, Any]) -> StoreOrder | None:
        delivery = body.get("delivery")
        if isinstance(delivery, int) and 0 <= delivery < len(self.delivered):
            return self.delivered[delivery]
        return None

    async def import_history(self) -> AsyncIterator[StoreOrder]:
        self._checked()
        for order in list(self.history):
            yield order

    async def import_products(self) -> AsyncIterator[StoreProduct]:
        self._checked()
        for product in list(self.shop_products.values()):
            yield product

    async def fields(self) -> list[StoreField]:
        self._checked()
        return list(self.shop_fields)

    async def field_values(
        self, handles: Mapping[FieldOwner, Sequence[str]], product_ids: Sequence[str] | None
    ) -> FieldValues:
        self._checked()
        found: FieldValues = {}
        for product_id in self.shop_products if product_ids is None else product_ids:
            product = self.shop_products.get(product_id)
            if product is None:
                continue
            owners = [(product_id, FieldOwner.PRODUCT)]
            owners += [
                (variant.id, FieldOwner.VARIANT)
                for variant in product.variants
                if variant.id is not None and handles.get(FieldOwner.VARIANT)
            ]
            for owner_id, owner in owners:
                held = self.field_store.get(owner_id, {})
                found[owner_id] = {
                    handle: held[handle] for handle in handles.get(owner, ()) if handle in held
                }
        return found

    async def set_field_values(self, values: Sequence[FieldValue]) -> list[FieldValue]:
        self._checked()
        for value in values:
            self.field_writes.append(value)
            held = self.field_store.setdefault(value.owner_id, {})
            if value.value is None:
                held.pop(value.handle, None)
            else:
                held[value.handle] = value.value
        return list(values)
