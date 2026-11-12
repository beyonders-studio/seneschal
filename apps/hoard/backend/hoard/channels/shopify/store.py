"""Hoard's :class:`~hoard.channels.Connector` for a Shopify store.

* **Checking** asks for a fresh token, reads the store's own address and
  name, and refuses an address that is not the store's ``myshopify.com`` one
  or a store whose app lacks a permission in :data:`REQUIRED_SCOPES`.
* **Webhooks** are subscribed per topic in :data:`TOPICS`, aimed at the
  address Hoard is given. A delivery names the order, product or inventory
  item that changed, by its Admin API id.
* **Products** are read and written live: title, description, status,
  options, variants and their prices and SKUs, images, and the sales
  channels showing it.
* **Stock** is set as the ``available`` quantity of an inventory item at a
  location, one setting a call, each with its own idempotency key and the
  quantity Hoard last knew the store held.
* **Orders** carry their lines, the fulfillments sent, and, where the store
  granted a fulfillment order permission, the parts still to send: a
  scheduled part carries the fulfillment order's id and the day it is due.
* **The history import** reads the window the store keeps through a bulk
  query: each order with its lines, not its fulfillments.
* **Pushing a fulfillment** stays off (:data:`PUSH_FULFILLMENTS`) until the
  permission it needs can be held by a store's own app.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from collections.abc import AsyncIterator, Iterable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx2

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
    StoreImage,
    StoreLine,
    StoreOrder,
    StoreProduct,
    StoreVariant,
)
from hoard.channels.shopify import queries
from hoard.channels.shopify.client import Pacer, ShopifyClient, user_errors
from hoard.channels.tokens import TOKENS, Tokens

logger = logging.getLogger(__name__)

#: The permissions a store's app is granted for Hoard to connect.
REQUIRED_SCOPES = (
    "read_locations",
    "read_orders",
    "write_files",
    "write_inventory",
    "write_products",
    "write_publications",
)

#: Either lets Hoard read an order's fulfillment orders. Both need Shopify's
#: approval, so neither is required.
FULFILLMENT_ORDER_SCOPES = (
    "read_merchant_managed_fulfillment_orders",
    "write_merchant_managed_fulfillment_orders",
)

#: What pushing a fulfillment needs.
FULFILL_SCOPE = "write_merchant_managed_fulfillment_orders"

#: Whether shipments are pushed to the store as fulfillments. Off until a
#: store's own app can hold :data:`FULFILL_SCOPE`; merchants mark orders
#: fulfilled in the store meanwhile.
PUSH_FULFILLMENTS = False

#: The webhook topics Hoard subscribes to.
TOPICS = (
    "ORDERS_CREATE",
    "ORDERS_UPDATED",
    "ORDERS_CANCELLED",
    "REFUNDS_CREATE",
    "PRODUCTS_CREATE",
    "PRODUCTS_UPDATE",
    "PRODUCTS_DELETE",
    "INVENTORY_LEVELS_UPDATE",
)

#: How many days back the store lets Hoard read orders.
HISTORY_DAYS = 60

CAPABILITIES = Capabilities(
    multi_location=True,
    compare_and_set=True,
    idempotency=True,
    # One setting a call: each carries its own idempotency key.
    batch_size=1,
    history_days=HISTORY_DAYS,
    bundles=True,
    scheduled_fulfillments=True,
)

#: The most items one input list carries.
INPUT_LIMIT = 250

#: How long the history import waits between asking after its bulk query,
#: doubling from the first to the most.
BULK_POLL_SECONDS = (1.0, 30.0)

#: The fulfillment order statuses whose lines are still to send.
_OPEN_FULFILLMENT_ORDERS = frozenset({"OPEN", "SCHEDULED", "IN_PROGRESS", "ON_HOLD"})
#: Those a fulfillment can be made from now.
_FULFILLABLE = frozenset({"OPEN", "IN_PROGRESS"})

_WEBHOOK_RECORDS = {
    "orders": ("Order", "id", NudgeTopic.ORDER),
    "refunds": ("Order", "order_id", NudgeTopic.ORDER),
    "products": ("Product", "id", NudgeTopic.PRODUCT),
    "inventory_levels": ("InventoryItem", "inventory_item_id", NudgeTopic.INVENTORY),
}


class ShopifyConnector:
    """A Shopify store, through the app its owner made for it."""

    capabilities = CAPABILITIES

    def __init__(
        self,
        values: Mapping[str, Any],
        *,
        http: httpx2.AsyncClient | None = None,
        pacer: Pacer | None = None,
        tokens: Tokens = TOKENS,
        push_fulfillments: bool = PUSH_FULFILLMENTS,
    ) -> None:
        self.values = dict(values)
        self.push_fulfillments = push_fulfillments
        self._http = http
        self._pacer = pacer
        self._tokens = tokens
        self._client: ShopifyClient | None = None

    @property
    def client(self) -> ShopifyClient:
        """The store's API, from the connection's values: the address the
        store answered once checked, else the one typed."""
        if self._client is None:
            values = self.values
            self._client = ShopifyClient(
                str(values.get("shop_domain") or values.get("shop") or ""),
                str(values.get("client_id") or ""),
                str(values.get("client_secret") or ""),
                http=self._http,
                pacer=self._pacer,
                tokens=self._tokens,
            )
        return self._client

    def required_scopes(self) -> tuple[str, ...]:
        return (*REQUIRED_SCOPES, FULFILL_SCOPE) if self.push_fulfillments else REQUIRED_SCOPES

    async def check(self) -> Connected:
        client = self.client
        granted = await client.granted(fresh=True)
        shop = (await client.query(queries.SHOP)).get("shop") or {}
        domain = str(shop.get("myshopifyDomain") or "").lower()
        if domain != client.shop:
            raise ConnectRefused(ConnectError.STORE_ADDRESS_INVALID)
        missing = [scope for scope in self.required_scopes() if not _covers(granted, scope)]
        if missing:
            raise ConnectRefused(ConnectError.STORE_SCOPES_MISSING, ", ".join(missing))
        name = shop.get("name")
        return Connected(domain, name if isinstance(name, str) else None, client.client_secret)

    async def _subscriptions(self, address: str) -> list[dict[str, Any]]:
        return await self._pages(queries.WEBHOOKS, {"uri": address}, ("webhookSubscriptions",))

    async def subscribe(self, address: str) -> None:
        held = {subscription.get("topic") for subscription in await self._subscriptions(address)}
        for topic in TOPICS:
            if topic in held:
                continue
            data = await self.client.query(
                queries.WEBHOOK_CREATE, {"topic": topic, "input": {"uri": address}}
            )
            try:
                user_errors(data.get("webhookSubscriptionCreate"))
            except ConnectRefused as refused:
                raise ConnectRefused(
                    ConnectError.STORE_SUBSCRIPTION_REFUSED, f"{topic}: {refused.detail}"
                ) from None

    async def unsubscribe(self, address: str) -> None:
        for subscription in await self._subscriptions(address):
            data = await self.client.query(queries.WEBHOOK_DELETE, {"id": subscription["id"]})
            user_errors(data.get("webhookSubscriptionDelete"))

    # Products.

    async def products(self, since: datetime | None = None) -> AsyncIterator[StoreProduct]:
        search = None if since is None else f"updated_at:>'{_moment(since)}'"
        after: str | None = None
        while True:
            data = await self.client.query(queries.PRODUCTS, {"query": search, "after": after})
            page = data.get("products") or {}
            for node in page.get("nodes") or []:
                yield await self._product(node)
            after = _next(page)
            if after is None:
                return

    async def product(self, product_id: str) -> StoreProduct | None:
        node = (await self.client.query(queries.PRODUCT, {"id": product_id})).get("product")
        return None if node is None else await self._product(node)

    async def _product(self, node: dict[str, Any]) -> StoreProduct:
        variants = await self._rest(
            node.get("variants"), queries.VARIANTS_PAGE, {"id": node["id"]}, ("product", "variants")
        )
        return _store_product(node, variants)

    async def save_product(self, product: StoreProduct) -> StoreProduct:
        if product.id is None:
            raise ValueError("a product not yet in the store is made with create_product")
        current = await self.product(product.id)
        if current is None:
            raise ConnectRefused(ConnectError.STORE_UNREACHABLE, "the product is gone")
        data = await self.client.query(
            queries.PRODUCT_UPDATE,
            {"product": {"id": product.id, **_product_input(product)}, "media": _media(product)},
        )
        user_errors(data.get("productUpdate"))
        await self._save_images(product.id, current.images, product.images)
        await self._save_variants(product.id, current, product)
        await self._publish(product.id, current.published_on, product.published_on)
        saved = await self.product(product.id)
        if saved is None:
            raise ConnectRefused(ConnectError.STORE_UNREACHABLE, "the product is gone")
        return saved

    async def create_product(self, product: StoreProduct) -> StoreProduct:
        made: dict[str, Any] = {**_product_input(product)}
        if product.options and product.variants:
            made["productOptions"] = [
                {"name": name, "values": [{"name": value} for value in values]}
                for name, values in _option_values(product).items()
            ]
        data = await self.client.query(
            queries.PRODUCT_CREATE, {"product": made, "media": _media(product)}
        )
        payload = data.get("productCreate") or {}
        user_errors(payload)
        node = payload.get("product") or {}
        product_id = str(node["id"])
        standalone = [variant["id"] for variant in (node.get("variants") or {}).get("nodes") or []]
        if product.options and product.variants:
            for chunk in _chunks(
                [_variant_input(product, variant) for variant in product.variants]
            ):
                data = await self.client.query(
                    queries.VARIANTS_CREATE,
                    {
                        "productId": product_id,
                        "variants": chunk,
                        "strategy": "REMOVE_STANDALONE_VARIANT",
                    },
                )
                user_errors(data.get("productVariantsBulkCreate"))
        elif product.variants and standalone:
            only = _variant_input(product, product.variants[0])
            data = await self.client.query(
                queries.VARIANTS_UPDATE,
                {"productId": product_id, "variants": [{**only, "id": standalone[0]}]},
            )
            user_errors(data.get("productVariantsBulkUpdate"))
        await self._publish(product_id, (), product.published_on)
        created = await self.product(product_id)
        if created is None:
            raise ConnectRefused(ConnectError.STORE_UNREACHABLE, "the product is gone")
        return created

    async def _save_images(
        self, product_id: str, before: Sequence[StoreImage], after: Sequence[StoreImage]
    ) -> None:
        kept = {image.id: image for image in after if image.id is not None}
        changes: list[dict[str, Any]] = []
        for image in before:
            if image.id not in kept:
                changes.append({"id": image.id, "referencesToRemove": [product_id]})
            elif kept[image.id].alt != image.alt:
                changes.append({"id": image.id, "alt": kept[image.id].alt or ""})
        for chunk in _chunks(changes):
            data = await self.client.query(queries.FILES_UPDATE, {"files": chunk})
            user_errors(data.get("fileUpdate"))

    async def _save_variants(
        self, product_id: str, current: StoreProduct, product: StoreProduct
    ) -> None:
        before = {variant.id: variant for variant in current.variants if variant.id is not None}
        updates, creates = [], []
        for variant in product.variants:
            if variant.id is None:
                creates.append(_variant_input(product, variant))
            elif variant != before.get(variant.id):
                updates.append({**_variant_input(product, variant), "id": variant.id})
        kept = {variant.id for variant in product.variants}
        removed = [variant_id for variant_id in before if variant_id not in kept]
        for chunk in _chunks(updates):
            data = await self.client.query(
                queries.VARIANTS_UPDATE, {"productId": product_id, "variants": chunk}
            )
            user_errors(data.get("productVariantsBulkUpdate"))
        for chunk in _chunks(creates):
            data = await self.client.query(
                queries.VARIANTS_CREATE, {"productId": product_id, "variants": chunk}
            )
            user_errors(data.get("productVariantsBulkCreate"))
        for chunk in _chunks(removed):
            data = await self.client.query(
                queries.VARIANTS_DELETE, {"productId": product_id, "variantsIds": chunk}
            )
            user_errors(data.get("productVariantsBulkDelete"))

    async def _publish(self, product_id: str, before: Iterable[str], after: Iterable[str]) -> None:
        was, wanted = set(before), set(after)
        for document, name, publications in (
            (queries.PUBLISH, "publishablePublish", sorted(wanted - was)),
            (queries.UNPUBLISH, "publishableUnpublish", sorted(was - wanted)),
        ):
            for chunk in _chunks([{"publicationId": publication} for publication in publications]):
                data = await self.client.query(document, {"id": product_id, "input": chunk})
                user_errors(data.get(name))

    # Locations and stock.

    async def locations(self) -> dict[str, str]:
        nodes = await self._pages(queries.LOCATIONS, {}, ("locations",))
        return {str(node["id"]): str(node.get("name") or "") for node in nodes}

    async def push_available(self, sets: Sequence[StockSet]) -> list[PushResult]:
        return [await self._push(stock_set) for stock_set in sets]

    async def _push(self, stock_set: StockSet) -> PushResult:
        key = stock_set.idempotency_key
        if stock_set.inventory_item_id is None or stock_set.location_id is None:
            return PushResult(
                key, False, error="a Shopify listing needs its inventory item and location"
            )
        data = await self.client.query(
            queries.SET_AVAILABLE,
            {
                "idempotencyKey": key,
                "input": {
                    "name": "available",
                    "reason": "correction",
                    "referenceDocumentUri": f"hoard://stock-push/{key}",
                    "quantities": [
                        {
                            "inventoryItemId": stock_set.inventory_item_id,
                            "locationId": stock_set.location_id,
                            "quantity": stock_set.quantity,
                            # Sent as null to skip the check, which the store requires be said.
                            "changeFromQuantity": stock_set.compare_quantity,
                        }
                    ],
                },
            },
        )
        errors = (data.get("inventorySetQuantities") or {}).get("userErrors") or []
        if not errors:
            return PushResult(key, True)
        if any(error.get("code") == "CHANGE_FROM_QUANTITY_STALE" for error in errors):
            held = await self._available(stock_set.inventory_item_id, stock_set.location_id)
            return PushResult(key, False, store_quantity=held)
        return PushResult(
            key, False, error="; ".join(str(error.get("message")) for error in errors)
        )

    async def _available(self, item_id: str, location_id: str) -> int | None:
        data = await self.client.query(
            queries.AVAILABLE, {"item": item_id, "location": location_id}
        )
        level = (data.get("inventoryItem") or {}).get("inventoryLevel") or {}
        for quantity in level.get("quantities") or []:
            if quantity.get("name") == "available" and isinstance(quantity.get("quantity"), int):
                return quantity["quantity"]
        return None

    # Orders.

    async def orders(self, since: datetime | None = None) -> AsyncIterator[StoreOrder]:
        search = None if since is None else f"updated_at:>'{_moment(since)}'"
        after: str | None = None
        while True:
            data = await self.client.query(queries.ORDER_IDS, {"query": search, "after": after})
            page = data.get("orders") or {}
            for node in page.get("nodes") or []:
                order = await self.order(str(node["id"]))
                if order is not None:
                    yield order
            after = _next(page)
            if after is None:
                return

    async def order(self, order_id: str) -> StoreOrder | None:
        node = (await self.client.query(queries.ORDER, {"id": order_id})).get("order")
        if node is None:
            return None
        lines = await self._rest(
            node.get("lineItems"), queries.LINES_PAGE, {"id": order_id}, ("order", "lineItems")
        )
        fulfillments = [*await self._fulfillments(order_id), *await self._due(order_id)]
        return _store_order(node, lines, fulfillments)

    async def _fulfillments(self, order_id: str) -> list[StoreFulfillment]:
        data = await self.client.query(queries.FULFILLMENTS, {"id": order_id})
        made = []
        for node in (data.get("order") or {}).get("fulfillments") or []:
            if node.get("status") not in ("SUCCESS", "CANCELLED"):
                continue
            items = await self._rest(
                node.get("fulfillmentLineItems"),
                queries.FULFILLMENT_LINES_PAGE,
                {"id": node["id"]},
                ("fulfillment", "fulfillmentLineItems"),
            )
            tracking = next(iter(node.get("trackingInfo") or []), None) or {}
            made.append(
                StoreFulfillment(
                    id=str(node["id"]),
                    location_id=_id(node.get("location")),
                    lines=_quantities(items, "quantity"),
                    shipped_at=_datetime(node.get("createdAt")),
                    cancelled=node.get("status") == "CANCELLED",
                    tracking_number=tracking.get("number"),
                    tracking_company=tracking.get("company"),
                    tracking_url=tracking.get("url"),
                )
            )
        return made

    async def _fulfillment_orders(self, order_id: str) -> list[dict[str, Any]]:
        """The order's fulfillment orders, each with all its lines, where the
        store granted a permission that reads them; none otherwise."""
        granted = await self.client.granted()
        if not any(_covers(granted, scope) for scope in FULFILLMENT_ORDER_SCOPES):
            return []
        data = await self.client.query(queries.FULFILLMENT_ORDERS, {"id": order_id})
        found = []
        for node in ((data.get("order") or {}).get("fulfillmentOrders") or {}).get("nodes") or []:
            lines = await self._rest(
                node.get("lineItems"),
                queries.FULFILLMENT_ORDER_LINES_PAGE,
                {"id": node["id"]},
                ("fulfillmentOrder", "lineItems"),
            )
            found.append({**node, "lineItems": lines})
        return found

    async def _due(self, order_id: str) -> list[StoreFulfillment]:
        """The parts of the order still to send, by fulfillment order."""
        due = []
        for node in await self._fulfillment_orders(order_id):
            if node.get("status") not in _OPEN_FULFILLMENT_ORDERS:
                continue
            lines = {
                line_id: quantity
                for line_id, quantity in _quantities(node["lineItems"], "remainingQuantity").items()
                if quantity > 0
            }
            if not lines:
                continue
            fulfill_at = _datetime(node.get("fulfillAt"))
            due.append(
                StoreFulfillment(
                    id=str(node["id"]),
                    location_id=_id((node.get("assignedLocation") or {}).get("location")),
                    lines=lines,
                    due_on=None if fulfill_at is None else fulfill_at.date(),
                )
            )
        return due

    async def push_fulfillment(self, order_id: str, fulfillment: StoreFulfillment) -> str:
        if not self.push_fulfillments:
            raise ConnectRefused(ConnectError.STORE_SCOPES_MISSING, FULFILL_SCOPE)
        by_order = _allocate(await self._fulfillment_orders(order_id), fulfillment)
        tracking = {
            name: value
            for name, value in (
                ("number", fulfillment.tracking_number),
                ("company", fulfillment.tracking_company),
                ("url", fulfillment.tracking_url),
            )
            if value
        }
        data = await self.client.query(
            queries.FULFILLMENT_CREATE,
            {
                "fulfillment": {
                    "lineItemsByFulfillmentOrder": [
                        {
                            "fulfillmentOrderId": fulfillment_order_id,
                            "fulfillmentOrderLineItems": [
                                {"id": line_id, "quantity": quantity}
                                for line_id, quantity in lines.items()
                            ],
                        }
                        for fulfillment_order_id, lines in by_order.items()
                    ],
                    "notifyCustomer": False,
                    **({"trackingInfo": tracking} if tracking else {}),
                }
            },
        )
        payload = data.get("fulfillmentCreate") or {}
        user_errors(payload)
        return str((payload.get("fulfillment") or {})["id"])

    # Webhooks and the history import.

    def parse_webhook(self, headers: Mapping[str, str], body: str) -> Nudge | None:
        topic = headers.get("x-shopify-topic", "")
        record = _WEBHOOK_RECORDS.get(topic.split("/", 1)[0])
        if record is None:
            return None
        kind, field, nudge_topic = record
        try:
            payload = json.loads(body)
        except ValueError:
            return None
        if not isinstance(payload, dict):
            return None
        api_id = payload.get("admin_graphql_api_id")
        if (
            field == "id"
            and isinstance(api_id, str)
            and api_id.startswith(f"gid://shopify/{kind}/")
        ):
            return Nudge(nudge_topic, api_id)
        number = payload.get(field)
        if isinstance(number, bool) or not isinstance(number, int | str) or not str(number):
            return None
        return Nudge(nudge_topic, f"gid://shopify/{kind}/{number}")

    async def import_history(self) -> AsyncIterator[StoreOrder]:
        start = datetime.now(UTC) - timedelta(days=HISTORY_DAYS)
        search = f"created_at:>='{_moment(start)}'"
        data = await self.client.query(queries.BULK_RUN, {"query": queries.bulk_orders(search)})
        payload = data.get("bulkOperationRunQuery") or {}
        user_errors(payload)
        operation_id = str((payload.get("bulkOperation") or {})["id"])
        url = await self._finished(operation_id)
        if url is None:
            return
        orders: dict[str, dict[str, Any]] = {}
        lines: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
        async for line in self.client.lines(url):
            row = json.loads(line)
            parent = row.pop("__parentId", None)
            if parent is None:
                orders[str(row["id"])] = row
            else:
                lines[str(parent)].append(row)
        for order_id, node in orders.items():
            yield _store_order(node, lines.get(order_id, []), [])

    async def _finished(self, operation_id: str) -> str | None:
        """The address of a bulk query's result once it finished; None when
        it found nothing."""
        wait, longest = BULK_POLL_SECONDS
        while True:
            data = await self.client.query(queries.BULK_STATUS, {"id": operation_id})
            operation = data.get("bulkOperation") or {}
            status = operation.get("status")
            if status == "COMPLETED":
                url = operation.get("url")
                return url if isinstance(url, str) and url else None
            if status in ("FAILED", "CANCELED", "CANCELING", "EXPIRED"):
                raise ConnectRefused(
                    ConnectError.STORE_UNREACHABLE,
                    f"bulk query {str(status).lower()}: {operation.get('errorCode')}",
                )
            await self.client.pacer.sleep(wait)
            wait = min(wait * 2, longest)

    # Pages.

    async def _pages(
        self, document: str, variables: Mapping[str, Any], path: Sequence[str]
    ) -> list[dict[str, Any]]:
        """Every node of the connection at ``path``, page by page."""
        nodes: list[dict[str, Any]] = []
        after: str | None = None
        while True:
            page = _dig(await self.client.query(document, {**variables, "after": after}), path)
            nodes.extend(page.get("nodes") or [])
            after = _next(page)
            if after is None:
                return nodes

    async def _rest(
        self,
        first: object,
        document: str,
        variables: Mapping[str, Any],
        path: Sequence[str],
    ) -> list[dict[str, Any]]:
        """A connection's first page, and every page after it."""
        page = first if isinstance(first, dict) else {}
        nodes = list(page.get("nodes") or [])
        after = _next(page)
        while after is not None:
            page = _dig(await self.client.query(document, {**variables, "after": after}), path)
            nodes.extend(page.get("nodes") or [])
            after = _next(page)
        return nodes


