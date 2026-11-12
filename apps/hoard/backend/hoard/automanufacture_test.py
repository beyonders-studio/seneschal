"""Auto-manufacture: which say holds for a variation, and what a store order
that claims its stock makes."""

from __future__ import annotations

from decimal import Decimal

from sqlmodel import col, select

from hoard import automanufacture
from hoard.records import (
    AutoManufacture,
    Location,
    Order,
    OrderLine,
    OrganizationSettings,
    OutboxEvent,
    Product,
    ProductionRun,
    Recipe,
    RecipeLine,
    RunStatus,
    Variation,
)
from hoard.stock import add_product, add_variation, on_hand
from seneschal_core.db import WriteSession


async def _made_from(db: WriteSession, variation: Variation, material, quantity: str) -> None:
    """A recipe making ``variation`` from ``quantity`` of ``material`` a batch."""
    recipe = Recipe(organization_id=db.organization_id, variation_id=variation.id)
    db.add(recipe)
    await db.flush()
    db.add(
        RecipeLine(
            organization_id=db.organization_id,
            recipe_id=recipe.id,
            item_id=material.id,
            quantity=Decimal(quantity),
        )
    )
    await db.flush()


async def _lines(db: WriteSession, order: Order) -> list[OrderLine]:
    found = await db.exec(
        select(OrderLine)
        .where(OrderLine.order_id == order.id)
        .order_by(col(OrderLine.id))
        .execution_options(populate_existing=True)
    )
    return list(found.all())


async def _runs(db: WriteSession) -> list[tuple[int, Decimal | None, RunStatus]]:
    found = await db.exec(select(ProductionRun).order_by(col(ProductionRun.id)))
    return [(run.item_id, run.actual_quantity, run.status) for run in found.all()]


async def test_a_variation_takes_its_own_say_then_its_products_then_the_organizations(
    db: WriteSession,
):
    product, plain = await add_product(db, Product(name="Tea"))
    large = await add_variation(
        db,
        Variation(
            product_id=product.id,
            attributes={"size": "large"},
            auto_manufacture=AutoManufacture.ALWAYS,
        ),
    )
    assert await automanufacture.rule_for(db, plain) is AutoManufacture.OFF

    db.add(
        OrganizationSettings(
            organization_id=db.organization_id, auto_manufacture=AutoManufacture.OUT_OF_STOCK
        )
    )
    await db.flush()
    assert await automanufacture.rule_for(db, plain) is AutoManufacture.OUT_OF_STOCK

    product.auto_manufacture = AutoManufacture.OFF
    await db.flush()
    assert await automanufacture.rule_for(db, plain) is AutoManufacture.OFF
    assert await automanufacture.rule_for(db, large) is AutoManufacture.ALWAYS


async def test_a_claimed_order_makes_what_it_sells_as_each_variation_says(
    db: WriteSession, main: Location, material, buy, lot, sell
):
    leaf = await material("Leaf")
    await buy((leaf, "100", "1"))
    _, always = await add_product(db, Product(name="Chai", auto_manufacture=AutoManufacture.ALWAYS))
    _, short = await add_product(
        db, Product(name="Sencha", auto_manufacture=AutoManufacture.OUT_OF_STOCK)
    )
    _, never = await add_product(db, Product(name="Oolong"))
    _, no_recipe = await add_product(
        db, Product(name="Mug", auto_manufacture=AutoManufacture.ALWAYS)
    )
    for variation in (always, short, never):
        await _made_from(db, variation, leaf, "2")
    await lot(short.id, "S-1", "3")
    # Another open order commits one of the three on hand.
    await sell((short.id, "1", "4"))

    order = await sell(
        (always.id, "2", "4"),
        (always.id, "1", "4"),
        (short.id, "5", "4"),
        (never.id, "1", "4"),
        (no_recipe.id, "1", "4"),
    )
    await automanufacture.on_claimed(db, order)

    # All three of chai, and what sencha lacks: five less the two left.
    assert await _runs(db) == [
        (always.id, 3, RunStatus.COMPLETED),
        (short.id, 3, RunStatus.COMPLETED),
    ]
    assert (await on_hand(db, always.id), await on_hand(db, short.id)) == (3, 6)
    assert await on_hand(db, leaf.id) == 100 - 2 * 6
    made = [line.production_run_id is not None for line in await _lines(db, order)]
    assert made == [True, True, True, False, False]
    recorded = await db.exec(select(OutboxEvent).where(col(OutboxEvent.name).like("run-%")))
    assert recorded.all() == []

    # Claimed again, nothing more is made.
    await automanufacture.on_claimed(db, order)
    assert len(await _runs(db)) == 2
