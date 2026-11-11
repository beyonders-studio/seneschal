"""Each organization's records stay its own, as the serving login meets them."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlmodel import select

from hoard.records import (
    MAX_IMAGE_BYTES,
    CategoryKind,
    Material,
    Product,
    ProductImage,
    Stocktake,
    Unit,
)
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

    async with serving_database.write_session(other, actor=None) as theirs:
        assert (await theirs.exec(select(Material))).all() == []
        with pytest.raises(DBAPIError, match="foreign key"):
            await add_material(theirs, Material(name="Planted", tracking_unit_id=unit.id))
        await theirs.rollback()
        with pytest.raises(DBAPIError, match="row-level security"):
            await theirs.execute(
                text("INSERT INTO units (organization_id, name) VALUES (:org, 'stray')"),
                {"org": db.organization_id},
            )


async def test_records_say_who_made_and_last_changed_them(
    serving_database: Database, db: WriteSession, main, material
):
    member = db.actor
    flour = await material("Flour")
    await db.commit()
    assert member and (flour.created_by, flour.updated_by) == (member, member)
    assert flour.created_at == flour.updated_at
    # The main location is Hoard's own.
    assert main.created_by is None

    async with serving_database.write_session(db.organization_id, actor="another-ref") as theirs:
        theirs_flour = await theirs.get_one(Material, flour.id)
        theirs_flour.name = "Flour"
        await theirs.flush()
        assert theirs_flour.updated_by == member
        theirs_flour.name = "Rye flour"
        await theirs.commit()
        assert (theirs_flour.created_by, theirs_flour.updated_by) == (member, "another-ref")
        assert theirs_flour.updated_at > theirs_flour.created_at


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


async def test_a_product_image_is_an_image_of_at_most_five_megabytes(db: WriteSession):
    product = Product(organization_id=db.organization_id, name="Loaf")
    db.add(product)
    await db.flush()

    def image(content_type: str, content: bytes) -> ProductImage:
        return ProductImage(
            organization_id=db.organization_id,
            product_id=product.id,
            content_type=content_type,
            content=content,
        )

    db.add(image("image/png", b"\x89PNG"))
    await db.flush()
    for refused in (
        image("text/html", b"<p>"),
        image("image/png", b""),
        image("image/png", b"0" * (MAX_IMAGE_BYTES + 1)),
    ):
        async with db.begin_nested():
            db.add(refused)
            with pytest.raises(DBAPIError, match="ck_product_images"):
                await db.flush()

    await db.delete(product)
    await db.flush()
    assert (await db.exec(select(ProductImage))).all() == []
