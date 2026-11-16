"""The organization's settings."""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlmodel import select

from hoard import units
from hoard.records import (
    AdjustmentReason,
    DefaultReason,
    ItemType,
    OrderStatus,
    OrganizationSettings,
    Unit,
)
from seneschal_core.db import WriteSession


async def test_settings_read_as_their_defaults_until_changed(client, sign_in, db: WriteSession):
    await sign_in(client)
    assert (await client.get("/api/v1/settings")).json() == {
        "currency": "USD",
        "auto_manufacture": "off",
        "regimes": [],
        "labour_rate": None,
        "overhead": None,
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
        "overhead": None,
    }
    # Regimes are kept once each, in their own order.
    ticked = await client.put(
        "/api/v1/settings", json={"regimes": ["eu_cosmetics", "us_cosmetics", "eu_cosmetics"]}
    )
    assert ticked.json()["regimes"] == ["us_cosmetics", "eu_cosmetics"]
    unknown = await client.put("/api/v1/settings", json={"regimes": ["moon_food"]})
    assert unknown.status_code == 422


@pytest.mark.parametrize("setting", ["labour_rate", "overhead"])
async def test_the_labour_rate_and_overhead_are_costs_an_admin_sets(client, sign_in, setting):
    await sign_in(client, ("costs",))
    refused = await client.put("/api/v1/settings", json={setting: "20"})
    assert (refused.status_code, refused.json()) == (403, {"detail": "ADMIN_ONLY"})

    # An admin who doesn't see costs neither sets it nor reads it.
    await sign_in(client, admin=True)
    unseen = await client.put("/api/v1/settings", json={setting: "20"})
    assert (unseen.status_code, unseen.json()) == (403, {"detail": "COSTS_HIDDEN"})
    assert (await client.get("/api/v1/settings")).json()[setting] is None

    await sign_in(client, ("costs",), admin=True)
    assert (await client.get("/api/v1/settings")).json()[setting] is None
    rated = await client.put("/api/v1/settings", json={setting: "22.50"})
    assert rated.json()[setting] == "22.500000"
    negative = await client.put("/api/v1/settings", json={setting: "-1"})
    assert negative.status_code == 422
    kept = await client.put("/api/v1/settings", json={"currency": "CAD"})
    assert kept.json()[setting] == "22.500000"

    await sign_in(client, admin=True)
    assert (await client.get("/api/v1/settings")).json()[setting] is None
    await client.put("/api/v1/settings", json={setting: None})
    await sign_in(client, ("costs",))
    assert (await client.get("/api/v1/settings")).json()[setting] == "22.500000"

    await sign_in(client, ("costs",), admin=True)
    cleared = await client.put("/api/v1/settings", json={setting: None})
    assert cleared.json()[setting] is None


