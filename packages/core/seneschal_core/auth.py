"""A page's sign-in: Initiative's handoff, exchanged once for the app's session.

1. The page, framed by Initiative, posts that it is ready; Initiative answers
   with a one-minute handoff token, the reader's theme and their locale.
2. The page sends them to ``POST /auth/handoff/exchange``. The app checks the
   token, trades the community's reference for its organization, spends the
   token's ``jti`` so it is taken once, and answers with who is signed in and
   a session cookie.
3. Later calls carry the cookie. ``GET /auth/me`` answers the same as the
   exchange did, with where the organization stands now. When the session has
   ended (401), the page asks Initiative for a fresh handoff.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Response, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import BigInteger, Column, DateTime, ForeignKey, delete
from sqlalchemy.dialects.postgresql import insert
from sqlmodel import Field as ModelField
from sqlmodel import SQLModel, col
from sqlmodel.ext.asyncio.session import AsyncSession

from seneschal_core import organizations
from seneschal_core.api import ErrorCode, OrganizationDep, PluginDep, SessionDep, refuse
from seneschal_core.db import REGISTRY_TABLE
from seneschal_core.initiative import HandoffClaims, TokenRefused
from seneschal_core.organizations import Organization
from seneschal_core.sessions import SESSION_LIFETIME, SessionClaims, Theme

#: How long a spent ``jti`` is kept past its token's expiry, for clocks that
#: disagree a little. Past it, the token's own expiry refuses it.
JTI_GRACE = timedelta(minutes=5)


class HandoffRedemption(SQLModel, table=True):
    """A page handoff already exchanged, by its ``jti``."""

    __tablename__ = "handoff_redemptions"

    jti: str = ModelField(primary_key=True)
    organization_id: int = ModelField(
        sa_column=Column(
            BigInteger,
            ForeignKey(f"{REGISTRY_TABLE}.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    expires_at: datetime = ModelField(sa_type=DateTime(timezone=True))


class HandoffExchange(BaseModel):
    """What the page sends: the handoff token and how Initiative is drawn."""

    handoff_token: str = Field(min_length=1, max_length=8192)
    theme: Theme | None = None
    locale: str | None = Field(default=None, min_length=1, max_length=35)

    @field_validator("locale")
    @classmethod
    def _locale_tag(cls, value: str | None) -> str | None:
        if value is not None and not all(
            ch.isascii() and (ch.isalnum() or ch == "-") for ch in value
        ):
            raise ValueError("a locale is a language tag")
        return value


class Me(BaseModel):
    """Who is signed in, where the page was opened, and where the
    organization stands."""

    #: The member, by this app's reference for them in the community.
    member_ref: str
    #: The community, by this app's reference for it.
    community_ref: str
    #: The initiative the page was opened in; null for a community-level page.
    initiative_id: int | None
    #: The page that was opened.
    page: str
    #: Whether the member administers the community.
    admin: bool
    #: The app's permissions the member holds where the page was opened.
    permissions: list[str]
    theme: Theme | None
    locale: str | None
    #: Whether the organization reads but changes nothing for now.
    read_only: bool
    #: The plan billing says the community holds; null before any statement.
    plan: str | None


def _me(session: SessionClaims, organization: Organization) -> Me:
    return Me(
        member_ref=session.member_ref,
        community_ref=session.community_ref,
        initiative_id=session.initiative_id,
        page=session.page,
        admin=session.admin,
        permissions=list(session.permissions),
        theme=session.theme,
        locale=session.locale,
        read_only=organization.read_only,
        plan=organization.plan,
    )


async def _spend(db: AsyncSession, claims: HandoffClaims, organization_id: int) -> bool:
    """Record the handoff's ``jti``: true the first time, false after."""
    await db.execute(
        delete(HandoffRedemption).where(
            col(HandoffRedemption.expires_at) < datetime.now(UTC) - JTI_GRACE
        )
    )
    spent = await db.execute(
        insert(HandoffRedemption)
        .values(jti=claims.jti, organization_id=organization_id, expires_at=claims.expires_at)
        .on_conflict_do_nothing(index_elements=["jti"])
        .returning(col(HandoffRedemption.jti))
    )
    return spent.first() is not None


router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/handoff/exchange",
    responses={
        401: {"description": "INVALID_HANDOFF_TOKEN"},
        403: {"description": "COMMUNITY_PAGE_ADMIN_ONLY or COMMUNITY_UNAVAILABLE"},
        409: {"description": "HANDOFF_ALREADY_REDEEMED"},
    },
)
async def exchange_handoff(body: HandoffExchange, response: Response, plugin: PluginDep) -> Me:
    """Exchange Initiative's page handoff for this app's session cookie."""
    try:
        claims = await plugin.initiative.verify_handoff(body.handoff_token)
    except TokenRefused:
        raise refuse(status.HTTP_401_UNAUTHORIZED, ErrorCode.INVALID_HANDOFF_TOKEN) from None
    # A community-level page reaches every initiative the app is placed in.
    if claims.initiative_id is None and not claims.admin:
        raise refuse(status.HTTP_403_FORBIDDEN, ErrorCode.COMMUNITY_PAGE_ADMIN_ONLY)

    async with plugin.database.session() as db:
        organization_id = await organizations.organization_for(
            db, community_ref=claims.community_ref, plugin_install_id=claims.plugin_install_id
        )
        await db.commit()
    async with plugin.database.organization_session(organization_id) as db:
        if not await _spend(db, claims, organization_id):
            raise refuse(status.HTTP_409_CONFLICT, ErrorCode.HANDOFF_ALREADY_REDEEMED)
        await organizations.set_status(db, organization_id, claims.community_status)
        await db.commit()
        organization = await organizations.get(db, organization_id)
    if organization is None or not organization.available:
        raise refuse(status.HTTP_403_FORBIDDEN, ErrorCode.COMMUNITY_UNAVAILABLE)

    token = plugin.sessions.mint(
        claims, organization_id=organization_id, theme=body.theme, locale=body.locale
    )
    secure = plugin.sessions.cookie_secure
    response.set_cookie(
        plugin.sessions.cookie_name,
        token,
        max_age=int(SESSION_LIFETIME.total_seconds()),
        httponly=True,
        secure=secure,
        # The page is framed by Initiative on another site in production, where
        # only a secure cookie marked None is sent.
        samesite="none" if secure else "lax",
        path="/",
    )
    return _me(plugin.sessions.verify(token), organization)


@router.get("/me", responses={401: {"description": "NOT_AUTHENTICATED"}})
async def read_me(session: SessionDep, organization: OrganizationDep) -> Me:
    """Who is signed in, for a page that already holds a session."""
    return _me(session, organization)
