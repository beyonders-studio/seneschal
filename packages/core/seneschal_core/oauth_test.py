"""An MCP client's sign-in: the app as its OAuth authorization server, with a
loopback redirect, a code spent once, a Bearer session, and a refresh token
bound to the client and spent once."""

from __future__ import annotations

import secrets

import pytest
from httpx2 import URL, AsyncClient

from seneschal_core.testing import InitiativeStandIn, McpClientStandIn

ORIGIN = "http://test"
ME = "/api/v1/auth/me"
AUTHORIZE = "/api/v1/auth/oauth/authorize"
REGISTER = "/api/v1/auth/oauth/register"
REVOKE = "/api/v1/auth/oauth/revoke"

pytestmark = pytest.mark.usefixtures("mcp_served")


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_the_metadata_names_the_app_as_the_mcp_servers_authorization_server(
    client: AsyncClient,
):
    resource = await client.get("/.well-known/oauth-protected-resource/api/v1/mcp")
    at_root = await client.get("/.well-known/oauth-protected-resource")
    server = await client.get("/.well-known/oauth-authorization-server")

    assert resource.json() == at_root.json()
    assert resource.json()["resource"] == f"{ORIGIN}/api/v1/mcp"
    assert resource.json()["authorization_servers"] == [ORIGIN]
    metadata = server.json()
    assert metadata["issuer"] == ORIGIN
    assert metadata["authorization_endpoint"] == f"{ORIGIN}{AUTHORIZE}"
    assert metadata["code_challenge_methods_supported"] == ["S256"]
    assert metadata["token_endpoint_auth_methods_supported"] == ["none"]


@pytest.mark.parametrize(
    "redirect_uri",
    [
        "https://assistant.example/callback",
        "http://assistant.example/callback",
        "http://127.0.0.1:53682/callback#fragment",
        "http://user@localhost:53682/callback",
        "custom-scheme://callback",
    ],
)
async def test_only_a_loopback_redirect_is_registered(client: AsyncClient, redirect_uri: str):
    answer = await client.post(REGISTER, json={"redirect_uris": [redirect_uri]})

    assert (answer.status_code, answer.json()) == (400, {"error": "invalid_redirect_uri"})


@pytest.mark.parametrize(
    "redirect_uri",
    ["http://127.0.0.1:53682/callback", "http://localhost:9/cb", "http://[::1]:8080/"],
)
async def test_a_loopback_redirect_is_registered(client: AsyncClient, redirect_uri: str):
    answer = await client.post(REGISTER, json={"redirect_uris": [redirect_uri]})

    assert answer.status_code == 201
    assert answer.json()["redirect_uris"] == [redirect_uri]
    assert answer.json()["token_endpoint_auth_method"] == "none"


async def test_an_authorize_request_for_an_unknown_client_or_redirect_goes_back_nowhere(
    client: AsyncClient,
):
    mcp = McpClientStandIn()
    await mcp.register(client)
    params = {
        "response_type": "code",
        "client_id": mcp.client_id,
        "redirect_uri": mcp.redirect_uri,
        "code_challenge": mcp.challenge,
        "code_challenge_method": "S256",
    }

    unknown = await client.get(AUTHORIZE, params=params | {"client_id": "made-up"})
    elsewhere = await client.get(
        AUTHORIZE, params=params | {"redirect_uri": "http://127.0.0.1:53682/other"}
    )
    plain = await client.get(AUTHORIZE, params=params | {"code_challenge_method": "plain"})

    assert (unknown.status_code, unknown.json()) == (400, {"error": "invalid_client"})
    assert (elsewhere.status_code, elsewhere.json()) == (400, {"error": "invalid_request"})
    # A known client and redirect is told why, at its redirect.
    assert plain.status_code == 303
    told = URL(plain.headers["location"])
    assert told.params["error"] == "invalid_request"
    assert str(told.copy_with(query=None)) == mcp.redirect_uri