#: A change to each kind of setting, as a method, path and body.
CHANGES = [
    ("PUT", "/api/v1/settings", {"currency": "EUR"}),
    ("POST", "/api/v1/settings/units", {"name": "Jar"}),
    ("POST", "/api/v1/settings/categories", {"kind": "material", "name": "Tea"}),
    ("POST", "/api/v1/settings/types", {"kind": "product", "name": "Bought snacks"}),
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


TYPES = "/api/v1/settings/types"


async def test_each_kind_starts_with_a_default_type_made_by_hoard(
    client, sign_in, db: WriteSession
):
    await sign_in(client)
    types = (await client.get(TYPES)).json()
    assert [(each["kind"], each["name"], each["shape"], each["is_default"]) for each in types] == [
        ("material", None, "ingredient", True),
        ("product", None, "made", True),
        ("base", None, "base", True),
    ]
    assert {each.created_by for each in (await db.exec(select(ItemType))).all()} == {None}
    # Read again, a kind keeps its one default.
    assert len((await client.get(TYPES, params={"kind": "base"})).json()) == 1

    await sign_in(client, admin=True)
    default = types[0]["id"]
    kept = await client.delete(f"{TYPES}/{default}")
    assert (kept.status_code, kept.json()) == (409, {"detail": "TYPE_DEFAULT"})
    renamed = await client.patch(f"{TYPES}/{default}", json={"name": "Ingredient"})
    assert (renamed.json()["name"], renamed.json()["is_default"]) == ("Ingredient", True)


async def test_a_type_takes_one_of_its_kinds_shapes_and_stays_while_records_have_it(
    client, sign_in, db: WriteSession, material
):
    await sign_in(client, admin=True)
    made = (await client.post(TYPES, json={"kind": "material", "name": "Tins"})).json()
    # Without a shape it takes its kind's first.
    assert (made["shape"], made["is_default"]) == ("ingredient", False)
    shaped = await client.patch(f"{TYPES}/{made['id']}", json={"shape": "packaging"})
    assert shaped.json()["shape"] == "packaging"
    refused = await client.post(TYPES, json={"kind": "material", "name": "Box", "shape": "bought"})
    assert (refused.status_code, refused.json()) == (422, {"detail": "SHAPE_UNKNOWN"})
    refused = await client.patch(f"{TYPES}/{made['id']}", json={"shape": "base"})
    assert (refused.status_code, refused.json()) == (422, {"detail": "SHAPE_UNKNOWN"})
    taken = await client.post(TYPES, json={"kind": "material", "name": "Tins"})
    assert (taken.status_code, taken.json()) == (409, {"detail": "NAME_TAKEN"})
    # Another kind may have a type of the same name.
    assert (await client.post(TYPES, json={"kind": "product", "name": "Tins"})).status_code == 201

    tin = await material("Round tin", type_id=made["id"])
    await db.commit()
    in_use = await client.delete(f"{TYPES}/{made['id']}")
    assert (in_use.status_code, in_use.json()) == (409, {"detail": "IN_USE"})
    # Moved to another type, it goes.
    tin.type_id = next(
        each["id"]
        for each in (await client.get(TYPES, params={"kind": "material"})).json()
        if each["is_default"]
    )
    db.add(tin)
    await db.commit()
    assert (await client.delete(f"{TYPES}/{made['id']}")).status_code == 204


async def test_a_category_names_a_default_type_of_its_kind_which_goes_with_the_type(
    client, sign_in
):
    await sign_in(client, admin=True)
    bought = (
        await client.post(TYPES, json={"kind": "product", "name": "Bought", "shape": "bought"})
    ).json()
    tins = (await client.post(TYPES, json={"kind": "material", "name": "Tins"})).json()
    refused = await client.post(
        "/api/v1/settings/categories",
        json={"kind": "product", "name": "Drinkware", "default_type_id": tins["id"]},
    )
    assert (refused.status_code, refused.json()) == (422, {"detail": "TYPE_UNKNOWN"})
    drinkware = (
        await client.post(
            "/api/v1/settings/categories",
            json={"kind": "product", "name": "Drinkware", "default_type_id": bought["id"]},
        )
    ).json()
    assert drinkware["default_type_id"] == bought["id"]
    path = f"/api/v1/settings/categories/{drinkware['id']}"
    # A change that leaves it out keeps it; null clears it.
    assert (await client.patch(path, json={"picking": "first_in"})).json()["default_type_id"] == (
        bought["id"]
    )
    assert (await client.patch(path, json={"default_type_id": None})).json()[
        "default_type_id"
    ] is None
    await client.patch(path, json={"default_type_id": bought["id"]})

    # A removed type is cleared from the categories naming it.
    assert (await client.delete(f"{TYPES}/{bought['id']}")).status_code == 204
    categories = (await client.get("/api/v1/settings/categories")).json()
    assert [each["default_type_id"] for each in categories] == [None]


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
    # Without a unit to convert to, a variation's: so many of it.
    dozen = next(unit for unit in listed if unit["standard"] == "dozen")
    of_itself = await client.get(
        "/api/v1/settings/units/conversion", params={"from_unit_id": dozen["id"]}
    )
    assert of_itself.json() == {"factor": "12.000000"}


async def test_price_points_are_costs_an_admin_sets(
    client, sign_in, db: WriteSession, sell, product
):
    point = {"name": "Wholesale", "markup": "60", "listing_fee": "0.5", "commission": "10"}
    await sign_in(client, ("costs",))
    refused = await client.post("/api/v1/settings/price-points", json=point)
    assert (refused.status_code, refused.json()) == (403, {"detail": "ADMIN_ONLY"})
    await sign_in(client, admin=True)
    unseen = await client.post("/api/v1/settings/price-points", json=point)
    assert (unseen.status_code, unseen.json()) == (403, {"detail": "COSTS_HIDDEN"})
    # None until an admin adds one.
    assert (await client.get("/api/v1/settings/price-points")).json() == []

    await sign_in(client, ("costs",), admin=True)
    made = (await client.post("/api/v1/settings/price-points", json=point)).json()
    assert made == {
        "id": made["id"],
        "name": "Wholesale",
        "markup": "60.000000",
        "listing_fee": "0.500000",
        "commission": "10.000000",
    }
    taken = await client.post("/api/v1/settings/price-points", json=point)
    assert (taken.status_code, taken.json()) == (409, {"detail": "NAME_TAKEN"})
    whole = await client.post(
        "/api/v1/settings/price-points", json={**point, "name": "All", "commission": "100"}
    )
    assert whole.status_code == 422
    changed = await client.put(
        f"/api/v1/settings/price-points/{made['id']}", json={**point, "markup": "50"}
    )
    assert changed.json()["markup"] == "50.000000"

    # Every member reads its name; only one who sees costs its numbers.
    await sign_in(client)
    assert (await client.get("/api/v1/settings/price-points")).json() == [
        {
            "id": made["id"],
            "name": "Wholesale",
            "markup": None,
            "listing_fee": None,
            "commission": None,
        }
    ]

    # Removing it keeps the orders priced at it, and its contacts buy at the
    # default price again.
    chai = await product("Chai", default_price=Decimal(10))
    order = await sell(status=OrderStatus.OPEN)
    await db.commit()
    await sign_in(client, ("costs",), admin=True)
    await client.put("/api/v1/contacts/1/price-point", json={"price_point_id": made["id"]})
    priced = await client.put(
        f"/api/v1/products/{chai.product_id}/variations/{chai.id}/prices/{made['id']}",
        json={"price": "7"},
    )
    assert priced.status_code == 200, priced.text
    added = await client.post(
        f"/api/v1/orders/{order.id}/lines", json={"item_id": chai.id, "quantity": "1"}
    )
    assert added.json()["lines"][0]["price"] == "7.000000"
    removed = await client.delete(f"/api/v1/settings/price-points/{made['id']}")
    assert removed.status_code == 204
    assert (await client.get("/api/v1/contacts/1/price-point")).json() == {"price_point_id": None}
    again = await client.post(
        f"/api/v1/orders/{order.id}/lines", json={"item_id": chai.id, "quantity": "1"}
    )
    assert [line["price"] for line in again.json()["lines"]] == ["7.000000", "10.000000"]
