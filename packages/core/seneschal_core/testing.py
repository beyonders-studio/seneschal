"""What every app's tests share: a database of the run's own, made as a
deployment makes one, and a stand-in for Initiative and its billing.

Loaded by pytest (``-p seneschal_core.testing``, in the root ``pyproject.toml``).

**The database.** ``TEST_POSTGRES_URL`` names a superuser (default: the
compose database, ``postgresql://postgres:postgres@localhost:5236/postgres``).
Each run makes its own database with its own two logins, an owner that owns the
database and a serving login that owns nothing, runs the app's migrations as
the owner, and drops all three at the end. An app's ``conftest.py`` names the
fixture::

    database = database_fixture("hoard", migrate=upgrade)

and tests check access as the serving login (``serving_database``).

**Initiative.** :class:`InitiativeStandIn` holds an Ed25519 key for the plug-in
platform and one for billing, publishes the first as Initiative does, and signs
handoffs, context tokens and statements with them. It holds the app's own key
too, issues installation tokens for assertions signed with it, and answers the
community's routes of its plug-in API: member lookups and searches from
:attr:`people`, the install's configuration from :attr:`connections`, and
whatever a test adds to :attr:`routes`.
"""

from __future__ import annotations

import json
import os
import secrets
from collections.abc import AsyncIterator, Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qsl

import httpx2
import jwt
import psycopg
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from jwt.algorithms import OKPAlgorithm
from psycopg import sql
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool

from seneschal_core.db import Database
from seneschal_core.initiative import (
    ALGORITHM,
    CONTEXT_TYPE,
    HANDOFF_TYPE,
    ISSUER,
    JWKS_PATH,
    STATEMENT_ISSUER,
    STATEMENT_TYPE,
    Initiative,
    audience,
)
from seneschal_core.initiative_api import (
    ASSERTION_TYPE,
    COMMUNITY_PREFIX,
    CONSENT_REQUESTS_PATH,
    INSTALLATION_PREFIX,
    MEMBER_GRANT,
    TOKEN_PATH,
    InitiativeApi,
    key_id,
)

DEFAULT_TEST_POSTGRES_URL = "postgresql://postgres:postgres@localhost:5236/postgres"


@dataclass(frozen=True)
class AppDatabase:
    name: str
    #: The owner login, which runs migrations (sync driver, for Alembic).
    owner_url: str
    #: The serving login (async driver).
    serving_url: str
    serving_login: str
    #: A superuser, in this database.
    superuser_url: str


def _superuser_url() -> str:
    return os.environ.get("TEST_POSTGRES_URL", DEFAULT_TEST_POSTGRES_URL)


def _url(login: str, password: str, database: str, driver: str) -> str:
    return (
        make_url(_superuser_url())
        .set(drivername=driver, username=login, password=password, database=database)
        .render_as_string(hide_password=False)
    )


@contextmanager
def migrated_database(prefix: str, migrate: Callable[[AppDatabase], None]) -> Iterator[AppDatabase]:
    """A migrated database of this run's own, dropped with its logins afterwards."""
    name = f"{prefix}_test_{secrets.token_hex(4)}"
    owner, serving = f"{name}_owner", f"{name}_app"
    owner_password, serving_password = secrets.token_hex(16), secrets.token_hex(16)
    database = AppDatabase(
        name=name,
        owner_url=_url(owner, owner_password, name, "postgresql+psycopg"),
        serving_url=_url(serving, serving_password, name, "postgresql+asyncpg"),
        serving_login=serving,
        superuser_url=make_url(_superuser_url())
        .set(drivername="postgresql+asyncpg", database=name)
        .render_as_string(hide_password=False),
    )
    with psycopg.connect(_superuser_url(), autocommit=True) as admin:
        for login, password in ((owner, owner_password), (serving, serving_password)):
            admin.execute(
                sql.SQL(
                    "CREATE ROLE {} LOGIN PASSWORD {} "
                    "NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS"
                ).format(sql.Identifier(login), sql.Literal(password))
            )
        admin.execute(
            sql.SQL("CREATE DATABASE {} OWNER {}").format(
                sql.Identifier(name), sql.Identifier(owner)
            )
        )
    try:
        migrate(database)
        yield database
    finally:
        with psycopg.connect(_superuser_url(), autocommit=True) as admin:
            admin.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
            )
            for login in (owner, serving):
                admin.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(login)))


