"""The Reports page's reports: what to reorder for every member, and what stock
is worth for those who see costs."""

from __future__ import annotations

from decimal import Decimal

from hoard import plugin_manifest
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
