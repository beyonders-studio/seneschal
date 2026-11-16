"""Materials, and their stock at each location."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from hoard import plugin_manifest, recipes, runs
from hoard.recipes import Line
from hoard.records import (
    Adjustment,
    AdjustmentKind,
    Category,
    CategoryKind,
    ItemStatus,
    ItemType,
    Location,
    Material,
    Product,
    Purchase,
    PurchaseLine,
    Recipe,
    TypeShape,
    Unit,
)
from hoard.stock import add_product, adjust, transfer
from seneschal_core.db import WriteSession

#: A material's stock row's figures that only a recipe or a stocktake gives.
NONE = {"bin": None, "could_make": None, "last_stocktake": None, "consigned": "0"}


async def test_a_material_page_holds_its_stock_and_lots_at_each_location(
    client, sign_in, db: WriteSession, main: Location, material, buy
):
    back = Location(organization_id=db.organization_id, name="Back room")
    shop = Location(organization_id=db.organization_id, name="Shop", contact_id=3, consignee=True)
    db.add_all([back, shop])
    await db.flush()
    oolong = await material("Oolong", low_stock_limit=Decimal(5))
    await buy((oolong, "10", "2"))
    await adjust(
        db,
        Adjustment(
            item_id=oolong.id,
            location_id=back.id,
            kind=AdjustmentKind.MANUAL,
            dated_on=date(2026, 11, 2),
            change=Decimal(3),
        ),
    )
    await transfer(db, oolong.id, back.id, shop.id, Decimal(1), dated_on=date(2026, 11, 3))
    await db.commit()
    await sign_in(client)

    page = (await client.get(f"/api/v1/materials/{oolong.id}")).json()

    assert (page["name"], page["on_hand"], page["consigned"], page["low_stock_limit"]) == (
        "Oolong",
        "13.000000",
        "1.000000",
        "5.000000",
    )
    assert page["stock"] == [
        {**NONE, "location_id": main.id, "quantity": "10.000000"},
        {**NONE, "location_id": back.id, "quantity": "2.000000"},
        {**NONE, "location_id": shop.id, "quantity": "1.000000", "consigned": "1.000000"},
    ]
    assert [(lot["location_id"], lot["balance"]) for lot in page["lots"]] == [
        (main.id, "10.000000")
    ]
    assert page["created_by"] == "member-ref"
    listed = (await client.get("/api/v1/materials", params={"q": "oolo"})).json()
    assert (listed["total"], listed["items"][0]["on_hand"]) == (1, "13.000000")


async def test_costs_go_only_to_a_member_who_sees_them(
    client, sign_in, db: WriteSession, material, buy
):
    oolong = await material("Oolong")
    await buy((oolong, "4", "2.5"))
    await db.commit()

    await sign_in(client)
    assert (await client.get(f"/api/v1/materials/{oolong.id}")).json()["average_cost"] is None
    assert (await client.get("/api/v1/materials")).json()["items"][0]["average_cost"] is None

    await sign_in(client, (plugin_manifest.SEE_COSTS,))
    assert (await client.get(f"/api/v1/materials/{oolong.id}")).json()["average_cost"] == "2.500000"


async def test_a_material_is_added_edited_and_removed_with_the_organization_s_settings(
    client, sign_in, db: WriteSession, unit: Unit, material, buy
):
    tins = ItemType(
        organization_id=db.organization_id,
        kind=CategoryKind.MATERIAL,
        name="Tins",
        shape=TypeShape.PACKAGING,
    )
    db.add(tins)
    await db.flush()
    tea = Category(
        organization_id=db.organization_id,
        kind=CategoryKind.MATERIAL,
        name="Tea",
        default_type_id=tins.id,
    )
    boxes = Category(organization_id=db.organization_id, kind=CategoryKind.PRODUCT, name="Boxes")
    db.add_all([tea, boxes])
    bought = await material("Sencha")
    await buy((bought, "1", "1"))
    await db.commit()
    await sign_in(client)

    made = await client.post(
        "/api/v1/materials",
        json={"name": "Oolong", "tracking_unit_id": unit.id, "category_id": tea.id},
    )
    assert made.status_code == 201
    oolong = made.json()
    assert (oolong["on_hand"], oolong["stock"], oolong["category_id"]) == ("0", [], tea.id)
    # Made in a category, it takes the category's default type; listed by its type.
    assert oolong["type_id"] == tins.id
    typed = (await client.get("/api/v1/materials", params={"type_id": tins.id})).json()
    assert [each["name"] for each in typed["items"]] == ["Oolong"]

    wrong = await client.put(
        f"/api/v1/materials/{oolong['id']}",
        json={"name": "Oolong", "tracking_unit_id": unit.id, "category_id": boxes.id},
    )
    assert (wrong.status_code, wrong.json()) == (422, {"detail": "CATEGORY_UNKNOWN"})
    edited = await client.put(
        f"/api/v1/materials/{oolong['id']}",
        json={"name": "Milk oolong", "tracking_unit_id": unit.id, "purchase_conversion": "250"},
    )
    assert (edited.json()["name"], edited.json()["purchase_conversion"]) == (
        "Milk oolong",
        "250.000000",
    )

    assert (await client.delete(f"/api/v1/materials/{oolong['id']}")).status_code == 204
    assert (await client.get(f"/api/v1/materials/{oolong['id']}")).status_code == 404
    kept = await client.delete(f"/api/v1/materials/{bought.id}")
    assert (kept.status_code, kept.json()) == (409, {"detail": "IN_USE"})


async def test_standard_units_fill_in_the_conversion_and_a_tracking_unit_with_history_stays(
    client, sign_in, db: WriteSession, unit: Unit, buy, base
):
    jar = Unit(organization_id=db.organization_id, name="Jar")
    db.add(jar)
    await db.commit()
    await sign_in(client)
    listed = (await client.get("/api/v1/settings/units")).json()
    kilograms, millilitres = (
        next(each["id"] for each in listed if each["standard"] == key)
        for key in ("kilogram", "millilitre")
    )

    def body(**fields):
        return {"name": "Catnip", "tracking_unit_id": unit.id, **fields}

    bought = await client.post(
        "/api/v1/materials",
        json=body(purchase_unit_id=kilograms, purchase_conversion="999", unit_weight="3"),
    )
    by_jar = await client.post(
        "/api/v1/materials",
        json=body(name="Honey", purchase_unit_id=jar.id, purchase_conversion="250"),
    )
    flavor = await client.post(
        "/api/v1/materials",
        json=body(name="Lychee", tracking_unit_id=millilitres, unit_weight="1.04"),
    )

    assert (bought.json()["purchase_conversion"], bought.json()["unit_weight"]) == (
        "1000.000000",
        None,
    )
    assert by_jar.json()["purchase_conversion"] == "250.000000"
    assert flavor.json()["unit_weight"] == "1.040000"
    catnip = bought.json()["id"]
    assert (
        await client.put(f"/api/v1/materials/{catnip}", json=body(tracking_unit_id=kilograms))
    ).status_code == 200
    # The refusal names what holds the unit: a recipe, then its stock first of all.
    syrup = await base("Syrup")
    await recipes.save(db, Recipe(base_id=syrup.id), [Line(catnip, Decimal(1))])
    await db.commit()
    held = await client.put(f"/api/v1/materials/{catnip}", json=body())
    assert (held.status_code, held.json()) == (409, {"detail": "TRACKING_UNIT_IN_RECIPES"})
    await buy((await db.get_one(Material, catnip), "1", "4"))
    await db.commit()
    held = await client.put(f"/api/v1/materials/{catnip}", json=body())
    assert (held.status_code, held.json()) == (409, {"detail": "TRACKING_UNIT_IN_STOCK"})


async def test_a_new_material_starts_with_its_notes_bin_and_stock(
    client, sign_in, db: WriteSession, main: Location, unit: Unit
):
    back = Location(organization_id=db.organization_id, name="Back room")
    db.add(back)
    await db.commit()
    body = {"name": "Oolong", "tracking_unit_id": unit.id, "notes": "Keep sealed"}
    await sign_in(client, (plugin_manifest.SEE_COSTS,))

    made = await client.post(
        "/api/v1/materials",
        json=body
        | {"start": {"quantity": "500", "unit_cost": "0.04", "bin": {"zone": "A", "section": "3"}}},
    )
    assert made.status_code == 201, made.text
    page = made.json()
    assert (page["notes"], page["status"], page["on_hand"], page["average_cost"]) == (
        "Keep sealed",
        "active",
        "500.000000",
        "0.040000",
    )
    assert page["stock"] == [
        {
            **NONE,
            "location_id": main.id,
            "quantity": "500.000000",
            "bin": {"zone": "A", "section": "3", "position": None},
        }
    ]
    [started] = (await client.get("/api/v1/adjustments", params={"item_id": page["id"]})).json()[
        "items"
    ]
    assert (started["kind"], started["change"], started["unit_cost"]) == (
        "starting",
        "500.000000",
        "0.040000",
    )

    # From a member who doesn't see costs, at another location, it costs nothing.
    await sign_in(client)
    elsewhere = await client.post(
        "/api/v1/materials",
        json=body
        | {"name": "Sencha", "start": {"location_id": back.id, "quantity": "2", "unit_cost": "9"}},
    )
    assert elsewhere.json()["stock"] == [{**NONE, "location_id": back.id, "quantity": "2.000000"}]
    await sign_in(client, (plugin_manifest.SEE_COSTS,))
    [uncosted] = (
        await client.get("/api/v1/adjustments", params={"item_id": elsewhere.json()["id"]})
    ).json()["items"]
    assert (uncosted["kind"], uncosted["unit_cost"]) == ("starting", "0.000000")

    # A bin with no stock is kept; a material that carries no stock starts with none.
    binned = await client.post(
        "/api/v1/materials",
        json=body | {"name": "Matcha", "start": {"bin": {"position": "Top"}}},
    )
    assert binned.json()["stock"] == [
        {
            **NONE,
            "location_id": main.id,
            "quantity": "0",
            "bin": {"zone": None, "section": None, "position": "Top"},
        }
    ]
    refused = await client.post(
        "/api/v1/materials",
        json=body | {"name": "Scoop", "inventoriable": False, "start": {"quantity": "1"}},
    )
    assert (refused.status_code, refused.json()) == (409, {"detail": "NOT_INVENTORIABLE"})
    listed = (await client.get("/api/v1/materials", params={"q": "scoop"})).json()
    assert listed["total"] == 0


async def test_an_archived_material_leaves_pickers_and_comes_back_as_it_was(
    client, sign_in, db: WriteSession, unit: Unit, material, buy
):
    oolong = await material("Oolong", notes="Keep sealed")
    purchase = await buy((oolong, "4", "2.5"))
    await db.commit()
    await sign_in(client)
    body = {"name": "Oolong", "tracking_unit_id": unit.id, "notes": "Keep sealed"}

    archived = await client.put(
        f"/api/v1/materials/{oolong.id}", json=body | {"status": "archived"}
    )
    assert archived.json()["status"] == "archived"
    assert (await client.get("/api/v1/materials", params={"q": "oolong"})).json()["total"] == 0
    asked = (await client.get("/api/v1/materials", params={"archived": True})).json()
    assert [(row["name"], row["status"]) for row in asked["items"]] == [("Oolong", "archived")]
    bought = (await client.get(f"/api/v1/purchases/{purchase.id}")).json()
    assert [line["item"]["archived"] for line in bought["lines"]] == [True]

    restored = (await client.put(f"/api/v1/materials/{oolong.id}", json=body)).json()
    assert (restored["status"], restored["notes"], restored["on_hand"]) == (
        "active",
        "Keep sealed",
        "4.000000",
    )


async def test_a_material_page_shows_what_is_on_order_its_usage_and_what_uses_it(
    client, sign_in, db: WriteSession, main: Location, unit: Unit, material, base, buy
):
    chamomile = await material("Chamomile")
    blend = await base("Sleepy blend", status=ItemStatus.ARCHIVED)
    await recipes.save(
        db,
        Recipe(base_id=blend.id, batch_quantity=Decimal(100)),
        [Line(chamomile.id, Decimal(100))],
    )
    product, tin = await add_product(db, Product(name="Chamomile tin"))
    await recipes.save(db, Recipe(variation_id=tin.id), [Line(chamomile.id, Decimal(50))])
    today = date.today()
    await buy((chamomile, "500", "1"), dated_on=today)
    await runs.make(db, blend.id, main.id, Decimal(200), today)
    late = Purchase(
        organization_id=db.organization_id,
        code="P-LATE",
        dated_on=today - timedelta(days=10),
        expected_on=today - timedelta(days=1),
        location_id=main.id,
    )
    db.add(late)
    await db.flush()
    db.add(
        PurchaseLine(
            organization_id=db.organization_id,
            purchase_id=late.id,
            item_id=chamomile.id,
            purchase_quantity=Decimal(250),
            tracking_quantity=Decimal(250),
            unit_cost=Decimal(1),
        )
    )
    await db.commit()
    await sign_in(client)

    page = (await client.get(f"/api/v1/materials/{chamomile.id}")).json()

    assert (page["on_order"], page["overdue"]) == ("250.000000", True)
    assert page["usage"][0] == {"window": "week", "received": "500.000000", "used": "200.000000"}
    assert [usage["window"] for usage in page["usage"]] == [
        "week",
        "month",
        "months_3",
        "months_6",
        "months_12",
        "all",
    ]
    # By what each makes, the archived blend marked so.
    assert [
        (used["output"], used["output_id"], used["product_id"], used["archived"])
        for used in page["used_in"]
    ] == [
        ("variation", tin.id, product.id, False),
        ("base", blend.id, None, True),
    ]
    assert {
        (used["amount"], used["unit_id"], used["batch_quantity"], used["batch_unit_id"])
        for used in page["used_in"]
    } == {
        ("50.000000", unit.id, "1.000000", None),
        ("100.000000", unit.id, "100.000000", unit.id),
    }
