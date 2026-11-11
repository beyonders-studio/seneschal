"""The core's tests run against a database holding the registry and one
organization table of their own, ``widgets``, made by the same helpers an
app's migrations call."""

from __future__ import annotations

import secrets
from collections.abc import AsyncIterator

import pytest
import sqlalchemy as sa
from alembic import op
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import FastAPI
from httpx2 import ASGITransport, AsyncClient

from seneschal_core import migrations
from seneschal_core.api import Plugin, install
from seneschal_core.auth import router as auth_router
from seneschal_core.db import Database
from seneschal_core.platform import ORGANIZATION_HOOKS, platform_router
from seneschal_core.sessions import Sessions
from seneschal_core.testing import AppDatabase, InitiativeStandIn, database_fixture, new_key_pem


def _migrate(database: AppDatabase) -> None:
    engine = sa.create_engine(database.owner_url)
    with engine.begin() as connection:
        context = MigrationContext.configure(
            connection, opts={"serving_login": database.serving_login}
        )
        with Operations.context(context):
            migrations.create_registry()
            op.create_table(
                "widgets",
                sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
                sa.Column("name", sa.Text(), nullable=False),
            )
            migrations.isolate("widgets")
    engine.dispose()


database = database_fixture("core", migrate=_migrate)


@pytest.fixture
def initiative_stand_in() -> InitiativeStandIn:
    return InitiativeStandIn(public_id="beyonders-studio.core-test", listing_uid="CORETEST000000")


@pytest.fixture
def community_ref() -> str:
    """A community no other test has seen."""
    return f"community-{secrets.token_hex(6)}"


@pytest.fixture
async def plugin(
    serving_database: Database, initiative_stand_in: InitiativeStandIn
) -> AsyncIterator[Plugin]:
    plugin = Plugin(
        database=serving_database,
        initiative=initiative_stand_in.initiative(),
        sessions=Sessions(app="core", private_key_pem=new_key_pem(), cookie_secure=False),
    )
    yield plugin
    await plugin.initiative.aclose()


@pytest.fixture
def app(plugin: Plugin) -> FastAPI:
    app = FastAPI()
    install(app, plugin)
    app.include_router(auth_router, prefix="/api/v1")
    app.include_router(platform_router(hooks=ORGANIZATION_HOOKS, endpoints={}))
    return app


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client
