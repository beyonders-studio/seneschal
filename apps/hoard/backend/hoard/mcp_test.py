"""Hoard's MCP server: the tools are the reads and the writes it names, and
each answers as the person who signed the assistant in."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import pytest
from fastapi import FastAPI
from httpx2 import AsyncClient

from hoard import plugin_manifest
from hoard.mcp import NOT_READ, READ_TAGS, WRITES, build_mcp_server
from seneschal_core.db import WriteSession
from seneschal_core.testing import InitiativeStandIn, McpClientStandIn, call_mcp, running


@pytest.fixture
async def served(app: FastAPI) -> AsyncIterator[FastAPI]:
    """Hoard, with its MCP server running."""
    async with running(app.state.mcp):
        yield app


def _operations(app: FastAPI) -> dict[str, tuple[str, list[str]]]:
    """Each of Hoard's routes by name: its method and its areas."""
    return {
        operation["operationId"]: (method.upper(), operation.get("tags", []))
        for item in app.openapi()["paths"].values()
        for method, operation in item.items()
    }


async def test_the_tools_are_the_reads_of_the_named_areas_and_the_named_writes(app: FastAPI):
    operations = _operations(app)
    tools = {tool.name for tool in await build_mcp_server(app).list_tools()}

    assert WRITES | NOT_READ <= operations.keys(), "a named route that does not exist"
    reads = {
        name
        for name, (method, tags) in operations.items()
        if method == "GET" and not READ_TAGS.isdisjoint(tags) and name not in NOT_READ
    }
    assert tools == reads | WRITES
    assert all(operations[name][0] != "DELETE" for name in tools)
    assert not any(operations[name][1] in (["stores"], ["imports"], ["auth"]) for name in tools)


async def _signed_in(
    client: AsyncClient,
    initiative: InitiativeStandIn,
    community_ref: str,
    permissions: tuple[str, ...] = (),
) -> str:
    tokens = await McpClientStandIn().sign_in(
        client, initiative, community_ref=community_ref, permissions=("use", *permissions)
    )
    return tokens["access_token"]


async def _call(client: AsyncClient, token: str, name: str, **arguments: Any) -> dict[str, Any]:
    answer = await call_mcp(client, token, "tools/call", name=name, arguments=arguments)
    assert answer.status_code == 200, answer.text
    return answer.json()["result"]


@pytest.mark.usefixtures("served")
async def test_an_assistant_reads_stock_as_the_person_who_signed_it_in(
    client: AsyncClient,
    initiative_stand_in: InitiativeStandIn,
    community_ref: str,
    db: WriteSession,
    material,
    buy,
):
    oolong = await material("Oolong")
    await buy((oolong, "4", "2.5"))
    await db.commit()

    without_costs = await _signed_in(client, initiative_stand_in, community_ref)
    listed = await _call(client, without_costs, "list_materials", q="Oolong")
    with_costs = await _signed_in(
        client, initiative_stand_in, community_ref, (plugin_manifest.SEE_COSTS,)
    )
    read = await _call(client, with_costs, "read_material", material_id=oolong.id)

    page = json.loads(listed["content"][0]["text"])
    assert [(row["name"], row["average_cost"]) for row in page["items"]] == [("Oolong", None)]
    assert json.loads(read["content"][0]["text"])["average_cost"] == "2.500000"


@pytest.mark.usefixtures("served")
async def test_a_route_that_is_not_a_tool_cannot_be_called(
    client: AsyncClient,
    initiative_stand_in: InitiativeStandIn,
    community_ref: str,
    db: WriteSession,
    material,
):
    oolong = await material("Oolong")
    await db.commit()
    token = await _signed_in(client, initiative_stand_in, community_ref)

    deleted = await _call(client, token, "delete_material", material_id=oolong.id)

    assert deleted["isError"] is True
    still = await _call(client, token, "read_material", material_id=oolong.id)
    assert json.loads(still["content"][0]["text"])["name"] == "Oolong"
