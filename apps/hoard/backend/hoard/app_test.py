"""Hoard's app: its API, the plug-in platform's routes and its pages."""

from __future__ import annotations

import pytest
from fastapi import FastAPI

from hoard import plugin_manifest
from hoard.app import create_app
from hoard.core.config import Settings
from seneschal_core.db import ServingLoginRefused


async def test_it_answers_that_it_is_up(client):
    answer = await client.get("/api/v1/healthz")

    assert (answer.status_code, answer.json()) == (200, {"status": "ok"})


async def test_it_starts_on_the_serving_login_and_refuses_the_owner(settings: Settings):
    async with create_app(settings).router.lifespan_context(FastAPI()):
        pass

    owner = settings.model_copy(update={"database_url_app": settings.database_url})
    with pytest.raises(ServingLoginRefused, match="owns tables"):
        async with create_app(owner).router.lifespan_context(FastAPI()):
            pass


async def test_a_page_signs_in_with_its_handoff(client, initiative_stand_in, community_ref):
    token = initiative_stand_in.handoff(
        community_ref=community_ref, member_ref="member-1", page="stock", initiative_id=4
    )

    exchanged = await client.post(
        "/api/v1/auth/handoff/exchange",
        json={"handoff_token": token, "theme": "light", "locale": "en"},
    )

    assert exchanged.status_code == 200
    assert exchanged.headers["set-cookie"].startswith("hoard_session=")
    assert (await client.get("/api/v1/auth/me")).json() == {
        "member_ref": "member-1",
        "community_ref": community_ref,
        "initiative_id": 4,
        "page": "stock",
        "admin": False,
        "permissions": ["use"],
        "theme": "light",
        "locale": "en",
        "read_only": False,
        "plan": None,
    }


async def test_initiative_reaches_hoards_hooks(client, initiative_stand_in, community_ref):
    async def call(hook: str, body: dict):
        token = initiative_stand_in.lifecycle(hook, community_ref=community_ref)
        return await client.post(
            f"/v1/hooks/{hook}", json=body, headers={"Authorization": f"Bearer {token}"}
        )

    statement = initiative_stand_in.statement(
        1, {"plan": "workshop", "active": True, "paid_through": "2026-12-11T00:00:00+00:00"}
    )
    for hook, body in (
        ("installed", {}),
        ("community_status", {"status": "active"}),
        ("entitlement", {"statement": statement}),
        ("uninstalled", {}),
        ("erase", {}),
    ):
        assert (await call(hook, body)).status_code == 204, hook
    assert (await call("schedule", {"schedule": "nightly", "since": None})).status_code == 404


async def test_each_page_opens_the_built_pages(client):
    for _, path, _, _ in plugin_manifest.PAGES:
        answer = await client.get(path)
        assert answer.status_code == 200, path
        assert answer.text.startswith("<!doctype html>")
        assert answer.headers["cache-control"] == "no-cache"

    asset = await client.get("/assets/app.js")
    assert (asset.status_code, asset.text) == (200, "console.log('hoard')")


async def test_only_pages_fall_back_to_the_built_pages(client):
    for path in ("/api/v1/nothing", "/v1/hooks/installed", "/.well-known/jwks.json"):
        assert (await client.get(path)).status_code == 404, path


async def test_nothing_outside_the_built_pages_is_served(client):
    answer = await client.get("/%2e%2e/beside.txt")

    assert "not a page" not in answer.text