def database_fixture(prefix: str, *, migrate: Callable[[AppDatabase], None]) -> Any:
    """A session fixture holding the run's database, migrated by ``migrate``."""

    @pytest.fixture(scope="session")
    def database() -> Iterator[AppDatabase]:
        with migrated_database(prefix, migrate) as made:
            yield made

    return database


@pytest.fixture
async def serving_database(database: AppDatabase) -> AsyncIterator[Database]:
    """The run's database as the serving login."""
    served = Database(database.serving_url, poolclass=NullPool)
    yield served
    await served.dispose()


def private_key_pem(key: ed25519.Ed25519PrivateKey) -> str:
    return key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()


def public_key_pem(key: ed25519.Ed25519PrivateKey) -> str:
    return (
        key.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )


def new_key_pem() -> str:
    """A fresh Ed25519 private key, as a PKCS#8 PEM."""
    return private_key_pem(ed25519.Ed25519PrivateKey.generate())


#: A route of the community's plug-in API: the request, answered.
Route = Callable[[httpx2.Request], httpx2.Response]


class InitiativeStandIn:
    """Initiative's plug-in platform and its billing, as far as an app's tests need them."""

    API_URL = "http://initiative.test/api/v1"
    KID = "platform-key"
    #: How long an installation token lives.
    TOKEN_LIFETIME_SECONDS = 3600

    def __init__(self, *, public_id: str, listing_uid: str) -> None:
        self.public_id = public_id
        self.listing_uid = listing_uid
        self.platform_key = ed25519.Ed25519PrivateKey.generate()
        self.billing_key = ed25519.Ed25519PrivateKey.generate()
        #: The app's own key, as its registration holds it.
        self.plugin_key = ed25519.Ed25519PrivateKey.generate()
        #: Each member the lookup finds, by the install's reference for them,
        #: as Initiative reads a person: ``display_name``, ``username``, ….
        self.people: dict[str, dict[str, Any]] = {}
        #: The community's routes, by method and path under ``/c/0``, and the
        #: install's own, by their path under the API.
        self.routes: dict[tuple[str, str], Route] = {
            ("GET", "/users/search"): self._search_people,
            ("POST", f"{INSTALLATION_PREFIX}/events"): self._emit,
            ("PUT", f"{INSTALLATION_PREFIX}/metadata"): self._write_metadata,
            ("GET", f"{INSTALLATION_PREFIX}/config"): self._config,
            ("POST", CONSENT_REQUESTS_PATH): self._ask_consent,
        }
        #: Each installation token issued, and the community it is for.
        self.tokens: dict[str, str] = {}
        #: The initiative each installation token is narrowed to, if any.
        self.narrowed: dict[str, int | None] = {}
        #: The member each member token acts for, by token.
        self.acting: dict[str, str] = {}
        #: The members who consented to the app acting for them, by community
        #: and the install's reference for them.
        self.consents: set[tuple[str, str]] = set()
        #: Each consent request: the community, and the body it was sent with.
        self.consent_requests: list[tuple[str, dict[str, Any]]] = []
        #: Each event emitted: the community, and the body it was sent with.
        self.events: list[tuple[str, dict[str, Any]]] = []
        #: The values the install keeps on each item, by community and
        #: ``(entity_type, entity_id)``.
        self.metadata: dict[tuple[str, str, int | None], dict[str, Any]] = {}
        #: Each static connection's values, by community and connection id, as
        #: the install's configuration hands them over.
        self.connections: dict[str, dict[str, dict[str, Any]]] = {}

    def jwks(self) -> dict[str, Any]:
        """The key set Initiative publishes."""
        jwk = OKPAlgorithm.to_jwk(self.platform_key.public_key(), as_dict=True)
        return {"keys": [{**jwk, "kid": self.KID, "alg": ALGORITHM, "use": "sig"}]}

    def http(self) -> httpx2.AsyncClient:
        """A client that reaches only this stand-in."""

        def answer(request: httpx2.Request) -> httpx2.Response:
            if request.url == f"{self.API_URL}{JWKS_PATH}":
                return httpx2.Response(200, json=self.jwks())
            if (request.method, request.url.copy_with(query=None)) == (
                "POST",
                f"{self.API_URL}{TOKEN_PATH}",
            ):
                return self._issue(request)
            path = request.url.path.removeprefix(httpx2.URL(self.API_URL).path)
            route = self.routes.get((request.method, path.removeprefix(COMMUNITY_PREFIX)))
            if route is None:
                return httpx2.Response(404)
            scheme, _, token = request.headers.get("authorization", "").partition(" ")
            if scheme != "Bearer" or token not in self.tokens:
                return httpx2.Response(401, json={"detail": "NOT_AUTHENTICATED"})
            return route(request)

        return httpx2.AsyncClient(transport=httpx2.MockTransport(answer))

    def api(self) -> InitiativeApi:
        """The app's client of this stand-in's plug-in API, signing with its key."""
        return InitiativeApi(
            api_url=self.API_URL,
            public_id=self.public_id,
            key_pem=private_key_pem(self.plugin_key),
            http=self.http(),
        )

    def _issue(self, request: httpx2.Request) -> httpx2.Response:
        """An installation token, for an assertion the app's key signed; or a
        member token, for one naming a member who consented."""
        form = dict(parse_qsl(request.content.decode()))
        member = form.get("grant_type") == MEMBER_GRANT
        assertion = form.get("assertion" if member else "client_assertion", "")
        try:
            if jwt.get_unverified_header(assertion).get("kid") != key_id(self.plugin_key):
                raise jwt.InvalidTokenError("unknown key")
            claims = jwt.decode(
                assertion,
                self.plugin_key.public_key(),
                algorithms=[ALGORITHM],
                audience=f"{self.API_URL}{TOKEN_PATH}",
                issuer=self.public_id,
                subject=None if member else self.public_id,
                options={"require": ["iss", "sub", "aud", "jti", "iat", "exp"]},
            )
        except jwt.PyJWTError:
            return httpx2.Response(401, json={"error": "invalid_client"})
        if member:
            community_ref = claims.get("installation")
            if not community_ref or claims["exp"] - claims["iat"] > 300:
                return httpx2.Response(400, json={"error": "invalid_grant"})
            if (community_ref, claims["sub"]) not in self.consents:
                return httpx2.Response(400, json={"error": "consent_required"})
        elif (
            form.get("grant_type") != "client_credentials"
            or form.get("client_assertion_type") != ASSERTION_TYPE
            or not form.get("installation")
            or claims["exp"] - claims["iat"] > 300
        ):
            return httpx2.Response(400, json={"error": "invalid_grant"})
        else:
            community_ref = form["installation"]
        resource = form.get("resource")
        if resource is not None and not resource.startswith("urn:initiative:initiative:"):
            return httpx2.Response(400, json={"error": "invalid_target"})
        token = secrets.token_hex(16)
        self.tokens[token] = community_ref
        self.narrowed[token] = int(resource.rsplit(":", 1)[1]) if resource else None
        if member:
            self.acting[token] = claims["sub"]
        return httpx2.Response(
            200,
            json={
                "access_token": token,
                "token_type": "Bearer",
                "expires_in": self.TOKEN_LIFETIME_SECONDS,
                "scope": "members:read",
            },
        )

    def _community(self, request: httpx2.Request) -> str:
        return self.tokens[request.headers["authorization"].partition(" ")[2]]

    def member_of(self, request: httpx2.Request) -> str | None:
        """The member a request's token acts for; None for the install's own."""
        return self.acting.get(request.headers["authorization"].partition(" ")[2])

    def _ask_consent(self, request: httpx2.Request) -> httpx2.Response:
        """Keeps the request; answers it pending, or granted once the member
        has consented."""
        community_ref, body = self._community(request), json.loads(request.content)
        self.consent_requests.append((community_ref, body))
        granted = (community_ref, body["member"]) in self.consents
        return httpx2.Response(
            201,
            json={
                **body,
                "requested_access": body["access"],
                "status": "granted" if granted else "pending",
                "granted_access": body["access"] if granted else None,
                "requested_at": datetime.now(UTC).isoformat(),
            },
        )

    def _emit(self, request: httpx2.Request) -> httpx2.Response:
        self.events.append((self._community(request), json.loads(request.content)))
        return httpx2.Response(202, json={"status": "accepted"})

    def _write_metadata(self, request: httpx2.Request) -> httpx2.Response:
        """Keeps ``values`` by key, a null removing its key."""
        body = json.loads(request.content)
        held = self.metadata.setdefault(
            (self._community(request), body["entity_type"], body.get("entity_id")), {}
        )
        for key, value in (body.get("values") or {}).items():
            if value is None:
                held.pop(key, None)
            else:
                held[key] = value
        return httpx2.Response(200, json={"values": held})

    def _config(self, request: httpx2.Request) -> httpx2.Response:
        """The install's configuration in the token's community."""
        community_ref = self._community(request)
        connections = self.connections.get(community_ref, {})
        return httpx2.Response(
            200,
            json={
                "community_ref": community_ref,
                "install_id": 1,
                "listing_uid": self.listing_uid,
                "listing_version": "1.0.0",
                "enabled": True,
                "config_state": "ok" if connections else "unverified",
                "config_state_detail": None,
                "needs_config": not connections,
                "connections": connections,
                "connection_refs": {},
                "member_connections": [],
            },
        )

    def _search_people(self, request: httpx2.Request) -> httpx2.Response:
        """The members ``user_id`` names, or those whose handle holds
        ``search``; every member for neither."""
        named = request.url.params.get_list("user_id")
        search = request.url.params.get("search", "").casefold()
        items = [
            {"id": ref, **person}
            for ref, person in self.people.items()
            if (not named or ref in named) and search in person.get("username", "").casefold()
        ]
        return httpx2.Response(
            200,
            json={
                "items": items,
                "total_count": len(items),
                "page": 1,
                "page_size": int(request.url.params.get("page_size", 20)),
                "has_next": False,
                "has_prev": False,
            },
        )

    def initiative(self) -> Initiative:
        """The app's connection to this stand-in."""
        return Initiative(
            api_url=self.API_URL,
            public_id=self.public_id,
            listing_uid=self.listing_uid,
            statement_key_pem=public_key_pem(self.billing_key),
            http=self.http(),
        )

    def sign(
        self,
        claims: Mapping[str, Any],
        *,
        typ: str,
        key: Any = None,
        algorithm: str = ALGORITHM,
        lifetime: timedelta = timedelta(seconds=60),
    ) -> str:
        """``claims`` in the envelope every platform token has, signed."""
        now = datetime.now(UTC)
        payload = {
            "jti": secrets.token_hex(16),
            "iss": ISSUER,
            "aud": audience(self.public_id),
            "iat": int(now.timestamp()),
            "exp": int((now + lifetime).timestamp()),
            "plugin_install_id": 1,
            "community_status": "active",
            **claims,
        }
        return jwt.encode(
            payload,
            key if key is not None else self.platform_key,
            algorithm=algorithm,
            headers={"kid": self.KID, "typ": typ},
        )

    def handoff(
        self,
        *,
        community_ref: str,
        member_ref: str = "member-ref",
        page: str = "stock",
        initiative_id: int | None = 1,
        admin: bool = False,
        permissions: tuple[str, ...] = ("use",),
        **claims: Any,
    ) -> str:
        """A page handoff, as Initiative mints one."""
        body: dict[str, Any] = {
            "sub": member_ref,
            "community_ref": community_ref,
            "surface_id": page,
            "community_admin": admin,
            "permissions": list(permissions),
        }
        if initiative_id is not None:
            body["initiative_id"] = initiative_id
            body["initiative_moderator"] = False
        return self.sign({**body, **claims}, typ=HANDOFF_TYPE)

    def lifecycle(self, hook: str, *, community_ref: str, **claims: Any) -> str:
        """A hook's lifecycle token."""
        return self.sign(
            {"community_ref": community_ref, "scope": "lifecycle", "hook": hook, **claims},
            typ=CONTEXT_TYPE,
        )

    def context(self, endpoint_id: str, *, community_ref: str, **claims: Any) -> str:
        """An endpoint call's context token."""
        return self.sign(
            {
                "community_ref": community_ref,
                "scope": "endpoint",
                "endpoint_id": endpoint_id,
                **claims,
            },
            typ=CONTEXT_TYPE,
        )

    def statement(
        self,
        version: int,
        values: Mapping[str, Any],
        *,
        key: ed25519.Ed25519PrivateKey | None = None,
        **claims: Any,
    ) -> str:
        """Billing's signed statement of a community's plan."""
        payload = {
            "iss": STATEMENT_ISSUER,
            "listing_uid": self.listing_uid,
            "community_ref": "billing-community-ref",
            "version": version,
            "values": dict(values),
            "iat": int(datetime.now(UTC).timestamp()),
            **claims,
        }
        return jwt.encode(
            payload,
            key or self.billing_key,
            algorithm=ALGORITHM,
            headers={"typ": STATEMENT_TYPE},
        )
