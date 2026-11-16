"""Recipes hold materials, bases and products' variations, a bundle's hold
variations, nothing is made from itself, and each line keeps what was written, by amount or by ratio,
beside its quantity in its item's tracking unit."""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlmodel import select

from hoard import recipes
from hoard.recipes import Line, RecipeError, RecipeRefused
from hoard.records import ItemStatus, Product, Recipe, RecipeLine, Unit, Variation
from hoard.stock import add_product, add_variation
from hoard.units import UnitError
from seneschal_core.db import WriteSession


async def _product(db: WriteSession, name: str) -> tuple[Product, Variation]:
    return await add_product(db, Product(name=name))


async def _refused(db: WriteSession, recipe: Recipe, *lines: Line) -> RecipeError | UnitError:
    with pytest.raises(RecipeRefused) as refused:
        await recipes.save(db, recipe, lines)
    return refused.value.code


async def test_a_recipe_holds_materials_that_carry_stock_bases_and_variations(
    db: WriteSession, material, base
):
    tea = await material("Black tea")
    spice = await base("Chai spice")
    cup = await material("Cup", inventoriable=False)
    _, latte = await _product(db, "Chai latte")
    _, mug = await _product(db, "Mug")
    gift, gift_one = await _product(db, "Gift set")
    await recipes.save(db, Recipe(product_id=gift.id), [Line(mug.id, Decimal(1))])
    recipe = Recipe(variation_id=latte.id, batch_quantity=Decimal(4))

    await recipes.save(db, recipe, [Line(tea.id, Decimal(10)), Line(spice.id, Decimal(2))])
    await recipes.save(
        db,
        recipe,
        [Line(spice.id, Decimal(3)), Line(tea.id, Decimal(12)), Line(mug.id, Decimal(4))],
    )

    lines = (await recipes.lines_of(db, [recipe.id]))[recipe.id]
    assert [(line.item_id, line.quantity) for line in lines] == [
        (spice.id, Decimal(3)),
        (tea.id, Decimal(12)),
        (mug.id, Decimal(4)),
    ]
    # A bundle has no stock of its own to take.
    assert await _refused(db, recipe, Line(gift_one.id, Decimal(1))) is RecipeError.RECIPE_LINE_KIND
    assert await _refused(db, recipe, Line(cup.id, Decimal(1))) is RecipeError.RECIPE_LINE_KIND
    assert (
        await _refused(db, recipe, Line(tea.id, Decimal(1)), Line(tea.id, Decimal(2)))
        is RecipeError.RECIPE_LINE_REPEATED
    )
    again = Recipe(variation_id=latte.id)
    assert await _refused(db, again, Line(tea.id, Decimal(1))) is RecipeError.RECIPE_EXISTS


async def test_a_gift_box_holds_variations_counted_in_each_beside_what_is_weighed(
    db: WriteSession, material, unit
):
    """A gift box of loose chamomile, a tea tin and an infuser, by ratio: the
    tin made of chamomile, the infuser archived and bought as it is sold."""
    chamomile = await material("Chamomile")
    infuser_product, infuser = await _product(db, "Tea infuser")
    infuser_product.status = ItemStatus.ARCHIVED
    _, tin = await _product(db, "Chamomile tin")
    await recipes.save(db, Recipe(variation_id=tin.id), [Line(chamomile.id, Decimal(50))])
    _, gift = await _product(db, "Gift box of chamomile")
    dozen = await _unit(db, "dozen", "dozen")
    recipe = Recipe(
        variation_id=gift.id,
        by_ratio=True,
        total=Decimal(100),
        total_unit_id=unit.id,
        batch_quantity=Decimal(12),
    )

    await recipes.save(
        db,
        recipe,
        [
            Line(chamomile.id, Decimal(1)),
            Line(tin.id, Decimal("0.01")),
            Line(infuser.id, Decimal("0.01"), dozen.id),
        ],
    )
    worked = await recipes.work_out(
        db,
        recipe,
        [
            Line(line.item_id, line.written, line.unit_id)
            for line in (await recipes.lines_of(db, [recipe.id]))[recipe.id]
        ],
    )

    # The 100 g is the chamomile's alone; the tin and the infusers, counted
    # in each, scale along with it and weigh nothing in its shares.
    assert worked.figures.quantities == [Decimal(100), Decimal(1), Decimal(12)]
    assert worked.percents == [Decimal(100), None, None]
    grams = await _refused(db, recipe, Line(infuser.id, Decimal(1), unit.id))
    assert grams is UnitError.UNIT_NOT_CONVERTIBLE
    # A product made of itself, or of what is made of it, is refused.
    tin_recipe = (await db.exec(select(Recipe).where(Recipe.variation_id == tin.id))).one()
    refused = await _refused(
        db, tin_recipe, Line(chamomile.id, Decimal(1)), Line(gift.id, Decimal(1))
    )
    assert refused is RecipeError.RECIPE_CYCLE
    assert await _refused(db, recipe, Line(gift.id, Decimal(1))) is RecipeError.RECIPE_CYCLE
    # The infuser held by the gift box can't become a bundle.
    bundle = Recipe(product_id=infuser_product.id)
    assert await _refused(db, bundle, Line(tin.id, Decimal(1))) is RecipeError.RECIPE_BUNDLE


