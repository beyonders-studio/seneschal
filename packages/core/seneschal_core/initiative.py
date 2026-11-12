"""Checking what Initiative and its billing sign before an app acts on it.

Initiative signs three kinds of token for an app, each an EdDSA JWT under a key
from the set it publishes at ``<api>/plugin-platform/jwks.json``, issued by
``initiative`` for the app's own audience, ``initiative-plugin:<public id>``:

* the **page handoff** (header ``typ`` ``initiative-handoff+jwt``), which a page
  receives from Initiative over the frame's message bridge and exchanges once
  for the app's own session. It names the member by the app's reference for
  them, the community, the page, the initiative it was opened in, whether the
  member administers the community and the app's permissions they hold there;
* the **context token** (``typ`` ``initiative-context+jwt``, scope
  ``endpoint``) on a call to one of the app's endpoints, naming the endpoint.
  A call another plug-in made through Initiative names that plug-in
  (``act.sub``), whether it acts as the community or for one of its members,
  and the initiative its token is narrowed to; the app takes one only for an
  endpoint it declares public. A call one of the app's actions made names the
  reader who ran it and the item it was run on;
* the **lifecycle token** (the same ``typ``, scope ``lifecycle``) on a call to
  one of the app's hooks, naming the hook.

Every token names one community and its install, and where the community stands.

Billing signs its statement of what a community holds, and Initiative hands it
on unchanged in the ``entitlement`` hook. It is checked against billing's own
key, which reaches the app from the deployment, never from Initiative.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Collection, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

import httpx2
import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from seneschal_core.organizations import CommunityStatus, Statement

logger = logging.getLogger(__name__)

#: The one algorithm every token and statement is signed with.
ALGORITHM = "EdDSA"
#: Who issues Initiative's tokens.
ISSUER = "initiative"
#: The header ``typ`` of a page handoff.
HANDOFF_TYPE = "initiative-handoff+jwt"
#: The header ``typ`` of a context or lifecycle token.
CONTEXT_TYPE = "initiative-context+jwt"
#: Where Initiative publishes its keys, under its API.
JWKS_PATH = "/plugin-platform/jwks.json"
#: How long a fetched key set is used before it is fetched again.
JWKS_CACHE_SECONDS = 600.0
#: The least time between two fetches asked for by a key the set lacks.
JWKS_REFRESH_SECONDS = 30.0

#: The header ``typ`` of billing's statement.
STATEMENT_TYPE = "initiative-entitlement+jwt"
#: Who signs statements.
STATEMENT_ISSUER = "initiative-billing"


def audience(public_id: str) -> str:
    """The audience of every token Initiative signs for the plug-in ``public_id``."""
    return f"initiative-plugin:{public_id}"


class TokenRefused(Exception):
    """A token Initiative did not sign for this app, or not for this use."""


class StatementRefused(Exception):
    """A statement billing did not sign for this app. ``reason`` is one of
    ``no_issuer_key``, ``malformed``, ``alg``, ``typ``, ``signature``, ``iss``,
    ``listing_uid`` and ``claims``."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class HandoffClaims:
    """A page handoff's claims, verified."""

    jti: str
    #: The member, by the app's reference for them in this community.
    member_ref: str
    community_ref: str
    community_status: CommunityStatus
    plugin_install_id: int
    #: The page that was opened.
    page: str
    #: The initiative it was opened in; ``None`` for a community-level page.
    initiative_id: int | None
    #: Whether the member administers the community.
    admin: bool
    #: The app's permissions the member holds where the page was opened.
    permissions: tuple[str, ...]
    expires_at: datetime


@dataclass(frozen=True)
class ContextSubject:
    """The item an action was run on."""

    #: Initiative's kind of item, such as ``task``.
    type: str
    id: int


