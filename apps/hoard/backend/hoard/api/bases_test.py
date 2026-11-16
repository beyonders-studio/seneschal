"""Bases, their properties, and their stock at each location; and the places
stock is moved, counted and reported, which hold bases beside materials."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from hoard import recipes, runs
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
    Stocktake,
    Unit,
)
from hoard.stock import add_product, adjust
from hoard.stocktakes import confirm_row, start_stocktake
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
        json={
            "name": "Brown sugar syrup",
            "tracking_unit_id": unit.id,
            "category_id": syrups.id,
            "cure_days": 2,
        },
    )
    assert made.status_code == 201
    syrup = made.json()
    assert (syrup["on_hand"], syrup["stock"], syrup["category_id"], syrup["cure_days"]) == (
        "0",
        [],
        syrups.id,
        2,
    )
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
    client, sign_in, db: WriteSession, main: Location, base, export
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
    exported = await export("bases", format="json")

    assert moved["item"] == {
        "id": spice.id,
        "kind": "base",
        "name": "Chai spice",
        "attributes": {},
        "archived": False,
    }
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


async def test_an_archived_base_leaves_lists_and_pickers_and_keeps_its_history(
    client, sign_in, db: WriteSession, main: Location, unit: Unit, base
):
    spice = await base("Chai spice", low_stock_limit=Decimal(100))
    _, latte = await add_product(db, Product(name="Chai latte"))
    recipe = await recipes.save(db, Recipe(variation_id=latte.id), [Line(spice.id, Decimal(1))])
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
    body = {"name": "Chai spice", "tracking_unit_id": unit.id, "low_stock_limit": "100"}

    archived = await client.put(f"/api/v1/bases/{spice.id}", json=body | {"status": "archived"})
    assert archived.json()["status"] == "archived"

    def names(page: dict) -> list[str]:
        return [row["name"] for row in page["items"]]

    assert names((await client.get("/api/v1/bases")).json()) == []
    assert names((await client.get("/api/v1/bases", params={"archived": True})).json()) == [
        "Chai spice"
    ]
    [line] = (await client.get(f"/api/v1/recipes/{recipe.id}")).json()["lines"]
    assert (line["item"]["name"], line["item"]["archived"]) == ("Chai spice", True)
    counted = await client.post(
        "/api/v1/stocktakes", json={"kind": "base", "dated_on": "2026-11-03"}
    )
    assert [row["item"]["id"] for row in counted.json()["rows"]] == [spice.id]
    valuation = (await client.get("/api/v1/reports/valuation")).json()
    assert [row["value"] for row in valuation["items"]] == ["80.000000"]
    assert (await client.get("/api/v1/reports/reorder")).json() == []

    restored = await client.put(f"/api/v1/bases/{spice.id}", json=body)
    assert restored.json()["status"] == "active"
    assert names((await client.get("/api/v1/bases")).json()) == ["Chai spice"]
    assert [row["name"] for row in (await client.get("/api/v1/reports/reorder")).json()] == [
        "Chai spice"
    ]


async def test_a_base_page_shows_its_figures_and_its_history(
    client, sign_in, db: WriteSession, main: Location, material, base, lot
):
    shed = Location(organization_id=db.organization_id, name="Shed")
    db.add(shed)
    await db.flush()
    catnip, chamomile = await material("Catnip"), await material("Chamomile")
    alyx = await base("Alyx batch")
    await recipes.save(
        db,
        Recipe(base_id=alyx.id, batch_quantity=Decimal(300)),
        [Line(catnip.id, Decimal(200)), Line(chamomile.id, Decimal(100))],
    )
    await lot(catnip.id, "C1", "1000")
    await lot(chamomile.id, "H1", "150")
    await lot(chamomile.id, "H2", "1000", location=shed)
    made = await runs.make(db, alyx.id, main.id, Decimal(300), date(2026, 11, 4))
    [row] = await start_stocktake(
        db, Stocktake(kind=CategoryKind.BASE, location_id=main.id, dated_on=date(2026, 11, 5))
    )
    await confirm_row(db, row, Decimal(300))
    await db.commit()
    await sign_in(client)

    page = (await client.get(f"/api/v1/bases/{alyx.id}")).json()

    # 800 catnip and 50 chamomile are left at the main location, which make
    # half a batch; the shed's chamomile has no catnip beside it.
    assert (page["potential"], page["made"], page["last_stocktake"]) == (
        "150.000000",
        "300.000000",
        "2026-11-05",
    )
    assert page["stock"] == [
        {
            "location_id": main.id,
            "quantity": "300.000000",
            "bin": None,
            "could_make": "150.000000",
            "last_stocktake": "2026-11-05",
        }
    ]
    assert (await client.get(f"/api/v1/materials/{catnip.id}")).json()["potential"] is None

    async def codes(**params: int) -> list[str]:
        runs_of = (await client.get("/api/v1/runs", params=params)).json()["items"]
        return [run["code"] for run in runs_of]

    assert made is not None
    assert await codes(item_id=alyx.id) == await codes(used_item_id=catnip.id) == [made.code]
    assert await codes(used_item_id=alyx.id) == []
    counted = (await client.get("/api/v1/stocktakes", params={"item_id": alyx.id})).json()
    assert (counted["total"], counted["items"][0]["count"]) == (
        1,
        {"expected": "300.000000", "counted": "300.000000"},
    )
    assert (await client.get("/api/v1/stocktakes", params={"item_id": catnip.id})).json()[
        "total"
    ] == 0
    assert (await client.get("/api/v1/stocktakes")).json()["items"][0]["count"] is None
