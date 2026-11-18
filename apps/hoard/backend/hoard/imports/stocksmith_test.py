"""Stocksmith's exports become Hoard's catalogue: a check counts what they
make and names their problems, an import makes it through Hoard's own write
paths, listing a row they refuse and carrying on, and running it again makes
only what is missing, stock included."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from typing import Any

import httpx2
import pytest
from sqlmodel import col, select

from hoard import plugin_manifest
from hoard.app import JOB_HANDLERS
from hoard.records import (
    Adjustment,
    AdjustmentKind,
    AutoManufacture,
    Base,
    Bin,
    Category,
    ItemType,
    Material,
    Product,
    Recipe,
    RecipeLine,
    TypeShape,
    Unit,
    Variation,
)
from seneschal_core import jobs
from seneschal_core.db import WriteSession
from seneschal_core.testing import InitiativeStandIn

MATERIALS_HEADER = (
    "id,sku,name,category,description,zone,section,position,inventoriable,stock_on_hand,"
    "stock_on_order,total_purchased,tracking_unit,purchase_unit,purchase_to_tracking_conversion,"
    "stock_status,state,unit_cost,actual_unit_cost,low_stock_limit,currency,starting_quantity,"
    "starting_unit_price,preferred_vendor,last_stocktake,Created At,Updated At"
)
RECIPES_HEADER = (
    "Recipe ID,Recipe Name,Product SKU,Product Name,Variation Name,Batch Quantity,"
    "Manufacturing Minutes,Recipe Notes,Material SKU,Material Name,Material Quantity,"
    "Variation ID,Project ID"
)
MANUFACTURES_HEADER = (
    "id,Start Date,End Date,Production Deadline,Batch Code,Auto Manufactured?,"
    "Order Associated?,Order Code,Order Quantity,Product,Variation,SKU,Minutes Worked,Quantity,"
    "Batch Quantity,Actual Quantity,Quantity Type,Total Labour Cost,Total Materials Cost,"
    "Currency,Notes,Status,Created At,Updated At"
)


def _material(
    name: str,
    category: str,
    tracking: str,
    bought: str,
    conversion: str,
    on_hand: str,
    cost: str,
    limit: str,
    vendor: str,
    zone: str = "",
    description: str = "",
    inventoriable: str = "yes",
) -> str:
    return (
        f'1,"",{name},{category},"{description}","{zone}","","",{inventoriable},{on_hand},0.0,0.0,'
        f"{tracking},{bought},{conversion},In Stock,active,0.1,{cost},{limit},USD,0.0,0.0,"
        f'"{vendor}","",2026-01-01 10:00:00 -0600,2026-01-02 10:00:00 -0600'
    )


MATERIALS = "\n".join(
    [
        MATERIALS_HEADER,
        _material(
            "Oolong Leaf", "Teas", "g", "oz", "28.34952", "500.0", "0.05", "100.0",
            "Leaf Traders", zone="A", description="Rolled",
        ),
        _material("Mint Leaf", "Herbs", "gram", "gram", "1.0", "200.0", "0.02", "0.0", "Leaf Traders"),
        _material(
            "Paper Tube", "Packaging", "item", "item", "1.0", "40.0", "0.5", "0.0", "Box Works",
            zone="Shelf 2",
        ),
        _material("Label", "packaging", "items", "items", "1.0", "0.0", "0.0", "0.0", ""),
    ]
)  # fmt: skip

RECIPES = "\n".join(
    [
        RECIPES_HEADER,
        '1,Garden Blend Batch,,Garden Blend,,700.0,15,Ratio: 5 oolong :: 2 mint,"",Oolong Leaf,500.0,,11',
        '1,,,,,,,,"",Mint Leaf,200.0,,',
        '2,Tea Packaging,,Tea Packaging,,1.0,0,,"",Paper Tube,1.0,,12',
        '2,,,,,,,,"",Label,2.0,,',
        '3,Size: Full Size,GB-FULL,Garden Blend Tea,Size: Full Size,1.0,0,,"",Garden Blend,60.0,9001,13',
        '3,,GB-FULL,,,,,,"",Tea Packaging,1.0,,',
        '4,Size: Sample,GB-SAMPLE,Garden Blend Tea,Size: Sample,1.0,0,,"",Garden Blend,10.0,9002,14',
        '5,Tea Sampler,SAMPLER-1,Tea Sampler,,1.0,0,,"",Garden Blend Tea,1.0,,15',
        '5,,SAMPLER-1,,,,,,"",Mint Leaf,0.0,,',
    ]
)

MANUFACTURES = "\n".join(
    [
        MANUFACTURES_HEADER,
        "1,2026-01-11 00:00:00 -0600,2026-01-11 00:00:00 -0600,,M1,No,No,,,Tea Sampler,"
        "Default Variation,SAMPLER-1,0,4.0,1.0,4.0,item,0.0,2.0,USD,,completed,,",
        "2,2026-01-11 00:00:00 -0600,2026-01-11 00:00:00 -0600,,M2,Yes,No,,,Tea Packaging,"
        ",,0,4.0,1.0,4.0,item,0.0,2.0,USD,,completed,,",
    ]
)


@pytest.fixture
def suppliers(initiative_stand_in: InitiativeStandIn) -> dict[str, int]:
    """The community's contacts, which Initiative's stand-in lists and adds to."""
    held = {"Box Works": 7}

    def listed(request: httpx2.Request) -> httpx2.Response:
        items = [{"id": id_, "name": name, "is_organization": True} for name, id_ in held.items()]
        return httpx2.Response(200, json={"items": items, "has_next": False})

    def added(request: httpx2.Request) -> httpx2.Response:
        name = json.loads(request.content)["name"]
        held[name] = 100 + len(held)
        return httpx2.Response(201, json={"id": held[name], "name": name})

    initiative_stand_in.routes[("GET", "/contacts/")] = listed
    initiative_stand_in.routes[("POST", "/contacts/")] = added
    return held


def _upload(stock_dated: str | None = None, **files: str) -> dict[str, Any]:
    return {
        "files": {name: (f"{name}.csv", content.encode()) for name, content in files.items()},
        "data": {"options": json.dumps({"stock_dated": stock_dated})},
    }


EVERYTHING = {"materials": MATERIALS, "recipes": RECIPES, "manufactures": MANUFACTURES}


async def _import(client, app, organization_id, **upload: Any) -> dict[str, Any]:
    queued = await client.post("/api/v1/imports/stocksmith", **upload)
    assert queued.status_code == 202, queued.text
    await jobs.work(app.state.plugin, organization_id, JOB_HANDLERS)
    done = (await client.get(f"/api/v1/jobs/{queued.json()['id']}")).json()
    assert done["state"] == "done", done
    return done["result"]


def _counts(checked: dict[str, Any]) -> dict[str, tuple[int, int]]:
    return {count["kind"]: (count["new"], count["unchanged"]) for count in checked["counts"]}


async def test_a_check_counts_what_the_exports_make_and_writes_nothing(
    client, sign_in, suppliers, db: WriteSession
):
    await sign_in(client, admin=True)

    checked = await client.post(
        "/api/v1/imports/stocksmith/check", **_upload("2026-12-31", **EVERYTHING)
    )

    assert checked.status_code == 200, checked.text
    assert checked.json()["problems"] == []
    assert _counts(checked.json()) == {
        "materials": (4, 0),
        "categories": (3, 0),
        "suppliers": (1, 1),
        "stock": (3, 0),
        "bases": (2, 0),
        "products": (2, 0),
        "variations": (3, 0),
        "recipes": (5, 0),
    }
    assert (await db.exec(select(Material))).all() == []
    assert suppliers == {"Box Works": 7}


async def test_an_import_makes_the_catalogue_through_hoards_own_paths(
    client, sign_in, suppliers, app, organization_id, db: WriteSession
):
    await sign_in(client, (plugin_manifest.SEE_COSTS,), admin=True)

    result = await _import(client, app, organization_id, **_upload(**EVERYTHING))

    assert result == {
        "created": {
            "suppliers": 1,
            "categories": 3,
            "materials": 4,
            "bases": 2,
            "products": 2,
            "variations": 3,
            "recipes": 5,
        },
        "matched": {"suppliers": 1},
        "changed": {},
        "failed": {},
        "failed_rows": [],
    }
    units = {unit.id: unit.standard for unit in await db.exec(select(Unit))}
    oolong = (await db.exec(select(Material).where(Material.name == "Oolong Leaf"))).one()
    assert (units[oolong.tracking_unit_id], units[oolong.purchase_unit_id or 0]) == (
        "gram",
        "ounce",
    )
    assert (oolong.purchase_conversion, oolong.low_stock_limit, oolong.notes) == (
        Decimal("28.349523"),
        Decimal("100"),
        "Rolled",
    )
    assert oolong.supplier_contact_id == suppliers["Leaf Traders"]
    tube = (await db.exec(select(Material).where(Material.name == "Paper Tube"))).one()
    assert (units[tube.tracking_unit_id], tube.purchase_unit_id, tube.supplier_contact_id) == (
        "each",
        None,
        7,
    )
    assert {
        material.name: material.low_stock_limit for material in await db.exec(select(Material))
    }["Mint Leaf"] is None
    # "packaging" and "Packaging" are one category.
    assert sorted((await db.exec(select(Category.name))).all()) == ["Herbs", "Packaging", "Teas"]
    assert {(row.item_id, row.zone) for row in await db.exec(select(Bin))} == {
        (oolong.id, "A"),
        (tube.id, "Shelf 2"),
    }
    assert (await db.exec(select(Adjustment))).all() == []

    blend = (await db.exec(select(Base).where(Base.name == "Garden Blend"))).one()
    packaging = (await db.exec(select(Base).where(Base.name == "Tea Packaging"))).one()
    assert (units[blend.tracking_unit_id], units[packaging.tracking_unit_id]) == ("gram", "each")
    # Stocksmith made the packaging by itself when a run needed it.
    assert (blend.auto_manufacture, packaging.auto_manufacture) == (
        None,
        AutoManufacture.OUT_OF_STOCK,
    )
    by_ratio = (await db.exec(select(Recipe).where(Recipe.base_id == blend.id))).one()
    assert (
        by_ratio.by_ratio,
        by_ratio.total,
        units[by_ratio.total_unit_id or 0],
        by_ratio.batch_quantity,
        by_ratio.minutes,
        by_ratio.notes,
    ) == (True, Decimal("700"), "gram", Decimal("700"), 15, "Ratio: 5 oolong :: 2 mint")
    tubes = (await db.exec(select(Recipe).where(Recipe.base_id == packaging.id))).one()
    assert (tubes.total, units[tubes.total_unit_id or 0]) == (Decimal("3"), "each")

    tea = (await db.exec(select(Product).where(Product.name == "Garden Blend Tea"))).one()
    made_type = await db.get_one(ItemType, tea.type_id)
    assert made_type.shape == TypeShape.MADE
    variations = {
        variation.sku: variation
        for variation in await db.exec(select(Variation).where(Variation.product_id == tea.id))
    }
    assert {sku: variation.attributes for sku, variation in variations.items()} == {
        "GB-FULL": {"Size": "Full Size"},
        "GB-SAMPLE": {"Size": "Sample"},
    }
    full = (
        await db.exec(select(Recipe).where(Recipe.variation_id == variations["GB-FULL"].id))
    ).one()
    assert not full.by_ratio
    assert [
        (line.item_id, line.written, line.quantity)
        for line in await db.exec(
            select(RecipeLine)
            .where(RecipeLine.recipe_id == full.id)
            .order_by(col(RecipeLine.position))
        )
    ] == [(blend.id, Decimal("60"), Decimal("60")), (packaging.id, Decimal("1"), Decimal("1"))]
    sampler = (
        await db.exec(
            select(Variation)
            .join(Product, col(Product.id) == col(Variation.product_id))
            .where(Product.name == "Tea Sampler")
        )
    ).one()
    assert (sampler.sku, sampler.attributes, sampler.is_default) == ("SAMPLER-1", {}, True)
    sampled = (await db.exec(select(Recipe).where(Recipe.variation_id == sampler.id))).one()
    # A line of zero is left out; a line naming a product takes its default variation.
    assert [
        line.item_id
        for line in await db.exec(select(RecipeLine).where(RecipeLine.recipe_id == sampled.id))
    ] == [variations["GB-FULL"].id]


async def test_running_it_again_makes_only_what_is_missing_and_stock_once(
    client, sign_in, suppliers, app, organization_id, db: WriteSession
):
    await sign_in(client, (plugin_manifest.SEE_COSTS,), admin=True)
    await _import(client, app, organization_id, **_upload(**EVERYTHING))

    again = await client.post(
        "/api/v1/imports/stocksmith/check", **_upload("2026-12-31", materials=MATERIALS)
    )
    assert _counts(again.json()) == {
        "materials": (0, 4),
        "categories": (0, 3),
        "suppliers": (0, 2),
        "stock": (3, 0),
    }
    stocked = await _import(
        client, app, organization_id, **_upload("2026-12-31", materials=MATERIALS)
    )
    assert (stocked["created"], stocked["matched"]) == (
        {"stock": 3},
        {"suppliers": 2, "categories": 3, "materials": 4},
    )
    again = await _import(client, app, organization_id, **_upload("2026-12-31", **EVERYTHING))
    assert (again["created"], again["failed"]) == ({}, {})
    assert again["matched"] == {
        "suppliers": 2,
        "categories": 3,
        "materials": 4,
        "bases": 2,
        "products": 2,
        "variations": 3,
        "recipes": 5,
    }

    started = {
        (row.kind, row.dated_on, row.change, row.unit_cost)
        for row in await db.exec(select(Adjustment))
    }
    assert started == {
        (AdjustmentKind.STARTING, date(2026, 12, 31), Decimal("500"), Decimal("0.05")),
        (AdjustmentKind.STARTING, date(2026, 12, 31), Decimal("200"), Decimal("0.02")),
        (AdjustmentKind.STARTING, date(2026, 12, 31), Decimal("40"), Decimal("0.5")),
    }
    assert len((await db.exec(select(Material))).all()) == 4
    assert len((await db.exec(select(Recipe))).all()) == 5


async def test_the_check_names_each_problem_with_its_row(client, sign_in, suppliers, app):
    await sign_in(client, admin=True)
    recipes = "\n".join(
        [
            RECIPES_HEADER,
            '1,A,,Blend A,,10.0,0,,"",Blend B,5.0,,',
            '1,,,,,,,,"",Ghost Leaf,5.0,,',
            '2,B,,Blend B,,10.0,0,,"",Blend A,5.0,,',
            '3,A again,,Blend A,,10.0,0,,"",Mint Leaf,5.0,,',
            '4,C,,Blend C,,10.0,0,,"",Mint Leaf,5.0,,',
            '4,,,,,,,,"",Mint Leaf,1.0,,',
        ]
    )
    materials = "\n".join(
        [
            MATERIALS_HEADER,
            _material("Mint Leaf", "Herbs", "gram", "gram", "1.0", "0.0", "0", "0", ""),
            _material("Rose Water", "Herbs", "pinch", "pinch", "1.0", "0.0", "0", "0", ""),
        ]
    )

    checked = await client.post(
        "/api/v1/imports/stocksmith/check",
        **_upload(materials=materials, recipes=recipes, manufactures="Product,SKU\n"),
    )

    assert sorted(
        (problem["code"], problem["file"], problem["row"], problem["name"])
        for problem in checked.json()["problems"]
    ) == [
        ("COLUMN_MISSING", "manufactures", 1, "Auto Manufactured?"),
        ("COLUMN_MISSING", "manufactures", 1, "Variation"),
        ("LINE_NAMES_NOTHING", "recipes", 3, "Ghost Leaf"),
        ("LINE_REPEATED", "recipes", 7, "Mint Leaf"),
        ("RECIPE_CONTAINS_ITSELF", "recipes", 2, "Blend A"),
        ("RECIPE_CONTAINS_ITSELF", "recipes", 4, "Blend B"),
        ("RECIPE_REPEATED", "recipes", 5, "Blend A"),
        ("UNIT_UNKNOWN", "materials", 3, "pinch"),
    ]
    refused = await client.post(
        "/api/v1/imports/stocksmith", **_upload(materials=materials, recipes=recipes)
    )
    assert (refused.status_code, refused.json()["detail"]) == (409, "IMPORT_PROBLEMS")


async def test_a_row_its_write_path_refuses_is_listed_and_the_import_carries_on(
    client, sign_in, suppliers, app, organization_id, db: WriteSession
):
    await sign_in(client, admin=True)
    materials = "\n".join(
        [
            MATERIALS_HEADER,
            _material("Mint Leaf", "Herbs", "gram", "gram", "1.0", "0", "0", "0", ""),
            _material(
                "Spoon", "Tools", "item", "item", "1.0", "0", "0", "0", "", inventoriable="no"
            ),
        ]
    )
    recipes = "\n".join(
        [
            RECIPES_HEADER,
            '1,Mint Mix,,Mint Mix,,10.0,0,,"",Mint Leaf,5.0,,',
            '2,Stirred,,Stirred Mix,,10.0,0,,"",Mint Leaf,5.0,,',
            '2,,,,,,,,"",Spoon,1.0,,',
        ]
    )

    result = await _import(
        client, app, organization_id, **_upload(materials=materials, recipes=recipes)
    )

    # A tool carries no stock, so no recipe holds it: that recipe alone is left out.
    assert (result["failed"], result["failed_rows"]) == (
        {"recipes": 1},
        [{"file": "recipes", "row": 3, "field": None, "code": "RECIPE_LINE_KIND"}],
    )
    assert result["created"]["recipes"] == 1
    assert {base.name for base in await db.exec(select(Base))} == {"Mint Mix", "Stirred Mix"}
    assert len((await db.exec(select(Recipe))).all()) == 1