async def test_a_bundle_holds_only_variations_and_its_own_take_no_recipe(
    db: WriteSession, material
):
    tea = await material("Black tea")
    box, _ = await _product(db, "Tea box")
    _, tin = await _product(db, "Tin of oolong")
    bundle = Recipe(product_id=box.id)

    assert await _refused(db, bundle, Line(tea.id, Decimal(1))) is RecipeError.RECIPE_LINE_KIND
    # A bundle counts whole variations, so it is never written by ratio.
    by_ratio = Recipe(product_id=box.id, by_ratio=True, total=Decimal(2))
    assert await _refused(db, by_ratio, Line(tin.id, Decimal(1))) is RecipeError.RECIPE_BUNDLE_RATIO
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


async def _unit(db: WriteSession, name: str, standard: str | None = None) -> Unit:
    unit = Unit(organization_id=db.organization_id, name=name, standard=standard)
    db.add(unit)
    await db.flush()
    return unit


async def test_a_line_keeps_what_was_written_and_its_quantity_in_the_tracking_unit(
    db: WriteSession, material, base
):
    catnip = await material("Catnip")
    blend = await base("Alyx batch")
    kilograms = await _unit(db, "kg", "kilogram")
    millilitres = await _unit(db, "mL", "millilitre")
    recipe = Recipe(base_id=blend.id)

    await recipes.save(db, recipe, [Line(catnip.id, Decimal("0.5"), kilograms.id)])

    [line] = (await recipes.lines_of(db, [recipe.id]))[recipe.id]
    assert (line.written, line.unit_id, line.quantity) == (
        Decimal("0.5"),
        kilograms.id,
        Decimal("500.000000"),
    )
    refused = await _refused(db, recipe, Line(catnip.id, Decimal(1), millilitres.id))
    assert refused is UnitError.UNIT_NOT_CONVERTIBLE


async def test_a_ratio_splits_its_total_and_rescales_with_it(db: WriteSession, material, base):
    catnip, chamomile, rose_hip = [
        await material(name) for name in ("Catnip", "Chamomile", "Rose hip")
    ]
    lychee = await material(
        "Lychee flavor", tracking_unit_id=(await _unit(db, "mL", "millilitre")).id
    )
    blend = await base("Alyx batch")
    alyx = [
        Line(catnip.id, Decimal(2)),
        Line(chamomile.id, Decimal(1)),
        Line(rose_hip.id, Decimal(1)),
        Line(lychee.id, Decimal("0.1")),
    ]
    recipe = Recipe(
        base_id=blend.id,
        by_ratio=True,
        total=Decimal(931),
        total_unit_id=catnip.tracking_unit_id,
        batch_quantity=Decimal(908),
    )

    await recipes.save(db, recipe, alyx)
    quantities = [line.quantity for line in (await recipes.lines_of(db, [recipe.id]))[recipe.id]]
    recipe.total = Decimal(1862)
    await recipes.save(db, recipe, alyx)
    doubled = [line.quantity for line in (await recipes.lines_of(db, [recipe.id]))[recipe.id]]

    assert quantities == [
        Decimal("465.500000"),
        Decimal("232.750000"),
        Decimal("232.750000"),
        Decimal("23.275000"),
    ]
    assert doubled == [quantity * 2 for quantity in quantities]
    assert (await recipes.lines_of(db, [recipe.id]))[recipe.id][3].written == Decimal("0.1")
    no_total = await _refused(db, recipe, Line(lychee.id, Decimal(1)))
    assert no_total is UnitError.RECIPE_RATIO_TOTAL