@dataclass(frozen=True)
class ContextClaims:
    """A context or lifecycle token's claims, verified."""

    jti: str
    community_ref: str
    community_status: CommunityStatus
    plugin_install_id: int
    #: The endpoint a context token was minted for; ``None`` on a lifecycle token.
    endpoint_id: str | None
    #: The hook a lifecycle token was minted for; ``None`` on a context token.
    hook: str | None
    expires_at: datetime
    #: The plug-in that made the call through Initiative, by its public id;
    #: ``None`` for a call Initiative made itself.
    caller: str | None = None
    #: The member a call from another plug-in acts for, by this app's
    #: reference for them; ``None`` when it acts as the community.
    actor: str | None = None
    #: The reader who ran one of the app's actions, by this app's reference
    #: for them.
    viewer: str | None = None
    #: The item an action was run on.
    subject: ContextSubject | None = None
    #: The initiative the call is narrowed to.
    initiative_id: int | None = None

    @property
    def member_ref(self) -> str | None:
        """Who a change the call makes is recorded as made by: the member a
        caller acts for, or the reader who ran an action; ``None`` for the
        community itself."""
        return self.actor or self.viewer


class _Keys:
    """Initiative's published keys, fetched from its API and kept a while."""

    def __init__(self, url: str, http: httpx2.AsyncClient) -> None:
        self._url = url
        self._http = http
        self._keys: dict[str, ed25519.Ed25519PublicKey] = {}
        self._fetched_at = 0.0
        self._last_attempt = 0.0

    async def for_kid(self, kid: str | None) -> list[ed25519.Ed25519PublicKey]:
        """The key named ``kid``, or every key when the set does not hold it."""
        now = time.monotonic()
        if not self._keys or now - self._fetched_at > JWKS_CACHE_SECONDS:
            self._last_attempt = now
            await self._fetch()
        elif kid and kid not in self._keys and now - self._last_attempt > JWKS_REFRESH_SECONDS:
            self._last_attempt = now
            await self._fetch()
        if kid and kid in self._keys:
            return [self._keys[kid]]
        return list(self._keys.values())

    async def _fetch(self) -> None:
        try:
            response = await self._http.get(self._url, timeout=10.0)
            response.raise_for_status()
            entries = response.json()["keys"]
        except (httpx2.HTTPError, ValueError, KeyError, TypeError) as exc:
            logger.warning("could not read Initiative's keys from %s: %s", self._url, exc)
            raise TokenRefused(f"Initiative's keys could not be read from {self._url}") from exc
        keys: dict[str, ed25519.Ed25519PublicKey] = {}
        for entry in entries if isinstance(entries, list) else []:
            if not isinstance(entry, dict) or (entry.get("kty"), entry.get("crv")) != (
                "OKP",
                "Ed25519",
            ):
                continue
            try:
                key = jwt.PyJWK.from_dict(entry).key
            except jwt.PyJWTError:
                continue
            if isinstance(key, ed25519.Ed25519PublicKey):
                keys[str(entry.get("kid", ""))] = key
        if not keys:
            logger.warning("Initiative published no Ed25519 key at %s", self._url)
            raise TokenRefused(f"Initiative published no Ed25519 key at {self._url}")
        self._keys = keys
        self._fetched_at = time.monotonic()


