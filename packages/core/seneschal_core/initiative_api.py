"""Calling Initiative's plug-in API as a community's install.

The app authenticates with its own Ed25519 key (``private_key_jwt``, RFC 7523
§2.2) at ``POST <api>/plugin-platform/oauth/token`` and is given an
**installation token** for one community (``client_credentials`` with
``installation``, the community's reference): it acts as the community's
install, with the scopes the community granted it. The deployment holds the
key's public half in the app's registration, under the key's RFC 7638
thumbprint, as the plug-in SDK names it.

The assertion is addressed to the token endpoint under the configured API
address, which must be the deployment's public one.

Community routes are ``<api>/c/{community}/…``. An installation token names its
community, so the segment is always ``0``. Tokens are kept per community until
shortly before they expire, and a call answered 401 is sent once more on a
fresh token.

See Initiative's ``backend/app/services/marketplace/plugin_oauth.py`` for the
token endpoint, and the plug-in SDK's ``src/client.ts`` for a client of it.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import time
import uuid
from collections import defaultdict
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from typing import Any

import httpx2
import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from seneschal_core.initiative import ALGORITHM
from seneschal_core.sessions import load_private_key

logger = logging.getLogger(__name__)

#: The token endpoint, under Initiative's API.
TOKEN_PATH = "/plugin-platform/oauth/token"
#: Where a community's routes start, under Initiative's API.
COMMUNITY_PREFIX = "/c/0"
ASSERTION_TYPE = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
#: How long a client assertion is valid for.
ASSERTION_LIFETIME_SECONDS = 60
#: How long before a token expires it is replaced.
TOKEN_EXPIRY_SKEW_SECONDS = 30
#: The most member references one lookup names.
PEOPLE_PER_LOOKUP = 100
#: The most members one search answers with.
PEOPLE_PER_SEARCH = 20
_TIMEOUT_SECONDS = 10.0


class InitiativeUnavailable(Exception):
    """Initiative could not be asked: the app has no key, Initiative did not
    answer, or it gave no token."""


class InitiativeRefused(Exception):
    """Initiative's API answered a call with an error status, and its
    ``detail`` when the body carried one."""

    def __init__(self, status_code: int, detail: object) -> None:
        super().__init__(f"Initiative answered {status_code}: {detail}")
        self.status_code = status_code
        self.detail = detail


@dataclass(frozen=True)
class Person:
    """A member, as the install's reference names them."""

    member_ref: str
    #: The name they set in the community, else their handle.
    name: str


def key_id(key: ed25519.Ed25519PrivateKey) -> str:
    """The key's RFC 7638 thumbprint: the ``kid`` it is registered under."""
    x = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    members = {"crv": "Ed25519", "kty": "OKP", "x": _b64(x)}
    canonical = json.dumps(members, separators=(",", ":"), sort_keys=True).encode()
    return _b64(hashlib.sha256(canonical).digest())


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


@dataclass(frozen=True)
class _Token:
    value: str
    #: On the monotonic clock.
    expires_at: float


