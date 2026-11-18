"""An app's MCP server: routes of its own API as tools, for an MCP client
signed in as a person (:mod:`seneschal_core.oauth`).

Each tool is **route-backed**: a call goes through the app's real route, in
process, carrying the client's session, so the route's own checks (the
session, the organization and what the member may see and do there) apply by
reuse and are never written again here. Which routes become tools is the
app's to say, through a predicate (:func:`build_server`); a route it does not
name is not a tool.

The server is served at :data:`~seneschal_core.oauth.MCP_PATH` (with or without
its trailing slash) only behind :class:`McpGate`, which admits a request only
with an MCP client's session and otherwise answers 401 naming where the client
learns how to sign in. It is stateless and answers in JSON, so any of the
app's processes answers any call.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from typing import Any

import httpx2
from fastapi import FastAPI
from fastmcp import FastMCP
from fastmcp.server.dependencies import get_http_headers
from fastmcp.server.http import StarletteWithLifespan
from fastmcp.server.providers.openapi import MCPType, RouteMap
from fastmcp.utilities.openapi.models import HTTPRoute
from starlette.types import ASGIApp, Receive, Scope, Send

from seneschal_core.api import ErrorCode, Plugin
from seneschal_core.oauth import MCP_PATH, resource_metadata_url, well_known_router
from seneschal_core.oauth import router as oauth_router
from seneschal_core.sessions import SessionRefused, Sessions
from seneschal_core.sign_in import API_PREFIX, SignIn

#: Which of the app's routes are tools.
ToolFilter = Callable[[HTTPRoute], bool]


# --- what a model reads ----------------------------------------------------------

_SUBSCHEMA_KEYS = ("items", "additionalProperties", "not")
_SUBSCHEMA_LIST_KEYS = ("anyOf", "oneOf", "allOf", "prefixItems")
_SPHINX_ROLE = re.compile(r":(?:func|class|data|meth|attr|mod):`([^`]+)`")
_DOUBLE_BACKTICKS = re.compile(r"``([^`]+)``")


def _plain(text: str) -> str:
    """``text`` with its reStructuredText markup reduced to plain backticks."""
    return _DOUBLE_BACKTICKS.sub(r"`\1`", _SPHINX_ROLE.sub(r"`\1`", text))


def _subschemas(schema: Any) -> Iterator[dict[str, Any]]:
    """``schema`` and every schema nested in it. Only schema positions are
    walked, so a property named ``title`` is never taken for an annotation."""
    if not isinstance(schema, dict):
        return
    yield schema
    for sub in (schema.get("properties") or {}).values():
        yield from _subschemas(sub)
    for sub in (schema.get("$defs") or {}).values():
        yield from _subschemas(sub)
    for key in _SUBSCHEMA_KEYS:
        yield from _subschemas(schema.get(key))
    for key in _SUBSCHEMA_LIST_KEYS:
        for sub in schema.get(key) or ():
            yield from _subschemas(sub)


def _shape_tool(route: HTTPRoute, component: Any) -> None:
    """Publish a tool as a model reads it.

    * Its description and its arguments' in plain text.
    * No ``title`` annotations, which only repeat each field's name.
    * No output schema: it is most of what listing the tools sends, and is
      there for a client to check the answer against, not for a model to read.
    """
    component.description = _plain(component.description or route.summary or "")
    for sub in _subschemas(getattr(component, "parameters", None)):
        if isinstance(sub.get("title"), str):
            del sub["title"]
        if isinstance(sub.get("description"), str):
            sub["description"] = _plain(sub["description"])
    if getattr(component, "output_schema", None) is not None:
        component.output_schema = None


async def _forward_authorization(request: httpx2.Request) -> None:
    """Carry the MCP client's session to the route a tool calls.

    The client's headers are not forwarded unless named; the session is named
    here so the route authenticates the person who signed the client in, and
    never anyone else."""
    authorization = get_http_headers(include={"authorization"}).get("authorization")
    if authorization:
        request.headers["authorization"] = authorization


def build_server(app: FastAPI, *, name: str, instructions: str, tools: ToolFilter) -> FastMCP:
    """The app's MCP server: each of the app's routes ``tools`` admits, as a
    tool named after the route's function. Build it once the app's routers are
    included, so their routes are seen."""

    def route_type(route: HTTPRoute, _: MCPType) -> MCPType:
        return MCPType.TOOL if tools(route) else MCPType.EXCLUDE

    return FastMCP.from_fastapi(
        app=app,
        name=name,
        instructions=instructions,
        # Default-deny: a route is a tool only when ``tools`` says so.
        route_maps=[RouteMap(mcp_type=MCPType.EXCLUDE)],
        route_map_fn=route_type,
        mcp_component_fn=_shape_tool,
        httpx_client_kwargs={"event_hooks": {"request": [_forward_authorization]}},
    )


# --- where it is served ----------------------------------------------------------


class McpGate:
    """Admits a request to the MCP server only with an MCP client's session as
    a Bearer token; answers any other 401, naming the server's protected
    resource metadata so the client can sign in (RFC 9728 §5.1)."""

    def __init__(self, app: ASGIApp, *, sessions: Sessions, sign_in: SignIn) -> None:
        self.app = app
        self.sessions = sessions
        self.metadata = resource_metadata_url(sign_in)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        authorization = next(
            (value.decode("latin-1") for key, value in scope["headers"] if key == b"authorization"),
            None,
        )
        if authorization is None:
            await self._refuse(send, presented=False)
            return
        scheme, _, token = authorization.partition(" ")
        try:
            session = self.sessions.verify(token.strip()) if scheme.lower() == "bearer" else None
        except SessionRefused:
            session = None
        if session is None or session.via != "mcp":
            await self._refuse(send, presented=True)
            return
        await self.app(scope, receive, send)

    async def _refuse(self, send: Send, *, presented: bool) -> None:
        challenge = f'Bearer resource_metadata="{self.metadata}"'
        if presented:
            challenge += ', error="invalid_token"'
        body = b'{"detail":"' + ErrorCode.NOT_AUTHENTICATED.value.encode() + b'"}'
        await send(
            {
                "type": "http.response.start",
                "status": 401,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                    (b"www-authenticate", challenge.encode()),
                    (b"cache-control", b"no-store"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


class _BarePath:
    """Serves ``path`` as ``path/``: a mount matches only the spelling with
    the slash, and MCP clients differ over which one they send. Plain ASGI, so
    a streamed answer passes through."""

    def __init__(self, app: ASGIApp, *, path: str) -> None:
        self.app = app
        self.path = path

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["path"] == self.path:
            scope = {**scope, "path": f"{self.path}/"}
        await self.app(scope, receive, send)


def mount(app: FastAPI, plugin: Plugin, server: FastMCP) -> StarletteWithLifespan:
    """Serve ``server`` at :data:`~seneschal_core.oauth.MCP_PATH` behind
    :class:`McpGate`, and the authorization server and metadata an MCP client
    signs in by (:mod:`seneschal_core.oauth`). Mount it before any catch-all
    route. Answers the server's own app, whose lifespan
    must run while the app does."""
    if plugin.sign_in is None:
        raise ValueError("an MCP server needs Sign in with Initiative")
    served = server.http_app(
        path="/",
        stateless_http=True,
        json_response=True,
        # Every call carries its own Bearer session, never a cookie, and the
        # gate checks it before the server is reached.
        host_origin_protection=False,
    )
    app.include_router(well_known_router)
    app.include_router(oauth_router, prefix=API_PREFIX)
    app.mount(MCP_PATH, McpGate(served, sessions=plugin.sessions, sign_in=plugin.sign_in))
    app.add_middleware(_BarePath, path=MCP_PATH)
    return served
