"""Hoard's migrated database keeps organizations apart."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from hoard.db import downgrade, upgrade
from seneschal_core import organizations
from seneschal_core.catalog import isolation_problems
from seneschal_core.db import Database, create_async_engine
from seneschal_core.testing import AppDatabase, migrated_database


async def test_every_table_keeps_the_organization_rules(database: AppDatabase):
    engine = create_async_engine(database.owner_url)
    try:
        async with engine.connect() as connection:
            problems = await isolation_problems(connection, serving_login=database.serving_login)
    finally:
        await engine.dispose()

    assert problems == []


async def _organization(served: Database, community_ref: str) -> int:
    async with served.session() as db:
        organization_id = await organizations.organization_for(
            db, community_ref=community_ref, plugin_install_id=1
        )
        await db.commit()
    return organization_id


async def _spend(db, organization_id: int, jti: str) -> None:
    await db.execute(
        text(
            "INSERT INTO handoff_redemptions (jti, organization_id, expires_at) "
            "VALUES (:jti, :org, :expires)"
        ),
        {"jti": jti, "org": organization_id, "expires": datetime.now(UTC) + timedelta(minutes=1)},
    )


async def _spent(db) -> list[str]:
    return list((await db.execute(text("SELECT jti FROM handoff_redemptions"))).scalars())


async def test_the_serving_login_reaches_one_organizations_rows_at_a_time(
    serving_database: Database, community_ref: str
):
    first = await _organization(serving_database, community_ref)
    second = await _organization(serving_database, f"{community_ref}-other")
    for organization_id in (first, second):
        async with serving_database.organization_session(organization_id) as db:
            await _spend(db, organization_id, f"{community_ref}-{organization_id}")
            await db.commit()

    async with serving_database.organization_session(first) as db:
        assert await _spent(db) == [f"{community_ref}-{first}"]
        with pytest.raises(DBAPIError, match="row-level security"):
            await _spend(db, second, f"{community_ref}-planted")

    async with serving_database.session() as db:
        assert await _spent(db) == []
        with pytest.raises(DBAPIError, match="row-level security"):
            await _spend(db, first, f"{community_ref}-stray")


def test_the_migrations_go_down_and_up_again():
    def round_trip(made: AppDatabase) -> None:
        upgrade(made.owner_url, made.serving_login)
        downgrade(made.owner_url, made.serving_login, "base")
        upgrade(made.owner_url, made.serving_login)

    with migrated_database("hoard_round_trip", round_trip):
        pass
