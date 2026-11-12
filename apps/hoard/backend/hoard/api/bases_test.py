"""Bases, their properties, and their stock at each location; and the places
stock is moved, counted and reported, which hold bases beside materials."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from hoard import recipes
from hoard.recipes import Line
from hoard.records import (
    Adjustment,
    AdjustmentKind,
    Category,
    CategoryKind,
    Location,
    Product,
    PropertyDefinition,
    PropertyType,
    Recipe,
    RecordKind,
    Unit,
)
from hoard.stock import add_product, adjust
from seneschal_core.db import WriteSession


async def test_a_base_is_added_edited_and_removed_and_holds_its_properties(
    client, sign_in, db: WriteSession, unit: Unit, material, base
):
    syrups = Category(organization_id=db.organization_id, kind=CategoryKind.BASE, name="Syrups")
    tea = Category(organization_id=db.organization_id, kind=CategoryKind.MATERIAL, name="Tea")
    shelf_life = PropertyDefinition(
        organization_id=db.organization_id,
        kind=RecordKind.BASE,
        name="Shelf life",
        type=PropertyType.TEXT,
    )
    db.add_all([syrups, tea, shelf_life])
    used = await base("Chai spice")
    _, latte = await add_product(db, Product(name="Chai latte"))
    await recipes.save(db, Recipe(variation_id=latte.id), [Line(used.id, Decimal(1))])
    await db.commit()
    await sign_in(client)

    made = await client.post(
        "/api/v1/bases",
        json={"name": "Brown sugar syrup", "tracking_unit_id": unit.id, "category_id": syrups.id},
    )
    assert made.status_code == 201
    syrup = made.json()
    assert (syrup["on_hand"], syrup["stock"], syrup["category_id"]) == ("0", [], syrups.id)
    wrong = await client.put(
        f"/api/v1/bases/{syrup['id']}",
        json={"name": "Syrup", "tracking_unit_id": unit.id, "category_id": tea.id},
    )
    assert (wrong.status_code, wrong.json()) == (422, {"detail": "CATEGORY_UNKNOWN"})
    edited = await client.put(
        f"/api/v1/bases/{syrup['id']}",
        json={"name": "Syrup", "tracking_unit_id": unit.id, "low_stock_limit": "500"},
    )
    assert (edited.json()["name"], edited.json()["low_stock_limit"]) == ("Syrup", "500.000000")
    valued = await client.put(
        f"/api/v1/properties/values/base/{syrup['id']}",
        json={"values": [{"property_id": shelf_life.id, "value": "Two weeks"}]},
    )
    assert valued.status_code == 200, valued.text
    listed = (await client.get("/api/v1/bases", params={"q": "syr"})).json()
    assert (listed["total"], listed["items"][0]["properties"][0]["value"]) == (1, "Two weeks")

    assert (await client.delete(f"/api/v1/bases/{syrup['id']}")).status_code == 204
    assert (await client.get(f"/api/v1/bases/{syrup['id']}")).status_code == 404
    kept = await client.delete(f"/api/v1/bases/{used.id}")
    assert (kept.status_code, kept.json()) == (409, {"detail": "IN_USE"})


async def test_bases_are_moved_counted_reported_and_exported_beside_materials(
    client, sign_in, db: WriteSession, main: Location, base
):
    spice = await base("Chai spice", low_stock_limit=Decimal(100))
    await adjust(
        db,
        Adjustment(
            item_id=spice.id,
            location_id=main.id,
            kind=AdjustmentKind.STARTING,
            dated_on=date(2026, 11, 2),
            change=Decimal(40),
            unit_cost=Decimal(2),
        ),
    )
    await db.commit()
    await sign_in(client, ("costs",))

    [moved] = (await client.get("/api/v1/adjustments", params={"item_id": spice.id})).json()[
        "items"
    ]
    page = (await client.get(f"/api/v1/bases/{spice.id}")).json()
    counted = await client.post(
        "/api/v1/stocktakes", json={"kind": "base", "dated_on": "2026-11-03"}
    )
    reorder = (await client.get("/api/v1/reports/reorder")).json()
    valuation = (await client.get("/api/v1/reports/valuation")).json()
    exported = await client.get("/api/v1/export/bases", params={"format": "json"})

    assert moved["item"] == {"id": spice.id, "kind": "base", "name": "Chai spice", "attributes": {}}
    assert (page["on_hand"], page["average_cost"]) == ("40.000000", "2.000000")
    assert counted.status_code == 201, counted.text
    assert [row["item"]["id"] for row in counted.json()["rows"]] == [spice.id]
    assert [(row["kind"], row["name"], row["unit"]) for row in reorder] == [
        ("base", "Chai spice", "g")
    ]
    assert [(row["kind"], row["name"], row["value"]) for row in valuation["items"]] == [
        ("base", "Chai spice", "80.000000")
    ]
    assert [row["name"] for row in exported.json()] == ["Chai spice"]
