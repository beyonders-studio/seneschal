"""A Shopify store's token endpoint and Admin GraphQL API, kept in memory,
for tests.

A test says what each operation answers (:meth:`FakeShop.answer`), by the
operation's name, as a fixed ``data`` object or one computed from the call's
variables; every call is recorded in :attr:`FakeShop.calls`. The store prices
each call at :attr:`FakeShop.cost` and reports its budget, and throttles the
next :attr:`FakeShop.throttle` calls. Files a bulk query made are served from
:attr:`FakeShop.files`.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs

import httpx2

from hoard.channels.shopify.client import API_VERSION, Pacer
from hoard.channels.shopify.store import REQUIRED_SCOPES, ShopifyConnector
from hoard.channels.tokens import Tokens

Answer = Mapping[str, Any] | Callable[[dict[str, Any]], Mapping[str, Any]]


@dataclass
class FakeShop:
    shop: str = "tea.myshopify.com"
    name: str = "Tea House"
    client_id: str = "client-id"
    client_secret: str = "client-secret"
    #: The permissions the store granted its app.
    scopes: list[str] = field(default_factory=lambda: list(REQUIRED_SCOPES))
    #: What each operation answers, by its name.
    answers: dict[str, Answer] = field(default_factory=dict)
    #: Each GraphQL call: its operation's name and variables.
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    #: The tokens issued.
    tokens: list[str] = field(default_factory=list)
    #: Each call's price, and the budget the store reports.
    cost: float = 10.0
    maximum: float = 1000.0
    restore_rate: float = 50.0
    available: float = 1000.0
    #: How many coming calls are throttled.
    throttle: int = 0
    #: Status codes the coming calls answer with instead.
    statuses: list[int] = field(default_factory=list)
    #: Files by address, each as its lines.
    files: dict[str, list[str]] = field(default_factory=dict)
    #: The seconds slept, by the pacer the connector is given.
    slept: list[float] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.answers.setdefault(
            "HoardShop", {"shop": {"name": self.name, "myshopifyDomain": self.shop}}
        )

    def answer(self, operation: str, data: Answer) -> None:
        self.answers[operation] = data

    def operations(self) -> list[str]:
        return [operation for operation, _ in self.calls]

    def variables(self, operation: str) -> list[dict[str, Any]]:
        return [variables for name, variables in self.calls if name == operation]

    def http(self) -> httpx2.AsyncClient:
        return httpx2.AsyncClient(transport=httpx2.MockTransport(self._handle))

    def pacer(self) -> Pacer:
        async def sleep(seconds: float) -> None:
            self.slept.append(seconds)

        return Pacer(sleep=sleep)

    def connector(self, **values: Any) -> ShopifyConnector:
        """A connector to this store, with the values an admin typed unless
        ``values`` says otherwise."""
        typed = {
            "shop": self.shop,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            **values,
        }
        push_fulfillments = bool(typed.pop("push_fulfillments", False))
        return ShopifyConnector(
            typed,
            http=self.http(),
            pacer=self.pacer(),
            tokens=Tokens(),
            push_fulfillments=push_fulfillments,
        )

    def _handle(self, request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/admin/oauth/access_token":
            return self._token(request)
        if request.url.path == f"/admin/api/{API_VERSION}/graphql.json":
            return self._graphql(request)
        lines = self.files.get(str(request.url))
        if lines is not None:
            return httpx2.Response(200, text="\n".join(lines) + "\n")
        return httpx2.Response(404)

    def _token(self, request: httpx2.Request) -> httpx2.Response:
        if request.url.host != self.shop:
            return httpx2.Response(404)
        form = {name: values[0] for name, values in parse_qs(request.content.decode()).items()}
        if (
            form.get("grant_type") != "client_credentials"
            or form.get("client_id") != self.client_id
            or form.get("client_secret") != self.client_secret
        ):
            return httpx2.Response(400, json={"error": "invalid_client"})
        token = f"token-{len(self.tokens) + 1}"
        self.tokens.append(token)
        return httpx2.Response(
            200, json={"access_token": token, "scope": ",".join(self.scopes), "expires_in": 86399}
        )

    def _graphql(self, request: httpx2.Request) -> httpx2.Response:
        if request.headers.get("x-shopify-access-token") not in self.tokens:
            return httpx2.Response(401)
        body = json.loads(request.content)
        operation = _operation(body["query"])
        variables = body.get("variables") or {}
        self.calls.append((operation, variables))
        if self.statuses:
            return httpx2.Response(self.statuses.pop(0))
        extensions = {
            "cost": {
                "requestedQueryCost": self.cost,
                "actualQueryCost": self.cost,
                "throttleStatus": {
                    "maximumAvailable": self.maximum,
                    "currentlyAvailable": self.available,
                    "restoreRate": self.restore_rate,
                },
            }
        }
        if self.throttle:
            self.throttle -= 1
            return httpx2.Response(
                200,
                json={
                    "errors": [{"message": "Throttled", "extensions": {"code": "THROTTLED"}}],
                    "extensions": extensions,
                },
            )
        answer = self.answers.get(operation)
        if answer is None:
            raise AssertionError(f"the fake store has no answer for {operation}")
        data = answer if isinstance(answer, Mapping) else answer(variables)
        return httpx2.Response(200, json={"data": dict(data), "extensions": extensions})


def _operation(document: str) -> str:
    return document.strip().split("(", 1)[0].split("{", 1)[0].split()[1]
