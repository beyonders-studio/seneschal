"""The core's tests run against a database holding the registry and
organization tables of their own, ``widgets`` and a ledger of their moves,
made by the same helpers an app's migrations call."""

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

from seneschal_core import ledger, migrations
from seneschal_core.api import Plugin, install
from seneschal_core.auth import router as auth_router
from seneschal_core.db import Database
from seneschal_core.platform import (
    ORGANIZATION_HOOKS,
    EndpointCall,
    platform_router,
    write_session,
)
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
                sa.Column("stock", sa.Numeric(), nullable=False, server_default="0"),
            )
            migrations.isolate("widgets")
            migrations.referable("widgets")
            # A ledger: moves of widgets, kept as each widget's stock and as
            # its stock in each bin.
            op.create_table(
                "widget_bins",
                sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
                sa.Column("widget_id", sa.BigInteger(), nullable=False),
                sa.Column("bin", sa.Text(), nullable=False),
                sa.Column("quantity", sa.Numeric(), nullable=False),
            )
            migrations.isolate("widget_bins")
            migrations.reference("widget_bins", "widget_id", "widgets", ondelete="CASCADE")
            op.create_unique_constraint(
                "uq_widget_bins", "widget_bins", ["organization_id", "widget_id", "bin"]
            )
            op.create_table(
                "widget_moves",
                sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
                sa.Column("widget_id", sa.BigInteger(), nullable=False),
                sa.Column("bin", sa.Text(), nullable=True),
                sa.Column("amount", sa.Numeric(), nullable=False),
            )
            migrations.isolate("widget_moves")
            migrations.reference("widget_moves", "widget_id", "widgets", ondelete="CASCADE")
            ledger.keep_balance(
                "widget_moves",
                amount="amount",
                balances="widgets",
                total="stock",
                keys={"id": "widget_id"},
                create=False,
            )
            ledger.keep_balance(
                "widget_moves",
                amount="amount",
                balances="widget_bins",
                total="quantity",
                keys={"widget_id": "widget_id", "bin": "bin"},
                create=True,
            )
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
        api=initiative_stand_in.api(),
        sessions=Sessions(app="core", private_key_pem=new_key_pem(), cookie_secure=False),
    )
    yield plugin
    await plugin.initiative.aclose()
    await plugin.api.aclose()


#: An endpoint only Initiative calls, and one other plug-ins may call too.
WHO = "plugin.beyonders-studio.core-test.who"
PUBLIC_WHO = "plugin.beyonders-studio.core-test.public-who"


async def _who(call: EndpointCall) -> dict[str, object]:
    """Who the call says made it, and who a change it makes is recorded as made by."""
    async with write_session(call) as db:
        return {"caller": call.claims.caller, "actor": db.actor}


@pytest.fixture
def app(plugin: Plugin) -> FastAPI:
    app = FastAPI()
    install(app, plugin)
    app.include_router(auth_router, prefix="/api/v1")
    app.include_router(
        platform_router(
            hooks=ORGANIZATION_HOOKS,
            endpoints={WHO: _who, PUBLIC_WHO: _who},
            public={PUBLIC_WHO},
        )
    )
    return app


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client