class InitiativeApi:
    """Initiative's plug-in API, as each community's install calls it."""

    def __init__(
        self,
        *,
        api_url: str,
        public_id: str,
        key_pem: str | None,
        http: httpx2.AsyncClient | None = None,
    ) -> None:
        self.api_url = api_url.rstrip("/")
        self.public_id = public_id
        self._key = load_private_key(key_pem) if key_pem else None
        self._kid = key_id(self._key) if self._key else None
        self._http = http or httpx2.AsyncClient(timeout=_TIMEOUT_SECONDS)
        self._tokens: dict[str, _Token] = {}
        self._locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def call(
        self,
        community_ref: str,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        body: object = None,
    ) -> Any:
        """Call ``path`` among the community's routes (``/contacts/``, say) as
        its install, with ``body`` as JSON, and answer the response's body read
        as JSON (None when empty). Raises :class:`InitiativeRefused` for an
        error status and :class:`InitiativeUnavailable` when there is no answer."""
        url = f"{self.api_url}{COMMUNITY_PREFIX}{path}"
        response = await self._send(community_ref, method, url, params, body)
        if response.status_code == 401:
            self._tokens.pop(community_ref, None)
            response = await self._send(community_ref, method, url, params, body)
        if response.status_code >= 500:
            raise InitiativeUnavailable(f"Initiative answered {response.status_code}")
        answer = _json(response)
        if response.is_error:
            detail = answer.get("detail") if isinstance(answer, dict) else None
            raise InitiativeRefused(response.status_code, detail)
        return answer

    async def people(self, community_ref: str, member_refs: Collection[str]) -> dict[str, Person]:
        """The members ``member_refs`` name, by reference. A reference that no
        longer names a member of the community is left out."""
        wanted = sorted(set(member_refs))
        found: dict[str, Person] = {}
        for start in range(0, len(wanted), PEOPLE_PER_LOOKUP):
            batch = wanted[start : start + PEOPLE_PER_LOOKUP]
            page = await self.call(
                community_ref,
                "GET",
                "/users/search",
                params={"user_id": batch, "page_size": PEOPLE_PER_LOOKUP},
            )
            for person in _people(page):
                if person.member_ref in batch:
                    found[person.member_ref] = person
        return found

    async def search_people(self, community_ref: str, search: str) -> list[Person]:
        """The members whose handle matches ``search``, closest first, or the
        community's first members for no ``search``: at most
        :data:`PEOPLE_PER_SEARCH`."""
        page = await self.call(
            community_ref,
            "GET",
            "/users/search",
            params={"search": search, "page_size": PEOPLE_PER_SEARCH},
        )
        return _people(page)

    async def _send(
        self,
        community_ref: str,
        method: str,
        url: str,
        params: Mapping[str, Any] | None,
        body: object,
    ) -> httpx2.Response:
        token = await self._token(community_ref)
        try:
            return await self._http.request(
                method,
                url,
                params=params,
                json=body,
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            )
        except httpx2.HTTPError as exc:
            logger.warning("could not reach Initiative at %s: %s", url, exc)
            raise InitiativeUnavailable(str(exc)) from exc

    async def _token(self, community_ref: str) -> str:
        """The community's installation token, from the cache while it is fresh."""
        async with self._locks[community_ref]:
            cached = self._tokens.get(community_ref)
            if cached is not None and time.monotonic() < cached.expires_at:
                return cached.value
            token = await self._issue(community_ref)
            self._tokens[community_ref] = token
            return token.value

    async def _issue(self, community_ref: str) -> _Token:
        if self._key is None:
            raise InitiativeUnavailable("the app has no key to call Initiative with")
        requested_at = time.monotonic()
        try:
            response = await self._http.post(
                f"{self.api_url}{TOKEN_PATH}",
                data={
                    "grant_type": "client_credentials",
                    "client_assertion_type": ASSERTION_TYPE,
                    "client_assertion": self._assertion(self._key),
                    "installation": community_ref,
                },
                headers={"Accept": "application/json"},
            )
        except httpx2.HTTPError as exc:
            logger.warning("could not reach Initiative's token endpoint: %s", exc)
            raise InitiativeUnavailable(str(exc)) from exc
        body = _json(response)
        token = body.get("access_token") if isinstance(body, dict) else None
        if response.is_error or not isinstance(token, str) or not token:
            error = body.get("error") if isinstance(body, dict) else None
            logger.warning(
                "Initiative gave no installation token (%s, %s)", response.status_code, error
            )
            raise InitiativeUnavailable(f"no installation token: {error or response.status_code}")
        expires_in = body.get("expires_in")
        lifetime = expires_in if isinstance(expires_in, int) and expires_in > 0 else 0
        return _Token(token, requested_at + lifetime - TOKEN_EXPIRY_SKEW_SECONDS)

    def _assertion(self, key: ed25519.Ed25519PrivateKey) -> str:
        """A client assertion addressed to the token endpoint."""
        issued_at = int(time.time())
        return jwt.encode(
            {
                "iss": self.public_id,
                "sub": self.public_id,
                "aud": f"{self.api_url}{TOKEN_PATH}",
                "jti": str(uuid.uuid4()),
                "iat": issued_at,
                "exp": issued_at + ASSERTION_LIFETIME_SECONDS,
            },
            key,
            algorithm=ALGORITHM,
            headers={"kid": self._kid, "typ": "JWT"},
        )


def _json(response: httpx2.Response) -> Any:
    if not response.content:
        return None
    try:
        return response.json()
    except ValueError:
        return None


def _people(page: object) -> list[Person]:
    """The members a page of Initiative's member search names."""
    items = page.get("items", []) if isinstance(page, dict) else []
    return [person for item in items if (person := _person(item)) is not None]


def _person(item: object) -> Person | None:
    if not isinstance(item, dict) or not isinstance(item.get("id"), str):
        return None
    name = item.get("display_name") or item.get("username")
    if not isinstance(name, str) or not name:
        return None
    return Person(member_ref=item["id"], name=name)