async def test_a_loopback_redirect_may_come_back_on_another_port(
    client: AsyncClient, initiative_stand_in: InitiativeStandIn, community_ref: str
):
    mcp = McpClientStandIn()
    await mcp.register(client)
    mcp.redirect_uri = "http://127.0.0.1:61000/callback"

    url = await mcp.authorize(client, initiative_stand_in, community_ref=community_ref)

    assert url.params["code"]
    assert url.params["iss"] == ORIGIN


async def test_an_mcp_client_signs_in_and_calls_with_its_session(
    client: AsyncClient, initiative_stand_in: InitiativeStandIn, community_ref: str
):
    tokens = await McpClientStandIn().sign_in(
        client, initiative_stand_in, community_ref=community_ref, initiative_id=7
    )

    assert tokens["token_type"] == "Bearer"
    assert 0 < tokens["expires_in"] <= 15 * 60
    assert tokens["refresh_token"]
    me = await client.get(ME, headers=bearer(tokens["access_token"]))
    assert me.status_code == 200
    assert (me.json()["community_ref"], me.json()["member_ref"]) == (community_ref, "member-ref")
    assert me.json()["initiative_id"] == 7
    # Taken only as a Bearer token, never in the cookie.
    client.cookies.set("core_session", tokens["access_token"])
    assert (await client.get(ME)).status_code == 401


async def test_a_code_is_spent_at_the_first_attempt_and_a_wrong_one_ends_the_sign_in(
    client: AsyncClient, initiative_stand_in: InitiativeStandIn, community_ref: str
):
    mcp = McpClientStandIn()
    await mcp.register(client)
    code = (await mcp.authorize(client, initiative_stand_in, community_ref=community_ref)).params[
        "code"
    ]
    spend = {"grant_type": "authorization_code", "code": code, "redirect_uri": mcp.redirect_uri}

    wrong = await mcp.token(client, **spend, code_verifier=secrets.token_urlsafe(48))
    again = await mcp.token(client, **spend, code_verifier=mcp.verifier)

    assert (wrong.status_code, wrong.json()) == (400, {"error": "invalid_grant"})
    assert (again.status_code, again.json()) == (400, {"error": "invalid_grant"})


@pytest.mark.parametrize("presented", ["redirect_uri", "client_id"])
async def test_a_code_spent_by_another_client_or_redirect_is_refused(
    client: AsyncClient, initiative_stand_in: InitiativeStandIn, community_ref: str, presented: str
):
    mcp = McpClientStandIn()
    await mcp.register(client)
    code = (await mcp.authorize(client, initiative_stand_in, community_ref=community_ref)).params[
        "code"
    ]
    other = McpClientStandIn(redirect_uri="http://127.0.0.1:53682/elsewhere")
    await other.register(client)
    spend = {
        "grant_type": "authorization_code",
        "code": code,
        "code_verifier": mcp.verifier,
        "redirect_uri": mcp.redirect_uri,
    }

    if presented == "redirect_uri":
        answer = await mcp.token(client, **spend | {"redirect_uri": other.redirect_uri})
    else:
        answer = await other.token(client, **spend)

    assert (answer.status_code, answer.json()) == (400, {"error": "invalid_grant"})


async def test_a_refresh_token_spends_once_and_presented_again_ends_the_sign_in(
    client: AsyncClient, initiative_stand_in: InitiativeStandIn, community_ref: str
):
    mcp = McpClientStandIn()
    first = (await mcp.sign_in(client, initiative_stand_in, community_ref=community_ref))[
        "refresh_token"
    ]

    renewed = await mcp.token(client, grant_type="refresh_token", refresh_token=first)
    assert renewed.status_code == 200
    second = renewed.json()["refresh_token"]
    assert second != first
    me = await client.get(ME, headers=bearer(renewed.json()["access_token"]))
    assert me.status_code == 200

    replayed = await mcp.token(client, grant_type="refresh_token", refresh_token=first)
    current = await mcp.token(client, grant_type="refresh_token", refresh_token=second)

    assert (replayed.status_code, replayed.json()) == (400, {"error": "invalid_grant"})
    assert current.status_code == 400


