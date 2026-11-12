"""Where Initiative's plug-in platform calls an app, outside ``/api/v1``.

The protocol fixes the paths: ``POST /v1/endpoints`` answers every endpoint the
app declares, named in the body, and ``POST /v1/hooks/{name}`` every hook. Each
call carries a token Initiative signed in ``Authorization``, and that token is
the whole of its authentication: the community is the one the token names,
never one the body does.

Each app mounts :func:`platform_router` with its own handlers, and the ids of
the endpoints it declares public: only those take a call another plug-in made
through Initiative. A handler reads the call's organization through
:func:`organization_session`, and changes it through :func:`write_session`.
A hook it does not handle is answered 404, and Initiative stops sending it. The community's
own hooks, which keep the organization registry, are :data:`ORGANIZATION_HOOKS`:

* ``installed``: the organization, made on first sight;
* ``uninstalled``: nothing changes, and the community may come back to it all;
* ``erase``: the organization is deleted, and every row naming it with it;
* ``community_status``: where the community stands;
* ``entitlement``: billing's statement of the plan the community holds,
  ``{"statement": "<jws>"}``, kept once billing's signature verifies.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Awaitable, Callable, Collection, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Request, Response, status

from seneschal_core import organizations
from seneschal_core.api import ErrorCode, Plugin, PluginDep, refuse
from seneschal_core.db import OrganizationSession, WriteSession
from seneschal_core.initiative import ContextClaims, StatementRefused, TokenRefused
from seneschal_core.organizations import CommunityStatus

#: The most a call's body may hold. It is read before its token is checked.
MAX_BODY_BYTES = 64 * 1024


@dataclass(frozen=True)
class HookCall:
    plugin: Plugin
    claims: ContextClaims
    body: Mapping[str, Any]


@dataclass(frozen=True)
class EndpointCall:
    plugin: Plugin
    claims: ContextClaims
    params: Mapping[str, Any]


HookHandler = Callable[[HookCall], Awaitable[None]]
EndpointHandler = Callable[[EndpointCall], Awaitable[Any]]


class HookBodyInvalid(ValueError):
    """The hook's body is not the call its name says it is."""


async def _body(request: Request) -> dict[str, Any]:
    """The call's body, a JSON object of at most :data:`MAX_BODY_BYTES`."""
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_BODY_BYTES:
            raise refuse(status.HTTP_413_CONTENT_TOO_LARGE, ErrorCode.REQUEST_TOO_LARGE)
    try:
        call = json.loads(body) if body else {}
    except (UnicodeDecodeError, json.JSONDecodeError):
        call = None
    if not isinstance(call, dict):
        raise refuse(status.HTTP_400_BAD_REQUEST, ErrorCode.REQUEST_INVALID)
    return call


def _bearer(request: Request) -> str:
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise refuse(status.HTTP_401_UNAUTHORIZED, ErrorCode.INVALID_TOKEN)
    return token


async def _organization(call: EndpointCall) -> organizations.Organization:
    """The organization of the call's community, made on first sight. An
    unavailable community reads nothing."""
    if call.claims.community_status in organizations.UNAVAILABLE:
        raise refuse(status.HTTP_403_FORBIDDEN, ErrorCode.COMMUNITY_UNAVAILABLE)
    async with call.plugin.database.session() as db:
        organization_id = await organizations.organization_for(
            db,
            community_ref=call.claims.community_ref,
            plugin_install_id=call.claims.plugin_install_id,
        )
        await db.commit()
        return await db.get_one(organizations.Organization, organization_id)


@asynccontextmanager
async def organization_session(call: EndpointCall) -> AsyncIterator[OrganizationSession]:
    """A session reading the organization of the call's community."""
    organization = await _organization(call)
    async with call.plugin.database.organization_session(organization.id) as db:
        yield db


@asynccontextmanager
async def write_session(call: EndpointCall) -> AsyncIterator[WriteSession]:
    """A session changing the organization of the call's community, for the
    member the call names (:attr:`~seneschal_core.initiative.ContextClaims.member_ref`).
    A community that may not change is refused."""
    organization = await _organization(call)
    if organization.read_only or call.claims.community_status in organizations.READ_ONLY:
        raise refuse(status.HTTP_403_FORBIDDEN, ErrorCode.READ_ONLY)
    async with call.plugin.database.write_session(
        organization.id, actor=call.claims.member_ref
    ) as db:
        yield db


