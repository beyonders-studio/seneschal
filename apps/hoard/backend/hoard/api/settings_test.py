"""The organization's settings."""

from __future__ import annotations

import pytest
from sqlmodel import select

from hoard import units
from hoard.records import AdjustmentReason, DefaultReason, OrganizationSettings, Unit
from seneschal_core.db import WriteSession


async def test_settings_read_as_their_defaults_until_changed(client, sign_in, db: WriteSession):
    await sign_in(client)
    assert (await client.get("/api/v1/settings")).json() == {
        "currency": "USD",
        "auto_manufacture": "off",
        "regimes": [],
        "labour_rate": None,
    }

    db.add(OrganizationSettings(organization_id=db.organization_id, currency="EUR"))
    await db.commit()
    assert (await client.get("/api/v1/settings")).json()["currency"] == "EUR"

    # A change leaves what it does not send as it is.
    await sign_in(client, admin=True)
    changed = await client.put("/api/v1/settings", json={"auto_manufacture": "out_of_stock"})
    assert changed.json() == {
        "currency": "EUR",
        "auto_manufacture": "out_of_stock",
        "regimes": [],
        "labour_rate": None,
    }
    # Regimes are kept once each, in their own order.
    ticked = await client.put(
        "/api/v1/settings", json={"regimes": ["eu_cosmetics", "us_cosmetics", "eu_cosmetics"]}
    )
    assert ticked.json()["regimes"] == ["us_cosmetics", "eu_cosmetics"]
    unknown = await client.put("/api/v1/settings", json={"regimes": ["moon_food"]})
    assert unknown.status_code == 422


async def test_the_labour_rate_is_a_cost_an_admin_sets(client, sign_in):
    await sign_in(client, ("costs",))
    refused = await client.put("/api/v1/settings", json={"labour_rate": "20"})
    assert (refused.status_code, refused.json()) == (403, {"detail": "ADMIN_ONLY"})

    # An admin who doesn't see costs neither sets it nor reads it.
    await sign_in(client, admin=True)
    unseen = await client.put("/api/v1/settings", json={"labour_rate": "20"})
    assert (unseen.status_code, unseen.json()) == (403, {"detail": "COSTS_HIDDEN"})
    assert (await client.get("/api/v1/settings")).json()["labour_rate"] is None

    await sign_in(client, ("costs",), admin=True)
    assert (await client.get("/api/v1/settings")).json()["labour_rate"] is None
    rated = await client.put("/api/v1/settings", json={"labour_rate": "22.50"})
    assert rated.json()["labour_rate"] == "22.500000"
    negative = await client.put("/api/v1/settings", json={"labour_rate": "-1"})
    assert negative.status_code == 422
    kept = await client.put("/api/v1/settings", json={"currency": "CAD"})
    assert kept.json()["labour_rate"] == "22.500000"

    await sign_in(client, admin=True)
    assert (await client.get("/api/v1/settings")).json()["labour_rate"] is None
    await client.put("/api/v1/settings", json={"labour_rate": None})
    await sign_in(client, ("costs",))
    assert (await client.get("/api/v1/settings")).json()["labour_rate"] == "22.500000"

    await sign_in(client, ("costs",), admin=True)
    cleared = await client.put("/api/v1/settings", json={"labour_rate": None})
    assert cleared.json()["labour_rate"] is None


#: A change to each kind of setting, as a method, path and body.
CHANGES = [
    ("PUT", "/api/v1/settings", {"currency": "EUR"}),
    ("POST", "/api/v1/settings/units", {"name": "Jar"}),
    ("POST", "/api/v1/settings/categories", {"kind": "material", "name": "Tea"}),
    ("POST", "/api/v1/settings/locations", {"name": "Back room"}),
    ("POST", "/api/v1/settings/reasons", {"name": "Spilled"}),
]


@pytest.mark.parametrize(("method", "path", "body"), CHANGES)
async def test_only_an_admin_changes_the_settings(client, sign_in, method, path, body):
    await sign_in(client)
    refused = await client.request(method, path, json=body)
    assert (refused.status_code, refused.json()) == (403, {"detail": "ADMIN_ONLY"})

    await sign_in(client, admin=True)
    assert (await client.request(method, path, json=body)).status_code in (200, 201)