def _covers(granted: frozenset[str], scope: str) -> bool:
    """Whether ``granted`` includes ``scope``; writing a resource includes
    reading it."""
    if scope in granted:
        return True
    resource = scope.removeprefix("read_")
    return resource != scope and f"write_{resource}" in granted


def _allocate(
    fulfillment_orders: Sequence[Mapping[str, Any]], fulfillment: StoreFulfillment
) -> dict[str, dict[str, int]]:
    """The fulfillment order lines that send ``fulfillment``'s lines, from the
    open fulfillment orders at its location, by fulfillment order."""
    open_lines = [
        (str(order["id"]), _id((order.get("assignedLocation") or {}).get("location")), line)
        for order in fulfillment_orders
        if order.get("status") in _FULFILLABLE
        for line in order.get("lineItems") or []
    ]
    by_order: dict[str, dict[str, int]] = defaultdict(dict)
    locations = set()
    for line_id, wanted in fulfillment.lines.items():
        left = int(wanted)
        for order_id, location_id, line in open_lines:
            if left <= 0:
                break
            if _id(line.get("lineItem")) != line_id:
                continue
            if fulfillment.location_id is not None and location_id != fulfillment.location_id:
                continue
            taken = min(left, int(line.get("remainingQuantity") or 0))
            if taken <= 0:
                continue
            by_order[order_id][str(line["id"])] = by_order[order_id].get(str(line["id"]), 0) + taken
            locations.add(location_id)
            left -= taken
        if left > 0:
            raise ConnectRefused(
                ConnectError.STORE_UNREACHABLE, f"nothing left to send of line {line_id}"
            )
    if len(locations) > 1:
        raise ConnectRefused(
            ConnectError.STORE_UNREACHABLE, "a fulfillment is sent from one location"
        )
    return dict(by_order)