async def test_a_refresh_token_from_another_client_ends_the_sign_in(
    client: AsyncClient, initiative_stand_in: InitiativeStandIn, community_ref: str
):
    mcp = McpClientStandIn()
    renewal = (await mcp.sign_in(client, initiative_stand_in, community_ref=community_ref))[
        "refresh_token"
    ]
    other = McpClientStandIn()
    await other.register(client)

    stolen = await other.token(client, grant_type="refresh_token", refresh_token=renewal)
    own = await mcp.token(client, grant_type="refresh_token", refresh_token=renewal)

    assert (stolen.status_code, own.status_code) == (400, 400)


async def test_an_mcp_clients_refresh_token_is_not_taken_at_a_phones_route(
    client: AsyncClient, initiative_stand_in: InitiativeStandIn, community_ref: str
):
    mcp = McpClientStandIn()
    renewal = (await mcp.sign_in(client, initiative_stand_in, community_ref=community_ref))[
        "refresh_token"
    ]

    device = await client.post(
        "/api/v1/auth/device/refresh",
        json={"renewal": renewal, "install": "a-phone-install-id"},
    )
    own = await mcp.token(client, grant_type="refresh_token", refresh_token=renewal)

    assert (device.status_code, own.status_code) == (401, 400)


async def test_revoking_a_refresh_token_ends_the_sign_in(
    client: AsyncClient, initiative_stand_in: InitiativeStandIn, community_ref: str
):
    mcp = McpClientStandIn()
    renewal = (await mcp.sign_in(client, initiative_stand_in, community_ref=community_ref))[
        "refresh_token"
    ]

    revoked = await client.post(REVOKE, data={"token": renewal, "client_id": mcp.client_id})
    renewed = await mcp.token(client, grant_type="refresh_token", refresh_token=renewal)

    assert revoked.status_code == 200
    assert renewed.status_code == 400


async def test_signing_out_of_initiative_ends_the_sign_in_at_its_next_refresh(
    client: AsyncClient, initiative_stand_in: InitiativeStandIn, community_ref: str
):
    mcp = McpClientStandIn()
    renewal = (await mcp.sign_in(client, initiative_stand_in, community_ref=community_ref))[
        "refresh_token"
    ]

    initiative_stand_in.end_sign_in()
    renewed = await mcp.token(client, grant_type="refresh_token", refresh_token=renewal)

    assert (renewed.status_code, renewed.json()) == (400, {"error": "invalid_grant"})


async def test_a_person_who_cancels_at_initiative_is_sent_back_to_the_client_saying_so(
    client: AsyncClient,
):
    mcp = McpClientStandIn()
    await mcp.register(client)
    started = await client.get(
        AUTHORIZE,
        params={
            "response_type": "code",
            "client_id": mcp.client_id,
            "redirect_uri": mcp.redirect_uri,
            "code_challenge": mcp.challenge,
            "code_challenge_method": "S256",
            "state": mcp.state,
        },
    )
    oidc_state = URL(started.headers["location"]).params["state"]

    returned = await client.get(
        "/api/v1/auth/sign-in/return",
        params={"error": "access_denied", "state": oidc_state},
    )

    told = URL(returned.headers["location"])
    assert str(told.copy_with(query=None)) == mcp.redirect_uri
    assert (told.params["error"], told.params["state"]) == ("access_denied", mcp.state)
    assert told.params["error_description"] == "SIGN_IN_CANCELLED"


async def test_an_unknown_grant_or_client_is_refused(client: AsyncClient):
    mcp = McpClientStandIn()
    await mcp.register(client)

    unknown_grant = await mcp.token(client, grant_type="password")
    unknown_client = await client.post(
        "/api/v1/auth/oauth/token",
        data={"client_id": "made-up", "grant_type": "refresh_token", "refresh_token": "x"},
    )

    assert unknown_grant.json() == {"error": "unsupported_grant_type"}
    assert (unknown_client.status_code, unknown_client.json()) == (
        401,
        {"error": "invalid_client"},
    )
