"""Hoard's tests run against a database its own migrations made, as the
serving login, with Initiative stood in for."""

from __future__ import annotations

import secrets
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from httpx2 import ASGITransport, AsyncClient

from hoard import plugin_manifest
from hoard.app import create_app
from hoard.core.config import Settings
from hoard.db import upgrade
from hoard.purchases import receive_line
from hoard.records import Location, Material, Purchase, PurchaseLine, Unit
from hoard.stock import add_material, main_location
from seneschal_core import organizations
from seneschal_core.api import Plugin
from seneschal_core.db import Database, WriteSession
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


#: The member every test's write session writes for.
MEMBER = "member-ref"


@pytest.fixture
async def organization_id(serving_database: Database, community_ref: str) -> int:
    """An organization of the test's own."""
    async with serving_database.session() as db:
        organization_id = await organizations.organization_for(
            db, community_ref=community_ref, plugin_install_id=1
        )
        await db.commit()
    return organization_id


@pytest.fixture
async def db(serving_database: Database, organization_id: int) -> AsyncIterator[WriteSession]:
    """A write session for the test's organization, as the serving login,
    writing for :data:`MEMBER`."""
    async with serving_database.write_session(organization_id, actor=MEMBER) as session:
        yield session


@pytest.fixture
async def main(db: WriteSession) -> Location:
    return await main_location(db)


@pytest.fixture
async def unit(db: WriteSession) -> Unit:
    unit = Unit(organization_id=db.organization_id, name="g")
    db.add(unit)
    await db.flush()
    return unit


@pytest.fixture
def material(db: WriteSession, unit: Unit) -> Callable[..., Awaitable[Material]]:
    """Add a material counted in ``unit``."""

    async def material(name: str, **fields: Any) -> Material:
        return await add_material(db, Material(name=name, tracking_unit_id=unit.id, **fields))

    return material


#: A line to buy: a material, its quantity and its unit cost.
Line = tuple[Material, str, str]


@pytest.fixture
def buy(db: WriteSession, main: Location) -> Callable[..., Awaitable[Purchase]]:
    """Buy ``lines`` on one purchase received at the main location, receiving
    each line in turn. A line's purchase and tracking quantities are the same."""

    async def buy(*lines: Line, dated_on: date = date(2026, 11, 1), **fields: Any) -> Purchase:
        purchase = Purchase(
            organization_id=db.organization_id,
            code=f"P-{secrets.token_hex(3)}",
            dated_on=dated_on,
            location_id=main.id,
            **fields,
        )
        db.add(purchase)
        await db.flush()
        for bought, quantity, unit_cost in lines:
            line = PurchaseLine(
                organization_id=db.organization_id,
                purchase_id=purchase.id,
                material_id=bought.id,
                purchase_quantity=Decimal(quantity),
                tracking_quantity=Decimal(quantity),
                unit_cost=Decimal(unit_cost),
            )
            db.add(line)
            await db.flush()
            await receive_line(db, line)
        return purchase

    return buy


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
        api=initiative_stand_in.api(),
        sessions=Sessions(
            app="hoard", private_key_pem=settings.session_private_key_pem, cookie_secure=False
        ),
    )
    yield create_app(settings, plugin)
    await plugin.initiative.aclose()
    await plugin.api.aclose()


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client
