"""Hoard's catalogue goes out as spreadsheets and comes back: an export
imports back as no change at all, a check lists each change field by field
and each problem by file and row, the import writes the changes through
Hoard's own paths, and a row the store refuses is listed while the import
carries on."""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from sqlmodel import select

from hoard import channels, listings, plugin_manifest, recipes, units
from hoard.app import JOB_HANDLERS
from hoard.channels import ConnectError, StoreProduct, StoreVariant
from hoard.channels.fake import FakeConnector
from hoard.records import (
    Category,
    CategoryKind,
    Channel,
    Material,
    Product,
    PropertyDefinition,
    PropertyType,
    PropertyValue,
    Recipe,
    RecordKind,
    Store,
    Unit,
    Variation,
)
from hoard.stock import add_variation
from seneschal_core import jobs
from seneschal_core.db import WriteSession
from seneschal_core.testing import InitiativeStandIn

FILES = ("materials", "bases", "products", "recipes")
#: When the store last changed its product.
OPENED = datetime(2026, 11, 14, 8, tzinfo=UTC)


@pytest.fixture
def shop(
    monkeypatch: pytest.MonkeyPatch, initiative_stand_in: InitiativeStandIn, community_ref: str
) -> FakeConnector:
    """The store the community's Shopify connection reaches, selling chai in
    two sizes, the large at a price of its own."""
    fake = FakeConnector()
    monkeypatch.setitem(channels.CONNECTORS, Channel.SHOPIFY, lambda values: fake)
    initiative_stand_in.connections[community_ref] = {"shopify": {"shop": fake.shop_domain}}
    fake.shop_products["p1"] = StoreProduct(
        id="p1",
        title="Chai",
        status="active",
        options=["Size"],
        variants=[
            StoreVariant(
                id="v1", title="Large", sku="CHAI-L", price=Decimal(12), options={"Size": "Large"}
            ),
            StoreVariant(id="v2", title="Small", sku="CHAI-S", options={"Size": "Small"}),
        ],
        updated_at=OPENED,
    )
    return fake


@pytest.fixture
async def chai(db: WriteSession, product, shop: FakeConnector) -> tuple[Variation, Variation]:
    """Chai in two sizes, each with Hoard's own price, both listed by a
    synced store, which prices the large."""
    large = await product("Chai", default_price=Decimal(8))
    large.attributes, large.sku, large.price = {"Size": "Large"}, "CHAI-L", Decimal(10)
    db.add(large)
    small = await add_variation(
        db,
        Variation(
            product_id=large.product_id,
            attributes={"Size": "Small"},
            sku="CHAI-S",
            price=Decimal(6),
        ),
    )
    store = Store(
        organization_id=db.organization_id,
        channel=Channel.SHOPIFY,
        shop_domain=shop.shop_domain,
        connected=True,
        imported_at=OPENED,
    )
    db.add(store)
    await db.flush()
    await listings.match(db, store, [shop.shop_products["p1"]])
    await db.commit()
    return large, small


def _files(**files: str) -> dict[str, Any]:
    return {"files": {name: (f"{name}.csv", content.encode()) for name, content in files.items()}}


def _rows(text: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(text)))


def _edited(text: str, row: int, **cells: str) -> str:
    """``text`` with a row's cells changed, the first row after the header 0."""
    rows = _rows(text)
    rows[row] |= cells
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=list(rows[0]), lineterminator="\r\n")
    writer.writeheader()
    writer.writerows(rows)
    return out.getvalue()


def _counts(checked: dict[str, Any]) -> dict[str, tuple[int, int, int]]:
    return {
        count["kind"]: (count["new"], count["changed"], count["unchanged"])
        for count in checked["counts"]
    }


async def _import(client, app, organization_id, **files: str) -> dict[str, Any]:
    queued = await client.post("/api/v1/imports/spreadsheets", **_files(**files))
    assert queued.status_code == 202, queued.text
    await jobs.work(app.state.plugin, organization_id, JOB_HANDLERS)
    done = (await client.get(f"/api/v1/jobs/{queued.json()['id']}")).json()
    assert done["state"] == "done", done
    return done["result"]


