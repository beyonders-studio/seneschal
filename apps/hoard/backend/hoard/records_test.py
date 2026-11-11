"""Each organization's records stay its own, as the serving login meets them."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlmodel import select

from hoard.records import CategoryKind, Material, Stocktake, Unit
from hoard.stock import add_material
from hoard.stocktakes import confirm_row, start_stocktake
from seneschal_core import organizations
from seneschal_core.db import Database, WriteSession, create_async_engine
from seneschal_core.testing import AppDatabase


async def _another_organization(serving_database: Database, community_ref: str) -> int:
    async with serving_database.session() as session:
        organization_id = await organizations.organization_for(
            session, community_ref=f"{community_ref}-other", plugin_install_id=1
        )
        await session.commit()
    return organization_id


async def test_an_organization_reaches_and_references_none_of_anothers_materials(
    serving_database: Database, db: WriteSession, unit: Unit, community_ref: str, material
):
    await material("Flour")
    await db.commit()
    other = await _another_organization(serving_database, community_ref)

    async with serving_database.write_session(other) as theirs:
        assert (await theirs.exec(select(Material))).all() == []
        with pytest.raises(DBAPIError, match="foreign key"):
            await add_material(theirs, Material(name="Planted", tracking_unit_id=unit.id))
        await theirs.rollback()
        with pytest.raises(DBAPIError, match="row-level security"):
            await theirs.execute(
                text("INSERT INTO units (organization_id, name) VALUES (:org, 'stray')"),
                {"org": db.organization_id},
            )


async def test_erasing_an_organization_takes_its_stock_with_it(
    database: AppDatabase,
    serving_database: Database,
    db: WriteSession,
    community_ref: str,
    main,
    material,
    buy,
):
    flour = await material("Flour")
    await buy((flour, "10", "2"))
    stocktake = Stocktake(
        kind=CategoryKind.MATERIAL, location_id=main.id, dated_on=date(2026, 11, 10)
    )
    [row] = await start_stocktake(db, stocktake)
    await confirm_row(db, row, Decimal(7))
    await db.commit()

    async with serving_database.session() as session:
        assert await organizations.erase(session, community_ref)
        await session.commit()

    engine = create_async_engine(database.superuser_url)
    try:
        async with engine.connect() as connection:
            for table in ("items", "lots", "stock_levels", "adjustments", "purchases"):
                remaining = await connection.execute(
                    text(f"SELECT count(*) FROM {table} WHERE organization_id = :org"),
                    {"org": db.organization_id},
                )
                assert remaining.scalar_one() == 0, table
    finally:
        await engine.dispose()
