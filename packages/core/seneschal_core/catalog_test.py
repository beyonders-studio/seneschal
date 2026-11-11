"""The catalog check: it passes a database the helpers made, and names each
way a table can fall outside the organization policy."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from seneschal_core.catalog import isolation_problems
from seneschal_core.db import POLICY_CONDITION, create_async_engine
from seneschal_core.testing import AppDatabase


@pytest.fixture
async def owner(database: AppDatabase) -> AsyncIterator[AsyncConnection]:
    """The owner's connection, in a transaction rolled back afterwards."""
    engine = create_async_engine(database.owner_url)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        yield connection
        await transaction.rollback()
    await engine.dispose()


async def _problems(owner: AsyncConnection, database: AppDatabase) -> list[str]:
    return await isolation_problems(owner, serving_login=database.serving_login)


async def test_the_registry_and_an_isolated_table_keep_the_rules(owner, database):
    assert await _problems(owner, database) == []


async def test_a_table_made_without_the_helper_is_named(owner, database):
    await owner.execute(text("CREATE TABLE loose (id bigint PRIMARY KEY)"))

    problems = await _problems(owner, database)

    assert problems == [
        "public.loose has no not-null bigint organization_id",
        "public.loose.organization_id does not reference organizations(id) on delete cascade",
        "public.loose does not enable row security",
        "public.loose does not force row security",
        "public.loose must carry the one policy organization_isolation, and carries: none",
    ]


async def test_a_second_policy_is_named(owner, database):
    await owner.execute(text("CREATE POLICY wider ON widgets FOR SELECT USING (true)"))

    assert await _problems(owner, database) == [
        "public.widgets must carry the one policy organization_isolation, "
        "and carries: organization_isolation, wider"
    ]


async def test_a_policy_holding_rows_to_something_else_is_named(owner, database):
    await owner.execute(text("DROP POLICY organization_isolation ON widgets"))
    await owner.execute(
        text("CREATE POLICY organization_isolation ON widgets USING (organization_id > 0)")
    )

    assert await _problems(owner, database) == [
        "public.widgets.organization_isolation must hold rows to the transaction's organization"
    ]


async def test_row_security_left_unforced_is_named(owner, database):
    await owner.execute(text("ALTER TABLE widgets NO FORCE ROW LEVEL SECURITY"))

    assert await _problems(owner, database) == ["public.widgets does not force row security"]


async def test_a_table_in_another_schema_is_read_too(owner, database):
    await owner.execute(text("CREATE SCHEMA elsewhere"))
    await owner.execute(
        text(
            "CREATE TABLE elsewhere.things (organization_id bigint NOT NULL "
            "REFERENCES organizations(id) ON DELETE CASCADE)"
        )
    )
    await owner.execute(text("ALTER TABLE elsewhere.things ENABLE ROW LEVEL SECURITY"))
    await owner.execute(text("ALTER TABLE elsewhere.things FORCE ROW LEVEL SECURITY"))
    await owner.execute(
        text(
            f"CREATE POLICY organization_isolation ON elsewhere.things "
            f"USING ({POLICY_CONDITION}) WITH CHECK ({POLICY_CONDITION})"
        )
    )
    assert await _problems(owner, database) == []

    await owner.execute(text("CREATE TABLE elsewhere.loose (id bigint)"))
    assert "elsewhere.loose does not enable row security" in await _problems(owner, database)


async def test_a_serving_login_that_may_write_the_registry_is_named(owner, database):
    await owner.execute(text(f"GRANT UPDATE ON organizations TO {database.serving_login}"))

    assert await _problems(owner, database) == [
        "the serving login must only read organizations, and holds ['SELECT', 'UPDATE']"
    ]


async def test_a_definer_function_without_a_pinned_search_path_is_named(owner, database):
    await owner.execute(
        text("CREATE FUNCTION loose() RETURNS int LANGUAGE sql SECURITY DEFINER AS 'SELECT 1'")
    )

    assert await _problems(owner, database) == [
        "security-definer function public.loose must pin its search_path"
    ]
