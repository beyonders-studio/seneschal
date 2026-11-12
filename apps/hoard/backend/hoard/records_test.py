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
    Adjustment,
    AdjustmentKind,
    Allergen,
    CategoryKind,
    Channel,
    DocumentLink,
    Hold,
    HoldEnd,
    Item,
    ItemKind,
    Listing,
    Location,
    Lot,
    Material,
    Order,
    OrderStatus,
    Product,
    ProductImage,
    PropertyDefinition,
    PropertyType,
    PropertyValue,
    Recipe,
    RecordKind,
    Stocktake,
    Store,
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
    origin = PropertyDefinition(
        organization_id=db.organization_id,
        kind=RecordKind.MATERIAL,
        name="Origin",
        type=PropertyType.TEXT,
    )
    db.add(origin)
    await db.flush()
    db.add(
        PropertyValue(
            organization_id=db.organization_id,
            definition_id=origin.id,
            material_id=flour.id,
            value="Kansas",
        )
    )
    await db.commit()

    async with serving_database.session() as session:
        assert await organizations.erase(session, community_ref)
        await session.commit()

    engine = create_async_engine(database.superuser_url)
    try:
        async with engine.connect() as connection:
            for table in (
                "items",
                "lots",
                "stock_levels",
                "adjustments",
                "purchases",
                "property_definitions",
                "property_values",
            ):
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


async def test_making_keeps_its_shapes(db: WriteSession, main: Location, material, base):
    await material("Flour", allergens=[Allergen.WHEAT, Allergen.GLUTEN])
    dough = await base("Dough")
    assert (await db.get_one(Item, dough.id)).kind is ItemKind.BASE

    for row, constraint in (
        (Recipe(organization_id=db.organization_id), "ck_recipes_output"),
        (
            Material(
                organization_id=db.organization_id,
                name="Rye",
                tracking_unit_id=dough.tracking_unit_id,
                allergens=["gluten", "unknown"],
            ),
            "ck_materials_allergens",
        ),
        (
            Adjustment(
                organization_id=db.organization_id,
                item_id=dough.id,
                location_id=main.id,
                kind=AdjustmentKind.PRODUCTION,
                dated_on=date(2026, 11, 2),
                change=Decimal(1),
            ),
            "ck_adjustments_production",
        ),
    ):
        async with db.begin_nested():
            with pytest.raises(DBAPIError, match=constraint):
                if isinstance(row, Material):
                    await add_material(db, row)
                db.add(row)
                await db.flush()


async def test_orders_and_stores_keep_their_shapes(db: WriteSession, main: Location, material):
    flour = await material("Flour")
    store = Store(organization_id=db.organization_id, channel=Channel.SHOPIFY)
    db.add(store)
    await db.flush()
    # A store's product without variants is listed once.
    db.add(Listing(organization_id=db.organization_id, store_id=store.id, product_id="p-1"))
    await db.flush()

    def order(**fields) -> Order:
        return Order(
            organization_id=db.organization_id,
            code=f"O-{len(fields)}",
            location_id=main.id,
            ordered_on=date(2026, 11, 12),
            **fields,
        )

    for row, constraint in (
        (Store(organization_id=db.organization_id, channel=Channel.SHOPIFY), "uq_stores_channel"),
        (
            Listing(organization_id=db.organization_id, store_id=store.id, product_id="p-1"),
            "uq_listings_external",
        ),
        (
            Listing(
                organization_id=db.organization_id,
                store_id=store.id,
                product_id="p-2",
                item_id=flour.id,
            ),
            "ck_listings_matched",
        ),
        (order(), "ck_orders_customer"),
        (order(store_id=store.id), "ck_orders_external"),
        (
            Adjustment(
                organization_id=db.organization_id,
                item_id=flour.id,
                location_id=main.id,
                kind=AdjustmentKind.SHIPMENT,
                dated_on=date(2026, 11, 12),
                change=Decimal(-1),
            ),
            "ck_adjustments_shipment",
        ),
    ):
        async with db.begin_nested():
            with pytest.raises(DBAPIError, match=constraint):
                db.add(row)
                await db.flush()

    by_hand = order(customer_contact_id=7)
    db.add(by_hand)
    await db.flush()
    assert by_hand.status is OrderStatus.OPEN


async def test_holds_and_document_links_keep_their_shapes(
    db: WriteSession, main: Location, material, hold
):
    flour = await material("Flour")
    held = Lot(
        organization_id=db.organization_id,
        item_id=flour.id,
        location_id=main.id,
        number="F-1",
        dated_on=date(2026, 11, 1),
    )
    db.add(held)
    await db.flush()
    first = await hold(held)
    product = Product(organization_id=db.organization_id, name="Loaf")
    db.add(product)
    await db.flush()
    db.add(DocumentLink(organization_id=db.organization_id, file_id=9, lot_id=held.id))
    await db.flush()

    for row, constraint in (
        (
            Hold(organization_id=db.organization_id, lot_id=held.id, reason_id=first.reason_id),
            "uq_holds_open",
        ),
        (
            Hold(
                organization_id=db.organization_id,
                lot_id=held.id,
                reason_id=first.reason_id,
                ended=HoldEnd.RELEASED,
            ),
            "ck_holds_ended",
        ),
        (
            DocumentLink(organization_id=db.organization_id, file_id=9, lot_id=held.id),
            "uq_document_links_lot",
        ),
        (
            DocumentLink(
                organization_id=db.organization_id, file_id=9, lot_id=held.id, product_id=product.id
            ),
            "ck_document_links_record",
        ),
        (
            Adjustment(
                organization_id=db.organization_id,
                item_id=flour.id,
                location_id=main.id,
                kind=AdjustmentKind.STARTING,
                dated_on=date(2026, 11, 12),
                change=Decimal(1),
                hold_id=first.id,
            ),
            "ck_adjustments_hold",
        ),
        (
            Material(
                organization_id=db.organization_id,
                name="Rose oil",
                tracking_unit_id=flour.tracking_unit_id,
                fragrance_allergens=["linalool"],
            ),
            "ck_materials_fragrance_allergens",
        ),
    ):
        async with db.begin_nested():
            with pytest.raises(DBAPIError, match=constraint):
                if isinstance(row, Material):
                    await add_material(db, row)
                db.add(row)
                await db.flush()

    # An ended hold is kept, and the lot may be held again.
    first.ended, first.ended_on = HoldEnd.RELEASED, date(2026, 11, 12)
    await db.flush()
    await hold(held)
    # Deleting a lot takes its holds and links with it.
    await db.delete(held)
    await db.flush()
    assert (await db.exec(select(Hold))).all() == []
    assert (await db.exec(select(DocumentLink))).all() == []
