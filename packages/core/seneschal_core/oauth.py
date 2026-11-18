"""The app as an OAuth 2.1 authorization server for MCP clients.

An MCP client (an assistant on the person's own computer) reaches the app's
MCP server (:mod:`seneschal_core.mcp`) with a Bearer session of its own. It
gets one the way the MCP authorization spec lays out, and the person signs in
with their Initiative account as they would at the app's own address
(:mod:`seneschal_core.sign_in`):

1. The MCP server answers a call without a session 401, naming its protected
   resource metadata (RFC 9728), which names the app as its authorization
   server; the app's metadata (RFC 8414) names the endpoints below.
2. The client registers (RFC 7591) at ``POST /auth/oauth/register``. Nothing
   is kept: its ``client_id`` is its registration, sealed. Only a loopback
   redirect URI is taken (RFC 8252), so the code goes back only to a program
   on the computer the person signed in on.
3. ``GET /auth/oauth/authorize`` starts the sign-in with the client's PKCE
   challenge (S256 only), its state and its redirect URI. The person signs in
   to Initiative and chooses the place, and the browser goes back to the
   client with a code: sealed, good for
   :data:`~seneschal_core.sign_in.MCP_CODE_LIFETIME`, spent at the first
   attempt.
4. ``POST /auth/oauth/token`` spends the code with the PKCE verifier, and
   answers the app's session as the access token (``via="mcp"``) and a renewal
   as the refresh token. A refresh token is spent once, as a renewal is: one
   presented again ends the whole sign-in, and Initiative is asked on every
   renewal whether the person may still open the place. A renewal is bound to
   the client it was made for.
5. ``POST /auth/oauth/revoke`` ends the sign-in a refresh token belongs to.

None of this is offered without Sign in with Initiative (the app's sealing key).
"""

from __future__ import annotations

import hashlib
import ipaddress
import re
import secrets
import time
from datetime import UTC, datetime
from typing import Annotated, Any
from urllib.parse import urlsplit

import jwt
from fastapi import APIRouter, Query, Request, status
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel
from pydantic import Field as BodyField

from seneschal_core.api import Plugin, PluginDep
from seneschal_core.auth import spend_jti
from seneschal_core.initiative_api import InitiativeUnavailable
from seneschal_core.sign_in import (
    API_PREFIX,
    MCP_CODE_LIFETIME,
    Renewed,
    SignIn,
    SignInEnded,
    Unsealable,
    _back_to_mcp_client,
    _offered,
    _s256,
    _user_agent,
    end,
    renew,
    send_to_initiative,
)

#: Where the app serves its MCP server: the protected resource.
MCP_PATH = f"{API_PREFIX}/mcp"
#: The authorization server's routes, under :data:`~seneschal_core.sign_in.API_PREFIX`.
REGISTER_PATH = "/auth/oauth/register"
AUTHORIZE_PATH = "/auth/oauth/authorize"
TOKEN_PATH = "/auth/oauth/token"
REVOKE_PATH = "/auth/oauth/revoke"
#: The well-known addresses of the metadata.
AUTHORIZATION_SERVER_METADATA = "/.well-known/oauth-authorization-server"
PROTECTED_RESOURCE_METADATA = "/.well-known/oauth-protected-resource"

#: The most redirect URIs one client registers.
_MOST_REDIRECT_URIS = 8
_LONGEST_REDIRECT_URI = 512
_LONGEST_CLIENT_NAME = 100
_LONGEST_STATE = 512
#: The longest ``client_id`` an authorize request carries.
_LONGEST_CLIENT_ID = 4096
_CHALLENGE = re.compile(r"^[A-Za-z0-9_-]{43}$")
_VERIFIER = re.compile(r"^[A-Za-z0-9._~-]{43,128}$")
_LOOPBACK_NAMES = frozenset({"localhost"})

_NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}


