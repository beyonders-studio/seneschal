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
from sqlmodel import select

from hoard import plugin_manifest
from hoard.app import create_app
from hoard.core.config import Settings
from hoard.db import upgrade
from hoard.purchases import receive_line
from hoard.records import (
    Adjustment,
    AdjustmentKind,
    Base,
    DefaultHoldReason,
    Hold,
    HoldReason,
    Location,
    Lot,
    Material,
    Order,
    OrderLine,
    OrderStatus,
    Purchase,
    PurchaseLine,
    Unit,
)
from hoard.stock import add_base, add_default_hold_reasons, add_material, adjust, main_location
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


@pytest.fixture
def base(db: WriteSession, unit: Unit) -> Callable[..., Awaitable[Base]]:
    """Add a base counted in ``unit``."""

    async def base(name: str, **fields: Any) -> Base:
        return await add_base(db, Base(name=name, tracking_unit_id=unit.id, **fields))

    return base


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
def lot(db: WriteSession, main: Location) -> Callable[..., Awaitable[Lot]]:
    """Add a lot of an item at the main location, unless another is named,
    starting with ``quantity`` at ``unit_cost`` each."""

    async def lot(
        item_id: int,
        number: str,
        quantity: str,
        dated_on: date = date(2026, 11, 1),
        *,
        expires_on: date | None = None,
        unit_cost: str = "1",
        location: Location | None = None,
    ) -> Lot:
        made = Lot(
            organization_id=db.organization_id,
            item_id=item_id,
            location_id=(location or main).id,
            number=number,
            dated_on=dated_on,
            expires_on=expires_on,
        )
        db.add(made)
        await db.flush()
        await adjust(
            db,
            Adjustment(
                item_id=item_id,
                location_id=made.location_id,
                lot_id=made.id,
                kind=AdjustmentKind.STARTING,
                dated_on=dated_on,
                change=Decimal(quantity),
                unit_cost=Decimal(unit_cost),
            ),
        )
        await db.refresh(made)
        return made

    return lot


@pytest.fixture
def hold(db: WriteSession) -> Callable[..., Awaitable[Hold]]:
    """Put a lot on hold, open, for one of the default hold reasons."""

    async def hold(
        lot: Lot, reason: DefaultHoldReason = DefaultHoldReason.SUSPECT, **fields: Any
    ) -> Hold:
        await add_default_hold_reasons(db)
        reason_id = (await db.exec(select(HoldReason.id).where(HoldReason.key == reason))).one()
        made = Hold(
            organization_id=db.organization_id, lot_id=lot.id, reason_id=reason_id, **fields
        )
        db.add(made)
        await db.flush()
        return made

    return hold


#: A line to sell: the item, its quantity and its price.
Sale = tuple[int, str, str]


@pytest.fixture
def sell(db: WriteSession, main: Location) -> Callable[..., Awaitable[Order]]:
    """Add an order by hand from the main location, open unless another
    status is named, with ``lines``."""

    async def sell(*lines: Sale, status: OrderStatus = OrderStatus.OPEN, **fields: Any) -> Order:
        order = Order(
            organization_id=db.organization_id,
            code=f"O-{secrets.token_hex(3)}",
            customer_contact_id=fields.pop("customer_contact_id", 1),
            location_id=fields.pop("location_id", main.id),
            ordered_on=fields.pop("ordered_on", date(2026, 11, 2)),
            status=status,
            **fields,
        )
        db.add(order)
        await db.flush()
        for item_id, quantity, price in lines:
            db.add(
                OrderLine(
                    organization_id=db.organization_id,
                    order_id=order.id,
                    item_id=item_id,
                    quantity=Decimal(quantity),
                    price=Decimal(price),
                )
            )
        await db.flush()
        return order

    return sell


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


SignIn = Callable[..., Awaitable[dict[str, Any]]]


@pytest.fixture
def sign_in(initiative_stand_in: InitiativeStandIn, community_ref: str) -> SignIn:
    """Sign ``client`` in to the test's community as a page does, as a member
    holding ``use`` and ``permissions`` (``admin``: one who administers the
    community), and answer who it is signed in as."""

    async def sign_in(
        client: AsyncClient,
        permissions: tuple[str, ...] = (),
        admin: bool = False,
        *,
        member_ref: str = MEMBER,
        initiative_id: int | None = 1,
    ) -> dict[str, Any]:
        token = initiative_stand_in.handoff(
            community_ref=community_ref,
            member_ref=member_ref,
            initiative_id=initiative_id,
            admin=admin,
            permissions=("use", *permissions),
        )
        answer = await client.post("/api/v1/auth/handoff/exchange", json={"handoff_token": token})
        assert answer.status_code == 200, answer.text
        return answer.json()

    return sign_in
