"""One Shopify store's Admin GraphQL API, as Hoard calls it.

* The store's address is its ``myshopify.com`` one (:func:`shop_address`).
* A token comes from the client credentials grant: the store's app's client
  ID and secret, posted to the store, answer a token for 24 hours and the
  permissions the store granted. Tokens are kept in
  :data:`hoard.channels.tokens.TOKENS`, and the permissions beside them.
* Every call is paced by the store's query cost budget (:class:`Pacer`): the
  store says what a call cost and how much budget is left, and the next call
  waits while too little is left. A call the store throttled is sent again.
* A call the store refuses raises :class:`~hoard.channels.ConnectRefused`.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

import httpx2

from hoard.channels import ConnectError, ConnectRefused
from hoard.channels.tokens import TOKENS, Tokens

logger = logging.getLogger(__name__)

#: The Admin API version Hoard speaks. Shopify releases one each quarter and
#: supports each for at least a year, so this moves at least once a year.
API_VERSION = "2026-10"

#: What a token lasts when the store does not say: its 24 hours, less a second.
TOKEN_SECONDS = 86399.0

#: How many times a call is sent before Hoard gives up on it: throttled,
#: refused for a stale token once, or answered with a server error.
ATTEMPTS = 5

#: What a call is assumed to cost before the store has priced it.
DEFAULT_COST = 50.0

#: How long to wait before resending a call the store failed, doubled each time.
RETRY_SECONDS = 1.0

_TIMEOUT_SECONDS = 30.0
_SUFFIX = ".myshopify.com"
_NAME_CHARACTERS = frozenset("abcdefghijklmnopqrstuvwxyz0123456789-")

Sleep = Callable[[float], Awaitable[None]]


def shop_address(value: object) -> str:
    """The store's ``myshopify.com`` address from what an admin typed: its
    name, or its address with or without ``https://`` and a trailing slash."""
    text = str(value or "").strip().lower()
    for scheme in ("https://", "http://"):
        text = text.removeprefix(scheme)
    text = text.rstrip("/")
    name = text.removesuffix(_SUFFIX)
    if (
        not name
        or name.startswith("-")
        or any(character not in _NAME_CHARACTERS for character in name)
    ):
        raise ConnectRefused(ConnectError.STORE_ADDRESS_INVALID)
    return f"{name}{_SUFFIX}"


@dataclass
class Pacer:
    """A store's query cost budget as it last reported it, refilled at its
    restore rate since."""

    sleep: Sleep = asyncio.sleep
    clock: Callable[[], float] = time.monotonic
    available: float | None = None
    maximum: float = 0.0
    restore_rate: float = 0.0
    noted_at: float = 0.0

    def left(self) -> float | None:
        """The budget left now; None until the store has reported one."""
        if self.available is None:
            return None
        refilled = self.available + (self.clock() - self.noted_at) * self.restore_rate
        return min(self.maximum, refilled)

    async def wait(self, cost: float) -> None:
        """Wait until the budget covers ``cost``."""
        left = self.left()
        if left is None or left >= cost or self.restore_rate <= 0:
            return
        await self.sleep((min(cost, self.maximum) - left) / self.restore_rate)

    def note(self, status: object) -> None:
        """Keep the budget a response reported (its ``throttleStatus``)."""
        if not isinstance(status, dict):
            return
        try:
            self.available = float(status["currentlyAvailable"])
            self.maximum = float(status["maximumAvailable"])
            self.restore_rate = float(status["restoreRate"])
        except (KeyError, TypeError, ValueError):
            return
        self.noted_at = self.clock()


#: Each store's budget, by its address: every connector to a store shares it.
PACERS: dict[str, Pacer] = {}

#: The permissions the store granted with each token, by the token's key.
_GRANTED: dict[str, tuple[str, frozenset[str]]] = {}

_HTTP: httpx2.AsyncClient | None = None


def _shared_http() -> httpx2.AsyncClient:
    global _HTTP
    if _HTTP is None:
        _HTTP = httpx2.AsyncClient(timeout=_TIMEOUT_SECONDS)
    return _HTTP


