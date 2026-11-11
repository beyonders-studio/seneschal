"""Organizations kept apart, as the serving login meets them."""

from __future__ import annotations

import secrets
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

from seneschal_core import organizations
from seneschal_core.db import (
    Database,
    ServingLoginRefused,
    create_async_engine,
    verify_serving_login,
)
from seneschal_core.testing import AppDatabase


async def _organization(database: Database) -> int:
    async with database.session() as db:
        organization_id = await organizations.organization_for(
            db, community_ref=f"community-{secrets.token_hex(6)}", plugin_install_id=1
        )
        await db.commit()
    return organization_id


async def _add_widget(database: Database, organization_id: int, name: str) -> None:
    async with database.organization_session(organization_id) as db:
        await db.execute(
            text("INSERT INTO widgets (organization_id, name) VALUES (:org, :name)"),
            {"org": organization_id, "name": name},
        )
        await db.commit()


async def _widgets(db) -> list[str]:
    return list((await db.execute(text("SELECT name FROM widgets ORDER BY name"))).scalars())


async def test_without_an_organization_nothing_is_read_or_written(serving_database: Database):
    organization_id = await _organization(serving_database)
    await _add_widget(serving_database, organization_id, "kept")

    async with serving_database.session() as db:
        assert await _widgets(db) == []
        with pytest.raises(DBAPIError, match="row-level security"):
            await db.execute(
                text("INSERT INTO widgets (organization_id, name) VALUES (:org, 'stray')"),
                {"org": organization_id},
            )


async def test_one_organization_reads_and_writes_none_of_anothers(serving_database: Database):
    first, second = await _organization(serving_database), await _organization(serving_database)
    await _add_widget(serving_database, first, "first's")
    await _add_widget(serving_database, second, "second's")

    async with serving_database.organization_session(first) as db:
        assert await _widgets(db) == ["first's"]
        updated = await db.execute(text("UPDATE widgets SET name = 'taken' WHERE true"))
        assert updated.rowcount == 1
        deleted = await db.execute(
            text("DELETE FROM widgets WHERE organization_id = :other"), {"other": second}
        )
        assert deleted.rowcount == 0
        with pytest.raises(DBAPIError, match="row-level security"):
            await db.execute(
                text("INSERT INTO widgets (organization_id, name) VALUES (:other, 'planted')"),
                {"other": second},
            )

    async with serving_database.organization_session(second) as db:
        assert await _widgets(db) == ["second's"]


async def test_a_row_references_only_its_own_organizations_rows(serving_database: Database):
    first, second = await _organization(serving_database), await _organization(serving_database)
    await _add_widget(serving_database, second, "second's")
    async with serving_database.organization_session(second) as db:
        theirs = (await db.execute(text("SELECT id FROM widgets"))).scalar_one()

    async with serving_database.organization_session(first) as db:
        with pytest.raises(DBAPIError, match="foreign key"):
            await db.execute(
                text(
                    "INSERT INTO widget_moves (organization_id, widget_id, amount) "
                    "VALUES (:org, :widget, 1)"
                ),
                {"org": first, "widget": theirs},
            )


async def test_the_organization_is_set_for_every_transaction_and_outlasts_none(
    database: AppDatabase,
):
    # One connection, so the session after reuses the one before.
    served = Database(database.serving_url, pool_size=1, max_overflow=0)
    try:
        organization_id = await _organization(served)
        await _add_widget(served, organization_id, "mine")
        async with served.organization_session(organization_id) as db:
            assert await _widgets(db) == ["mine"]
            await db.commit()
            assert await _widgets(db) == ["mine"]
        async with served.session() as db:
            assert await _widgets(db) == []
    finally:
        await served.dispose()


@pytest.fixture
async def engine_as(database: AppDatabase) -> AsyncIterator:
    """An engine signed in as a login made for the test, given ``attributes``."""
    engines: list[AsyncEngine] = []
    logins: list[str] = []
    admin = create_async_engine(database.superuser_url, isolation_level="AUTOCOMMIT")

    async def make(attributes: str = "", member_of: str | None = None) -> AsyncEngine:
        login = f"{database.name}_{secrets.token_hex(3)}"
        async with admin.connect() as connection:
            await connection.execute(text(f"CREATE ROLE {login} LOGIN PASSWORD 'x' {attributes}"))
            if member_of:
                await connection.execute(text(f"GRANT {member_of} TO {login}"))
        logins.append(login)
        url = make_url(database.serving_url).set(username=login, password="x")
        engines.append(create_async_engine(url.render_as_string(hide_password=False)))
        return engines[-1]

    yield make
    for engine in engines:
        await engine.dispose()
    async with admin.connect() as connection:
        for login in logins:
            await connection.execute(text(f"DROP ROLE {login}"))
    await admin.dispose()


async def test_the_serving_login_is_taken(serving_database: Database):
    await verify_serving_login(serving_database.engine)


async def test_a_superuser_is_refused(database: AppDatabase):
    engine = create_async_engine(database.superuser_url)
    try:
        with pytest.raises(ServingLoginRefused, match="superuser"):
            await verify_serving_login(engine)
    finally:
        await engine.dispose()


async def test_the_owner_is_refused(database: AppDatabase):
    engine = create_async_engine(database.owner_url)
    try:
        with pytest.raises(ServingLoginRefused, match="owns tables"):
            await verify_serving_login(engine)
    finally:
        await engine.dispose()


async def test_a_login_that_bypasses_row_security_is_refused(engine_as):
    with pytest.raises(ServingLoginRefused, match="bypasses row security"):
        await verify_serving_login(await engine_as("BYPASSRLS"))


async def test_a_member_of_the_owner_is_refused(engine_as, database: AppDatabase):
    owner = make_url(database.owner_url).username
    with pytest.raises(ServingLoginRefused, match="owns tables"):
        await verify_serving_login(await engine_as(member_of=owner))
