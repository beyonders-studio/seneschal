"""An app's MCP server: behind its gate, admitting only an MCP client's
session, with each tool going through the app's own route as the person who
signed the client in."""

from __future__ import annotations

import json

import pytest
from httpx2 import AsyncClient

from seneschal_core.testing import InitiativeStandIn, McpClientStandIn, call_mcp

pytestmark = pytest.mark.usefixtures("mcp_served")


async def _access_token(
    client: AsyncClient, initiative: InitiativeStandIn, community_ref: str
) -> str:
    tokens = await McpClientStandIn().sign_in(client, initiative, community_ref=community_ref)
    return tokens["access_token"]


async def test_a_call_without_a_session_is_told_where_to_sign_in(client: AsyncClient):
    for path in ("/api/v1/mcp", "/api/v1/mcp/"):
        answer = await client.post(path, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})

        assert answer.status_code == 401
        assert answer.headers["www-authenticate"] == (
            'Bearer resource_metadata="http://test/.well-known/oauth-protected-resource/api/v1/mcp"'
        )


async def test_a_session_made_for_anything_but_an_mcp_client_is_refused(
    client: AsyncClient, initiative_stand_in: InitiativeStandIn, community_ref: str
):
    handoff = initiative_stand_in.handoff(community_ref=community_ref)
    await client.post("/api/v1/auth/handoff/exchange", json={"handoff_token": handoff})
    framed = client.cookies["core_session"]
    client.cookies.clear()

    answer = await call_mcp(client, framed, "tools/list")

    assert answer.status_code == 401
    assert 'error="invalid_token"' in answer.headers["www-authenticate"]


async def test_a_tool_calls_the_route_as_the_person_who_signed_the_client_in(
    client: AsyncClient, initiative_stand_in: InitiativeStandIn, community_ref: str
):
    token = await _access_token(client, initiative_stand_in, community_ref)

    listed = await call_mcp(client, token, "tools/list")
    assert listed.status_code == 200, listed.text
    tools = listed.json()["result"]["tools"]
    assert len(tools) == 1
    assert "outputSchema" not in tools[0]

    called = await call_mcp(client, token, "tools/call", name=tools[0]["name"], arguments={})

    assert called.status_code == 200, called.text
    result = called.json()["result"]
    assert result.get("isError") is not True
    me = json.loads(result["content"][0]["text"])
    assert (me["community_ref"], me["member_ref"]) == (community_ref, "member-ref")


async def test_the_server_is_reached_without_its_trailing_slash(
    client: AsyncClient, initiative_stand_in: InitiativeStandIn, community_ref: str
):
    token = await _access_token(client, initiative_stand_in, community_ref)

    answer = await client.post(
        "/api/v1/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json, text/event-stream",
        },
    )

    assert answer.status_code == 200, answer.text