async def test_an_export_imports_back_as_no_change(
    client, sign_in, suppliers, export, app, organization_id, db: WriteSession, material, base,
    unit, chai,
):  # fmt: skip
    await units.add_standard(db)
    teas = Category(organization_id=db.organization_id, kind=CategoryKind.MATERIAL, name="Teas")
    origin = PropertyDefinition(
        organization_id=db.organization_id,
        kind=RecordKind.MATERIAL,
        name="Origin",
        type=PropertyType.SELECT,
        options=[{"value": "assam", "label": "Assam"}],
    )
    db.add_all([teas, origin])
    await db.flush()
    leaf = await material(
        "Assam Leaf",
        category_id=teas.id,
        supplier_contact_id=suppliers["Box Works"],
        low_stock_limit=Decimal(500),
        allergens=[],
        fragrance_allergens={"linalool": "0.5"},
        notes="=Keep dry",
    )
    tin = await material("Tin")
    db.add(
        PropertyValue(
            organization_id=db.organization_id,
            definition_id=origin.id,
            material_id=leaf.id,
            value="assam",
        )
    )
    blend = await base("Breakfast Blend")
    await recipes.save(
        db,
        Recipe(
            base_id=blend.id,
            by_ratio=True,
            total=Decimal(700),
            total_unit_id=unit.id,
            batch_quantity=Decimal(700),
            minutes=15,
        ),
        [recipes.Line(leaf.id, Decimal(5))],
    )
    large, _ = chai
    await recipes.save(
        db,
        Recipe(variation_id=large.id),
        [recipes.Line(blend.id, Decimal(60)), recipes.Line(tin.id, Decimal(1))],
    )
    await db.commit()
    await sign_in(client, (plugin_manifest.SEE_COSTS,), admin=True)

    files = {name: (await export(name)).text for name in FILES}

    materials = {row["name"]: row for row in _rows(files["materials"])}
    assert {
        column: materials["Assam Leaf"][column]
        for column in (
            "category",
            "type",
            "tracking_unit",
            "low_stock_limit",
            "supplier",
            "allergens",
            "fragrance_allergens",
            "notes",
            "Property: Origin",
        )
    } == {
        "category": "Teas",
        "type": "",
        "tracking_unit": "g",
        "low_stock_limit": "500",
        "supplier": "Box Works",
        "allergens": "none",
        "fragrance_allergens": "linalool: 0.5",
        "notes": "'=Keep dry",
        "Property: Origin": "Assam",
    }
    # Allergens not known are an empty cell; none are "none".
    assert materials["Tin"]["allergens"] == ""
    # A store's price shows in place of Hoard's own.
    assert [
        (row["name"], row["options"], row["sku"], row["price"], row["default_price"])
        for row in _rows(files["products"])
    ] == [("Chai", "Size: Large", "CHAI-L", "12", "8"), ("Chai", "Size: Small", "CHAI-S", "6", "8")]
    assert [
        (row["makes"], row["makes_options"], row["by_ratio"], row["total"], row["total_unit"])
        + (row["item"], row["item_options"], row["amount"], row["unit"])
        for row in _rows(files["recipes"])
    ] == [
        ("Breakfast Blend", "", "true", "700", "g", "Assam Leaf", "", "5", "g"),
        ("Chai", "Size: Large", "false", "", "", "Breakfast Blend", "", "60", "g"),
        ("Chai", "Size: Large", "false", "", "", "Tin", "", "1", "g"),
    ]

    checked = (await client.post("/api/v1/imports/spreadsheets/check", **_files(**files))).json()
    assert (checked["problems"], checked["changes"]) == ([], [])
    assert _counts(checked) == {
        "categories": (0, 0, 1),
        "suppliers": (0, 0, 1),
        "materials": (0, 0, 2),
        "bases": (0, 0, 1),
        "products": (0, 0, 1),
        "variations": (0, 0, 2),
        "recipes": (0, 0, 2),
    }

    # The store's price is left as it is, whatever the cell says.
    files["products"] = _edited(files["products"], 0, price="99")
    checked = (await client.post("/api/v1/imports/spreadsheets/check", **_files(**files))).json()
    assert (checked["problems"], checked["changes"]) == ([], [])
    result = await _import(client, app, organization_id, **files)
    assert (result["created"], result["changed"], result["failed"]) == ({}, {}, {})
    db.expunge_all()
    assert (await db.get_one(Variation, large.id)).price == Decimal(10)


