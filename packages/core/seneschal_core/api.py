"""What an app's routes depend on: its database, its connection to Initiative
and its sessions, and the request's organization.

An app builds one :class:`Plugin` and installs it on its FastAPI app
(:func:`install`). A page's request then names its organization only through
the session the app signed for the handoff Initiative signed: never through a
path, query, body or header.

* :data:`SessionDep`: the page's session, or 401.
* :data:`OrganizationDep`: its organization from the registry, or 401 when it
  was erased, or 403 when its community reaches nothing.
* :data:`OrganizationSessionDep`: a database session for that organization.
* :data:`WriteSessionDep`: the same, for a change, or 403 while the
  organization is read-only. Every write an app makes goes through it.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request, status

from seneschal_core import organizations
from seneschal_core.db import Database, OrganizationSession, verify_serving_login
from seneschal_core.initiative import Initiative
from seneschal_core.organizations import Organization
from seneschal_core.sessions import SessionClaims, SessionRefused, Sessions


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


def refuse(status_code: int, code: ErrorCode) -> HTTPException:
    return HTTPException(status_code=status_code, detail=code.value)


@dataclass(frozen=True)
class Plugin:
    """An app's database (as its serving login), its connection to Initiative,
    and its sessions."""

    database: Database
    initiative: Initiative
    sessions: Sessions

    async def start(self) -> None:
        """Before serving: refuse a serving login the organization policy does not bind."""
        await verify_serving_login(self.database.engine)

    async def stop(self) -> None:
        await self.initiative.aclose()
        await self.database.dispose()


def install(app: FastAPI, plugin: Plugin) -> None:
    app.state.plugin = plugin


def _plugin(request: Request) -> Plugin:
    return request.app.state.plugin


PluginDep = Annotated[Plugin, Depends(_plugin)]


def _session(request: Request, plugin: PluginDep) -> SessionClaims:
    token = request.cookies.get(plugin.sessions.cookie_name)
    if not token:
        raise refuse(status.HTTP_401_UNAUTHORIZED, ErrorCode.NOT_AUTHENTICATED)
    try:
        return plugin.sessions.verify(token)
    except SessionRefused:
        raise refuse(status.HTTP_401_UNAUTHORIZED, ErrorCode.NOT_AUTHENTICATED) from None


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
    plugin: PluginDep, organization: OrganizationDep
) -> AsyncIterator[OrganizationSession]:
    if organization.read_only:
        raise refuse(status.HTTP_403_FORBIDDEN, ErrorCode.READ_ONLY)
    async with plugin.database.organization_session(organization.id) as db:
        yield db


WriteSessionDep = Annotated[OrganizationSession, Depends(_write_session)]