def resource(sign_in: SignIn) -> str:
    """The MCP server's address, which is the protected resource."""
    return f"{sign_in.app_origin}{MCP_PATH}"


def resource_metadata_url(sign_in: SignIn) -> str:
    """Where the MCP server's protected resource metadata is (RFC 9728 §3.1)."""
    return f"{sign_in.app_origin}{PROTECTED_RESOURCE_METADATA}{MCP_PATH}"


def _error(error: str, status_code: int = status.HTTP_400_BAD_REQUEST) -> JSONResponse:
    """An OAuth error answer (RFC 6749 §5.2)."""
    return JSONResponse({"error": error}, status_code=status_code, headers=_NO_STORE)


# --- redirect URIs and clients -------------------------------------------------


def _is_loopback(redirect_uri: str) -> bool:
    """Whether ``redirect_uri`` is an ``http`` address on the loopback
    interface (RFC 8252 §7.3), with no fragment and no user."""
    if len(redirect_uri) > _LONGEST_REDIRECT_URI:
        return False
    try:
        parts = urlsplit(redirect_uri)
        _ = parts.port  # a malformed port raises
    except ValueError:
        return False
    if parts.scheme != "http" or parts.fragment or parts.username or parts.password:
        return False
    host = parts.hostname
    if host is None:
        return False
    if host in _LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _same_except_port(registered: str, presented: str) -> bool:
    """Whether ``presented`` is ``registered`` but for its port, which a
    loopback client picks when it starts (RFC 8252 §7.3)."""
    a, b = urlsplit(registered), urlsplit(presented)
    return (a.scheme, a.hostname, a.path or "/", a.query) == (
        b.scheme,
        b.hostname,
        b.path or "/",
        b.query,
    )


def _fingerprint(client_id: str) -> str:
    """What a sign-in keeps of the client it was made for."""
    return "mcp-" + hashlib.sha256(client_id.encode()).hexdigest()[:32]


def _registered(sign_in: SignIn, client_id: str) -> dict[str, Any] | None:
    try:
        client = sign_in.sealer.unseal("mcp-client", client_id)
    except Unsealable:
        return None
    if not isinstance(client.get("r"), list):
        return None
    return client


# --- metadata ------------------------------------------------------------------

well_known_router = APIRouter(include_in_schema=False)


@well_known_router.get(AUTHORIZATION_SERVER_METADATA)
async def read_authorization_server_metadata(plugin: PluginDep) -> dict[str, Any]:
    """The app as an authorization server (RFC 8414)."""
    sign_in = _offered(plugin)
    base = f"{sign_in.app_origin}{API_PREFIX}"
    return {
        "issuer": sign_in.app_origin,
        "authorization_endpoint": f"{base}{AUTHORIZE_PATH}",
        "token_endpoint": f"{base}{TOKEN_PATH}",
        "registration_endpoint": f"{base}{REGISTER_PATH}",
        "revocation_endpoint": f"{base}{REVOKE_PATH}",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none"],
        "revocation_endpoint_auth_methods_supported": ["none"],
        "scopes_supported": [],
        "authorization_response_iss_parameter_supported": True,
    }


@well_known_router.get(PROTECTED_RESOURCE_METADATA)
@well_known_router.get(PROTECTED_RESOURCE_METADATA + "/{path:path}")
async def read_protected_resource_metadata(plugin: PluginDep) -> dict[str, Any]:
    """The MCP server as a protected resource (RFC 9728), at the well-known
    address with or without the server's path inserted."""
    sign_in = _offered(plugin)
    return {
        "resource": resource(sign_in),
        "authorization_servers": [sign_in.app_origin],
        "bearer_methods_supported": ["header"],
        "scopes_supported": [],
    }


# --- the authorization server ----------------------------------------------------

router = APIRouter(tags=["auth"], include_in_schema=False)


