"""What to reorder, and what stock is worth."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from hoard import reports
from hoard.records import (
    Adjustment,
    AdjustmentKind,
    CategoryKind,
    ItemKind,
    Location,
    OrganizationSettings,
    Product,
    ProductStatus,
    Variation,
)
from hoard.stock import add_product, add_variation, adjust
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
