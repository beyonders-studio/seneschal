"""The request's organization, and the dependency every write goes through."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx2 import ASGITransport, AsyncClient
from sqlalchemy import text

from seneschal_core.api import OrganizationSessionDep, Plugin, WriteSessionDep, install
from seneschal_core.auth import router as auth_router
from seneschal_core.platform import ORGANIZATION_HOOKS, EndpointCall, platform_router


@pytest.fixture
async def client(plugin: Plugin) -> AsyncIterator[AsyncClient]:
    """An app with one read and one write of widgets."""
    app = FastAPI()
    install(app, plugin)
    app.include_router(auth_router, prefix="/api/v1")
    app.include_router(platform_router(hooks=ORGANIZATION_HOOKS, endpoints={}))

    @app.get("/widgets")
    async def read(db: OrganizationSessionDep) -> list[str]:
        return list((await db.execute(text("SELECT name FROM widgets"))).scalars())

    @app.post("/widgets", status_code=201)
    async def write(db: WriteSessionDep) -> None:
        await db.execute(
            text(
                "INSERT INTO widgets (organization_id, name) "
                "VALUES (current_setting('app.organization_id')::bigint, 'made')"
            )
        )
        await db.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


async def _signed_in(client, initiative_stand_in, community_ref, **claims) -> None:
    token = initiative_stand_in.handoff(community_ref=community_ref, **claims)
    exchanged = await client.post("/api/v1/auth/handoff/exchange", json={"handoff_token": token})
    assert exchanged.status_code == 200


async def _tell(client, initiative_stand_in, community_ref, hook, body) -> None:
    token = initiative_stand_in.lifecycle(hook, community_ref=community_ref)
    answer = await client.post(
        f"/v1/hooks/{hook}", json=body, headers={"Authorization": f"Bearer {token}"}
    )
    assert answer.status_code == 204


async def test_an_active_organization_writes_and_reads_its_own(
    client, initiative_stand_in, community_ref
):
    await _signed_in(client, initiative_stand_in, community_ref)

    assert (await client.post("/widgets")).status_code == 201
    assert (await client.get("/widgets")).json() == ["made"]


@pytest.mark.parametrize(
    ("hook", "body"),
    [
        ("community_status", {"status": "read_only"}),
        ("community_status", {"status": "on_hold"}),
        ("entitlement", "inactive"),
    ],
)
async def test_a_read_only_organization_reads_but_changes_nothing(
    client, initiative_stand_in, community_ref, hook, body
):
    await _signed_in(client, initiative_stand_in, community_ref)
    if body == "inactive":
        body = {
            "statement": initiative_stand_in.statement(1, {"plan": "workshop", "active": False})
        }
    await _tell(client, initiative_stand_in, community_ref, hook, body)

    refused = await client.post("/widgets")

    assert (refused.status_code, refused.json()) == (403, {"detail": "READ_ONLY"})
    assert (await client.get("/widgets")).status_code == 200
    assert (await client.get("/api/v1/auth/me")).json()["read_only"] is True


async def test_an_active_statement_writes_again(client, initiative_stand_in, community_ref):
    await _signed_in(client, initiative_stand_in, community_ref)
    for version, active in ((1, False), (2, True)):
        statement = initiative_stand_in.statement(version, {"plan": "workshop", "active": active})
        await _tell(
            client, initiative_stand_in, community_ref, "entitlement", {"statement": statement}
        )

    assert (await client.post("/widgets")).status_code == 201
    assert (await client.get("/api/v1/auth/me")).json()["plan"] == "workshop"


async def test_a_suspended_community_reaches_nothing(client, initiative_stand_in, community_ref):
    await _signed_in(client, initiative_stand_in, community_ref)
    await _tell(
        client, initiative_stand_in, community_ref, "community_status", {"status": "suspended"}
    )

    for answer in (await client.get("/widgets"), await client.get("/api/v1/auth/me")):
        assert (answer.status_code, answer.json()) == (403, {"detail": "COMMUNITY_UNAVAILABLE"})


async def test_an_endpoint_answers_in_initiatives_envelope(
    plugin: Plugin, initiative_stand_in, community_ref
):
    async def stock(call: EndpointCall) -> dict[str, object]:
        return {"community": call.claims.community_ref, "asked": dict(call.params)}

    app = FastAPI()
    install(app, plugin)
    app.include_router(platform_router(hooks={}, endpoints={"plugin.core-test.stock": stock}))
    token = initiative_stand_in.context("plugin.core-test.stock", community_ref=community_ref)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        answer = await client.post(
            "/v1/endpoints",
            json={"endpoint": "plugin.core-test.stock", "params": {"below": "3"}},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert answer.json() == {
        "endpoint": "plugin.core-test.stock",
        "actor": "installation",
        "result": {"community": community_ref, "asked": {"below": "3"}},
    }