class ClientRegistration(BaseModel):
    """What a client registers (RFC 7591 §2). Fields it may send beside these
    are taken and not kept."""

    redirect_uris: list[str] = BodyField(min_length=1, max_length=_MOST_REDIRECT_URIS)
    client_name: str | None = BodyField(default=None, max_length=_LONGEST_CLIENT_NAME)
    token_endpoint_auth_method: str | None = None
    grant_types: list[str] | None = None
    response_types: list[str] | None = None


@router.post(REGISTER_PATH)
async def register_client(request: Request, plugin: PluginDep) -> JSONResponse:
    """Register an MCP client: answer its ``client_id``, its registration
    sealed, keeping nothing."""
    sign_in = _offered(plugin)
    try:
        body = ClientRegistration.model_validate(await request.json())
    except ValueError:
        return _error("invalid_client_metadata")
    if not all(_is_loopback(uri) for uri in body.redirect_uris):
        return _error("invalid_redirect_uri")
    if body.token_endpoint_auth_method not in (None, "none"):
        return _error("invalid_client_metadata")
    if not {"authorization_code", "refresh_token"}.issuperset(body.grant_types or ()):
        return _error("invalid_client_metadata")
    if not {"code"}.issuperset(body.response_types or ()):
        return _error("invalid_client_metadata")
    client_id = sign_in.sealer.seal("mcp-client", {"r": body.redirect_uris, "n": body.client_name})
    if len(client_id) > _LONGEST_CLIENT_ID:
        return _error("invalid_client_metadata")
    registered: dict[str, Any] = {
        "client_id": client_id,
        "client_id_issued_at": int(time.time()),
        "redirect_uris": body.redirect_uris,
        "token_endpoint_auth_method": "none",
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
    }
    if body.client_name is not None:
        registered["client_name"] = body.client_name
    return JSONResponse(registered, status_code=status.HTTP_201_CREATED, headers=_NO_STORE)


@router.get(AUTHORIZE_PATH)
async def authorize(
    plugin: PluginDep,
    client_id: Annotated[str, Query(max_length=_LONGEST_CLIENT_ID)] = "",
    redirect_uri: Annotated[str, Query(max_length=_LONGEST_REDIRECT_URI)] = "",
    response_type: Annotated[str, Query(max_length=32)] = "",
    code_challenge: Annotated[str, Query(max_length=128)] = "",
    code_challenge_method: Annotated[str, Query(max_length=16)] = "",
    state: Annotated[str | None, Query(max_length=_LONGEST_STATE)] = None,
) -> Response:
    """Start an MCP client's sign-in: send the person to Initiative, and back
    to the client's ``redirect_uri`` with a code its ``code_challenge`` binds.

    A request naming no client the app registered, or a redirect URI the
    client did not register, is refused here, and goes back nowhere."""
    sign_in = _offered(plugin)
    client = _registered(sign_in, client_id)
    if client is None:
        return _error("invalid_client")
    if not _is_loopback(redirect_uri) or not any(
        _same_except_port(str(registered), redirect_uri) for registered in client["r"]
    ):
        return _error("invalid_request")
    pending: dict[str, Any] = {
        "next": "/",
        "client": "mcp",
        "redirect_uri": redirect_uri,
        "client_state": state,
    }
    if response_type != "code":
        return _refused(sign_in, pending, "unsupported_response_type")
    if code_challenge_method != "S256" or not _CHALLENGE.fullmatch(code_challenge):
        return _refused(sign_in, pending, "invalid_request")
    pending |= {"challenge": code_challenge, "install": _fingerprint(client_id)}
    return send_to_initiative(plugin, pending)


def _refused(sign_in: SignIn, pending: dict[str, Any], error: str) -> Response:
    """Send the browser back to the client saying why its request was refused."""
    return _back_to_mcp_client(sign_in, pending, {"error": error})