def _store_product(node: Mapping[str, Any], variants: Sequence[Mapping[str, Any]]) -> StoreProduct:
    status = node.get("status")
    return StoreProduct(
        id=str(node["id"]),
        title=str(node.get("title") or ""),
        description=node.get("descriptionHtml"),
        status=status.lower() if isinstance(status, str) else None,
        options=[str(option.get("name")) for option in node.get("options") or []],
        variants=[
            StoreVariant(
                id=str(variant["id"]),
                title=str(variant.get("title") or ""),
                sku=variant.get("sku") or None,
                price=_decimal(variant.get("price")),
                options={
                    str(option.get("name")): str(option.get("value"))
                    for option in variant.get("selectedOptions") or []
                },
                inventory_item_id=_id(variant.get("inventoryItem")),
            )
            for variant in variants
        ],
        images=[
            StoreImage(
                url=str(media["image"]["url"]),
                alt=media.get("alt") or None,
                id=str(media["id"]),
            )
            for media in (node.get("media") or {}).get("nodes") or []
            if isinstance(media.get("image"), dict) and media["image"].get("url")
        ],
        published_on=[
            str(publication)
            for resource in (node.get("resourcePublicationsV2") or {}).get("nodes") or []
            if (publication := _id(resource.get("publication"))) is not None
        ],
        updated_at=_datetime(node.get("updatedAt")),
    )


