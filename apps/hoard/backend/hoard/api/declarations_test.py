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
        json={
            "ingredient_name": "Hazelnut flavour syrup",
            "allergens": ["tree_nuts", "milk"],
            "fragrance_allergens": None,
        },
    )

    assert (unknown["recorded_allergens"], unknown["undeclared"]) == (None, ["Hazelnut syrup"])
    assert recorded.json() == {
        "records_own": True,
        "recorded_allergens": ["milk", "tree_nuts"],
        "ingredient_name": "Hazelnut flavour syrup",
        "ingredients": ["Hazelnut flavour syrup"],
        "allergens": ["milk", "tree_nuts"],
        "undeclared": [],
        "recorded_fragrance": None,
        "fragrance_recorded": False,
        "rinse_off": False,
        "fragrance_threshold": "0.001",
        "fragrance_shares": {},
        "fragrance_allergens": [],
        "fragrance_undeclared": ["Hazelnut syrup"],
    }


async def test_a_material_records_its_fragrance_allergens(
    client, sign_in, db: WriteSession, material
):
    oil = await material("Lavender oil", allergens=[])
    await db.commit()
    await sign_in(client)

    recorded = await client.put(
        f"/api/v1/declarations/material/{oil.id}",
        json={
            "ingredient_name": None,
            "allergens": [],
            "fragrance_allergens": {"limonene": "0.0005", "linalool": 30},
        },
    )
    refused = await client.put(
        f"/api/v1/declarations/material/{oil.id}",
        json={"ingredient_name": None, "allergens": [], "fragrance_allergens": {"linalool": 101}},
    )

    assert recorded.status_code == 200
    await db.refresh(oil)
    assert oil.fragrance_allergens == {"linalool": "30", "limonene": "0.0005"}
    read = recorded.json()
    assert (read["recorded_fragrance"], read["fragrance_recorded"]) == (
        {"linalool": "30", "limonene": "0.0005"},
        True,
    )
    assert (read["fragrance_allergens"], read["fragrance_undeclared"]) == (["linalool"], [])
    assert refused.status_code == 422


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

    bought = await client.put(
        f"/api/v1/declarations/lot/{lots[0].id}",
        json={"allergens": [], "fragrance_allergens": {"citral": "1.5"}},
    )
    made = await client.put(
        f"/api/v1/declarations/lot/{lots[1].id}",
        json={"allergens": [], "fragrance_allergens": None},
    )

    assert (bought.status_code, bought.json()["recorded_allergens"]) == (200, [])
    assert bought.json()["recorded_fragrance"] == {"citral": "1.5"}
    assert (made.status_code, made.json()) == (422, {"detail": "NOT_BOUGHT"})
