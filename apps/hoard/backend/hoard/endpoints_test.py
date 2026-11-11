"""The reads Hoard's widgets draw, called as Initiative calls them: at
``POST /v1/endpoints``, with a context token naming the community."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from hoard import plugin_manifest
from seneschal_core.db import WriteSession


async def _call(client, initiative_stand_in, community_ref: str, endpoint: str, **claims: Any):
    token = initiative_stand_in.context(endpoint, community_ref=community_ref, **claims)
    return await client.post(
        "/v1/endpoints",
        json={"endpoint": endpoint, "params": {}},
        headers={"Authorization": f"Bearer {token}"},
    )


async def test_the_reorder_endpoint_answers_its_returns_side_by_side(
    client, initiative_stand_in, community_ref, db: WriteSession, material
):
    await material("Flour", low_stock_limit=Decimal(5))
    await material("Salt")
    await db.commit()

    answer = await _call(client, initiative_stand_in, community_ref, plugin_manifest.REORDER)

    assert answer.status_code == 200
    assert answer.json() == {
        "endpoint": plugin_manifest.REORDER,
        "actor": "installation",
        "result": {
            "kind": ["material"],
            "name": ["Flour"],
            "unit": ["g"],
            "on_hand": ["0"],
            "low_stock_limit": ["5.000000"],
        },
    }


async def test_the_valuation_endpoint_answers_the_total_and_its_currency(
    client, initiative_stand_in, community_ref, db: WriteSession, material, buy
):
    flour = await material("Flour")
    await buy((flour, "3", "1.5"))
    await db.commit()

    answer = await _call(client, initiative_stand_in, community_ref, plugin_manifest.VALUATION)

    assert answer.status_code == 200
    assert answer.json()["result"] == {"total": "4.500000", "currency": "USD"}


async def test_an_unavailable_community_reads_nothing(client, initiative_stand_in, community_ref):
    answer = await _call(
        client,
        initiative_stand_in,
        community_ref,
        plugin_manifest.REORDER,
        community_status="suspended",
    )

    assert (answer.status_code, answer.json()) == (403, {"detail": "COMMUNITY_UNAVAILABLE"})
