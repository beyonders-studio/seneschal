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
import secrets
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import cache
from typing import Annotated, Any
from urllib.parse import urlencode

import sqlalchemy as sa
from alembic import op
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import CheckConstraint, Column, DateTime, LargeBinary, text
from sqlmodel import Field, col

from seneschal_core.api import ErrorCode, Plugin, PluginDep, refuse
from seneschal_core.auth import Me, admit, me_for
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


class SignInRow(OrganizationRow, table=True):
    """One renewal of a sign-in. A renewal spent is revoked, and the row that
    replaced it names it as its parent; together they are the sign-in's chain."""

    __tablename__ = "sign_ins"
    __table_args__ = (
        CheckConstraint("parent_id IS NULL OR parent_id <> id", name="ck_sign_ins_parent"),
        CheckConstraint("client IN ('browser', 'device')", name="ck_sign_ins_client"),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    parent_id: uuid.UUID | None = Field(default=None, index=True)
    #: The renewal's SHA-256; the renewal itself is never kept.
    token_hash: bytes = Field(sa_column=Column(LargeBinary, nullable=False, unique=True))
    #: Initiative's refresh token, sealed.
    initiative_refresh: str
    #: The member, by the app's reference for them.
    member_ref: str
    #: ``browser`` or ``device``.
    client: str
    #: The phone app's install, when a device signed in.
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
        sa.CheckConstraint("client IN ('browser', 'device')", name="ck_sign_ins_client"),
    )
    isolate("sign_ins")
    op.create_index("ix_sign_ins_parent_id", "sign_ins", ["parent_id"])


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
    ) -> None:
        #: The deployment's own address, which issues the ID tokens.
        self.issuer = issuer.rstrip("/")
        #: The manifest's ``return_path`` on the app's origin, exactly.
        self.redirect_uri = f"{app_origin.rstrip('/')}{API_PREFIX}{RETURN_PATH}"
        self.sealer = Sealer(sealing_key)
        self.cookie_secure = cookie_secure
        self.pending_cookie = f"{app}_sign_in"
        self.renewal_cookie = f"{app}_renew"

    def authorize_url(self, *, client_id: str, state: str, nonce: str, verifier: str) -> str:
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        query = urlencode(
            {
                "response_type": "code",
                "client_id": client_id,
                "redirect_uri": self.redirect_uri,
                "scope": "openid",
                "state": state,
                "nonce": nonce,
                "code_challenge": challenge.rstrip(b"=").decode(),
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


async def renew(plugin: Plugin, renewal: str, *, user_agent: str | None) -> Renewed:
    """Spend ``renewal``: ask Initiative again, and answer the claims as they
    stand now and the renewal that replaces it. Raises :class:`SignInEnded`
    when the person must sign in again, and
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


@router.get(
    "/sign-in/start",
    include_in_schema=False,
    responses={404: {"description": "SIGN_IN_NOT_OFFERED"}},
)
async def start_sign_in(
    plugin: PluginDep, next: Annotated[str | None, Query(max_length=2048)] = None
) -> RedirectResponse:
    """Send the person to Initiative to sign in, and back to ``next`` after."""
    sign_in = _offered(plugin)
    state, nonce, verifier = (secrets.token_urlsafe(32) for _ in range(3))
    response = RedirectResponse(
        sign_in.authorize_url(
            client_id=plugin.initiative.public_id, state=state, nonce=nonce, verifier=verifier
        ),
        status_code=status.HTTP_303_SEE_OTHER,
        headers=_NO_STORE,
    )
    sign_in.set_pending(
        response,
        sign_in.sealer.seal(
            "pending", {"state": state, "nonce": nonce, "verifier": verifier, "next": _local(next)}
        ),
    )
    return response


@router.get("/sign-in/return", include_in_schema=False)
async def finish_sign_in(request: Request, plugin: PluginDep) -> RedirectResponse:
    """Where Initiative sends the person back: spend the code, admit them,
    and give them a session and a renewal."""
    sign_in = _offered(plugin)
    params = request.query_params
    try:
        pending = sign_in.sealer.unseal(
            "pending", request.cookies.get(sign_in.pending_cookie, ""), max_age=PENDING_LIFETIME
        )
    except Unsealable:
        return _back(sign_in, "/", ErrorCode.SIGN_IN_EXPIRED)
    next_path = _local(pending.get("next"))
    # RFC 9207: the answer names who gave it.
    issuer = params.get("iss")
    if (issuer is not None and issuer != sign_in.issuer) or not secrets.compare_digest(
        params.get("state", ""), str(pending.get("state", ""))
    ):
        return _back(sign_in, next_path, ErrorCode.SIGN_IN_FAILED)
    if params.get("error") is not None:
        cancelled = params.get("error") == "access_denied"
        return _back(
            sign_in,
            next_path,
            ErrorCode.SIGN_IN_CANCELLED if cancelled else ErrorCode.SIGN_IN_FAILED,
        )
    code = params.get("code")
    if not code:
        return _back(sign_in, next_path, ErrorCode.SIGN_IN_FAILED)
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
        return _back(sign_in, next_path, ErrorCode.INITIATIVE_UNAVAILABLE)
    except (GrantRefused, TokenRefused):
        return _back(sign_in, next_path, ErrorCode.SIGN_IN_FAILED)
    except HTTPException as exc:
        return _back(sign_in, next_path, str(exc.detail))
    renewal = await open_chain(
        plugin,
        organization,
        claims,
        str(answer["refresh_token"]),
        client="browser",
        user_agent=_user_agent(request),
    )
    response = _back(sign_in, next_path)
    plugin.sessions.set_cookie(
        response,
        plugin.sessions.mint(
            claims,
            organization_id=organization.id,
            theme=None,
            locale=None,
            not_after=claims.expires_at,
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