def platform_router(
    *,
    hooks: Mapping[str, HookHandler],
    endpoints: Mapping[str, EndpointHandler],
    public: Collection[str] = frozenset(),
) -> APIRouter:
    """The plug-in platform's routes, answering ``hooks`` by name and
    ``endpoints`` by endpoint id. Another plug-in calls only those in ``public``."""
    router = APIRouter(include_in_schema=False)

    @router.post("/v1/hooks/{name}", status_code=status.HTTP_204_NO_CONTENT)
    async def call_hook(name: str, request: Request, plugin: PluginDep) -> Response:
        handler = hooks.get(name)
        if handler is None:
            raise refuse(status.HTTP_404_NOT_FOUND, ErrorCode.HOOK_UNKNOWN)
        body = await _body(request)
        try:
            claims = await plugin.initiative.verify_lifecycle(_bearer(request), hook=name)
        except TokenRefused:
            raise refuse(status.HTTP_401_UNAUTHORIZED, ErrorCode.INVALID_TOKEN) from None
        try:
            await handler(HookCall(plugin=plugin, claims=claims, body=body))
        except HookBodyInvalid:
            raise refuse(status.HTTP_400_BAD_REQUEST, ErrorCode.REQUEST_INVALID) from None
        except StatementRefused as refused:
            raise refuse(
                status.HTTP_422_UNPROCESSABLE_CONTENT, ErrorCode.STATEMENT_REFUSED
            ) from refused
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.post("/v1/endpoints")
    async def call_endpoint(request: Request, plugin: PluginDep) -> dict[str, Any]:
        body = await _body(request)
        try:
            claims = await plugin.initiative.verify_context(_bearer(request), public=public)
        except TokenRefused:
            raise refuse(status.HTTP_401_UNAUTHORIZED, ErrorCode.INVALID_TOKEN) from None
        params = body.get("params", {})
        if body.get("endpoint") != claims.endpoint_id or not isinstance(params, dict):
            raise refuse(status.HTTP_400_BAD_REQUEST, ErrorCode.REQUEST_INVALID)
        handler = endpoints.get(claims.endpoint_id)
        if handler is None:
            raise refuse(status.HTTP_400_BAD_REQUEST, ErrorCode.ENDPOINT_UNKNOWN)
        result = await handler(EndpointCall(plugin=plugin, claims=claims, params=params))
        return {"endpoint": claims.endpoint_id, "actor": "installation", "result": result}

    return router


async def _installed(call: HookCall) -> None:
    async with call.plugin.database.session() as db:
        organization_id = await organizations.organization_for(
            db,
            community_ref=call.claims.community_ref,
            plugin_install_id=call.claims.plugin_install_id,
        )
        await organizations.set_status(db, organization_id, call.claims.community_status)
        await db.commit()


async def _uninstalled(call: HookCall) -> None:
    """Everything is kept: the community may install the app again."""


async def _erase(call: HookCall) -> None:
    async with call.plugin.database.session() as db:
        await organizations.erase(db, call.claims.community_ref)
        await db.commit()


async def _community_status(call: HookCall) -> None:
    try:
        community_status = CommunityStatus(call.body.get("status"))
    except ValueError:
        raise HookBodyInvalid("status is not one of Initiative's") from None
    async with call.plugin.database.session() as db:
        organization_id = await organizations.organization_for(
            db,
            community_ref=call.claims.community_ref,
            plugin_install_id=call.claims.plugin_install_id,
        )
        await organizations.set_status(db, organization_id, community_status)
        await db.commit()


async def _entitlement(call: HookCall) -> None:
    # Only the signed statement: nothing else in the body is read.
    statement = call.plugin.initiative.verify_statement(call.body.get("statement"))
    async with call.plugin.database.session() as db:
        organization_id = await organizations.organization_for(
            db,
            community_ref=call.claims.community_ref,
            plugin_install_id=call.claims.plugin_install_id,
        )
        await organizations.apply_statement(db, organization_id, statement)
        await db.commit()


ORGANIZATION_HOOKS: Mapping[str, HookHandler] = {
    "installed": _installed,
    "uninstalled": _uninstalled,
    "erase": _erase,
    "community_status": _community_status,
    "entitlement": _entitlement,
}
