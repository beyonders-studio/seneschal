"""Hoard's tests run against a database its own migrations made, as the
serving login, with Initiative stood in for."""

from __future__ import annotations

import secrets
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx2 import ASGITransport, AsyncClient

from hoard import plugin_manifest
from hoard.app import create_app
from hoard.core.config import Settings
from hoard.db import upgrade
from seneschal_core.api import Plugin
from seneschal_core.db import Database
from seneschal_core.sessions import Sessions
from seneschal_core.testing import (
    AppDatabase,
    InitiativeStandIn,
    database_fixture,
    new_key_pem,
    public_key_pem,
)

database = database_fixture(
    "hoard", migrate=lambda made: upgrade(made.owner_url, made.serving_login)
)


@pytest.fixture
def initiative_stand_in() -> InitiativeStandIn:
    return InitiativeStandIn(public_id=plugin_manifest.PUBLIC_ID, listing_uid=plugin_manifest.UID)


@pytest.fixture
def community_ref() -> str:
    return f"community-{secrets.token_hex(6)}"


@pytest.fixture
def static_dir(tmp_path: Path) -> Path:
    """A build of Hoard's pages, its entry and one asset, with a file beside it."""
    built = tmp_path / "static"
    (built / "assets").mkdir(parents=True)
    (built / "index.html").write_text("<!doctype html><title>Hoard</title>")
    (built / "assets" / "app.js").write_text("console.log('hoard')")
    (tmp_path / "beside.txt").write_text("not a page")
    return built


@pytest.fixture
def settings(
    database: AppDatabase, initiative_stand_in: InitiativeStandIn, static_dir: Path
) -> Settings:
    return Settings(
        database_url=database.owner_url,
        database_url_app=database.serving_url,
        initiative_api_url=initiative_stand_in.API_URL,
        session_private_key_pem=new_key_pem(),
        entitlement_issuer_key_pem=public_key_pem(initiative_stand_in.billing_key),
        static_dir=static_dir,
    )


@pytest.fixture
async def app(
    settings: Settings, serving_database: Database, initiative_stand_in: InitiativeStandIn
) -> AsyncIterator[FastAPI]:
    """Hoard, reaching the stand-in for Initiative."""
    plugin = Plugin(
        database=serving_database,
        initiative=initiative_stand_in.initiative(),
        sessions=Sessions(
            app="hoard", private_key_pem=settings.session_private_key_pem, cookie_secure=False
        ),
    )
    yield create_app(settings, plugin)
    await plugin.initiative.aclose()


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client