def _product_input(product: StoreProduct) -> dict[str, Any]:
    fields: dict[str, Any] = {"title": product.title}
    if product.description is not None:
        fields["descriptionHtml"] = product.description
    if product.status is not None:
        fields["status"] = product.status.upper()
    return fields


def _media(product: StoreProduct) -> list[dict[str, Any]]:
    """The images not yet in the store, by the address the store copies them from."""
    return [
        {"originalSource": image.url, "alt": image.alt or "", "mediaContentType": "IMAGE"}
        for image in product.images
        if image.id is None
    ]


def _option_values(product: StoreProduct) -> dict[str, list[str]]:
    """Each option's values, in the order the variants name them."""
    values: dict[str, list[str]] = {name: [] for name in product.options}
    for variant in product.variants:
        for name in product.options:
            value = variant.options.get(name)
            if value is not None and value not in values[name]:
                values[name].append(value)
    return values


def _variant_input(product: StoreProduct, variant: StoreVariant) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    if variant.price is not None:
        fields["price"] = str(variant.price)
    if variant.sku is not None:
        fields["inventoryItem"] = {"sku": variant.sku}
    option_values = [
        {"optionName": name, "name": variant.options[name]}
        for name in product.options
        if name in variant.options
    ]
    if option_values:
        fields["optionValues"] = option_values
    return fields