async def test_an_organization_starts_with_its_main_location_and_reasons_made_by_hoard(
    client, sign_in, db: WriteSession
):
    await sign_in(client)

    locations = (await client.get("/api/v1/settings/locations")).json()
    assert [(location["name"], location["main"]) for location in locations] == [(None, True)]
    reasons = (await client.get("/api/v1/settings/reasons")).json()
    assert [(reason["key"], reason["name"]) for reason in reasons] == [
        (key, None) for key in DefaultReason
    ]

    made = (await db.exec(select(AdjustmentReason))).all()
    assert {reason.created_by for reason in made} == {None}

    await sign_in(client, admin=True)
    lost = reasons[3]
    renamed = await client.patch(f"/api/v1/settings/reasons/{lost['id']}", json={"name": "Gone"})
    assert renamed.json() == {"id": lost["id"], "key": "lost", "name": "Gone"}
    reasons = (await client.get("/api/v1/settings/reasons")).json()
    assert [reason["name"] for reason in reasons[:2]] == ["Gone", None]


async def test_names_stay_unique_and_what_records_name_stays(
    client, sign_in, db: WriteSession, material
):
    await sign_in(client, admin=True)
    jar = (await client.post("/api/v1/settings/units", json={"name": "Jar"})).json()
    await material("Oolong", tracking_unit_id=jar["id"])
    await db.commit()

    taken = await client.post("/api/v1/settings/units", json={"name": "Jar"})
    assert (taken.status_code, taken.json()) == (409, {"detail": "NAME_TAKEN"})
    in_use = await client.delete(f"/api/v1/settings/units/{jar['id']}")
    assert (in_use.status_code, in_use.json()) == (409, {"detail": "IN_USE"})

    main = (await client.get("/api/v1/settings/locations")).json()[0]
    kept = await client.delete(f"/api/v1/settings/locations/{main['id']}")
    assert (kept.status_code, kept.json()) == (409, {"detail": "MAIN_LOCATION"})
    renamed = await client.patch(
        f"/api/v1/settings/locations/{main['id']}", json={"name": "Shop", "contact_id": 4}
    )
    assert renamed.json() == {"id": main["id"], "name": "Shop", "main": True, "contact_id": 4}
    # A contact not sent is kept.
    again = await client.patch(f"/api/v1/settings/locations/{main['id']}", json={"name": "Shop"})
    assert again.json()["contact_id"] == 4

    category = await client.post(
        "/api/v1/settings/categories",
        json={"kind": "material", "name": "Packaging", "picking": "first_in"},
    )
    picked = await client.patch(
        f"/api/v1/settings/categories/{category.json()['id']}", json={"picking": "recorded"}
    )
    assert (category.json()["picking"], picked.json()["name"], picked.json()["picking"]) == (
        "first_in",
        "Packaging",
        "recorded",
    )


async def test_an_organization_has_the_standard_units_which_stay_as_they_are(
    client, sign_in, db: WriteSession
):
    await sign_in(client)

    listed = (await client.get("/api/v1/settings/units")).json()
    again = (await client.get("/api/v1/settings/units")).json()

    assert listed == again
    assert [unit["standard"] for unit in listed] == [unit.key for unit in units.STANDARD_UNITS]
    gram = next(unit for unit in listed if unit["standard"] == "gram")
    assert (gram["name"], gram["measure"]) == ("g", "mass")
    assert {"g", "grams", "gramme"} <= set(gram["aliases"])
    made = (await db.exec(select(Unit))).all()
    assert {unit.created_by for unit in made} == {None}

    await sign_in(client, admin=True)
    for name in ("kg", "Kilos", "fl. oz"):
        spelled = await client.post("/api/v1/settings/units", json={"name": name})
        assert (spelled.status_code, spelled.json()) == (409, {"detail": "NAME_TAKEN"})
    jar = (await client.post("/api/v1/settings/units", json={"name": "Jar"})).json()
    assert (jar["standard"], jar["measure"], jar["aliases"]) == (None, None, [])
    renamed = await client.patch(f"/api/v1/settings/units/{gram['id']}", json={"name": "Gram"})
    removed = await client.delete(f"/api/v1/settings/units/{gram['id']}")
    for refused in (renamed, removed):
        assert (refused.status_code, refused.json()) == (409, {"detail": "UNIT_STANDARD"})
    as_kg = await client.patch(f"/api/v1/settings/units/{jar['id']}", json={"name": "kg"})
    assert (as_kg.status_code, as_kg.json()) == (409, {"detail": "NAME_TAKEN"})

    kilogram = next(unit for unit in listed if unit["standard"] == "kilogram")
    conversions = [
        (
            await client.get(
                "/api/v1/settings/units/conversion",
                params={"from_unit_id": source["id"], "to_unit_id": gram["id"]},
            )
        ).json()
        for source in (kilogram, jar)
    ]
    assert conversions == [{"factor": "1000.000000"}, {"factor": None}]