async def test_a_check_lists_each_change_and_the_import_writes_it(
    client, sign_in, suppliers, app, organization_id, db: WriteSession, material, unit
):
    lavender = await material("Lavender", notes="Keep dry")
    await db.commit()
    await sign_in(client, admin=True)
    # Saved by a spreadsheet: a byte-order mark, and the other line ending.
    materials = "\r\n".join(
        [
            "﻿id,name,tracking_unit,category,low_stock_limit,allergens",
            f"{lavender.id},Lavender,g,,500,none",
            ",Rose Petals,g,Herbs,,",
        ]
    )

    checked = (
        await client.post("/api/v1/imports/spreadsheets/check", **_files(materials=materials))
    ).json()

    assert checked["problems"] == []
    assert _counts(checked) == {"categories": (1, 0, 0), "materials": (1, 1, 0)}
    assert [
        (change["row"], change["name"], change["field"], change["was"], change["now"])
        for change in checked["changes"]
    ] == [
        (2, "Lavender", "low_stock_limit", "", "500"),
        (2, "Lavender", "allergens", "", "none"),
    ]

    result = await _import(client, app, organization_id, materials=materials)

    assert result == {
        "created": {"categories": 1, "materials": 1},
        "matched": {},
        "changed": {"materials": 1},
        "failed": {},
        "failed_rows": [],
    }
    db.expunge_all()
    written = await db.get_one(Material, lavender.id)
    # A column the file doesn't hold is left as it is.
    assert (written.low_stock_limit, written.allergens, written.notes) == (
        Decimal(500),
        [],
        "Keep dry",
    )
    rose = (await db.exec(select(Material).where(Material.name == "Rose Petals"))).one()
    herbs = await db.get_one(Category, rose.category_id or 0)
    assert (herbs.kind, herbs.name, rose.tracking_unit_id) == (
        CategoryKind.MATERIAL,
        "Herbs",
        unit.id,
    )


