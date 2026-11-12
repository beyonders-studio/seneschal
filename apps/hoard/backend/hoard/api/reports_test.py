"""The Reports page's reports: what to reorder and the mass balance for every
member, and what stock is worth and what the goods cost for those who see
costs."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from hoard import plugin_manifest
from hoard.records import PropertyDefinition, PropertyType, RecordKind
from seneschal_core.db import WriteSession


async def test_every_member_reads_what_to_reorder(client, sign_in, db: WriteSession, material):
    flour = await material("Flour", low_stock_limit=Decimal(5))
    await db.commit()
    await sign_in(client)

    answer = await client.get("/api/v1/reports/reorder")

    assert answer.status_code == 200
    assert answer.json() == [
        {
            "kind": "material",
            "id": flour.id,
            "name": "Flour",
            "unit": "g",
            "on_hand": "0",
            "low_stock_limit": "5.000000",
        }
    ]


async def test_only_a_member_who_sees_costs_reads_what_stock_is_worth(
    client, sign_in, db: WriteSession, material, buy
):
    flour = await material("Flour")
    await buy((flour, "4", "2.5"))
    await db.commit()

    await sign_in(client)
    refused = await client.get("/api/v1/reports/valuation")
    await sign_in(client, (plugin_manifest.SEE_COSTS,))
    answer = await client.get("/api/v1/reports/valuation")

    assert (refused.status_code, refused.json()) == (403, {"detail": "COSTS_ONLY"})
    assert answer.status_code == 200
    assert answer.json() == {
        "currency": "USD",
        "items": [
            {
                "item_id": flour.id,
                "kind": "material",
                "name": "Flour",
                "attributes": {},
                "on_hand": "4.000000",
                "average_cost": "2.500000",
                "value": "10.000000",
            }
        ],
        "total": "10.000000",
    }


async def test_only_a_member_who_sees_costs_reads_what_the_goods_cost(
    client, sign_in, db: WriteSession, material, buy
):
    flour = await material("Flour")
    await buy((flour, "4", "2.5"), dated_on=date(2026, 3, 1))
    await db.commit()
    period = {"start": "2026-01-01", "end": "2026-12-31"}

    await sign_in(client)
    refused = await client.get("/api/v1/reports/cost-of-goods", params=period)
    await sign_in(client, (plugin_manifest.SEE_COSTS,))
    answer = await client.get("/api/v1/reports/cost-of-goods", params=period)
    backwards = await client.get(
        "/api/v1/reports/cost-of-goods", params={"start": "2026-12-31", "end": "2026-01-01"}
    )

    assert (refused.status_code, refused.json()) == (403, {"detail": "COSTS_ONLY"})
    assert (backwards.status_code, backwards.json()) == (422, {"detail": "PERIOD_INVALID"})
    assert answer.status_code == 200
    assert answer.json() == {
        "currency": "USD",
        "opening": "0.000000",
        "starting": "0.000000",
        "purchases": "10.000000",
        "personal_use": "0.000000",
        "closing": "10.000000",
        "cost_of_goods": "0.000000",
        "shipped": "0.000000",
        "uncosted_runs": 0,
    }


async def test_every_member_reads_the_mass_balance_split_only_by_a_lot_select(
    client, sign_in, db: WriteSession, material, buy
):
    flour = await material("Flour")
    await buy((flour, "4", "2.5"), dated_on=date(2026, 3, 1))
    origin = PropertyDefinition(
        organization_id=db.organization_id,
        kind=RecordKind.LOT,
        name="Origin",
        type=PropertyType.TEXT,
    )
    db.add(origin)
    await db.commit()
    period = {"start": "2026-01-01", "end": "2026-12-31"}
    await sign_in(client)

    answer = await client.get("/api/v1/reports/mass-balance", params=period)
    refused = await client.get(
        "/api/v1/reports/mass-balance", params={**period, "split_by": origin.id}
    )

    assert (refused.status_code, refused.json()) == (422, {"detail": "SPLIT_INVALID"})
    assert answer.status_code == 200
    assert answer.json() == [
        {
            "item_id": flour.id,
            "kind": "material",
            "name": "Flour",
            "attributes": {},
            "unit": "g",
            "option": None,
            "opening": "0.000000",
            "received": "4.000000",
            "made": "0.000000",
            "used": "0.000000",
            "sold": "0.000000",
            "lost": "0.000000",
            "closing": "4.000000",
        }
    ]