def _tokens(plugin: Plugin, renewed: Renewed) -> JSONResponse:
    """The access token (the app's session for an MCP client) and the refresh
    token (its renewal), as a token answer (RFC 6749 §5.1)."""
    token = plugin.sessions.mint(
        renewed.claims,
        organization_id=renewed.organization.id,
        theme=None,
        locale=None,
        not_after=renewed.claims.expires_at,
        via="mcp",
    )
    expires = jwt.decode(token, options={"verify_signature": False})["exp"]
    return JSONResponse(
        {
            "access_token": token,
            "token_type": "Bearer",
            "expires_in": max(0, int(expires - datetime.now(UTC).timestamp())),
            "refresh_token": renewed.renewal,
        },
        headers=_NO_STORE,
    )


async def _renewed(plugin: Plugin, renewal: str, install: str, request: Request) -> JSONResponse:
    try:
        renewed = await renew(
            plugin, renewal, user_agent=_user_agent(request), client="mcp", install_id=install
        )
    except SignInEnded:
        return _error("invalid_grant")
    except InitiativeUnavailable:
        return _error("temporarily_unavailable", status.HTTP_503_SERVICE_UNAVAILABLE)
    return _tokens(plugin, renewed)


@router.post(TOKEN_PATH)
async def issue_token(request: Request, plugin: PluginDep) -> JSONResponse:
    """Spend a code or a refresh token, and answer an access token and the
    refresh token that replaces it. Any attempt spends a code: one that does
    not answer its challenge, or comes from another client or redirect URI,
    also ends the sign-in it was for."""
    sign_in = _offered(plugin)
    form = await request.form()
    field = {key: value for key, value in form.items() if isinstance(value, str)}
    client_id = field.get("client_id", "")
    if _registered(sign_in, client_id) is None:
        return _error("invalid_client", status.HTTP_401_UNAUTHORIZED)
    install = _fingerprint(client_id)
    grant_type = field.get("grant_type")
    if grant_type == "refresh_token":
        refresh_token = field.get("refresh_token", "")
        if not refresh_token:
            return _error("invalid_request")
        return await _renewed(plugin, refresh_token, install, request)
    if grant_type != "authorization_code":
        return _error("unsupported_grant_type")
    verifier = field.get("code_verifier", "")
    if not _VERIFIER.fullmatch(verifier):
        return _error("invalid_request")
    try:
        sealed = sign_in.sealer.unseal("mcp-code", field.get("code", ""), max_age=MCP_CODE_LIFETIME)
        organization_id = int(sealed["o"])
        jti, renewal = str(sealed["jti"]), str(sealed["renewal"])
        challenge, made_for = str(sealed["challenge"]), str(sealed["install"])
        redirect_uri = str(sealed["redirect_uri"])
    except (Unsealable, KeyError, TypeError, ValueError):
        return _error("invalid_grant")
    async with plugin.database.organization_session(organization_id) as db:
        first = await spend_jti(
            db,
            f"mcp-code:{jti}",
            organization_id=organization_id,
            expires_at=datetime.now(UTC) + MCP_CODE_LIFETIME,
        )
        await db.commit()
    if not first:
        return _error("invalid_grant")
    if not (
        secrets.compare_digest(_s256(verifier), challenge)
        and secrets.compare_digest(install, made_for)
        and secrets.compare_digest(field.get("redirect_uri", ""), redirect_uri)
    ):
        await end(plugin, renewal)
        return _error("invalid_grant")
    return await _renewed(plugin, renewal, install, request)


@router.post(REVOKE_PATH)
async def revoke_token(request: Request, plugin: PluginDep) -> Response:
    """End the sign-in a refresh token belongs to (RFC 7009). An access token,
    or a token the app does not know, is answered the same way, and ends
    nothing: an access token lasts only minutes."""
    _offered(plugin)
    form = await request.form()
    token = form.get("token")
    if isinstance(token, str) and token:
        await end(plugin, token)
    return Response(status_code=status.HTTP_200_OK, headers=_NO_STORE)
