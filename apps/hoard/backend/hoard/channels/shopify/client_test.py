"""The Admin API client: the store's address, its tokens, and its pacing."""

from __future__ import annotations

from urllib.parse import parse_qs

import httpx2
import pytest

from hoard.channels import ConnectError, ConnectRefused
from hoard.channels.shopify.client import (
    API_VERSION,
    Pacer,
    ShopifyClient,
    shop_address,
    user_errors,
)
from hoard.channels.shopify.fake import FakeShop
from hoard.channels.tokens import Tokens


def _client(fake: FakeShop, **values: str) -> ShopifyClient:
    return ShopifyClient(
        values.get("shop", fake.shop),
        values.get("client_id", fake.client_id),
        values.get("client_secret", fake.client_secret),
        http=fake.http(),
        pacer=fake.pacer(),
        tokens=Tokens(),
    )


@pytest.mark.parametrize(
    "typed",
    ["tea", "Tea.myshopify.com", "https://tea.myshopify.com/", " tea.myshopify.com "],
)
def test_the_store_address_is_its_myshopify_one(typed: str):
    assert shop_address(typed) == "tea.myshopify.com"


@pytest.mark.parametrize(
    "typed", ["", "tea.example.com", "tea/admin", "-tea", "tea shop", "tea.myshopify.com.evil"]
)
def test_an_address_that_is_not_a_myshopify_one_is_refused(typed: str):
    with pytest.raises(ConnectRefused) as refused:
        shop_address(typed)
    assert refused.value.code is ConnectError.STORE_ADDRESS_INVALID


def test_the_api_version_is_pinned():
    assert API_VERSION == "2026-10"
    assert _client(FakeShop()).graphql_url == (
        "https://tea.myshopify.com/admin/api/2026-10/graphql.json"
    )


async def test_a_token_comes_by_client_credentials_and_carries_the_granted_scopes():
    seen: list[dict[str, list[str]]] = []

    def answer(request: httpx2.Request) -> httpx2.Response:
        seen.append(parse_qs(request.content.decode()))
        assert str(request.url) == "https://tea.myshopify.com/admin/oauth/access_token"
        assert request.headers["content-type"] == "application/x-www-form-urlencoded"
        return httpx2.Response(
            200,
            json={"access_token": "t", "scope": "write_products,read_orders", "expires_in": 86399},
        )

    client = ShopifyClient(
        "tea",
        "id",
        "secret",
        http=httpx2.AsyncClient(transport=httpx2.MockTransport(answer)),
        tokens=Tokens(),
    )
    assert await client.granted() == {"write_products", "read_orders"}
    assert await client.token() == "t"
    assert seen == [
        {"grant_type": ["client_credentials"], "client_id": ["id"], "client_secret": ["secret"]}
    ]
    await client.granted(fresh=True)
    assert len(seen) == 2


async def test_refused_credentials_and_an_unknown_store_are_told_apart():
    shop = FakeShop()
    with pytest.raises(ConnectRefused) as refused:
        await _client(shop, client_secret="wrong").token()
    assert refused.value.code is ConnectError.STORE_CREDENTIALS_REFUSED
    assert refused.value.detail == "invalid_client"
    with pytest.raises(ConnectRefused) as refused:
        await _client(shop, shop="coffee").token()
    assert refused.value.code is ConnectError.STORE_ADDRESS_INVALID
    with pytest.raises(ConnectRefused) as refused:
        await _client(shop, client_id="").token()
    assert refused.value.code is ConnectError.STORE_CREDENTIALS_REFUSED


async def test_a_call_waits_while_the_budget_is_short():
    shop = FakeShop(cost=100.0, available=0.0, restore_rate=50.0)
    client = _client(shop)
    await client.query("query HoardShop { shop { name } }")
    assert shop.slept == []
    await client.query("query HoardShop { shop { name } }")
    assert len(shop.slept) == 1
    assert shop.slept[0] == pytest.approx(2.0, abs=0.05)


async def test_a_throttled_call_is_sent_again():
    shop = FakeShop(throttle=2, available=0.0)
    data = await _client(shop).query("query HoardShop { shop { name } }")
    assert data["shop"]["name"] == "Tea House"
    assert shop.operations() == ["HoardShop"] * 3
    assert len(shop.slept) == 2


async def test_a_call_throttled_too_often_is_refused():
    shop = FakeShop(throttle=99)
    with pytest.raises(ConnectRefused) as refused:
        await _client(shop).query("query HoardShop { shop { name } }")
    assert refused.value.code is ConnectError.STORE_UNREACHABLE


async def test_a_server_error_is_retried_and_a_stale_token_replaced():
    shop = FakeShop(statuses=[502, 503])
    client = _client(shop)
    assert (await client.query("query HoardShop { shop { name } }"))["shop"]
    assert shop.slept == [1.0, 2.0]
    shop.tokens.clear()
    assert (await client.query("query HoardShop { shop { name } }"))["shop"]
    assert len(shop.tokens) == 1


async def test_errors_are_refusals():
    def answer(request: httpx2.Request) -> httpx2.Response:
        if request.url.path.endswith("access_token"):
            return httpx2.Response(200, json={"access_token": "t", "scope": ""})
        return httpx2.Response(
            200,
            json={"errors": [{"message": "no", "extensions": {"code": "ACCESS_DENIED"}}]},
        )

    client = ShopifyClient(
        "tea",
        "id",
        "secret",
        http=httpx2.AsyncClient(transport=httpx2.MockTransport(answer)),
        pacer=Pacer(),
        tokens=Tokens(),
    )
    with pytest.raises(ConnectRefused) as refused:
        await client.query("query HoardShop { shop { name } }")
    assert refused.value.code is ConnectError.STORE_SCOPES_MISSING
    with pytest.raises(ConnectRefused) as refused:
        user_errors({"userErrors": [{"field": ["id"], "message": "Product does not exist"}]})
    assert refused.value.detail == "Product does not exist"
    user_errors({"userErrors": []})