def _store_order(
    node: Mapping[str, Any],
    lines: Sequence[Mapping[str, Any]],
    fulfillments: Sequence[StoreFulfillment],
) -> StoreOrder:
    return StoreOrder(
        id=str(node["id"]),
        number=str(node.get("name") or ""),
        ordered_at=_datetime(node.get("processedAt")) or datetime.now(UTC),
        lines=[
            StoreLine(
                id=str(line["id"]),
                title=str(line.get("name") or ""),
                # Excludes what was removed or refunded.
                quantity=Decimal(int(line.get("currentQuantity") or 0)),
                price=_money(line.get("originalUnitPriceSet")),
                product_id=_id(line.get("product")),
                variant_id=_id(line.get("variant")),
                sku=line.get("sku") or None,
                bundle_id=_id(line.get("lineItemGroup")),
                selling_plan=(line.get("sellingPlan") or {}).get("sellingPlanId"),
            )
            for line in lines
        ],
        paid=bool(node.get("fullyPaid")),
        cancelled=node.get("cancelledAt") is not None,
        fulfilled=node.get("displayFulfillmentStatus") == "FULFILLED",
        currency=node.get("currencyCode"),
        shipping=_money(node.get("totalShippingPriceSet")),
        tax=_money(node.get("totalTaxSet")),
        discount=_money(node.get("totalDiscountsSet")),
        fulfillments=list(fulfillments),
        updated_at=_datetime(node.get("updatedAt")),
    )


