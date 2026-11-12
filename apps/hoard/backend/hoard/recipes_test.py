"""Recipes hold materials and bases, a bundle's hold variations, nothing is
made from itself, and a recipe scales and shows its shares by weight."""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlmodel import select

from hoard import recipes
from hoard.recipes import Line, RecipeError, RecipeRefused
from hoard.records import Product, Recipe, RecipeLine, Variation
from hoard.stock import add_product, add_variation
from seneschal_core.db import WriteSession


async def _product(db: WriteSession, name: str) -> tuple[Product, Variation]:
    return await add_product(db, Product(name=name))


async def _refused(db: WriteSession, recipe: Recipe, *lines: Line) -> RecipeError:
    with pytest.raises(RecipeRefused) as refused:
        await recipes.save(db, recipe, lines)
    return refused.value.code


async def test_a_recipe_holds_materials_that_carry_stock_and_bases(
    db: WriteSession, material, base
):
    tea = await material("Black tea")
    spice = await base("Chai spice")
    cup = await material("Cup", inventoriable=False)
    _, latte = await _product(db, "Chai latte")
    _, other = await _product(db, "Iced tea")
    recipe = Recipe(variation_id=latte.id, batch_quantity=Decimal(4))

    await recipes.save(db, recipe, [Line(tea.id, Decimal(10)), Line(spice.id, Decimal(2))])
    await recipes.save(db, recipe, [Line(spice.id, Decimal(3)), Line(tea.id, Decimal(12))])

    lines = (await recipes.lines_of(db, [recipe.id]))[recipe.id]
    assert [(line.item_id, line.quantity) for line in lines] == [
        (spice.id, Decimal(3)),
        (tea.id, Decimal(12)),
    ]
    assert await _refused(db, recipe, Line(other.id, Decimal(1))) is RecipeError.RECIPE_LINE_KIND
    assert await _refused(db, recipe, Line(cup.id, Decimal(1))) is RecipeError.RECIPE_LINE_KIND
    assert (
        await _refused(db, recipe, Line(tea.id, Decimal(1)), Line(tea.id, Decimal(2)))
        is RecipeError.RECIPE_LINE_REPEATED
    )
    again = Recipe(variation_id=latte.id)
    assert await _refused(db, again, Line(tea.id, Decimal(1))) is RecipeError.RECIPE_EXISTS


async def test_a_bundle_holds_only_variations_and_its_own_take_no_recipe(
    db: WriteSession, material
):
    tea = await material("Black tea")
    box, _ = await _product(db, "Tea box")
    _, tin = await _product(db, "Tin of oolong")
    bundle = Recipe(product_id=box.id)

    assert await _refused(db, bundle, Line(tea.id, Decimal(1))) is RecipeError.RECIPE_LINE_KIND
    await recipes.save(db, bundle, [Line(tin.id, Decimal(2))])

    assert (await db.get_one(Product, box.id)).bundle
    large = await add_variation(db, Variation(product_id=box.id, attributes={"size": "large"}))
    own = Recipe(variation_id=large.id)
    assert await _refused(db, own, Line(tea.id, Decimal(1))) is RecipeError.RECIPE_BUNDLE
    await recipes.remove(db, bundle)
    assert not (await db.get_one(Product, box.id)).bundle
    assert (await db.exec(select(RecipeLine))).all() == []
    await recipes.save(db, own, [Line(tea.id, Decimal(1))])
    again = Recipe(product_id=box.id)
    assert await _refused(db, again, Line(tin.id, Decimal(1))) is RecipeError.RECIPE_BUNDLE


async def test_nothing_is_made_from_itself_at_any_depth(db: WriteSession, material, base):
    tea = await material("Black tea")
    syrup = await base("Syrup")
    spice = await base("Spice")
    blend = await base("Blend")
    await recipes.save(db, Recipe(base_id=spice.id), [Line(tea.id, Decimal(1))])
    blend_recipe = Recipe(base_id=blend.id)
    await recipes.save(db, blend_recipe, [Line(spice.id, Decimal(1))])
    syrup_recipe = Recipe(base_id=syrup.id)
    await recipes.save(db, syrup_recipe, [Line(blend.id, Decimal(1))])

    spice_recipe = (await db.exec(select(Recipe).where(Recipe.base_id == spice.id))).one()
    deep = await _refused(db, spice_recipe, Line(syrup.id, Decimal(1)))
    itself = await _refused(db, syrup_recipe, Line(syrup.id, Decimal(1)))

    assert (deep, itself) == (RecipeError.RECIPE_CYCLE, RecipeError.RECIPE_CYCLE)
    box, box_one = await _product(db, "Box")
    crate, crate_one = await _product(db, "Crate")
    await recipes.save(db, Recipe(product_id=crate.id), [Line(box_one.id, Decimal(2))])
    assert (
        await _refused(db, Recipe(product_id=box.id), Line(crate_one.id, Decimal(1)))
        is RecipeError.RECIPE_CYCLE
    )


def test_a_recipe_scales_to_a_quantity():
    batches = recipes.scale(Decimal(4), Decimal(10))

    assert batches == Decimal("2.5")
    assert recipes.scaled(Decimal("1.5"), batches) == Decimal("3.750000")
    assert recipes.scale(Decimal(4), None) == 1


def test_shares_are_by_weight_among_the_lines_in_the_unit_most_share():
    grams, each = 1, 2

    shares = recipes.percents(
        [
            (grams, Decimal(30)),
            (each, Decimal(1)),
            (grams, Decimal(60)),
            (grams, Decimal(10)),
            (None, Decimal(1)),
        ]
    )

    assert shares == [Decimal("30.00"), None, Decimal("60.00"), Decimal("10.00"), None]
    assert recipes.percents([(each, Decimal(1)), (grams, Decimal(3))]) == [Decimal("100.00"), None]
    assert recipes.percents([(None, Decimal(1))]) == [None]
