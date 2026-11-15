"""Hoard's search finds each kind of record by what its source names, follows
the records it reads from other tables, never holds a cost or a price, and is
read through the routes."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from conftest import SignIn
from httpx2 import AsyncClient
from sqlalchemy import create_engine, text
from sqlmodel import select

from hoard import records
from hoard.records import (
    CategoryKind,
    HoldEnd,
    HoldReason,
    Listing,
    Location,
    Product,
    ProductionRun,
    ProductStatus,
    PropertyDefinition,
    PropertyType,
    PropertyValue,
    PurchaseLine,
    RecordKind,
    Stocktake,
    Store,
    StoreProductCopy,
    Variation,
)
from hoard.search_sources import SOURCES
from hoard.stock import add_product, add_variation
from seneschal_core import search
from seneschal_core.db import WriteSession
from seneschal_core.testing import AppDatabase


async def _entry(db: WriteSession, kind: str, record_id: int) -> dict[str, Any] | None:
    await db.flush()
    row = (
        await db.execute(
            text(
                "SELECT title, subtitle, path, codes, body, archived FROM search_entries "
                "WHERE kind = :kind AND record_id = :id"
            ),
            {"kind": kind, "id": record_id},
        )
    ).first()
    return None if row is None else dict(row._mapping)


async def _text_property(db: WriteSession, kind: RecordKind, value: str, **record: int) -> None:
    definition = PropertyDefinition(
        organization_id=db.organization_id, kind=kind, name=f"Note {kind}", type=PropertyType.TEXT
    )
    db.add(definition)
    await db.flush()
    db.add(
        PropertyValue(
            organization_id=db.organization_id, definition_id=definition.id, value=value, **record
        )
    )


async def test_materials_and_bases(db: WriteSession, material, base):
    oolong = await material(
        "Oolong", sku="TEA-01", supplier_code="SUP 9", ingredient_name="Camellia sinensis"
    )
    await _text_property(db, RecordKind.MATERIAL, "Fujian", material_id=oolong.id)
    assert await _entry(db, "material", oolong.id) == {
        "title": "Oolong",
        "subtitle": None,
        "path": f"/stock/materials/{oolong.id}",
        "codes": ["TEA-01", "SUP 9"],
        "body": "Camellia sinensis Fujian",
        "archived": False,
    }

    oolong.name = "Tieguanyin"
    db.add(oolong)
    assert (await _entry(db, "material", oolong.id) or {})["title"] == "Tieguanyin"

    syrup = await base("Syrup", sku="B-1")
    await _text_property(db, RecordKind.BASE, "Cane sugar", base_id=syrup.id)
    entry = await _entry(db, "base", syrup.id)
    assert entry is not None
    assert (entry["codes"], entry["body"], entry["path"]) == (
        ["B-1"],
        "Cane sugar",
        f"/stock/bases/{syrup.id}",
    )


async def test_products_by_their_variations_and_store_copies(db: WriteSession):
    product, default = await add_product(db, Product(name="Chai"))
    default.sku, default.attributes = "CH-S", {"size": "small"}
    db.add(default)
    large = await add_variation(
        db, Variation(product_id=product.id, sku="CH-L", attributes={"size": "large"})
    )
    await _text_property(db, RecordKind.PRODUCT, "Spiced", product_id=product.id)
    store = Store(organization_id=db.organization_id, channel=records.Channel.SHOPIFY)
    db.add(store)
    await db.flush()
    db.add(
        StoreProductCopy(
            organization_id=db.organization_id,
            store_id=store.id,
            product_id="gid://9",
            title="Masala chai",
            product_type="Tea",
            vendor="Leaf & Co",
            tags=["warming", "bestseller"],
        )
    )
    assert (await _entry(db, "product", product.id) or {})["body"] == "small large Spiced"

    listing = Listing(
        organization_id=db.organization_id,
        store_id=store.id,
        product_id="gid://9",
        variant_id="1",
        item_id=large.id,
        matched_by=records.MatchedBy.SKU,
    )
    db.add(listing)
    entry = await _entry(db, "product", product.id)
    assert entry is not None
    assert entry["codes"] == ["CH-S", "CH-L"]
    assert entry["body"] == "small large Masala chai Tea Leaf & Co warming bestseller Spiced"

    copy = (await db.exec(select(StoreProductCopy))).one()
    copy.vendor = "Leaf House"
    db.add(copy)
    assert "Leaf House" in ((await _entry(db, "product", product.id)) or {})["body"]

    product.status = ProductStatus.ARCHIVED
    db.add(product)
    assert (await _entry(db, "product", product.id) or {})["archived"] is True
    assert [hit.title for hit in (await search.search(db, "CH-L")).items] == []
    found = await search.search(db, "CH-L", archived=True)
    assert [(hit.title, hit.exact) for hit in found.items] == [("Chai", True)]


async def test_lots_runs_and_holds_by_their_items_and_lots(
    db: WriteSession, main: Location, material, lot, hold
):
    flour = await material("Flour")
    flour_lot = await lot(flour.id, "LOT/24/07", "10")
    flour_lot.notes = "milled fine"
    db.add(flour_lot)
    await _text_property(db, RecordKind.LOT, "Organic", lot_id=flour_lot.id)
    assert await _entry(db, "lot", flour_lot.id) == {
        "title": "LOT/24/07",
        "subtitle": "Flour",
        "path": f"/stock/lots/{flour_lot.id}",
        "codes": ["LOT/24/07"],
        "body": "Flour milled fine Organic",
        "archived": False,
    }

    _, bread = await add_product(db, Product(name="Bread"))
    bread.attributes = {"shape": "loaf"}
    db.add(bread)
    run = ProductionRun(
        organization_id=db.organization_id, code="R-0012", item_id=bread.id, location_id=main.id
    )
    db.add(run)
    await db.flush()
    entry = await _entry(db, "run", run.id)
    assert entry is not None
    assert (entry["title"], entry["subtitle"], entry["path"]) == (
        "R-0012",
        "Bread loaf",
        f"/production/runs/{run.id}",
    )

    # Renaming an item renames it wherever it is read.
    flour.name = "Rye flour"
    db.add(flour)
    product = await db.get_one(Product, bread.product_id)
    product.name = "Sourdough"
    db.add(product)
    assert (await _entry(db, "lot", flour_lot.id) or {})["subtitle"] == "Rye flour"
    assert (await _entry(db, "run", run.id) or {})["subtitle"] == "Sourdough loaf"

    held = await hold(flour_lot, notes="damp sacks")
    entry = await _entry(db, "hold", held.id)
    assert entry is not None
    assert (entry["title"], entry["codes"], entry["path"], entry["body"]) == (
        "LOT/24/07",
        ["LOT/24/07"],
        f"/stock/lots/{flour_lot.id}",
        "suspect damp sacks",
    )
    reason = await db.get_one(HoldReason, held.reason_id)
    reason.name = "Moisture"
    db.add(reason)
    flour_lot.number = "LOT/24/08"
    db.add(flour_lot)
    entry = await _entry(db, "hold", held.id)
    assert entry is not None
    assert (entry["title"], entry["body"]) == ("LOT/24/08", "Moisture damp sacks")

    held.ended, held.ended_on = HoldEnd.RELEASED, date(2026, 11, 3)
    db.add(held)
    assert (await _entry(db, "hold", held.id) or {})["archived"] is True


async def test_purchases_orders_stocktakes_and_locations(
    db: WriteSession, main: Location, material, buy, sell
):
    oolong = await material("Oolong")
    purchase = await buy((oolong, "5", "4242.42"), discount=Decimal("17.17"))
    assert (await _entry(db, "purchase", purchase.id) or {})["body"] == "Oolong"
    sencha = await material("Sencha")
    line = (await db.exec(select(PurchaseLine))).one()
    line.material_id = sencha.id
    db.add(line)
    assert (await _entry(db, "purchase", purchase.id) or {})["body"] == "Sencha"

    _, chai = await add_product(db, Product(name="Chai"))
    order = await sell(
        (chai.id, "2", "9393.93"),
        external_number="#1001",
        store_customer="C-778",
        delivery_address="1 Leaf Lane",
    )
    entry = await _entry(db, "order", order.id)
    assert entry is not None
    assert (entry["codes"], entry["body"], entry["path"]) == (
        [order.code, "#1001", "C-778"],
        "Chai",
        f"/orders/{order.id}",
    )

    main.name = "Back room"
    db.add(main)
    stocktake = Stocktake(
        organization_id=db.organization_id,
        kind=CategoryKind.MATERIAL,
        location_id=main.id,
        dated_on=date(2026, 11, 10),
        notes="quarter end",
    )
    db.add(stocktake)
    await db.flush()
    entry = await _entry(db, "stocktake", stocktake.id)
    assert entry is not None
    assert (entry["title"], entry["body"]) == ("Back room 2026-11-10", "quarter end")
    assert (await _entry(db, "location", main.id) or {})["path"] == "/settings"

    # No cost or price is ever held, nor a source reading one.
    held = await db.execute(text("SELECT title, subtitle, codes, body FROM search_entries"))
    assert not [
        row for row in held.all() if any(money in str(row) for money in ("4242", "9393", "17.17"))
    ]
    money = {
        column.name
        for table in records.Material.metadata.tables.values()
        for column in table.columns
        if any(word in column.name for word in ("cost", "price", "discount", "shipping", "tax"))
    }
    assert money
    for source in SOURCES:
        rendered = "\n".join(search.render(source))
        assert not [column for column in money if f".{column}" in rendered], source.kind


async def test_an_unnamed_main_location_is_not_found(db: WriteSession, main: Location):
    assert await _entry(db, "location", main.id) is None
    main.name = "Shop floor"
    db.add(main)
    assert (await _entry(db, "location", main.id) or {})["title"] == "Shop floor"


async def test_the_routes_read_the_organizations_records(
    client: AsyncClient, sign_in: SignIn, db: WriteSession, material, lot
):
    flour = await material("Flour", sku="FL-1")
    await lot(flour.id, "FL-1-A", "3")
    await material("Fluor paint")
    await db.commit()
    await sign_in(client)

    found = (await client.get("/api/v1/search", params={"q": "flour"})).json()
    assert found["counts"] == {"material": 1, "lot": 1}
    assert [hit["kind"] for hit in found["items"]] == ["material", "lot"]
    assert found["items"][0]["snippet"] == []
    lots = (await client.get("/api/v1/search", params={"q": "flour", "kind": "lot"})).json()
    assert (lots["total"], lots["items"][0]["snippet"]) == (1, [{"text": "Flour", "marked": True}])

    missed = (await client.get("/api/v1/search", params={"q": "flowr"})).json()
    assert (missed["items"], [hit["title"] for hit in missed["close_matches"]]) == (
        [],
        ["Flour"],
    )

    suggested = (await client.get("/api/v1/search/suggest", params={"q": "fl-1"})).json()
    assert suggested["exact_match"] is True
    assert [hit["title"] for hit in suggested["items"]] == ["Flour", "FL-1-A"]
    assert suggested["items"][0]["path"] == f"/stock/materials/{flour.id}"

    assert (await client.get("/api/v1/search", params={"q": "x" * 201})).status_code == 422
    client.cookies.clear()
    assert (await client.get("/api/v1/search", params={"q": "flour"})).status_code == 401


def test_the_sources_are_applied_on_migrating(database: AppDatabase):
    engine = create_engine(database.owner_url)
    try:
        with engine.begin() as connection:
            assert search.apply_sources(connection, SOURCES) == []
    finally:
        engine.dispose()
    assert {source.kind for source in SOURCES} == {
        "material",
        "base",
        "product",
        "lot",
        "run",
        "purchase",
        "order",
        "stocktake",
        "hold",
        "location",
    }