def _quantities(items: Iterable[Mapping[str, Any]], field: str) -> dict[str, Decimal]:
    """How many of each order line, summed across ``items``."""
    totals: dict[str, Decimal] = {}
    for item in items:
        line_id = _id(item.get("lineItem"))
        if line_id is not None:
            totals[line_id] = totals.get(line_id, Decimal(0)) + Decimal(int(item.get(field) or 0))
    return totals


def _chunks(items: Sequence[Any]) -> list[Sequence[Any]]:
    return [items[start : start + INPUT_LIMIT] for start in range(0, len(items), INPUT_LIMIT)]


def _dig(data: Mapping[str, Any], path: Sequence[str]) -> Mapping[str, Any]:
    for name in path:
        found = data.get(name)
        data = found if isinstance(found, dict) else {}
    return data


def _next(page: Mapping[str, Any]) -> str | None:
    info = page.get("pageInfo") or {}
    cursor = info.get("endCursor")
    return cursor if info.get("hasNextPage") and isinstance(cursor, str) else None


def _id(node: object) -> str | None:
    if isinstance(node, dict) and node.get("id") is not None:
        return str(node["id"])
    return None


def _money(bag: object) -> Decimal:
    money = bag.get("shopMoney") if isinstance(bag, dict) else None
    return _decimal(money.get("amount") if isinstance(money, dict) else None) or Decimal(0)


def _decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def _datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _moment(value: datetime) -> str:
    """A moment as the store's search reads it, in UTC."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
