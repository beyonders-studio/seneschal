"""Hoard's reports. Waste, planned against actual and the allergen matrix are
called as Initiative calls them: at ``POST /v1/endpoints``."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlmodel import select

from hoard import plugin_manifest, reports
from hoard.records import (
    Adjustment,
    AdjustmentKind,
    AdjustmentReason,
    Allergen,
    CategoryKind,
    DefaultReason,
    ItemKind,
    Location,
    OrganizationSettings,
    Product,
    ProductionRun,
    ProductStatus,
    Recipe,
    RecipeLine,
    RunStatus,
    Variation,
)
from hoard.stock import add_default_reasons, add_product, add_variation, adjust
from seneschal_core.db import WriteSession


async def _start(
    db: WriteSession, item_id: int, location: Location, change: str, unit_cost: str = "0"
) -> None:
    await adjust(
        db,
        Adjustment(
            item_id=item_id,
            location_id=location.id,
            kind=AdjustmentKind.STARTING,
            dated_on=date(2026, 11, 1),
            change=Decimal(change),
            unit_cost=Decimal(unit_cost),
        ),
    )


async def test_an_item_is_reordered_at_or_below_its_limit(db: WriteSession, main, material):
    at = await material("Flour", low_stock_limit=Decimal(10))
    above = await material("Sugar", low_stock_limit=Decimal(10))
    empty = await material("Yeast", low_stock_limit=Decimal(1))
    await material("Salt")
    await material("Whisk", inventoriable=False, low_stock_limit=Decimal(1))
    await _start(db, at.id, main, "10")
    await _start(db, above.id, main, "10.5")

    shortfalls = await reports.reorder(db)

    assert [(s.kind, s.id, s.unit, s.on_hand, s.low_stock_limit) for s in shortfalls] == [
        (CategoryKind.MATERIAL, at.id, "g", Decimal(10), Decimal(10)),
        (CategoryKind.MATERIAL, empty.id, "g", Decimal(0), Decimal(1)),
    ]


async def test_a_product_is_reordered_on_its_variations_together(db: WriteSession, main):
    product, small = await add_product(db, Product(name="Loaf", low_stock_limit=Decimal(10)))
    large = await add_variation(db, Variation(product_id=product.id, attributes={"size": "L"}))
    await add_product(
        db, Product(name="Bun", low_stock_limit=Decimal(5), status=ProductStatus.ARCHIVED)
    )
    await _start(db, small.id, main, "6")
    await _start(db, large.id, main, "6")
    assert await reports.reorder(db) == []

    await _start(db, large.id, main, "-3")

    assert await reports.reorder(db) == [
        reports.Shortfall(CategoryKind.PRODUCT, product.id, "Loaf", None, Decimal(9), Decimal(10))
    ]


async def test_stock_is_valued_at_its_average_cost(db: WriteSession, main, material, buy):
    flour = await material("flour")
    await material("Rye")
    product, loaf = await add_product(db, Product(name="Bread"))
    await buy((flour, "10", "2.50"))
    await _start(db, loaf.id, main, "3", "1.333333")
    db.add(OrganizationSettings(organization_id=db.organization_id, currency="EUR"))
    await db.flush()

    valuation = await reports.valuation(db)

    assert valuation.currency == "EUR"
    assert [(i.item_id, i.kind, i.name, i.on_hand, i.value) for i in valuation.items] == [
        (loaf.id, ItemKind.VARIATION, "Bread", Decimal(3), Decimal("3.999999")),
        (flour.id, ItemKind.MATERIAL, "flour", Decimal(10), Decimal("25.000000")),
    ]
    assert valuation.total == Decimal("28.999999")


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
