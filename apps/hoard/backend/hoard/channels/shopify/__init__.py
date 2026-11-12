"""Shopify's connector: a store's own app, made by its owner, whose client
ID and secret get Hoard a token by the client credentials grant; the Admin
GraphQL API, at a pinned version, for products, locations, stock, orders and
fulfillments; and Shopify's webhooks, which Initiative checks and forwards."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from hoard.channels import Connector


def connector(values: Mapping[str, Any]) -> Connector:
    """The connector for the store the connection's ``values`` name."""
    from hoard.channels.shopify.connector import ShopifyConnector

    return ShopifyConnector(values)