class ShopifyClient:
    """One store's Admin GraphQL API, with its app's client ID and secret."""

    def __init__(
        self,
        shop: str,
        client_id: str,
        client_secret: str,
        *,
        http: httpx2.AsyncClient | None = None,
        pacer: Pacer | None = None,
        tokens: Tokens = TOKENS,
    ) -> None:
        self.shop = shop_address(shop)
        self.client_id = client_id
        self.client_secret = client_secret
        self.http = http or _shared_http()
        self.pacer = pacer or PACERS.setdefault(self.shop, Pacer())
        self.tokens = tokens
        self._key = f"shopify:{self.shop}:{client_id}"
        self._costs: dict[str, float] = {}

    @property
    def graphql_url(self) -> str:
        return f"https://{self.shop}/admin/api/{API_VERSION}/graphql.json"

    async def token(self) -> str:
        return await self.tokens.get(self._key, self._issue)

    async def granted(self, fresh: bool = False) -> frozenset[str]:
        """The permissions the store granted the app, as its token says; a
        ``fresh`` token is asked for first."""
        if fresh:
            self.tokens.forget(self._key)
        token = await self.token()
        held = _GRANTED.get(self._key)
        if held is None or held[0] != token:
            self.tokens.forget(self._key)
            token = await self.token()
            held = _GRANTED.get(self._key)
        return held[1] if held is not None and held[0] == token else frozenset()

    async def _issue(self) -> tuple[str, float]:
        if not self.client_id or not self.client_secret:
            raise ConnectRefused(ConnectError.STORE_CREDENTIALS_REFUSED)
        try:
            response = await self.http.post(
                f"https://{self.shop}/admin/oauth/access_token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                },
                headers={"Accept": "application/json"},
            )
        except httpx2.HTTPError as exc:
            logger.warning("could not reach %s for a token: %s", self.shop, exc)
            raise ConnectRefused(ConnectError.STORE_UNREACHABLE) from exc
        if response.status_code == 404:
            raise ConnectRefused(ConnectError.STORE_ADDRESS_INVALID)
        if response.status_code >= 500:
            raise ConnectRefused(ConnectError.STORE_UNREACHABLE, str(response.status_code))
        body = _json(response)
        token = body.get("access_token")
        if response.is_error or not isinstance(token, str) or not token:
            error = body.get("error")
            raise ConnectRefused(
                ConnectError.STORE_CREDENTIALS_REFUSED, error if isinstance(error, str) else None
            )
        scope = body.get("scope")
        _GRANTED[self._key] = (
            token,
            frozenset(
                name.strip()
                for name in (scope if isinstance(scope, str) else "").split(",")
                if name.strip()
            ),
        )
        expires_in = body.get("expires_in")
        lifetime = float(expires_in) if isinstance(expires_in, int | float) else TOKEN_SECONDS
        return token, lifetime

    async def query(
        self, document: str, variables: Mapping[str, Any] | None = None
    ) -> dict[str, Any]:
        """The ``data`` the store answers ``document`` with."""
        operation = _operation(document)
        refreshed = False
        for attempt in range(ATTEMPTS):
            await self.pacer.wait(self._costs.get(operation, DEFAULT_COST))
            token = await self.token()
            try:
                response = await self.http.post(
                    self.graphql_url,
                    json={"query": document, "variables": dict(variables or {})},
                    headers={"X-Shopify-Access-Token": token, "Accept": "application/json"},
                )
            except httpx2.HTTPError as exc:
                logger.warning("could not reach %s: %s", self.shop, exc)
                raise ConnectRefused(ConnectError.STORE_UNREACHABLE) from exc
            if response.status_code == 401 and not refreshed:
                self.tokens.forget(self._key)
                refreshed = True
                continue
            if response.status_code == 401:
                raise ConnectRefused(ConnectError.STORE_CREDENTIALS_REFUSED)
            if response.status_code == 403:
                raise ConnectRefused(ConnectError.STORE_SCOPES_MISSING)
            if response.status_code == 429 or response.status_code >= 500:
                await self.pacer.sleep(RETRY_SECONDS * 2**attempt)
                continue
            body = _json(response)
            if response.is_error:
                raise ConnectRefused(ConnectError.STORE_UNREACHABLE, str(response.status_code))
            cost = (body.get("extensions") or {}).get("cost") or {}
            self.pacer.note(cost.get("throttleStatus"))
            if isinstance(cost.get("requestedQueryCost"), int | float):
                self._costs[operation] = float(cost["requestedQueryCost"])
            errors = body.get("errors")
            if errors:
                codes = {_code(error) for error in errors}
                if "THROTTLED" in codes:
                    continue
                message = _message(errors)
                if "ACCESS_DENIED" in codes:
                    raise ConnectRefused(ConnectError.STORE_SCOPES_MISSING, message)
                raise ConnectRefused(ConnectError.STORE_UNREACHABLE, message)
            data = body.get("data")
            if not isinstance(data, dict):
                raise ConnectRefused(ConnectError.STORE_UNREACHABLE)
            return data
        raise ConnectRefused(ConnectError.STORE_UNREACHABLE, "throttled")

    async def lines(self, url: str) -> AsyncIterator[str]:
        """Each line of a file the store made, such as a bulk query's result,
        at the signed address it gave."""
        try:
            async with self.http.stream("GET", url) as response:
                if response.is_error:
                    raise ConnectRefused(
                        ConnectError.STORE_UNREACHABLE, str(response.status_code)
                    )
                async for line in response.aiter_lines():
                    if line.strip():
                        yield line
        except httpx2.HTTPError as exc:
            raise ConnectRefused(ConnectError.STORE_UNREACHABLE) from exc


def user_errors(payload: object) -> None:
    """Refuse a mutation's answer that carries ``userErrors``."""
    errors = payload.get("userErrors") if isinstance(payload, dict) else None
    if errors:
        raise ConnectRefused(ConnectError.STORE_UNREACHABLE, _message(errors))


def _operation(document: str) -> str:
    """A document's operation name, which keys what it last cost."""
    head = document.strip().split("(", 1)[0].split("{", 1)[0].split()
    return head[1] if len(head) > 1 else document.strip()[:40]


def _code(error: object) -> str | None:
    if isinstance(error, dict):
        extensions = error.get("extensions")
        if isinstance(extensions, dict):
            return extensions.get("code")
        return error.get("code")
    return None


def _message(errors: object) -> str:
    if not isinstance(errors, list):
        return str(errors)
    return "; ".join(
        str(error.get("message")) if isinstance(error, dict) else str(error) for error in errors
    )


def _json(response: httpx2.Response) -> dict[str, Any]:
    if not response.content:
        return {}
    try:
        body = response.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}
