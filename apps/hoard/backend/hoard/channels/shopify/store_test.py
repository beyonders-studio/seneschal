"""The Shopify connector, against a fake store."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest

from hoard.channels import (
    Connected,
    ConnectError,
    ConnectRefused,
    FieldOwner,
    FieldValue,
    Nudge,
    NudgeTopic,
    PushResult,
    StockSet,
    StoreField,
    StoreFulfillment,
    StoreImage,
    StoreLine,
    StoreOrder,
    StoreProduct,
    StoreStock,
    StoreVariant,
    connector_for,
)
from hoard.channels.shopify import queries
from hoard.channels.shopify.fake import FakeShop
from hoard.channels.shopify.store import REQUIRED_SCOPES, TOPICS, ShopifyConnector
from hoard.records import Channel

PRODUCT = "gid://shopify/Product/1"
ORDER = "gid://shopify/Order/7"
PAGE_END = {"hasNextPage": False, "endCursor": None}


def _page(nodes: list[dict[str, Any]], cursor: str | None = None) -> dict[str, Any]:
    return {"nodes": nodes, "pageInfo": {"hasNextPage": cursor is not None, "endCursor": cursor}}


def _variant(number: int, size: str, price: str = "12.00") -> dict[str, Any]:
    return {
        "id": f"gid://shopify/ProductVariant/{number}",
        "title": size,
        "sku": f"TEA-{size[0]}",
        "price": price,
        "selectedOptions": [{"name": "Size", "value": size}],
        "inventoryItem": {"id": f"gid://shopify/InventoryItem/{number}"},
    }


def _product_node(variants: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "id": PRODUCT,
        "title": "Green tea",
        "descriptionHtml": "<p>Leaf</p>",
        "status": "ACTIVE",
        "updatedAt": "2026-11-01T10:00:00Z",
        "productType": "Loose leaf",
        "vendor": "",
        "tags": ["green"],
        "options": [{"name": "Size"}],
        "variants": variants or _page([_variant(11, "Small"), _variant(12, "Large", "20.00")]),
        "media": _page(
            [
                {"id": "gid://shopify/MediaImage/5", "alt": "Tin", "image": {"url": "https://i/5"}},
                {"id": "gid://shopify/Video/6", "alt": None},
            ]
        ),
        "resourcePublicationsV2": _page([{"publication": {"id": "gid://shopify/Publication/1"}}]),
    }


# Checking and subscribing.


async def test_check_answers_the_store_its_name_and_the_signing_secret():
    shop = FakeShop()
    connector = shop.connector(shop="https://Tea.myshopify.com/")
    assert await connector.check() == Connected("tea.myshopify.com", "Tea House", "client-secret")


async def test_check_names_the_permissions_missing():
    shop = FakeShop(scopes=["write_products", "read_inventory", "write_locations"])
    with pytest.raises(ConnectRefused) as refused:
        await shop.connector().check()
    assert refused.value.code is ConnectError.STORE_SCOPES_MISSING
    assert refused.value.detail == (
        "read_customers, read_metaobjects, read_orders, write_files, write_inventory, "
        "write_publications"
    )
    # Writing a resource covers reading it.
    shop.scopes = [scope.replace("read_", "write_") for scope in REQUIRED_SCOPES]
    await shop.connector().check()
    shop.scopes = list(REQUIRED_SCOPES)
    with pytest.raises(ConnectRefused) as refused:
        await shop.connector(push_fulfillments=True).check()
    assert refused.value.detail == "write_merchant_managed_fulfillment_orders"


async def test_check_refuses_a_store_answering_another_address():
    shop = FakeShop()
    shop.answer("HoardShop", {"shop": {"name": "x", "myshopifyDomain": "other.myshopify.com"}})
    with pytest.raises(ConnectRefused) as refused:
        await shop.connector().check()
    assert refused.value.code is ConnectError.STORE_ADDRESS_INVALID


async def test_check_refuses_a_custom_domain_before_calling():
    shop = FakeShop()
    with pytest.raises(ConnectRefused) as refused:
        await shop.connector(shop="tea.example.com").check()
    assert refused.value.code is ConnectError.STORE_ADDRESS_INVALID
    assert shop.tokens == []


async def test_subscribe_makes_only_the_missing_topics_and_unsubscribe_removes_them():
    shop = FakeShop()
    address = "https://initiative.example/api/v1/plugin-hooks/beyonders-studio.hoard"
    held = [{"id": "gid://shopify/WebhookSubscription/1", "topic": "ORDERS_CREATE"}]
    shop.answer("HoardWebhooks", {"webhookSubscriptions": _page(held)})
    shop.answer("HoardWebhookCreate", {"webhookSubscriptionCreate": {"userErrors": []}})
    shop.answer("HoardWebhookDelete", {"webhookSubscriptionDelete": {"userErrors": []}})
    connector = shop.connector()
    await connector.subscribe(address)
    assert shop.variables("HoardWebhooks") == [{"uri": address, "after": None}]
    made = shop.variables("HoardWebhookCreate")
    assert [call["topic"] for call in made] == [t for t in TOPICS if t != "ORDERS_CREATE"]
    assert {json.dumps(call["input"]) for call in made} == {json.dumps({"uri": address})}
    await connector.unsubscribe(address)
    assert shop.variables("HoardWebhookDelete") == [{"id": held[0]["id"]}]


async def test_a_refused_subscription_says_which():
    shop = FakeShop()
    shop.answer("HoardWebhooks", {"webhookSubscriptions": _page([])})
    shop.answer(
        "HoardWebhookCreate",
        {"webhookSubscriptionCreate": {"userErrors": [{"message": "Address is invalid"}]}},
    )
    with pytest.raises(ConnectRefused) as refused:
        await shop.connector().subscribe("https://x.example/hooks")
    assert refused.value.code is ConnectError.STORE_SUBSCRIPTION_REFUSED
    assert refused.value.detail == "ORDERS_CREATE: Address is invalid"


# Products.


async def test_a_product_is_read_with_every_variant():
    shop = FakeShop()
    shop.answer(
        "HoardProduct",
        {"product": _product_node(_page([_variant(11, "Small")], cursor="c1"))},
    )
    shop.answer(
        "HoardVariantsPage",
        {"product": {"variants": _page([_variant(12, "Large", "20.00")])}},
    )
    product = await shop.connector().product(PRODUCT)
    assert product == StoreProduct(
        id=PRODUCT,
        title="Green tea",
        description="<p>Leaf</p>",
        status="active",
        options=["Size"],
        variants=[
            StoreVariant(
                id="gid://shopify/ProductVariant/11",
                title="Small",
                sku="TEA-S",
                price=Decimal("12.00"),
                options={"Size": "Small"},
                inventory_item_id="gid://shopify/InventoryItem/11",
            ),
            StoreVariant(
                id="gid://shopify/ProductVariant/12",
                title="Large",
                sku="TEA-L",
                price=Decimal("20.00"),
                options={"Size": "Large"},
                inventory_item_id="gid://shopify/InventoryItem/12",
            ),
        ],
        images=[StoreImage("https://i/5", "Tin", "gid://shopify/MediaImage/5")],
        published_on=["gid://shopify/Publication/1"],
        updated_at=datetime(2026, 11, 1, 10, tzinfo=UTC),
        product_type="Loose leaf",
        vendor=None,
        tags=["green"],
    )
    assert shop.variables("HoardVariantsPage") == [{"id": PRODUCT, "after": "c1"}]
    shop.answer("HoardProduct", {"product": None})
    assert await shop.connector().product(PRODUCT) is None


async def test_products_are_read_page_by_page_since_a_moment():
    shop = FakeShop()
    pages = [
        {"products": _page([_product_node()], cursor="p1")},
        {"products": _page([{**_product_node(), "id": "gid://shopify/Product/2"}])},
    ]
    shop.answer("HoardProducts", lambda variables: pages.pop(0))
    since = datetime(2026, 11, 1, 12, tzinfo=UTC)
    found = [product.id async for product in shop.connector().products(since)]
    assert found == [PRODUCT, "gid://shopify/Product/2"]
    assert shop.variables("HoardProducts") == [
        {"query": "updated_at:>'2026-11-01T12:00:00Z'", "after": None},
        {"query": "updated_at:>'2026-11-01T12:00:00Z'", "after": "p1"},
    ]


def _writes(shop: FakeShop) -> None:
    for operation, payload in (
        ("HoardProductUpdate", "productUpdate"),
        ("HoardVariantsUpdate", "productVariantsBulkUpdate"),
        ("HoardVariantsCreate", "productVariantsBulkCreate"),
        ("HoardVariantsDelete", "productVariantsBulkDelete"),
        ("HoardFilesUpdate", "fileUpdate"),
        ("HoardPublish", "publishablePublish"),
        ("HoardUnpublish", "publishableUnpublish"),
    ):
        shop.answer(operation, {payload: {"userErrors": []}})


async def test_saving_a_product_writes_only_what_changed():
    shop = FakeShop()
    shop.answer("HoardProduct", {"product": _product_node()})
    _writes(shop)
    connector = shop.connector()
    current = await connector.product(PRODUCT)
    assert current is not None
    small, large = current.variants
    edited = StoreProduct(
        id=PRODUCT,
        title="Sencha",
        description="<p>Steamed</p>",
        status="draft",
        options=["Size"],
        variants=[
            StoreVariant(**{**small.__dict__, "price": Decimal("13.50")}),
            StoreVariant(id=None, title="Medium", sku="TEA-M", options={"Size": "Medium"}),
        ],
        images=[StoreImage("https://cdn.example/new.jpg", "Open tin")],
        published_on=["gid://shopify/Publication/2"],
    )
    await connector.save_product(edited)
    assert shop.variables("HoardProductUpdate") == [
        {
            "product": {
                "id": PRODUCT,
                "title": "Sencha",
                "descriptionHtml": "<p>Steamed</p>",
                "status": "DRAFT",
            },
            "media": [
                {
                    "originalSource": "https://cdn.example/new.jpg",
                    "alt": "Open tin",
                    "mediaContentType": "IMAGE",
                }
            ],
        }
    ]
    assert shop.variables("HoardFilesUpdate") == [
        {"files": [{"id": "gid://shopify/MediaImage/5", "referencesToRemove": [PRODUCT]}]}
    ]
    assert shop.variables("HoardVariantsUpdate") == [
        {
            "productId": PRODUCT,
            "variants": [
                {
                    "id": small.id,
                    "price": "13.50",
                    "inventoryItem": {"sku": "TEA-S"},
                    "optionValues": [{"optionName": "Size", "name": "Small"}],
                }
            ],
        }
    ]
    assert shop.variables("HoardVariantsCreate") == [
        {
            "productId": PRODUCT,
            "variants": [
                {
                    "inventoryItem": {"sku": "TEA-M"},
                    "optionValues": [{"optionName": "Size", "name": "Medium"}],
                }
            ],
        }
    ]
    assert shop.variables("HoardVariantsDelete") == [
        {"productId": PRODUCT, "variantsIds": [large.id]}
    ]
    assert shop.variables("HoardPublish") == [
        {"id": PRODUCT, "input": [{"publicationId": "gid://shopify/Publication/2"}]}
    ]
    assert shop.variables("HoardUnpublish") == [
        {"id": PRODUCT, "input": [{"publicationId": "gid://shopify/Publication/1"}]}
    ]


async def test_a_refused_edit_is_a_refusal():
    shop = FakeShop()
    shop.answer("HoardProduct", {"product": _product_node()})
    shop.answer(
        "HoardProductUpdate",
        {"productUpdate": {"userErrors": [{"field": ["title"], "message": "Title is too long"}]}},
    )
    with pytest.raises(ConnectRefused) as refused:
        await shop.connector().save_product(StoreProduct(id=PRODUCT, title="x" * 300))
    assert refused.value.code is ConnectError.STORE_UNREACHABLE
    assert refused.value.detail == "Title is too long"


async def test_a_product_with_options_is_made_with_its_variants():
    shop = FakeShop()
    _writes(shop)
    shop.answer(
        "HoardProductCreate",
        {
            "productCreate": {
                "product": {"id": PRODUCT, "variants": _page([{"id": "standalone"}])},
                "userErrors": [],
            }
        },
    )
    shop.answer("HoardProduct", {"product": _product_node()})
    made = await shop.connector().create_product(
        StoreProduct(
            id=None,
            title="Green tea",
            options=["Size"],
            variants=[
                StoreVariant(None, "Small", "TEA-S", Decimal("12"), {"Size": "Small"}),
                StoreVariant(None, "Large", "TEA-L", Decimal("20"), {"Size": "Large"}),
            ],
            published_on=["gid://shopify/Publication/1"],
        )
    )
    assert made.id == PRODUCT
    assert shop.variables("HoardProductCreate") == [
        {
            "product": {
                "title": "Green tea",
                "productOptions": [
                    {"name": "Size", "values": [{"name": "Small"}, {"name": "Large"}]}
                ],
            },
            "media": [],
        }
    ]
    created = shop.variables("HoardVariantsCreate")
    assert created[0]["strategy"] == "REMOVE_STANDALONE_VARIANT"
    assert [variant["price"] for variant in created[0]["variants"]] == ["12", "20"]
    assert shop.variables("HoardPublish") == [
        {"id": PRODUCT, "input": [{"publicationId": "gid://shopify/Publication/1"}]}
    ]


async def test_a_product_without_options_prices_its_one_variant():
    shop = FakeShop()
    _writes(shop)
    shop.answer(
        "HoardProductCreate",
        {
            "productCreate": {
                "product": {"id": PRODUCT, "variants": _page([{"id": "standalone"}])},
                "userErrors": [],
            }
        },
    )
    shop.answer("HoardProduct", {"product": _product_node()})
    await shop.connector().create_product(
        StoreProduct(
            id=None,
            title="Gift box",
            variants=[StoreVariant(None, "Default", "BOX", Decimal("30"))],
        )
    )
    assert shop.variables("HoardVariantsUpdate") == [
        {
            "productId": PRODUCT,
            "variants": [{"id": "standalone", "price": "30", "inventoryItem": {"sku": "BOX"}}],
        }
    ]
    assert "HoardVariantsCreate" not in shop.operations()


# Locations and stock.


async def test_locations_are_read_page_by_page():
    shop = FakeShop()
    pages = [
        {"locations": _page([{"id": "gid://shopify/Location/1", "name": "Shop"}], "l1")},
        {"locations": _page([{"id": "gid://shopify/Location/2", "name": "Warehouse"}])},
    ]
    shop.answer("HoardLocations", lambda variables: pages.pop(0))
    assert await shop.connector().locations() == {
        "gid://shopify/Location/1": "Shop",
        "gid://shopify/Location/2": "Warehouse",
    }


def _stock_set(key: str, compare: int | None = 4, item: str | None = "gid://i/1") -> StockSet:
    return StockSet(
        product_id=PRODUCT,
        variant_id="gid://v/1",
        inventory_item_id=item,
        location_id="gid://shopify/Location/1",
        quantity=7,
        compare_quantity=compare,
        idempotency_key=key,
    )


async def test_available_is_set_with_its_compare_and_key():
    shop = FakeShop()
    shop.answer("HoardSetAvailable", {"inventorySetQuantities": {"userErrors": []}})
    results = await shop.connector().push_available(
        [_stock_set("push-1"), _stock_set("push-2", compare=None)]
    )
    assert results == [PushResult("push-1", True), PushResult("push-2", True)]
    first, second = shop.variables("HoardSetAvailable")
    assert first == {
        "idempotencyKey": "push-1",
        "input": {
            "name": "available",
            "reason": "correction",
            "referenceDocumentUri": "hoard://stock-push/push-1",
            "quantities": [
                {
                    "inventoryItemId": "gid://i/1",
                    "locationId": "gid://shopify/Location/1",
                    "quantity": 7,
                    "changeFromQuantity": 4,
                }
            ],
        },
    }
    # The check is skipped by saying so.
    assert "changeFromQuantity" in second["input"]["quantities"][0]
    assert second["input"]["quantities"][0]["changeFromQuantity"] is None


async def test_a_stale_compare_answers_what_the_store_holds():
    shop = FakeShop()
    shop.answer(
        "HoardSetAvailable",
        {
            "inventorySetQuantities": {
                "userErrors": [{"code": "CHANGE_FROM_QUANTITY_STALE", "message": "stale"}]
            }
        },
    )
    shop.answer(
        "HoardAvailable",
        {
            "inventoryItem": {
                "inventoryLevel": {"quantities": [{"name": "available", "quantity": 9}]}
            }
        },
    )
    results = await shop.connector().push_available([_stock_set("push-1")])
    assert results == [PushResult("push-1", False, store_quantity=9)]
    assert shop.variables("HoardAvailable") == [
        {"item": "gid://i/1", "location": "gid://shopify/Location/1"}
    ]


async def test_a_listing_without_an_inventory_item_is_not_pushed():
    shop = FakeShop()
    [result] = await shop.connector().push_available([_stock_set("push-1", item=None)])
    assert not result.pushed and result.error
    assert shop.calls == []


# Orders.


def _money(amount: str) -> dict[str, Any]:
    return {"shopMoney": {"amount": amount}}


def _order_node(lines: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": ORDER,
        "name": "#1007",
        "processedAt": "2026-11-02T09:30:00Z",
        "updatedAt": "2026-11-03T09:30:00Z",
        "cancelledAt": None,
        "fullyPaid": True,
        "displayFulfillmentStatus": "PARTIALLY_FULFILLED",
        "currencyCode": "CAD",
        "totalShippingPriceSet": _money("8.00"),
        "totalTaxSet": _money("3.90"),
        "totalDiscountsSet": None,
        "totalRefundedSet": _money("12.50"),
        "customer": {"id": "gid://shopify/Customer/99"},
        "lineItems": lines,
    }


def _line(number: int, quantity: int, **extra: Any) -> dict[str, Any]:
    return {
        "id": f"gid://shopify/LineItem/{number}",
        "name": f"Tea {number}",
        "sku": f"TEA-{number}",
        "quantity": quantity,
        "currentQuantity": quantity,
        "originalUnitPriceSet": _money("12.50"),
        "product": {"id": PRODUCT},
        "variant": {"id": f"gid://shopify/ProductVariant/{number}"},
        "lineItemGroup": None,
        "sellingPlan": None,
        **extra,
    }


def _refunds(shop: FakeShop) -> None:
    """One unit of line 1 refunded, its lines read on a second page."""
    shop.answer(
        "HoardRefunds",
        {
            "order": {
                "refunds": [
                    {
                        "id": "gid://shopify/Refund/5",
                        "refundLineItems": _page([], cursor="r1"),
                    }
                ]
            }
        },
    )
    shop.answer(
        "HoardRefundLinesPage",
        {
            "refund": {
                "refundLineItems": _page(
                    [{"quantity": 1, "lineItem": {"id": "gid://shopify/LineItem/1"}}]
                )
            }
        },
    )


def _orders(shop: FakeShop) -> None:
    line = _line(1, 3, currentQuantity=2)
    shop.answer("HoardOrder", {"order": _order_node(_page([line], cursor="l1"))})
    _refunds(shop)
    shop.answer(
        "HoardLinesPage",
        {
            "order": {
                "lineItems": _page(
                    [
                        _line(
                            2,
                            1,
                            lineItemGroup={"id": "gid://shopify/LineItemGroup/3"},
                            sellingPlan={"sellingPlanId": "gid://shopify/SellingPlan/4"},
                        )
                    ]
                )
            }
        },
    )
    shop.answer(
        "HoardFulfillments",
        {
            "order": {
                "fulfillments": [
                    {
                        "id": "gid://shopify/Fulfillment/8",
                        "status": "SUCCESS",
                        "createdAt": "2026-11-03T08:00:00Z",
                        "location": {"id": "gid://shopify/Location/1"},
                        "trackingInfo": [{"number": "1Z", "company": "Post", "url": None}],
                        "fulfillmentLineItems": _page(
                            [{"quantity": 1, "lineItem": {"id": "gid://shopify/LineItem/1"}}]
                        ),
                    },
                    {"id": "gid://shopify/Fulfillment/9", "status": "FAILURE"},
                ]
            }
        },
    )
    shop.answer(
        "HoardFulfillmentOrders",
        {
            "order": {
                "fulfillmentOrders": _page(
                    [
                        {
                            "id": "gid://shopify/FulfillmentOrder/20",
                            "status": "OPEN",
                            "fulfillAt": None,
                            "assignedLocation": {"location": {"id": "gid://shopify/Location/1"}},
                            "lineItems": _page(
                                [
                                    {
                                        "id": "gid://shopify/FulfillmentOrderLineItem/21",
                                        "remainingQuantity": 1,
                                        "lineItem": {"id": "gid://shopify/LineItem/1"},
                                    }
                                ]
                            ),
                        },
                        {
                            "id": "gid://shopify/FulfillmentOrder/30",
                            "status": "SCHEDULED",
                            "fulfillAt": "2026-12-01T00:00:00Z",
                            "assignedLocation": {"location": {"id": "gid://shopify/Location/1"}},
                            "lineItems": _page(
                                [
                                    {
                                        "id": "gid://shopify/FulfillmentOrderLineItem/31",
                                        "remainingQuantity": 1,
                                        "lineItem": {"id": "gid://shopify/LineItem/2"},
                                    }
                                ]
                            ),
                        },
                        {
                            "id": "gid://shopify/FulfillmentOrder/40",
                            "status": "CLOSED",
                            "fulfillAt": None,
                            "assignedLocation": {"location": {"id": "gid://shopify/Location/1"}},
                            "lineItems": _page([]),
                        },
                    ]
                )
            }
        },
    )


async def test_an_order_carries_its_lines_and_fulfillments():
    shop = FakeShop()
    _orders(shop)
    order = await shop.connector().order(ORDER)
    assert order is not None
    assert (order.id, order.number, order.currency) == (ORDER, "#1007", "CAD")
    assert order.ordered_at == datetime(2026, 11, 2, 9, 30, tzinfo=UTC)
    assert (order.paid, order.cancelled, order.fulfilled) == (True, False, False)
    assert (order.shipping, order.tax, order.discount, order.refunded) == (
        Decimal("8.00"),
        Decimal("3.90"),
        Decimal(0),
        Decimal("12.50"),
    )
    # Of the customer, only the number at the end of their id.
    assert order.customer == "99"
    assert "customer { id }" in queries.ORDER_FIELDS
    assert queries.ORDER_FIELDS.count("customer") == 1
    first, second = order.lines
    assert (first.id, first.quantity, first.price, first.sku) == (
        "gid://shopify/LineItem/1",
        Decimal(3),
        Decimal("12.50"),
        "TEA-1",
    )
    assert (first.current_quantity, first.refunded_quantity) == (Decimal(2), Decimal(1))
    assert (second.current_quantity, second.refunded_quantity) == (Decimal(1), Decimal(0))
    assert shop.variables("HoardRefunds") == [{"id": ORDER}]
    assert (second.bundle_id, second.selling_plan) == (
        "gid://shopify/LineItemGroup/3",
        "gid://shopify/SellingPlan/4",
    )
    assert order.fulfillments == [
        StoreFulfillment(
            id="gid://shopify/Fulfillment/8",
            location_id="gid://shopify/Location/1",
            lines={"gid://shopify/LineItem/1": Decimal(1)},
            shipped_at=datetime(2026, 11, 3, 8, tzinfo=UTC),
            tracking_number="1Z",
            tracking_company="Post",
        )
    ]
    # Without a fulfillment order permission, the parts still to send are not read.
    assert "HoardFulfillmentOrders" not in shop.operations()


async def test_an_order_carries_its_parts_still_to_send_where_permitted():
    shop = FakeShop(scopes=[*REQUIRED_SCOPES, "read_merchant_managed_fulfillment_orders"])
    _orders(shop)
    order = await shop.connector().order(ORDER)
    assert order is not None
    assert order.fulfillments[1:] == [
        StoreFulfillment(
            id="gid://shopify/FulfillmentOrder/20",
            location_id="gid://shopify/Location/1",
            lines={"gid://shopify/LineItem/1": Decimal(1)},
        ),
        StoreFulfillment(
            id="gid://shopify/FulfillmentOrder/30",
            location_id="gid://shopify/Location/1",
            lines={"gid://shopify/LineItem/2": Decimal(1)},
            due_on=date(2026, 12, 1),
        ),
    ]


async def test_orders_changed_since_a_moment_are_read_by_id():
    shop = FakeShop()
    _orders(shop)
    shop.answer("HoardOrderIds", {"orders": _page([{"id": ORDER}])})
    since = datetime(2026, 11, 3, tzinfo=UTC)
    assert [order.id async for order in shop.connector().orders(since)] == [ORDER]
    assert shop.variables("HoardOrderIds") == [
        {"query": "updated_at:>'2026-11-03T00:00:00Z'", "after": None}
    ]


async def test_fulfillments_are_pushed_only_when_switched_on():
    shop = FakeShop(scopes=[*REQUIRED_SCOPES, "write_merchant_managed_fulfillment_orders"])
    _orders(shop)
    shop.answer(
        "HoardFulfillmentCreate",
        {
            "fulfillmentCreate": {
                "fulfillment": {"id": "gid://shopify/Fulfillment/50"},
                "userErrors": [],
            }
        },
    )
    sent = StoreFulfillment(
        id=None,
        location_id="gid://shopify/Location/1",
        lines={"gid://shopify/LineItem/1": Decimal(1)},
        tracking_number="1Z",
    )
    with pytest.raises(ConnectRefused) as refused:
        await shop.connector().push_fulfillment(ORDER, sent)
    assert refused.value.code is ConnectError.STORE_SCOPES_MISSING
    connector = shop.connector(push_fulfillments=True)
    assert await connector.push_fulfillment(ORDER, sent) == "gid://shopify/Fulfillment/50"
    assert shop.variables("HoardFulfillmentCreate") == [
        {
            "fulfillment": {
                "lineItemsByFulfillmentOrder": [
                    {
                        "fulfillmentOrderId": "gid://shopify/FulfillmentOrder/20",
                        "fulfillmentOrderLineItems": [
                            {"id": "gid://shopify/FulfillmentOrderLineItem/21", "quantity": 1}
                        ],
                    }
                ],
                "notifyCustomer": False,
                "trackingInfo": {"number": "1Z"},
            }
        }
    ]
    # A scheduled part is not open to send yet.
    with pytest.raises(ConnectRefused):
        await connector.push_fulfillment(
            ORDER, StoreFulfillment(None, None, {"gid://shopify/LineItem/2": Decimal(1)})
        )


async def test_the_history_import_reads_a_bulk_query():
    shop = FakeShop()
    statuses = iter(["RUNNING", "COMPLETED"])
    shop.answer(
        "HoardBulkRun",
        {
            "bulkOperationRunQuery": {
                "bulkOperation": {"id": "gid://shopify/BulkOperation/1", "status": "CREATED"},
                "userErrors": [],
            }
        },
    )
    shop.answer(
        "HoardBulkStatus",
        lambda variables: {
            "bulkOperation": {"status": next(statuses), "url": "https://files.example/1.jsonl"}
        },
    )
    _refunds(shop)
    order = {key: value for key, value in _order_node({}).items() if key != "lineItems"}
    shop.files["https://files.example/1.jsonl"] = [
        json.dumps(order),
        json.dumps({**_line(1, 3, currentQuantity=2), "__parentId": ORDER}),
        json.dumps({**order, "id": "gid://shopify/Order/8", "name": "#1008"}),
        json.dumps({**_line(2, 1), "__parentId": ORDER}),
    ]
    orders = [order async for order in shop.connector().import_history()]
    assert [(order.number, len(order.lines)) for order in orders] == [("#1007", 2), ("#1008", 0)]
    assert [
        (line.quantity, line.current_quantity, line.refunded_quantity) for line in orders[0].lines
    ] == [(3, 2, 1), (1, 1, 0)]
    assert orders[0].customer == "99"
    # Only the order with a line selling fewer than were ordered has its refunds read.
    assert shop.variables("HoardRefunds") == [{"id": ORDER}]
    query = shop.variables("HoardBulkRun")[0]["query"]
    assert "orders(query: \"created_at:>='" in query
    assert shop.slept == [1.0]


def _bulk(shop: FakeShop, rows: list[dict[str, Any]]) -> None:
    """Answer the next bulk query with ``rows``."""
    shop.answer(
        "HoardBulkRun",
        {"bulkOperationRunQuery": {"bulkOperation": {"id": "b"}, "userErrors": []}},
    )
    shop.answer(
        "HoardBulkStatus",
        {"bulkOperation": {"status": "COMPLETED", "url": "https://files.example/2.jsonl"}},
    )
    shop.files["https://files.example/2.jsonl"] = [json.dumps(row) for row in rows]


async def test_every_product_is_read_in_one_bulk_query():
    shop = FakeShop()
    node = {
        key: value
        for key, value in _product_node().items()
        if key not in ("variants", "media", "resourcePublicationsV2")
    }
    child = {"__parentId": PRODUCT}
    _bulk(
        shop,
        [
            node,
            {**child, "__typename": "ProductVariant", **_variant(11, "Small")},
            {
                **child,
                "__typename": "MediaImage",
                "id": "gid://shopify/MediaImage/5",
                "alt": "Tin",
                "image": {"url": "https://i/5"},
            },
            {**child, "__typename": "Video", "id": "gid://shopify/Video/6", "alt": None},
            {
                **child,
                "__typename": "ResourcePublicationV2",
                "publication": {"id": "gid://shopify/Publication/1"},
            },
            {**child, "__typename": "ProductVariant", **_variant(12, "Large", "20.00")},
        ],
    )

    [product] = [product async for product in shop.connector().import_products()]

    shop.answer("HoardProduct", {"product": _product_node()})
    assert product == await shop.connector().product(PRODUCT)
    query = shop.variables("HoardBulkRun")[0]["query"]
    assert "productType vendor tags" in query


async def test_stock_levels_are_read_in_one_bulk_query():
    shop = FakeShop()
    item = "gid://shopify/InventoryItem/11"
    _bulk(
        shop,
        [
            {"id": item, "unitCost": {"amount": "2.5"}},
            {
                "__parentId": item,
                "location": {"id": "gid://shopify/Location/1"},
                "quantities": [{"name": "available", "quantity": 7}],
            },
            {
                "__parentId": item,
                "location": {"id": "gid://shopify/Location/2"},
                "quantities": [{"name": "available", "quantity": -1}],
            },
            {"id": "gid://shopify/InventoryItem/12", "unitCost": None},
        ],
    )

    levels = await shop.connector().stock_levels()

    assert levels == [
        StoreStock(
            item,
            Decimal("2.5"),
            {"gid://shopify/Location/1": 7, "gid://shopify/Location/2": -1},
        ),
        StoreStock("gid://shopify/InventoryItem/12", None, {}),
    ]


async def test_a_failed_bulk_query_is_a_refusal():
    shop = FakeShop()
    shop.answer(
        "HoardBulkRun",
        {"bulkOperationRunQuery": {"bulkOperation": {"id": "b"}, "userErrors": []}},
    )
    shop.answer("HoardBulkStatus", {"bulkOperation": {"status": "FAILED", "errorCode": "TIMEOUT"}})
    with pytest.raises(ConnectRefused) as refused:
        [order async for order in shop.connector().import_history()]
    assert refused.value.detail == "bulk query failed: TIMEOUT"


# Fields.


async def test_fields_are_read_with_the_entries_they_pick_from():
    shop = FakeShop()

    def definitions(variables: dict[str, Any]) -> dict[str, Any]:
        if variables["ownerType"] == "PRODUCTVARIANT":
            return {"metafieldDefinitions": _page([])}
        return {
            "metafieldDefinitions": _page(
                [
                    {
                        "name": "Allergen information",
                        "namespace": "shopify",
                        "key": "allergen-information",
                        "type": {"name": "list.metaobject_reference"},
                        "validations": [
                            {"name": "metaobject_definition_id", "value": "gid://def/1"}
                        ],
                        "metafieldsCount": 12,
                    },
                    {
                        "name": "Leaf",
                        "namespace": "custom",
                        "key": "leaf",
                        "type": {"name": "single_line_text_field"},
                        "validations": [{"name": "choices", "value": '["Loose", "Bagged"]'}],
                        "metafieldsCount": 3,
                    },
                ]
            )
        }

    shop.answer("HoardFieldDefinitions", definitions)
    shop.answer(
        "HoardFieldEntries",
        {
            "metaobjectDefinition": {
                "metaobjects": _page(
                    [{"id": "gid://m/1", "displayName": "Milk"}, {"id": "gid://m/2"}]
                )
            }
        },
    )

    found = await shop.connector().fields()

    assert found == [
        StoreField(
            FieldOwner.PRODUCT,
            "shopify",
            "allergen-information",
            "Allergen information",
            "list.metaobject_reference",
            {"gid://m/1": "Milk", "gid://m/2": "gid://m/2"},
            12,
        ),
        StoreField(
            FieldOwner.PRODUCT,
            "custom",
            "leaf",
            "Leaf",
            "single_line_text_field",
            {"Loose": "Loose", "Bagged": "Bagged"},
            3,
        ),
    ]
    assert shop.variables("HoardFieldEntries")[0]["id"] == "gid://def/1"


async def test_every_products_field_values_are_read_in_one_bulk_query():
    shop = FakeShop()
    variant = "gid://shopify/ProductVariant/11"
    _bulk(
        shop,
        [
            {"id": PRODUCT},
            {
                "__parentId": PRODUCT,
                "__typename": "Metafield",
                "namespace": "custom",
                "key": "origin",
                "value": "Assam",
            },
            {
                "__parentId": PRODUCT,
                "__typename": "Metafield",
                "namespace": "reviews",
                "key": "rating",
                "value": "4",
            },
            {"__parentId": PRODUCT, "__typename": "ProductVariant", "id": variant},
            {
                "__parentId": variant,
                "__typename": "Metafield",
                "namespace": "custom",
                "key": "allergens",
                "value": "Milk",
            },
            {"id": "gid://shopify/Product/2"},
        ],
    )

    found = await shop.connector().field_values(
        {FieldOwner.PRODUCT: ["custom.origin"], FieldOwner.VARIANT: ["custom.allergens"]}, None
    )

    assert found == {
        PRODUCT: {"custom.origin": "Assam"},
        variant: {"custom.allergens": "Milk"},
        "gid://shopify/Product/2": {},
    }
    assert "variants" in shop.variables("HoardBulkRun")[0]["query"]


async def test_a_products_field_values_are_read_by_key():
    shop = FakeShop()
    shop.answer(
        "HoardFieldValues",
        {
            "product": {
                "id": PRODUCT,
                "metafields": {
                    "nodes": [{"namespace": "custom", "key": "origin", "value": "Assam"}]
                },
                "variants": {"nodes": [{"id": "v", "metafields": {"nodes": []}}]},
            }
        },
    )

    found = await shop.connector().field_values({FieldOwner.PRODUCT: ["custom.origin"]}, [PRODUCT])

    assert found == {PRODUCT: {"custom.origin": "Assam"}}
    assert shop.variables("HoardFieldValues") == [
        {"id": PRODUCT, "keys": ["custom.origin"], "count": 1, "variants": 1}
    ]


async def test_field_values_are_written_and_taken_off():
    shop = FakeShop()
    shop.answer(
        "HoardFieldsSet",
        {
            "metafieldsSet": {
                "metafields": [
                    {
                        "namespace": "custom",
                        "key": "origin",
                        "type": "single_line_text_field",
                        "value": "Assam",
                        "owner": {"id": PRODUCT},
                    }
                ],
                "userErrors": [],
            }
        },
    )
    shop.answer(
        "HoardFieldsDelete", {"metafieldsDelete": {"deletedMetafields": [], "userErrors": []}}
    )

    written = await shop.connector().set_field_values(
        [
            FieldValue(PRODUCT, "custom.origin", "single_line_text_field", " Assam"),
            FieldValue(PRODUCT, "custom.grade", "single_line_text_field", None),
        ]
    )

    assert written == [
        FieldValue(PRODUCT, "custom.origin", "single_line_text_field", "Assam"),
        FieldValue(PRODUCT, "custom.grade", "single_line_text_field", None),
    ]
    assert shop.variables("HoardFieldsSet") == [
        {
            "metafields": [
                {
                    "ownerId": PRODUCT,
                    "namespace": "custom",
                    "key": "origin",
                    "type": "single_line_text_field",
                    "value": " Assam",
                }
            ]
        }
    ]
    assert shop.variables("HoardFieldsDelete") == [
        {"metafields": [{"ownerId": PRODUCT, "namespace": "custom", "key": "grade"}]}
    ]


async def test_a_refused_field_write_is_a_refusal():
    shop = FakeShop()
    shop.answer(
        "HoardFieldsSet",
        {
            "metafieldsSet": {
                "metafields": [],
                "userErrors": [{"field": ["value"], "message": "bad"}],
            }
        },
    )
    with pytest.raises(ConnectRefused):
        await shop.connector().set_field_values(
            [FieldValue(PRODUCT, "custom.origin", "number_integer", "x")]
        )


# Webhooks.


@pytest.mark.parametrize(
    ("topic", "body", "nudge"),
    [
        (
            "orders/updated",
            {"id": 7, "admin_graphql_api_id": ORDER},
            Nudge(NudgeTopic.ORDER, ORDER, {"id": 7, "admin_graphql_api_id": ORDER}),
        ),
        ("orders/cancelled", {"id": 7}, Nudge(NudgeTopic.ORDER, ORDER, {"id": 7})),
        ("refunds/create", {"id": 3, "order_id": 7}, Nudge(NudgeTopic.ORDER, ORDER)),
        ("products/delete", {"id": 1}, Nudge(NudgeTopic.PRODUCT, PRODUCT)),
        (
            "inventory_levels/update",
            {"inventory_item_id": 5, "location_id": 2},
            Nudge(NudgeTopic.INVENTORY, "gid://shopify/InventoryItem/5"),
        ),
        ("app/uninstalled", {"id": 1}, None),
        ("orders/updated", {"name": "#7"}, None),
    ],
)
def test_a_webhook_names_what_changed(topic: str, body: dict[str, Any], nudge: Nudge | None):
    connector = connector_for(Channel.SHOPIFY, {})
    assert isinstance(connector, ShopifyConnector)
    assert connector.parse_webhook({"x-shopify-topic": topic}, json.dumps(body)) == nudge


def test_a_webhook_that_is_not_json_is_ignored():
    connector = connector_for(Channel.SHOPIFY, {})
    assert connector.parse_webhook({"x-shopify-topic": "orders/create"}, "not json") is None


def test_an_order_webhook_body_reads_as_the_order():
    connector = connector_for(Channel.SHOPIFY, {})
    body = {
        "id": 7,
        "admin_graphql_api_id": ORDER,
        "name": "#1007",
        "processed_at": "2026-11-02T04:30:00-05:00",
        "updated_at": "2026-11-20T10:00:00-05:00",
        "cancelled_at": None,
        "financial_status": "partially_refunded",
        "fulfillment_status": "fulfilled",
        "currency": "CAD",
        "total_shipping_price_set": {"shop_money": {"amount": "8.00"}},
        "total_tax_set": {"shop_money": {"amount": "3.90"}},
        "total_discounts_set": {"shop_money": {"amount": "0.00"}},
        "customer": {"id": 99, "first_name": "Ada", "email": "ada@example.com", "phone": "555"},
        "line_items": [
            {
                "id": 1,
                "admin_graphql_api_id": "gid://shopify/LineItem/1",
                "name": "Tea 1",
                "sku": "TEA-1",
                "price": "12.50",
                "quantity": 3,
                "current_quantity": 2,
                "product_id": 1,
                "variant_id": 1,
                "sales_line_item_group_id": None,
                "selling_plan_id": None,
            }
        ],
        "fulfillments": [
            {
                "id": 8,
                "admin_graphql_api_id": "gid://shopify/Fulfillment/8",
                "status": "success",
                "created_at": "2026-11-03T08:00:00Z",
                "location_id": 1,
                "tracking_number": "1Z",
                "tracking_company": "Post",
                "tracking_url": None,
                "line_items": [{"id": 1, "quantity": 2}],
            },
            {"id": 9, "status": "failure", "line_items": [{"id": 1, "quantity": 1}]},
        ],
        "refunds": [
            {
                "id": 5,
                "refund_line_items": [{"line_item_id": 1, "quantity": 1}],
                "transactions": [
                    {"kind": "refund", "status": "success", "amount": "12.50"},
                    {"kind": "refund", "status": "failure", "amount": "12.50"},
                ],
            }
        ],
    }

    order = connector.webhook_order(body)

    assert order == StoreOrder(
        id=ORDER,
        number="#1007",
        ordered_at=datetime(2026, 11, 2, 9, 30, tzinfo=UTC),
        lines=[
            StoreLine(
                id="gid://shopify/LineItem/1",
                title="Tea 1",
                quantity=Decimal(3),
                price=Decimal("12.50"),
                product_id=PRODUCT,
                variant_id="gid://shopify/ProductVariant/1",
                sku="TEA-1",
                current_quantity=Decimal(2),
                refunded_quantity=Decimal(1),
            )
        ],
        paid=True,
        fulfilled=True,
        currency="CAD",
        shipping=Decimal("8.00"),
        tax=Decimal("3.90"),
        discount=Decimal(0),
        refunded=Decimal("12.50"),
        # Of the customer, only their number.
        customer="99",
        fulfillments=[
            StoreFulfillment(
                id="gid://shopify/Fulfillment/8",
                location_id="gid://shopify/Location/1",
                lines={"gid://shopify/LineItem/1": Decimal(2)},
                shipped_at=datetime(2026, 11, 3, 8, tzinfo=UTC),
                tracking_number="1Z",
                tracking_company="Post",
            )
        ],
        updated_at=datetime(2026, 11, 20, 15, tzinfo=UTC),
    )
    assert connector.webhook_order({"name": "#1007"}) is None
