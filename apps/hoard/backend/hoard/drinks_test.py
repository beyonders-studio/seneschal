"""The Drinks reads, called as Initiative calls them: at ``POST /v1/endpoints``."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlmodel import select

from hoard import plugin_manifest
from hoard.records import (
    Adjustment,
    AdjustmentKind,
    AdjustmentReason,
    Allergen,
    DefaultReason,
    Location,
    Product,
    ProductionRun,
    ProductStatus,
    Recipe,
    RecipeLine,
    RunStatus,
)
from hoard.stock import add_default_reasons, add_product, adjust
from seneschal_core.db import WriteSession


async def _read(client, initiative_stand_in, community_ref, endpoint: str, **params: Any):
    token = initiative_stand_in.context(endpoint, community_ref=community_ref)
    answer = await client.post(
        "/v1/endpoints",
        json={"endpoint": endpoint, "params": params},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert answer.status_code == 200, answer.text
    return answer.json()["result"]


async def test_waste_is_what_was_taken_out_by_reason_but_corrections(
    client, initiative_stand_in, community_ref, db: WriteSession, main: Location, material, buy
):
    milk = await material("Oat milk")
    cups = await material("Cups")
    await buy((milk, "20", "1"), (cups, "100", "0.1"))
    await add_default_reasons(db)
    spill = AdjustmentReason(organization_id=db.organization_id, name="Spill")
    db.add(spill)
    await db.flush()
    reasons = {reason.key: reason.id for reason in (await db.exec(select(AdjustmentReason)))}
    today = date.today()
    for item, reason_id, change, dated_on in (
        (milk.id, spill.id, "-1", today),
        (milk.id, spill.id, "-2", today),
        (cups.id, spill.id, "-5", today),
        (milk.id, reasons[DefaultReason.EXPIRED.value], "-4", today),
        (milk.id, reasons[DefaultReason.CORRECTION.value], "-3", today),
        (milk.id, spill.id, "-6", today - timedelta(days=60)),
    ):
        await adjust(
            db,
            Adjustment(
                item_id=item,
                location_id=main.id,
                kind=AdjustmentKind.MANUAL,
                dated_on=dated_on,
                change=Decimal(change),
                reason_id=reason_id,
            ),
        )
    await db.commit()

    wasted = await _read(client, initiative_stand_in, community_ref, plugin_manifest.WASTE)

    assert wasted == {
        "reason": ["expired", "Spill", "Spill"],
        "item": ["Oat milk", "Cups", "Oat milk"],
        "unit": ["g", "g", "g"],
        "quantity": ["4.000000", "5.000000", "3.000000"],
    }
    longer = await _read(client, initiative_stand_in, community_ref, plugin_manifest.WASTE, days=90)
    assert (longer["item"], longer["quantity"]) == (
        ["Oat milk", "Oat milk", "Cups"],
        ["4.000000", "9.000000", "5.000000"],
    )


async def test_planned_against_actual_is_each_completed_run(
    client, initiative_stand_in, community_ref, db: WriteSession, main: Location, base
):
    cold_brew = await base("Cold brew")
    recipe = Recipe(organization_id=db.organization_id, base_id=cold_brew.id, batch_quantity=10)
    db.add(recipe)
    await db.flush()
    today = date.today()
    for code, status, completed_on in (
        ("R-1", RunStatus.COMPLETED, today - timedelta(days=1)),
        ("R-2", RunStatus.COMPLETED, today),
        ("R-3", RunStatus.IN_PROGRESS, None),
    ):
        db.add(
            ProductionRun(
                organization_id=db.organization_id,
                code=code,
                item_id=cold_brew.id,
                recipe_id=recipe.id,
                location_id=main.id,
                status=status,
                planned_batches=Decimal(2),
                completed_on=completed_on,
                actual_quantity=Decimal("18.5") if completed_on else None,
            )
        )
    await db.commit()

    runs = await _read(client, initiative_stand_in, community_ref, plugin_manifest.YIELD)

    assert runs == {
        "code": ["R-2", "R-1"],
        "item": ["Cold brew", "Cold brew"],
        "completed_on": [today.isoformat(), (today - timedelta(days=1)).isoformat()],
        "unit": ["g", "g"],
        "planned": ["20.000000", "20.000000"],
        "actual": ["18.500000", "18.500000"],
    }


async def test_the_allergen_matrix_is_each_active_products_allergens(
    client, initiative_stand_in, community_ref, db: WriteSession, material
):
    milk = await material("Milk", allergens=[Allergen.MILK])
    for name, status, lines in (
        ("Latte", ProductStatus.ACTIVE, [milk.id]),
        ("Americano", ProductStatus.ACTIVE, []),
        ("Eggnog", ProductStatus.ARCHIVED, [milk.id]),
    ):
        _, variation = await add_product(db, Product(name=name, status=status))
        recipe = Recipe(organization_id=db.organization_id, variation_id=variation.id)
        db.add(recipe)
        await db.flush()
        for item_id in lines:
            db.add(
                RecipeLine(
                    organization_id=db.organization_id,
                    recipe_id=recipe.id,
                    item_id=item_id,
                    quantity=Decimal(200),
                )
            )
    await db.commit()

    matrix = await _read(
        client, initiative_stand_in, community_ref, plugin_manifest.ALLERGEN_MATRIX
    )

    assert matrix["product"] == ["Americano", "Latte"]
    assert matrix["milk"] == [False, True]
    assert set(matrix) == {"product", *Allergen}
    assert not any(matrix[Allergen.EGGS.value])