class Initiative:
    """The app's connection to the Initiative deployment it is installed in."""

    def __init__(
        self,
        *,
        api_url: str,
        public_id: str,
        listing_uid: str,
        statement_key_pem: str | None,
        http: httpx2.AsyncClient | None = None,
    ) -> None:
        self.public_id = public_id
        self.listing_uid = listing_uid
        self._http = http or httpx2.AsyncClient()
        self._keys = _Keys(f"{api_url.rstrip('/')}{JWKS_PATH}", self._http)
        self._statement_key = _statement_key(statement_key_pem)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def verify_handoff(self, token: str) -> HandoffClaims:
        """A page handoff's claims, or :class:`TokenRefused`. Whether it was
        already spent is the caller's to check, by its ``jti``."""
        header, payload = await self._decode(token, required=["sub", "surface_id"])
        if header.get("typ") != HANDOFF_TYPE:
            raise TokenRefused("not a page handoff")
        member_ref = _text(payload, "sub")
        page = _text(payload, "surface_id")
        permissions = payload.get("permissions", [])
        if not isinstance(permissions, list) or not all(
            isinstance(permission, str) and permission for permission in permissions
        ):
            raise TokenRefused("permissions is not a list of names")
        return HandoffClaims(
            jti=str(payload["jti"]),
            member_ref=member_ref,
            community_ref=_text(payload, "community_ref"),
            community_status=_status(payload),
            plugin_install_id=_install(payload),
            page=page,
            initiative_id=_initiative(payload),
            # Only a literal true: an absent or malformed claim is a member's.
            admin=payload.get("community_admin") is True,
            permissions=tuple(permissions),
            expires_at=datetime.fromtimestamp(payload["exp"], tz=UTC),
        )

    async def verify_context(
        self, token: str, *, public: Collection[str] = frozenset()
    ) -> ContextClaims:
        """A context token's claims (scope ``endpoint``), or :class:`TokenRefused`.
        A call another plug-in made is taken only for an endpoint in ``public``."""
        payload = await self._context(token, scope="endpoint")
        endpoint_id = _text(payload, "endpoint_id")
        claims = self._context_claims(payload, endpoint_id=endpoint_id)
        if "act" not in payload:
            if "actor" in payload or "member" in payload:
                raise TokenRefused("an actor without a calling plug-in")
            return claims
        if endpoint_id not in public:
            raise TokenRefused("this endpoint is not offered to another plug-in")
        act = payload["act"]
        if not isinstance(act, Mapping):
            raise TokenRefused("act is not an object")
        caller = _text(act, "sub")
        match payload.get("actor"):
            case "installation":
                if "member" in payload:
                    raise TokenRefused("a member on a call made as the community")
                actor = None
            case "member":
                actor = _text(payload, "member")
            case _:
                raise TokenRefused("actor is not installation or member")
        return replace(claims, caller=caller, actor=actor)

    async def verify_lifecycle(self, token: str, *, hook: str) -> ContextClaims:
        """A lifecycle token's claims (scope ``lifecycle``) for ``hook``, or
        :class:`TokenRefused`: a token minted for one hook is not taken for another."""
        payload = await self._context(token, scope="lifecycle")
        if payload.get("hook") != hook:
            raise TokenRefused(f"not minted for the {hook} hook")
        if "act" in payload:
            raise TokenRefused("a hook is not called by another plug-in")
        return self._context_claims(payload, hook=hook)

    def verify_statement(self, statement: object) -> Statement:
        """Billing's statement for this app, or :class:`StatementRefused`.

        The key comes first, then the header, read before anything is decoded
        and never left to choose the algorithm; then the signature; then who
        signed it, for which listing, and in what shape.
        """
        if self._statement_key is None:
            logger.warning("refused a statement: the deployment gives no statement key")
            raise StatementRefused("no_issuer_key")
        if not isinstance(statement, str) or not statement:
            raise StatementRefused("malformed")
        try:
            header = jwt.get_unverified_header(statement)
        except jwt.PyJWTError:
            raise StatementRefused("malformed") from None
        if header.get("alg") != ALGORITHM:
            raise StatementRefused("alg")
        if header.get("typ") != STATEMENT_TYPE:
            raise StatementRefused("typ")
        try:
            # A statement holds until a higher version replaces it: it has no
            # expiry, and its iat is billing's clock, not ours.
            claims = jwt.decode(
                statement,
                self._statement_key,
                algorithms=[ALGORITHM],
                options={"verify_iat": False},
            )
        except jwt.InvalidSignatureError:
            raise StatementRefused("signature") from None
        except jwt.PyJWTError:
            raise StatementRefused("malformed") from None
        if claims.get("iss") != STATEMENT_ISSUER:
            raise StatementRefused("iss")
        if claims.get("listing_uid") != self.listing_uid:
            raise StatementRefused("listing_uid")
        return _statement(claims)

    async def _decode(
        self, token: str, *, required: list[str]
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """The header and payload of a token Initiative signed for this app:
        its signature under one of Initiative's keys, its issuer, audience and
        expiry, and the claims every token carries."""
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise TokenRefused("unreadable token") from exc
        if header.get("alg") != ALGORITHM:
            raise TokenRefused("not signed with EdDSA")
        keys = await self._keys.for_kid(header.get("kid"))
        first_error: jwt.PyJWTError | None = None
        for key in keys:
            try:
                payload = jwt.decode(
                    token,
                    key,
                    algorithms=[ALGORITHM],
                    audience=audience(self.public_id),
                    issuer=ISSUER,
                    options={
                        "require": [
                            "exp",
                            "iat",
                            "iss",
                            "aud",
                            "jti",
                            "community_ref",
                            "plugin_install_id",
                            *required,
                        ]
                    },
                )
            except jwt.PyJWTError as exc:
                first_error = first_error or exc
                continue
            return header, payload
        raise TokenRefused(f"token did not verify: {first_error}")

    async def _context(self, token: str, *, scope: str) -> dict[str, Any]:
        header, payload = await self._decode(token, required=["scope"])
        if header.get("typ") != CONTEXT_TYPE:
            raise TokenRefused("not a context token")
        if payload.get("scope") != scope:
            raise TokenRefused(f"not minted for scope {scope}")
        return payload

    @staticmethod
    def _context_claims(
        payload: dict[str, Any], *, endpoint_id: str | None = None, hook: str | None = None
    ) -> ContextClaims:
        return ContextClaims(
            jti=str(payload["jti"]),
            community_ref=_text(payload, "community_ref"),
            community_status=_status(payload),
            plugin_install_id=_install(payload),
            endpoint_id=endpoint_id,
            hook=hook,
            expires_at=datetime.fromtimestamp(payload["exp"], tz=UTC),
            viewer=_text(payload, "viewer") if "viewer" in payload else None,
            subject=_subject(payload["subject"]) if "subject" in payload else None,
            initiative_id=_initiative(payload),
        )


def _text(payload: Mapping[str, Any], claim: str) -> str:
    value = payload.get(claim)
    if not isinstance(value, str) or not value:
        raise TokenRefused(f"{claim} is not a non-empty string")
    return value


def _number(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _initiative(payload: Mapping[str, Any]) -> int | None:
    value = payload.get("initiative_id")
    if value is not None and not _number(value):
        raise TokenRefused("initiative_id is not a number")
    return value


def _subject(value: object) -> ContextSubject:
    if not isinstance(value, Mapping) or not _number(value.get("id")):
        raise TokenRefused("subject is not an item")
    return ContextSubject(type=_text(value, "type"), id=value["id"])


def _install(payload: Mapping[str, Any]) -> int:
    value = payload.get("plugin_install_id")
    if not isinstance(value, int) or isinstance(value, bool):
        raise TokenRefused("plugin_install_id is not a number")
    return value


def _status(payload: Mapping[str, Any]) -> CommunityStatus:
    try:
        return CommunityStatus(payload.get("community_status"))
    except ValueError:
        raise TokenRefused("community_status is not one of Initiative's") from None


def _statement_key(pem: str | None) -> ed25519.Ed25519PublicKey | None:
    """Billing's key, when the deployment gives one; without it every statement
    is refused."""
    if not pem:
        return None
    key = serialization.load_pem_public_key(pem.encode())
    if not isinstance(key, ed25519.Ed25519PublicKey):
        raise ValueError("the statement key must be an Ed25519 public key")
    return key


def _statement(claims: Mapping[str, Any]) -> Statement:
    """The plan a statement names, from its verified claims: a whole ``version``
    from one, and ``values`` holding ``plan``, ``active`` and ``paid_through``.
    A statement that does not say ``active`` counts as active."""
    version = claims.get("version")
    values = claims.get("values")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise StatementRefused("claims")
    if not isinstance(values, Mapping):
        raise StatementRefused("claims")
    plan = values.get("plan")
    active = values.get("active", True)
    if (plan is not None and not isinstance(plan, str)) or not isinstance(active, bool):
        raise StatementRefused("claims")
    paid_through: datetime | None = None
    if values.get("paid_through") is not None:
        try:
            paid_through = datetime.fromisoformat(values["paid_through"])
        except (TypeError, ValueError):
            raise StatementRefused("claims") from None
        if paid_through.tzinfo is None:
            raise StatementRefused("claims")
    return Statement(version=version, plan=plan, active=active, paid_through=paid_through)
