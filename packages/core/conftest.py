"""The core's tests run against a database holding the registry, the jobs
table, the search index, and organization tables of their own: ``widgets`` and
a ledger of their moves, and ``gadgets`` and their parts to search, made by the
same helpers an app's migrations call."""

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

from seneschal_core import jobs, ledger, mcp, migrations, search, sign_in
from seneschal_core.api import Plugin, install
from seneschal_core.auth import router as auth_router
from seneschal_core.csrf import SameOriginOnly
from seneschal_core.db import Database
from seneschal_core.platform import (
    ORGANIZATION_HOOKS,
    EndpointCall,
    HookCall,
    platform_router,
    write_session,
)
from seneschal_core.sessions import Sessions
from seneschal_core.sign_in import SignIn
from seneschal_core.sign_in import router as sign_in_router
from seneschal_core.testing import (
    AppDatabase,
    InitiativeStandIn,
    database_fixture,
    new_key_pem,
    running,
)


def _migrate(database: AppDatabase) -> None:
    engine = sa.create_engine(database.owner_url)
    with engine.begin() as connection:
        context = MigrationContext.configure(
            connection, opts={"serving_login": database.serving_login}
        )
        with Operations.context(context):
            migrations.create_registry()
            migrations.add_plugin_status()
            jobs.create_table()
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
            search.create_table()
            sign_in.create_table()
            op.create_table(
                "gadgets",
                sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
                sa.Column("name", sa.Text(), nullable=False),
                sa.Column("code", sa.Text(), nullable=True),
                sa.Column("maker", sa.Text(), nullable=True),
                sa.Column("notes", sa.Text(), nullable=True),
                sa.Column("retired", sa.Boolean(), nullable=False, server_default=sa.false()),
            )
            migrations.isolate("gadgets")
            migrations.referable("gadgets")
            op.create_table(
                "gadget_parts",
                sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
                sa.Column("gadget_id", sa.BigInteger(), nullable=False),
                sa.Column("name", sa.Text(), nullable=False),
                sa.Column("sku", sa.Text(), nullable=True),
            )
            migrations.isolate("gadget_parts")
            migrations.reference("gadget_parts", "gadget_id", "gadgets", ondelete="CASCADE")
    engine.dispose()


database = database_fixture("core", migrate=_migrate)

#: Where the test app's pages are served, and its sign-in returns to.
APP_ORIGIN = "http://test"


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
        sign_in=SignIn(
            app="core",
            issuer=initiative_stand_in.ORIGIN,
            app_origin=APP_ORIGIN,
            sealing_key=secrets.token_urlsafe(32),
            cookie_secure=False,
            device_scheme="coretest",
        ),
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


async def _answer(call: HookCall) -> dict[str, object]:
    """A hook that answers: what its body held, and which community called."""
    return {"body": dict(call.body), "community_ref": call.claims.community_ref}


async def _received(call: HookCall) -> None:
    """A hook that answers nothing."""


@pytest.fixture
def app(plugin: Plugin) -> FastAPI:
    app = FastAPI()
    install(app, plugin)
    app.add_middleware(
        SameOriginOnly,
        cookie_names=["core_session", "core_renew"],
        origins=[APP_ORIGIN],
    )
    app.include_router(auth_router, prefix="/api/v1")
    app.include_router(sign_in_router, prefix="/api/v1")
    app.include_router(jobs.router, prefix="/api/v1")
    app.include_router(
        platform_router(
            hooks={**ORGANIZATION_HOOKS, "after_connect": _answer, "webhook": _received},
            endpoints={WHO: _who, PUBLIC_WHO: _who},
            public={PUBLIC_WHO},
        )
    )
    return app


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    # As the app's own pages call it: from its origin.
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url=APP_ORIGIN, headers={"Origin": APP_ORIGIN}
    ) as client:
        yield client


#: The one route the test app's MCP server makes a tool: who the session is.
MCP_TOOL_PATH = "/api/v1/auth/me"


@pytest.fixture
async def mcp_served(app: FastAPI, plugin: Plugin) -> AsyncIterator[FastAPI]:
    """The test app, serving an MCP server whose one tool reads who is signed in."""
    server = mcp.build_server(
        app,
        name="Core",
        instructions="Says who signed you in.",
        tools=lambda route: route.method == "GET" and route.path == MCP_TOOL_PATH,
    )
    served = mcp.mount(app, plugin, server)
    async with running(served):
        yield app
