"""What an app's routes depend on: its database, its connection to Initiative
and its plug-in API, its sessions, and the request's organization.

An app builds one :class:`Plugin` and installs it on its FastAPI app
(:func:`install`). A page's request then names its organization only through
the session the app signed for the handoff Initiative signed: never through a
path, query, body or header.

* :data:`SessionDep`: the page's session, or 401.
* :data:`OrganizationDep`: its organization from the registry, or 401 when it
  was erased, or 403 when its community reaches nothing.
* :data:`OrganizationSessionDep`: a database session for that organization.
* :data:`WriteSessionDep`: the same, for a change, or 403 while the
  organization is read-only, writing for the page's member. Every write an
  app makes goes through it, and a service that writes takes its type,
  :class:`~seneschal_core.db.WriteSession`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Annotated

from fastapi import Depends, FastAPI, HTTPException, Request, status

from seneschal_core import organizations
from seneschal_core.db import Database, OrganizationSession, WriteSession, verify_serving_login
from seneschal_core.initiative import Initiative
from seneschal_core.initiative_api import InitiativeApi
from seneschal_core.organizations import Organization
from seneschal_core.sessions import BEARER_VIAS, SessionClaims, SessionRefused, Sessions

if TYPE_CHECKING:
    from seneschal_core.sign_in import SignIn


class ErrorCode(StrEnum):
    """Why a request was refused: the ``detail`` of every refusal, which pages
    translate."""

    NOT_AUTHENTICATED = "NOT_AUTHENTICATED"
    INVALID_HANDOFF_TOKEN = "INVALID_HANDOFF_TOKEN"
    HANDOFF_ALREADY_REDEEMED = "HANDOFF_ALREADY_REDEEMED"
    COMMUNITY_PAGE_ADMIN_ONLY = "COMMUNITY_PAGE_ADMIN_ONLY"
    COMMUNITY_UNAVAILABLE = "COMMUNITY_UNAVAILABLE"
    READ_ONLY = "READ_ONLY"
    INVALID_TOKEN = "INVALID_TOKEN"
    REQUEST_INVALID = "REQUEST_INVALID"
    REQUEST_TOO_LARGE = "REQUEST_TOO_LARGE"
    HOOK_UNKNOWN = "HOOK_UNKNOWN"
    ENDPOINT_UNKNOWN = "ENDPOINT_UNKNOWN"
    STATEMENT_REFUSED = "STATEMENT_REFUSED"
    JOB_NOT_FOUND = "JOB_NOT_FOUND"
    JOB_RESULT_NOT_FOUND = "JOB_RESULT_NOT_FOUND"
    JOB_STARTER_ONLY = "JOB_STARTER_ONLY"
    SIGN_IN_NOT_OFFERED = "SIGN_IN_NOT_OFFERED"
    SIGN_IN_ENDED = "SIGN_IN_ENDED"
    SIGN_IN_EXPIRED = "SIGN_IN_EXPIRED"
    SIGN_IN_FAILED = "SIGN_IN_FAILED"
    SIGN_IN_CANCELLED = "SIGN_IN_CANCELLED"
    INITIATIVE_UNAVAILABLE = "INITIATIVE_UNAVAILABLE"
    CROSS_SITE_REQUEST = "CROSS_SITE_REQUEST"


def refuse(status_code: int, code: StrEnum) -> HTTPException:
    """A refusal whose ``detail`` is ``code``: one of :class:`ErrorCode`, or of
    an app's own codes."""
    return HTTPException(status_code=status_code, detail=code.value)


@dataclass(frozen=True)
class Plugin:
    """An app's database (as its serving login), its connection to Initiative
    and the plug-in API it calls there, and its sessions."""

    database: Database
    initiative: Initiative
    api: InitiativeApi
    sessions: Sessions
    #: Sign in with Initiative at the app's own address; None when the app
    #: has no sealing key, and its pages open only inside Initiative.
    sign_in: SignIn | None = None

    async def start(self) -> None:
        """Before serving: refuse a serving login the organization policy does not bind."""
        await verify_serving_login(self.database.engine)

    async def stop(self) -> None:
        await self.initiative.aclose()
        await self.api.aclose()
        await self.database.dispose()


def install(app: FastAPI, plugin: Plugin) -> None:
    app.state.plugin = plugin


def _plugin(request: Request) -> Plugin:
    return request.app.state.plugin


PluginDep = Annotated[Plugin, Depends(_plugin)]


def _session(request: Request, plugin: PluginDep) -> SessionClaims:
    """The request's session: a phone app's or an MCP client's as a Bearer
    token, a page's in the cookie. Each is taken only the way it was made to
    travel."""
    unauthenticated = refuse(status.HTTP_401_UNAUTHORIZED, ErrorCode.NOT_AUTHENTICATED)
    authorization = request.headers.get("authorization")
    if authorization is not None:
        scheme, _, token = authorization.partition(" ")
        bearer = True
    else:
        scheme, token = "bearer", request.cookies.get(plugin.sessions.cookie_name, "")
        bearer = False
    if scheme.lower() != "bearer" or not token:
        raise unauthenticated
    try:
        session = plugin.sessions.verify(token.strip())
    except SessionRefused:
        raise unauthenticated from None
    if (session.via in BEARER_VIAS) != bearer:
        raise unauthenticated
    return session


SessionDep = Annotated[SessionClaims, Depends(_session)]


async def _organization(plugin: PluginDep, session: SessionDep) -> Organization:
    async with plugin.database.session() as db:
        organization = await organizations.get(db, session.organization_id)
    if organization is None:
        raise refuse(status.HTTP_401_UNAUTHORIZED, ErrorCode.NOT_AUTHENTICATED)
    if not organization.available:
        raise refuse(status.HTTP_403_FORBIDDEN, ErrorCode.COMMUNITY_UNAVAILABLE)
    return organization


OrganizationDep = Annotated[Organization, Depends(_organization)]


async def _organization_session(
    plugin: PluginDep, organization: OrganizationDep
) -> AsyncIterator[OrganizationSession]:
    async with plugin.database.organization_session(organization.id) as db:
        yield db


OrganizationSessionDep = Annotated[OrganizationSession, Depends(_organization_session)]


async def _write_session(
    plugin: PluginDep, session: SessionDep, organization: OrganizationDep
) -> AsyncIterator[WriteSession]:
    if organization.read_only:
        raise refuse(status.HTTP_403_FORBIDDEN, ErrorCode.READ_ONLY)
    async with plugin.database.write_session(organization.id, actor=session.member_ref) as db:
        yield db


WriteSessionDep = Annotated[WriteSession, Depends(_write_session)]