async def test_the_check_names_each_problem_by_file_and_row(
    client, sign_in, db: WriteSession, material, base, product
):
    await units.add_standard(db)
    leaf = await material("Leaf")
    await material("Twin")
    await material("Twin")
    blend = await base("Blend")
    large = await product("Chai")
    large.attributes, large.sku = {"Size": "Large"}, "CHAI-L"
    db.add(large)
    small = await add_variation(
        db, Variation(product_id=large.product_id, attributes={"Size": "Small"}, sku="CHAI-S")
    )
    await recipes.save(db, Recipe(variation_id=large.id), [recipes.Line(blend.id, Decimal(60))])
    await db.commit()
    await sign_in(client, admin=True)
    files = {
        "materials": "\n".join(
            [
                "id,name,status,tracking_unit,low_stock_limit,type,price",
                "999999,Ghost,active,g,,,",
                f'{leaf.id},Leaf,paused,pinch,"1,000",Fragile,',
                f"{leaf.id},Leaf,active,g,,,",
                ",Twin,active,g,,,",
                ",Rose,active,g,-5,,",
            ]
        ),
        "bases": f"id,tracking_unit\n{blend.id},each",
        "products": "\n".join(
            [
                "id,name,default_price,variation_id,sku",
                f"{large.product_id},Chai,5,{large.id},CHAI-L",
                f"{large.product_id},Chai Tea,5,{small.id},CHAI-L",
            ]
        ),
        "recipes": "\n".join(
            [
                "makes,makes_options,batch_quantity,item,item_options,amount,unit",
                "Blend,,10,Chai,Size: Large,1,",
                "Blend,,12,Ghost Leaf,,1,",
                "Chai,Size: Huge,1,Leaf,,1,",
            ]
        ),
    }

    checked = await client.post("/api/v1/imports/spreadsheets/check", **_files(**files))

    assert checked.status_code == 200, checked.text
    assert sorted(
        (problem["code"], problem["file"], problem["row"], problem["field"])
        for problem in checked.json()["problems"]
    ) == [
        ("COLUMNS_DISAGREE", "products", 3, "name"),
        ("COLUMNS_DISAGREE", "recipes", 3, "batch_quantity"),
        ("COLUMN_UNKNOWN", "materials", 1, "price"),
        # A member who doesn't see costs sends none.
        ("COSTS_HIDDEN", "products", 1, "default_price"),
        ("ID_UNKNOWN", "materials", 2, "id"),
        ("LINE_NAMES_NOTHING", "recipes", 3, "item"),
        ("NAME_AMBIGUOUS", "materials", 5, "name"),
        # A thousands separator could mean either way.
        ("NUMBER_INVALID", "materials", 3, "low_stock_limit"),
        ("NUMBER_OUT_OF_RANGE", "materials", 6, "low_stock_limit"),
        ("RECIPE_CONTAINS_ITSELF", "recipes", 2, "makes"),
        ("RECIPE_OUTPUT_UNKNOWN", "recipes", 4, "makes"),
        ("ROW_REPEATED", "materials", 4, "id"),
        ("SKU_REPEATED", "products", 2, "sku"),
        ("SKU_REPEATED", "products", 3, "sku"),
        # A recipe counts the base in grams, so its unit stays.
        ("TRACKING_UNIT_IN_RECIPES", "bases", 2, "tracking_unit"),
        ("TYPE_UNKNOWN", "materials", 3, "type"),
        ("UNIT_UNKNOWN", "materials", 3, "tracking_unit"),
        ("VALUE_INVALID", "materials", 3, "status"),
    ]
    refused = await client.post("/api/v1/imports/spreadsheets", **_files(**files))
    assert (refused.status_code, refused.json()["detail"]) == (409, "IMPORT_PROBLEMS")
    assert (await db.exec(select(Unit).where(Unit.name == "pinch"))).all() == []


async def test_a_row_the_store_refuses_is_listed_and_the_import_carries_on(
    client, sign_in, app, organization_id, db: WriteSession, product, shop, chai
):
    large, _ = chai
    mug = await product("Mug")
    await db.commit()
    await sign_in(client, admin=True)
    products = "\n".join(
        [
            "id,name,low_stock_limit",
            f"{large.product_id},Masala Chai,",
            f"{mug.product_id},Mug,4",
        ]
    )
    shop.refuse = ConnectError.STORE_UNREACHABLE

    result = await _import(client, app, organization_id, products=products)

    assert (result["changed"], result["failed"], result["failed_rows"]) == (
        {"products": 1},
        {"products": 1},
        [{"file": "products", "row": 2, "field": None, "code": "STORE_UNREACHABLE"}],
    )
    db.expunge_all()
    assert (await db.get_one(Product, large.product_id)).name == "Chai"
    assert (await db.get_one(Product, mug.product_id)).low_stock_limit == Decimal(4)

    # Once the store answers, the name reaches it first, and its price stays its own.
    shop.refuse = None
    again = await _import(client, app, organization_id, products=products)

    assert (again["changed"], again["failed"]) == ({"products": 1}, {})
    assert shop.shop_products["p1"].title == "Masala Chai"
    assert [variant.price for variant in shop.shop_products["p1"].variants] == [Decimal(12), None]
    db.expunge_all()
    assert (await db.get_one(Product, large.product_id)).name == "Masala Chai"
