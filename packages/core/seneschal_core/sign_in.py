"""Sign in with Initiative: a person opens the app at its own address.

Inside Initiative a page is framed and handed off (:mod:`seneschal_core.auth`).
At the app's own address nobody frames it, so the app signs the person in with
their Initiative account through OpenID Connect's authorization code flow, with
Initiative as the provider and the app's server as the client:

1. ``GET /auth/sign-in/start`` keeps a ``state``, a ``nonce`` and a PKCE
   verifier in a short sealed cookie and sends the person to Initiative's
   authorize page.
2. They sign in to Initiative and choose the place to open the app in: an
   initiative, or the community.
3. Initiative sends them back to ``GET /auth/sign-in/return`` with a code. The
   app spends it with its own key and checks the ID token, which carries what a
   handoff carries for that place, then admits them as a handoff would
   (:func:`~seneschal_core.auth.admit`).
4. They get the app's session, which lasts no longer than the ID token, and a
   **renewal**.

**A renewal is spent once.** It is a random secret, sealed with the organization
it belongs to, and kept only as its SHA-256 in ``sign_ins``, with Initiative's
refresh token sealed beside it. ``POST /auth/refresh`` spends it: the row is
claimed in one statement, Initiative is asked again, and a new row takes its
place in the sign-in's chain. Presenting a renewal that was already renewed is
taken as theft: the whole chain ends, for the thief and the person both.
Initiative checks on every renewal that the person is still signed in there
and may still open the page in that place, so signing out of Initiative,
leaving the community or the initiative, a changed role and an uninstall each
end the app's sign-in too, as does Initiative's own longest sign-in. A renewal
not used for :data:`BROWSER_IDLE` ends on its own.

``POST /auth/sign-out`` ends the chain and clears the cookies. It signs the
person out of the app, not out of Initiative.

**A phone app signs in the same way, through the system browser.** It starts
the sign-in with ``client=device``, its own PKCE challenge, its own ``state``
and its install's id. Once the person is admitted, the app opens a device
sign-in for that install (ending the install's earlier one), and sends the
browser back to the app (``<scheme>://sign-in/callback``) with a code: sealed,
good for :data:`DEVICE_CODE_LIFETIME`, spent at the first attempt. The app
spends it at ``POST /auth/device/token`` with its verifier and install, and
gets a session it sends as a Bearer token and a renewal it keeps itself, which
``POST /auth/device/refresh`` spends as the browser's is spent. A device's
renewal is bound to its install: presented from another, or at the browser's
route, it ends the sign-in.

**An MCP client signs in the same way too**, as an OAuth client of the app
(:mod:`seneschal_core.oauth`): its authorize request starts the sign-in with
``client=mcp``, and once the person is admitted the browser goes back to the
client's own redirect URI with a code the client spends at the app's token
endpoint. Its renewal is its refresh token, bound to the client it was made for.

The renewal travels in an HttpOnly cookie sent only to ``/api/v1/auth``, so no
other call carries it. Every answer here is ``no-store``. A renewal that is
refused clears both cookies and says only ``SIGN_IN_ENDED``.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import secrets
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import cache
from typing import Annotated, Any
from urllib.parse import urlencode

import jwt
import sqlalchemy as sa
from alembic import op
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel
from pydantic import Field as BodyField
from sqlalchemy import CheckConstraint, Column, DateTime, LargeBinary, text
from sqlmodel import Field, col

from seneschal_core.api import ErrorCode, Plugin, PluginDep, refuse
from seneschal_core.auth import Me, admit, me_for, spend_jti
from seneschal_core.db import OrganizationSession
from seneschal_core.initiative import HandoffClaims, TokenRefused
from seneschal_core.initiative_api import GrantRefused, InitiativeUnavailable
from seneschal_core.migrations import isolate
from seneschal_core.models import OrganizationRow
from seneschal_core.organizations import Organization

logger = logging.getLogger(__name__)

#: Where every app serves its API.
API_PREFIX = "/api/v1"
#: The routes, under :data:`API_PREFIX`.
START_PATH = "/auth/sign-in/start"
RETURN_PATH = "/auth/sign-in/return"
#: Initiative's authorize page, on the deployment's own address.
AUTHORIZE_PATH = "/oauth/authorize"
#: How long a person has to sign in to Initiative and come back.
PENDING_LIFETIME = timedelta(minutes=10)
#: How long a renewal from a browser stays good unused.
BROWSER_IDLE = timedelta(days=30)
#: How long a renewal from a phone app stays good unused.
DEVICE_IDLE = timedelta(days=90)
#: How long a phone app has to spend the code the browser brings it back with.
DEVICE_CODE_LIFETIME = timedelta(minutes=2)
#: Where the system browser hands a phone app its code, under its own scheme.
DEVICE_CALLBACK = "sign-in/callback"
#: The kinds of client a sign-in is made for: a browser at the app's own
#: address, a phone app, and an MCP client.
CLIENTS = ("browser", "device", "mcp")
#: How long an MCP client has to spend the code the browser brings it back with.
MCP_CODE_LIFETIME = timedelta(minutes=2)
#: A PKCE challenge (S256, base64url) and the verifier that answers it.
_CHALLENGE = re.compile(r"^[A-Za-z0-9_-]{43}$")
_VERIFIER = re.compile(r"^[A-Za-z0-9._~-]{43,128}$")
#: An install's id and a phone app's own state.
_INSTALL = re.compile(r"^[A-Za-z0-9-]{8,64}$")
_STATE = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
#: How long an ended chain's rows are kept before they are deleted.
KEEP_ENDED = timedelta(days=30)
#: The longest user agent kept beside a sign-in.
USER_AGENT_LIMIT = 512

_NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}


def issuer_for(public_api_url: str) -> str:
    """The deployment's own address, which issues its ID tokens, from its
    public API address (``https://initiative.example.com/api/v1``)."""
    return public_api_url.rstrip("/").removesuffix(API_PREFIX)


# --- sealing -----------------------------------------------------------------


class Unsealable(Exception):
    """Something the app did not seal, sealed for another use, or too old."""


class Sealer:
    """Seals what the app hands out and must read back unchanged: AES-256-GCM,
    under a key derived for each use from the app's sealing secret, so a value
    sealed for one use is never read as another's."""

    #: The least secret the sealing key is made from.
    MIN_SECRET_LENGTH = 32

    def __init__(self, secret: str) -> None:
        if len(secret) < self.MIN_SECRET_LENGTH:
            raise ValueError(
                f"the sealing key must be at least {self.MIN_SECRET_LENGTH} characters"
            )
        self._secret = secret.encode()

    @cache  # noqa: B019 - one per use, for the life of the sealer
    def _aead(self, use: str) -> AESGCM:
        key = HKDF(
            algorithm=hashes.SHA256(), length=32, salt=None, info=f"seneschal:{use}".encode()
        ).derive(self._secret)
        return AESGCM(key)

    def seal(self, use: str, payload: Mapping[str, Any]) -> str:
        nonce = os.urandom(12)
        body = json.dumps({**payload, "iat": int(time.time())}, separators=(",", ":"))
        sealed = self._aead(use).encrypt(nonce, body.encode(), use.encode())
        return base64.urlsafe_b64encode(nonce + sealed).rstrip(b"=").decode()

    def unseal(self, use: str, sealed: str, *, max_age: timedelta | None = None) -> dict[str, Any]:
        try:
            raw = base64.urlsafe_b64decode(sealed + "=" * (-len(sealed) % 4))
            body = json.loads(self._aead(use).decrypt(raw[:12], raw[12:], use.encode()))
        except (ValueError, InvalidTag, TypeError) as exc:
            raise Unsealable(use) from exc
        if not isinstance(body, dict) or not isinstance(body.get("iat"), int):
            raise Unsealable(use)
        if max_age is not None and time.time() - body["iat"] > max_age.total_seconds():
            raise Unsealable(f"{use}: too old")
        return body


# --- the chain of renewals -----------------------------------------------------


_CLIENT_CHECK = "client IN (" + ", ".join(f"'{client}'" for client in CLIENTS) + ")"


class SignInRow(OrganizationRow, table=True):
    """One renewal of a sign-in. A renewal spent is revoked, and the row that
    replaced it names it as its parent; together they are the sign-in's chain."""

    __tablename__ = "sign_ins"
    __table_args__ = (
        CheckConstraint("parent_id IS NULL OR parent_id <> id", name="ck_sign_ins_parent"),
        CheckConstraint(_CLIENT_CHECK, name="ck_sign_ins_client"),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    parent_id: uuid.UUID | None = Field(default=None, index=True)
    #: The renewal's SHA-256; the renewal itself is never kept.
    token_hash: bytes = Field(sa_column=Column(LargeBinary, nullable=False, unique=True))
    #: Initiative's refresh token, sealed.
    initiative_refresh: str
    #: The member, by the app's reference for them.
    member_ref: str
    #: One of :data:`CLIENTS`.
    client: str
    #: The phone app's install, when a device signed in; the client's
    #: fingerprint, when an MCP client did.
    install_id: str | None = None
    user_agent: str | None = None
    #: When the person signed in: the chain's first row's time, kept on each.
    started_at: datetime = Field(sa_type=DateTime(timezone=True))
    created_at: datetime = Field(sa_type=DateTime(timezone=True))
    #: When the renewal ends unused.
    expires_at: datetime = Field(sa_type=DateTime(timezone=True))
    revoked_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    #: ``renewed``, ``reused``, ``ended``, ``expired`` or ``signed_out``.
    revoked_reason: str | None = None


def create_table() -> None:
    """Make the ``sign_ins`` table, an organization's table, for :class:`SignInRow`."""
    moment = sa.DateTime(timezone=True)
    op.create_table(
        "sign_ins",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("parent_id", sa.Uuid(), nullable=True),
        sa.Column("token_hash", sa.LargeBinary(), nullable=False, unique=True),
        sa.Column("initiative_refresh", sa.Text(), nullable=False),
        sa.Column("member_ref", sa.Text(), nullable=False),
        sa.Column("client", sa.Text(), nullable=False),
        sa.Column("install_id", sa.Text(), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("started_at", moment, nullable=False),
        sa.Column("created_at", moment, nullable=False),
        sa.Column("expires_at", moment, nullable=False),
        sa.Column("revoked_at", moment, nullable=True),
        sa.Column("revoked_reason", sa.Text(), nullable=True),
        sa.CheckConstraint("parent_id IS NULL OR parent_id <> id", name="ck_sign_ins_parent"),
        sa.CheckConstraint(_CLIENT_CHECK, name="ck_sign_ins_client"),
    )
    isolate("sign_ins")
    op.create_index("ix_sign_ins_parent_id", "sign_ins", ["parent_id"])


def allow_clients() -> None:
    """Let ``sign_ins`` hold a sign-in for each of :data:`CLIENTS`, in a table
    an earlier :func:`create_table` made for fewer."""
    op.drop_constraint("ck_sign_ins_client", "sign_ins", type_="check")
    op.create_check_constraint("ck_sign_ins_client", "sign_ins", _CLIENT_CHECK)


def drop_table() -> None:
    op.drop_table("sign_ins")


#: Ends every live row of the chain ``:id`` is in: its ancestors to the first
#: sign-in, and every row descended from that.
_END_CHAIN = text("""
    WITH RECURSIVE up(id, parent_id) AS (
        SELECT id, parent_id FROM sign_ins WHERE id = :id
        UNION
        SELECT s.id, s.parent_id FROM sign_ins s JOIN up ON s.id = up.parent_id
    ),
    down(id) AS (
        SELECT id FROM up
        UNION
        SELECT s.id FROM sign_ins s JOIN down ON s.parent_id = down.id
    )
    UPDATE sign_ins SET revoked_at = now(), revoked_reason = :reason
    WHERE id IN (SELECT id FROM down) AND revoked_at IS NULL
    RETURNING id
""")

#: Claims a live renewal by its hash: the one statement two requests spending
#: it at once cannot both pass.
_CLAIM = text("""
    UPDATE sign_ins SET revoked_at = now(), revoked_reason = 'renewed'
    WHERE token_hash = :hash AND revoked_at IS NULL AND expires_at > now()
    RETURNING id, initiative_refresh, member_ref, client, install_id, started_at
""")


#: Ends an install's live sign-ins in the community, when the install signs in
#: anew: one live sign-in per member and install.
_END_INSTALL = text("""
    UPDATE sign_ins SET revoked_at = now(), revoked_reason = 'replaced'
    WHERE member_ref = :member AND install_id = :install AND revoked_at IS NULL
""")


def _s256(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def _hash(secret: str) -> bytes:
    return hashlib.sha256(secret.encode()).digest()


async def _end_chain(db: OrganizationSession, row_id: uuid.UUID, reason: str) -> None:
    """End the chain until a pass finds nothing left to end, so a renewal that
    committed while it was being ended is ended too."""
    while (await db.execute(_END_CHAIN, {"id": row_id, "reason": reason})).first() is not None:
        pass


class SignInEnded(Exception):
    """The renewal is spent, ended or not the app's: the person signs in again.
    ``code`` says why to the page when it can act on it."""

    def __init__(self, code: str = ErrorCode.SIGN_IN_ENDED) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class Renewed:
    claims: HandoffClaims
    organization: Organization
    #: The new renewal, to hand back in place of the one spent.
    renewal: str


# --- the client of Initiative ------------------------------------------------


class SignIn:
    """The app's Sign in with Initiative: where it sends people, where they
    come back, and how it seals what it hands them."""

    def __init__(
        self,
        *,
        app: str,
        issuer: str,
        app_origin: str,
        sealing_key: str,
        cookie_secure: bool,
        device_scheme: str | None = None,
    ) -> None:
        #: The deployment's own address, which issues the ID tokens.
        self.issuer = issuer.rstrip("/")
        #: Where people's browsers reach the app, which issues what it hands
        #: an MCP client.
        self.app_origin = app_origin.rstrip("/")
        #: The manifest's ``return_path`` on the app's origin, exactly.
        self.redirect_uri = f"{self.app_origin}{API_PREFIX}{RETURN_PATH}"
        self.sealer = Sealer(sealing_key)
        self.cookie_secure = cookie_secure
        self.pending_cookie = f"{app}_sign_in"
        self.renewal_cookie = f"{app}_renew"
        #: The phone app's URL scheme (``hoard``), where the system browser
        #: hands it its code; None when the app has no phone app.
        self.device_scheme = device_scheme

    def device_callback(self, params: Mapping[str, str]) -> str:
        return f"{self.device_scheme}://{DEVICE_CALLBACK}?{urlencode(params)}"

    def authorize_url(self, *, client_id: str, state: str, nonce: str, verifier: str) -> str:
        query = urlencode(
            {
                "response_type": "code",
                "client_id": client_id,
                "redirect_uri": self.redirect_uri,
                "scope": "openid",
                "state": state,
                "nonce": nonce,
                "code_challenge": _s256(verifier),
                "code_challenge_method": "S256",
            }
        )
        return f"{self.issuer}{AUTHORIZE_PATH}?{query}"

    def set_renewal(self, response: Response, renewal: str) -> None:
        response.set_cookie(
            self.renewal_cookie,
            renewal,
            max_age=int(BROWSER_IDLE.total_seconds()),
            httponly=True,
            secure=self.cookie_secure,
            samesite="lax",
            path=f"{API_PREFIX}/auth",
        )

    def clear_renewal(self, response: Response) -> None:
        response.delete_cookie(
            self.renewal_cookie, httponly=True, secure=self.cookie_secure, path=f"{API_PREFIX}/auth"
        )

    def set_pending(self, response: Response, pending: str) -> None:
        response.set_cookie(
            self.pending_cookie,
            pending,
            max_age=int(PENDING_LIFETIME.total_seconds()),
            httponly=True,
            secure=self.cookie_secure,
            # Initiative sends the person back by a top-level navigation,
            # which carries a Lax cookie.
            samesite="lax",
            path=f"{API_PREFIX}/auth/sign-in",
        )

    def clear_pending(self, response: Response) -> None:
        response.delete_cookie(
            self.pending_cookie,
            httponly=True,
            secure=self.cookie_secure,
            path=f"{API_PREFIX}/auth/sign-in",
        )


def _offered(plugin: Plugin) -> SignIn:
    if plugin.sign_in is None:
        raise refuse(status.HTTP_404_NOT_FOUND, ErrorCode.SIGN_IN_NOT_OFFERED)
    return plugin.sign_in


def _local(path: str | None) -> str:
    """``path`` when it is one of the app's own pages, else the app's front."""
    if (
        not path
        or len(path) > 2048
        or not path.startswith("/")
        or path.startswith("//")
        or "\\" in path
        or any(ord(ch) < 0x20 for ch in path)
        or path.startswith(f"{API_PREFIX}/")
    ):
        return "/"
    return path


def _user_agent(request: Request) -> str | None:
    agent = request.headers.get("user-agent")
    return agent[:USER_AGENT_LIMIT] if agent else None


async def _verified(plugin: Plugin, answer: Mapping[str, Any], nonce: str | None) -> HandoffClaims:
    id_token = answer.get("id_token")
    if not isinstance(id_token, str) or not isinstance(answer.get("refresh_token"), str):
        raise TokenRefused("the answer carries no ID token or refresh token")
    return await plugin.initiative.verify_id_token(
        id_token, issuer=_offered(plugin).issuer, nonce=nonce
    )


async def open_chain(
    plugin: Plugin,
    organization: Organization,
    claims: HandoffClaims,
    initiative_refresh: str,
    *,
    client: str,
    user_agent: str | None,
    install_id: str | None = None,
) -> str:
    """Start a sign-in's chain, and answer its first renewal."""
    sign_in = _offered(plugin)
    secret = secrets.token_urlsafe(32)
    now = datetime.now(UTC)
    idle = DEVICE_IDLE if client == "device" else BROWSER_IDLE
    async with plugin.database.organization_session(organization.id) as db:
        if install_id is not None:
            await db.execute(_END_INSTALL, {"member": claims.member_ref, "install": install_id})
        db.add(
            SignInRow(
                organization_id=organization.id,
                token_hash=_hash(secret),
                initiative_refresh=sign_in.sealer.seal(
                    "initiative-refresh", {"t": initiative_refresh}
                ),
                member_ref=claims.member_ref,
                client=client,
                install_id=install_id,
                user_agent=user_agent,
                started_at=now,
                created_at=now,
                expires_at=now + idle,
            )
        )
        await db.commit()
    return sign_in.sealer.seal("renewal", {"o": organization.id, "t": secret})


async def renew(
    plugin: Plugin,
    renewal: str,
    *,
    user_agent: str | None,
    client: str = "browser",
    install_id: str | None = None,
) -> Renewed:
    """Spend ``renewal``, made for ``client`` (and a device's for its
    ``install_id``): ask Initiative again, and answer the claims as they stand
    now and the renewal that replaces it. A renewal presented by another kind
    of client, or from another install, ends its sign-in. Raises
    :class:`SignInEnded` when the person must sign in again, and
    :class:`~seneschal_core.initiative_api.InitiativeUnavailable` when
    Initiative did not answer, leaving the renewal as it was."""
    sign_in = _offered(plugin)
    try:
        sealed = sign_in.sealer.unseal("renewal", renewal)
        organization_id, secret = int(sealed["o"]), str(sealed["t"])
    except (Unsealable, KeyError, TypeError, ValueError):
        raise SignInEnded from None
    async with plugin.database.organization_session(organization_id) as db:
        claimed = (await db.execute(_CLAIM, {"hash": _hash(secret)})).mappings().first()
        if claimed is None:
            await _refuse_unclaimed(db, secret, organization_id)
            await db.commit()
            raise SignInEnded
        row_id: uuid.UUID = claimed["id"]
        if claimed["client"] != client or claimed["install_id"] != install_id:
            logger.warning(
                "a renewal was presented by another client or install; its sign-in is ended "
                "(organization %s, chain row %s)",
                organization_id,
                row_id,
            )
            await db.execute(
                text("UPDATE sign_ins SET revoked_reason = 'misused' WHERE id = :id"),
                {"id": row_id},
            )
            await _end_chain(db, row_id, "misused")
            await db.commit()
            raise SignInEnded
        try:
            initiative_refresh = sign_in.sealer.unseal(
                "initiative-refresh", claimed["initiative_refresh"]
            )["t"]
            answer = await plugin.api.grant(
                {"grant_type": "refresh_token", "refresh_token": initiative_refresh}
            )
            claims = await _verified(plugin, answer, nonce=None)
            if claims.member_ref != claimed["member_ref"]:
                raise TokenRefused("the ID token names another member")
            organization = await admit(plugin, claims, spend=False)
            if organization.id != organization_id:
                raise TokenRefused("the ID token names another community")
        except InitiativeUnavailable:
            # Not the person's doing: the renewal stands, to be tried again.
            await db.rollback()
            raise
        except (GrantRefused, TokenRefused, Unsealable, KeyError) as exc:
            logger.info("a sign-in ended at renewal: %s", type(exc).__name__)
            await _end_chain(db, row_id, "ended")
            await db.commit()
            raise SignInEnded from None
        except HTTPException as exc:
            await _end_chain(db, row_id, "ended")
            await db.commit()
            raise SignInEnded(str(exc.detail)) from None
        next_secret = secrets.token_urlsafe(32)
        now = datetime.now(UTC)
        idle = DEVICE_IDLE if claimed["client"] == "device" else BROWSER_IDLE
        db.add(
            SignInRow(
                organization_id=organization_id,
                parent_id=row_id,
                token_hash=_hash(next_secret),
                initiative_refresh=sign_in.sealer.seal(
                    "initiative-refresh", {"t": answer["refresh_token"]}
                ),
                member_ref=claimed["member_ref"],
                client=claimed["client"],
                install_id=claimed["install_id"],
                user_agent=user_agent,
                started_at=claimed["started_at"],
                created_at=now,
                expires_at=now + idle,
            )
        )
        await db.execute(
            sa.delete(SignInRow).where(
                sa.or_(
                    col(SignInRow.revoked_at) < now - KEEP_ENDED,
                    col(SignInRow.expires_at) < now - KEEP_ENDED,
                )
            )
        )
        await db.commit()
    return Renewed(
        claims=claims,
        organization=organization,
        renewal=sign_in.sealer.seal("renewal", {"o": organization_id, "t": next_secret}),
    )


async def _refuse_unclaimed(db: OrganizationSession, secret: str, organization_id: int) -> None:
    """A renewal that could not be claimed: one already renewed is being
    presented again, and ends its chain; one that ran out unused is ended."""
    row = (
        await db.execute(
            sa.select(col(SignInRow.id), col(SignInRow.revoked_at)).where(
                col(SignInRow.token_hash) == _hash(secret)
            )
        )
    ).first()
    if row is None:
        return
    renewed = (
        await db.execute(
            sa.select(col(SignInRow.id)).where(col(SignInRow.parent_id) == row.id).limit(1)
        )
    ).first()
    if renewed is not None:
        logger.warning(
            "a renewal was presented again after it was renewed; its sign-in is ended "
            "(organization %s, chain row %s)",
            organization_id,
            row.id,
        )
        await _end_chain(db, row.id, "reused")
    elif row.revoked_at is None:
        await _end_chain(db, row.id, "expired")


async def end(plugin: Plugin, renewal: str) -> None:
    """End the sign-in ``renewal`` belongs to, wherever its chain has got to."""
    sign_in = _offered(plugin)
    try:
        sealed = sign_in.sealer.unseal("renewal", renewal)
        organization_id, secret = int(sealed["o"]), str(sealed["t"])
    except (Unsealable, KeyError, TypeError, ValueError):
        return
    async with plugin.database.organization_session(organization_id) as db:
        row = (
            await db.execute(
                sa.select(col(SignInRow.id)).where(col(SignInRow.token_hash) == _hash(secret))
            )
        ).first()
        if row is not None:
            await _end_chain(db, row.id, "signed_out")
            await db.commit()


# --- routes ------------------------------------------------------------------

router = APIRouter(prefix="/auth", tags=["auth"])


def _back(sign_in: SignIn, path: str, error: str | None = None) -> RedirectResponse:
    """Send the person to ``path`` on the app, or to its front saying why
    they could not sign in, and forget the sign-in that was pending."""
    target = path if error is None else f"/?{urlencode({'sign_in_error': error})}"
    response = RedirectResponse(target, status_code=status.HTTP_303_SEE_OTHER, headers=_NO_STORE)
    sign_in.clear_pending(response)
    return response


def _back_to_device(sign_in: SignIn, params: Mapping[str, str]) -> RedirectResponse:
    """Send the system browser back to the phone app with ``params``: its
    code and state, or why it could not sign in."""
    response = RedirectResponse(
        sign_in.device_callback(params), status_code=status.HTTP_303_SEE_OTHER, headers=_NO_STORE
    )
    sign_in.clear_pending(response)
    return response


#: The OAuth error an MCP client is sent back with, for why the person could
#: not sign in; any other reason is ``access_denied``.
_OAUTH_ERRORS = {ErrorCode.INITIATIVE_UNAVAILABLE.value: "temporarily_unavailable"}


def _back_to_mcp_client(
    sign_in: SignIn, pending: Mapping[str, Any], params: Mapping[str, str]
) -> RedirectResponse:
    """Send the browser back to the MCP client's redirect URI with ``params``,
    its own ``state`` and the app as the answer's issuer (RFC 9207)."""
    redirect_uri = str(pending["redirect_uri"])
    query = dict(params) | {"iss": sign_in.app_origin}
    if pending.get("client_state") is not None:
        query["state"] = str(pending["client_state"])
    separator = "&" if "?" in redirect_uri else "?"
    response = RedirectResponse(
        f"{redirect_uri}{separator}{urlencode(query)}",
        status_code=status.HTTP_303_SEE_OTHER,
        headers=_NO_STORE,
    )
    sign_in.clear_pending(response)
    return response


class SignInOffer(BaseModel):
    """Which sign-ins the app offers at its own address. A phone app reads it
    to know a server is the app it is for before it signs in there."""

    app: str
    browser: bool
    device: bool


@router.get("/sign-in")
async def read_sign_in_offer(plugin: PluginDep) -> SignInOffer:
    """The app, and whether people may sign in to it in a browser and from
    its phone app."""
    sign_in = plugin.sign_in
    return SignInOffer(
        app=plugin.sessions.app,
        browser=sign_in is not None,
        device=sign_in is not None and sign_in.device_scheme is not None,
    )


@router.get(
    "/sign-in/start",
    include_in_schema=False,
    responses={404: {"description": "SIGN_IN_NOT_OFFERED"}},
)
async def start_sign_in(
    plugin: PluginDep,
    next: Annotated[str | None, Query(max_length=2048)] = None,
    client: Annotated[str, Query(pattern="^(browser|device)$")] = "browser",
    challenge: Annotated[str | None, Query(max_length=43)] = None,
    state: Annotated[str | None, Query(max_length=128)] = None,
    install: Annotated[str | None, Query(max_length=64)] = None,
) -> RedirectResponse:
    """Send the person to Initiative to sign in, and back to ``next`` after;
    or, for the phone app (``client=device``), back to the app with a code
    its ``challenge`` binds, for its ``install``, carrying its ``state``."""
    sign_in = _offered(plugin)
    pending: dict[str, Any] = {"next": _local(next), "client": client}
    if client == "device":
        if (
            sign_in.device_scheme is None
            or not _CHALLENGE.fullmatch(challenge or "")
            or not _STATE.fullmatch(state or "")
            or not _INSTALL.fullmatch(install or "")
        ):
            raise refuse(status.HTTP_400_BAD_REQUEST, ErrorCode.REQUEST_INVALID)
        pending |= {"challenge": challenge, "device_state": state, "install": install}
    return send_to_initiative(plugin, pending)


def send_to_initiative(plugin: Plugin, pending: Mapping[str, Any]) -> RedirectResponse:
    """Send the person to Initiative's authorize page, keeping ``pending``
    (where to go after, and for which client) for their return."""
    sign_in = _offered(plugin)
    oidc_state, nonce, verifier = (secrets.token_urlsafe(32) for _ in range(3))
    response = RedirectResponse(
        sign_in.authorize_url(
            client_id=plugin.initiative.public_id,
            state=oidc_state,
            nonce=nonce,
            verifier=verifier,
        ),
        status_code=status.HTTP_303_SEE_OTHER,
        headers=_NO_STORE,
    )
    sign_in.set_pending(
        response,
        sign_in.sealer.seal(
            "pending", {**pending, "state": oidc_state, "nonce": nonce, "verifier": verifier}
        ),
    )
    return response


@router.get("/sign-in/return", include_in_schema=False)
async def finish_sign_in(request: Request, plugin: PluginDep) -> RedirectResponse:
    """Where Initiative sends the person back: spend the code, admit them,
    and give them a session and a renewal; or, for the phone app or an MCP
    client, send the browser back to it with a code for them."""
    sign_in = _offered(plugin)
    params = request.query_params
    try:
        pending = sign_in.sealer.unseal(
            "pending", request.cookies.get(sign_in.pending_cookie, ""), max_age=PENDING_LIFETIME
        )
    except Unsealable:
        return _back(sign_in, "/", ErrorCode.SIGN_IN_EXPIRED)
    next_path = _local(pending.get("next"))
    device = pending.get("client") == "device" and sign_in.device_scheme is not None
    mcp = pending.get("client") == "mcp"
    client = "device" if device else "mcp" if mcp else "browser"

    def fail(code: str) -> RedirectResponse:
        if device:
            return _back_to_device(
                sign_in, {"error": code, "state": str(pending.get("device_state", ""))}
            )
        if mcp:
            error = _OAUTH_ERRORS.get(code, "access_denied")
            return _back_to_mcp_client(
                sign_in, pending, {"error": error, "error_description": code}
            )
        return _back(sign_in, next_path, code)

    # RFC 9207: the answer names who gave it.
    issuer = params.get("iss")
    if (issuer is not None and issuer != sign_in.issuer) or not secrets.compare_digest(
        params.get("state", ""), str(pending.get("state", ""))
    ):
        return fail(ErrorCode.SIGN_IN_FAILED)
    if params.get("error") is not None:
        cancelled = params.get("error") == "access_denied"
        return fail(ErrorCode.SIGN_IN_CANCELLED if cancelled else ErrorCode.SIGN_IN_FAILED)
    code = params.get("code")
    if not code:
        return fail(ErrorCode.SIGN_IN_FAILED)
    try:
        answer = await plugin.api.grant(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": sign_in.redirect_uri,
                "code_verifier": str(pending.get("verifier", "")),
            }
        )
        claims = await _verified(plugin, answer, nonce=str(pending.get("nonce", "")))
        organization = await admit(plugin, claims, spend=False)
    except InitiativeUnavailable:
        return fail(ErrorCode.INITIATIVE_UNAVAILABLE)
    except (GrantRefused, TokenRefused):
        return fail(ErrorCode.SIGN_IN_FAILED)
    except HTTPException as exc:
        return fail(str(exc.detail))
    renewal = await open_chain(
        plugin,
        organization,
        claims,
        str(answer["refresh_token"]),
        client=client,
        user_agent=_user_agent(request),
        install_id=str(pending["install"]) if device or mcp else None,
    )
    if device:
        device_code = sign_in.sealer.seal(
            "device-code",
            {
                "o": organization.id,
                "jti": secrets.token_urlsafe(16),
                "renewal": renewal,
                "challenge": pending["challenge"],
                "install": pending["install"],
            },
        )
        return _back_to_device(
            sign_in, {"code": device_code, "state": str(pending["device_state"])}
        )
    if mcp:
        mcp_code = sign_in.sealer.seal(
            "mcp-code",
            {
                "o": organization.id,
                "jti": secrets.token_urlsafe(16),
                "renewal": renewal,
                "challenge": pending["challenge"],
                "redirect_uri": pending["redirect_uri"],
                "install": pending["install"],
            },
        )
        return _back_to_mcp_client(sign_in, pending, {"code": mcp_code})
    response = _back(sign_in, next_path)
    plugin.sessions.set_cookie(
        response,
        plugin.sessions.mint(
            claims,
            organization_id=organization.id,
            theme=None,
            locale=None,
            not_after=claims.expires_at,
            via="browser",
        ),
        framed=False,
    )
    sign_in.set_renewal(response, renewal)
    return response


def _ended(plugin: Plugin, sign_in: SignIn, code: str) -> JSONResponse:
    """A refused renewal, answered rather than raised so its cookies are cleared."""
    response = JSONResponse(
        {"detail": code}, status_code=status.HTTP_401_UNAUTHORIZED, headers=_NO_STORE
    )
    plugin.sessions.clear_cookie(response)
    sign_in.clear_renewal(response)
    return response


@router.post(
    "/refresh",
    response_model=Me,
    responses={
        401: {"description": "SIGN_IN_ENDED, or why it ended"},
        404: {"description": "SIGN_IN_NOT_OFFERED"},
        503: {"description": "INITIATIVE_UNAVAILABLE"},
    },
)
async def refresh_session(
    request: Request, response: Response, plugin: PluginDep
) -> Me | JSONResponse:
    """Renew the session of a person who signed in at the app's own address."""
    sign_in = _offered(plugin)
    response.headers.update(_NO_STORE)
    renewal = request.cookies.get(sign_in.renewal_cookie)
    if not renewal:
        return _ended(plugin, sign_in, ErrorCode.SIGN_IN_ENDED)
    try:
        renewed = await renew(plugin, renewal, user_agent=_user_agent(request))
    except SignInEnded as ended:
        return _ended(plugin, sign_in, ended.code)
    except InitiativeUnavailable:
        raise refuse(
            status.HTTP_503_SERVICE_UNAVAILABLE, ErrorCode.INITIATIVE_UNAVAILABLE
        ) from None
    token = plugin.sessions.mint(
        renewed.claims,
        organization_id=renewed.organization.id,
        theme=None,
        locale=None,
        not_after=renewed.claims.expires_at,
        via="browser",
    )
    plugin.sessions.set_cookie(response, token, framed=False)
    sign_in.set_renewal(response, renewed.renewal)
    return me_for(plugin.sessions.verify(token), renewed.organization)


@router.post("/sign-out", status_code=status.HTTP_204_NO_CONTENT)
async def sign_out(request: Request, plugin: PluginDep) -> Response:
    """End the sign-in at the app's own address, and forget its cookies. The
    person stays signed in to Initiative."""
    sign_in = _offered(plugin)
    renewal = request.cookies.get(sign_in.renewal_cookie)
    if renewal:
        await end(plugin, renewal)
    response = Response(status_code=status.HTTP_204_NO_CONTENT, headers=_NO_STORE)
    plugin.sessions.clear_cookie(response)
    sign_in.clear_renewal(response)
    return response


# --- the phone app -----------------------------------------------------------


class DeviceCode(BaseModel):
    """What the phone app sends for the code the browser brought it back with."""

    code: str = BodyField(min_length=1, max_length=4096)
    verifier: str = BodyField(min_length=43, max_length=128)
    install: str = BodyField(min_length=8, max_length=64)


class DeviceRenewal(BaseModel):
    """A phone app's renewal, from its install."""

    renewal: str = BodyField(min_length=1, max_length=1024)
    install: str = BodyField(min_length=8, max_length=64)


class DeviceSession(BaseModel):
    """A phone app's session, sent as a Bearer token until it expires, and
    the renewal that renews it, which the app keeps and spends once."""

    session: str
    session_expires_at: datetime
    renewal: str
    me: Me


def _device_session(plugin: Plugin, renewed: Renewed) -> DeviceSession:
    token = plugin.sessions.mint(
        renewed.claims,
        organization_id=renewed.organization.id,
        theme=None,
        locale=None,
        not_after=renewed.claims.expires_at,
        via="device",
    )
    session = plugin.sessions.verify(token)
    expires = jwt.decode(token, options={"verify_signature": False})["exp"]
    return DeviceSession(
        session=token,
        session_expires_at=datetime.fromtimestamp(expires, tz=UTC),
        renewal=renewed.renewal,
        me=me_for(session, renewed.organization),
    )


async def _renew_device(
    plugin: Plugin, renewal: str, install: str, request: Request
) -> DeviceSession:
    try:
        renewed = await renew(
            plugin, renewal, user_agent=_user_agent(request), client="device", install_id=install
        )
    except SignInEnded as ended:
        raise refuse(status.HTTP_401_UNAUTHORIZED, ErrorCode(ended.code)) from None
    except InitiativeUnavailable:
        raise refuse(
            status.HTTP_503_SERVICE_UNAVAILABLE, ErrorCode.INITIATIVE_UNAVAILABLE
        ) from None
    return _device_session(plugin, renewed)


@router.post(
    "/device/token",
    responses={
        400: {"description": "REQUEST_INVALID"},
        401: {"description": "SIGN_IN_FAILED, SIGN_IN_ENDED"},
        404: {"description": "SIGN_IN_NOT_OFFERED"},
        503: {"description": "INITIATIVE_UNAVAILABLE"},
    },
)
async def exchange_device_code(
    body: DeviceCode, request: Request, response: Response, plugin: PluginDep
) -> DeviceSession:
    """Spend the code the system browser brought the phone app back with, and
    answer its session and renewal. Any attempt spends the code: one that does
    not answer its challenge, or comes from another install, also ends the
    sign-in it was for."""
    sign_in = _offered(plugin)
    response.headers.update(_NO_STORE)
    failed = refuse(status.HTTP_401_UNAUTHORIZED, ErrorCode.SIGN_IN_FAILED)
    if not _VERIFIER.fullmatch(body.verifier) or not _INSTALL.fullmatch(body.install):
        raise refuse(status.HTTP_400_BAD_REQUEST, ErrorCode.REQUEST_INVALID)
    try:
        sealed = sign_in.sealer.unseal("device-code", body.code, max_age=DEVICE_CODE_LIFETIME)
        organization_id = int(sealed["o"])
        jti, renewal = str(sealed["jti"]), str(sealed["renewal"])
        challenge, install = str(sealed["challenge"]), str(sealed["install"])
    except (Unsealable, KeyError, TypeError, ValueError):
        raise failed from None
    async with plugin.database.organization_session(organization_id) as db:
        first = await spend_jti(
            db,
            f"device-code:{jti}",
            organization_id=organization_id,
            expires_at=datetime.now(UTC) + DEVICE_CODE_LIFETIME,
        )
        await db.commit()
    if not first:
        raise failed
    if not secrets.compare_digest(_s256(body.verifier), challenge) or not secrets.compare_digest(
        body.install, install
    ):
        await end(plugin, renewal)
        raise failed
    return await _renew_device(plugin, renewal, install, request)


@router.post(
    "/device/refresh",
    responses={
        401: {"description": "SIGN_IN_ENDED, or why it ended"},
        404: {"description": "SIGN_IN_NOT_OFFERED"},
        503: {"description": "INITIATIVE_UNAVAILABLE"},
    },
)
async def refresh_device_session(
    body: DeviceRenewal, request: Request, response: Response, plugin: PluginDep
) -> DeviceSession:
    """Renew a phone app's session: spend its renewal, from its install."""
    _offered(plugin)
    response.headers.update(_NO_STORE)
    return await _renew_device(plugin, body.renewal, body.install, request)


@router.post("/device/sign-out", status_code=status.HTTP_204_NO_CONTENT)
async def sign_out_device(body: DeviceRenewal, plugin: PluginDep) -> Response:
    """End a phone app's sign-in. The person stays signed in to Initiative."""
    _offered(plugin)
    await end(plugin, body.renewal)
    return Response(status_code=status.HTTP_204_NO_CONTENT, headers=_NO_STORE)


def phone_app_origins(app_id: str) -> list[str]:
    """The origins a phone app's pages run at, for its release and its dev
    build (``<app_id>.dev``): where its calls to the app's API come from."""
    return [
        f"{scheme}://{app}"
        for app in (app_id, f"{app_id}.dev")
        for scheme in ("https", "capacitor")
    ]
