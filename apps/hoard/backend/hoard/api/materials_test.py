"""Materials, and their stock at each location."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from hoard import plugin_manifest
from hoard.records import Adjustment, AdjustmentKind, Category, CategoryKind, Location, Unit
from hoard.stock import adjust
from seneschal_core.db import WriteSession


async def test_a_material_page_holds_its_stock_and_lots_at_each_location(
    client, sign_in, db: WriteSession, main: Location, material, buy
):
    back = Location(organization_id=db.organization_id, name="Back room")
    db.add(back)
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
    await db.commit()
    await sign_in(client)

    page = (await client.get(f"/api/v1/materials/{oolong.id}")).json()

    assert (page["name"], page["on_hand"], page["low_stock_limit"]) == (
        "Oolong",
        "13.000000",
        "5.000000",
    )
    assert page["stock"] == [
        {"location_id": main.id, "quantity": "10.000000"},
        {"location_id": back.id, "quantity": "3.000000"},
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
    tea = Category(organization_id=db.organization_id, kind=CategoryKind.MATERIAL, name="Tea")
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
