"""Declarations: recorded on a material and a bought lot, read rolled up."""

from __future__ import annotations

from datetime import date

from hoard.records import Location, Lot
from seneschal_core.db import WriteSession


async def test_a_material_records_what_its_supplier_declares(
    client, sign_in, db: WriteSession, material
):
    syrup = await material("Hazelnut syrup")
    await db.commit()
    await sign_in(client)

    unknown = (await client.get(f"/api/v1/declarations/material/{syrup.id}")).json()
    recorded = await client.put(
        f"/api/v1/declarations/material/{syrup.id}",
        json={"ingredient_name": "Hazelnut flavour syrup", "allergens": ["tree_nuts", "milk"]},
    )

    assert (unknown["recorded_allergens"], unknown["undeclared"]) == (None, ["Hazelnut syrup"])
    assert recorded.json() == {
        "records_own": True,
        "recorded_allergens": ["milk", "tree_nuts"],
        "ingredient_name": "Hazelnut flavour syrup",
        "ingredients": ["Hazelnut flavour syrup"],
        "allergens": ["milk", "tree_nuts"],
        "undeclared": [],
    }


async def test_only_a_bought_lot_records_allergens_of_its_own(
    client, sign_in, db: WriteSession, material, base, main: Location
):
    lots = []
    for item in (await material("Matcha", allergens=[]), await base("Matcha paste")):
        lot = Lot(
            organization_id=db.organization_id,
            item_id=item.id,
            location_id=main.id,
            number=f"L-{item.id}",
            dated_on=date(2026, 11, 1),
        )
        db.add(lot)
        lots.append(lot)
    await db.commit()
    await sign_in(client)

    bought = await client.put(f"/api/v1/declarations/lot/{lots[0].id}", json={"allergens": []})
    made = await client.put(f"/api/v1/declarations/lot/{lots[1].id}", json={"allergens": []})

    assert (bought.status_code, bought.json()["recorded_allergens"]) == (200, [])
    assert (made.status_code, made.json()) == (422, {"detail": "NOT_BOUGHT"})
