"""Declarations roll up through recipes to the materials, and through a run
to the lots it consumed."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from hoard.declarations import (
    EU_ALLERGENS,
    FRAGRANCE_ALLERGENS,
    LEAVE_ON,
    RINSE_OFF,
    US_ALLERGENS,
    Declaration,
    Declarations,
)
from hoard.records import (
    Allergen,
    Location,
    Lot,
    Product,
    ProductionRun,
    ProductionRunLine,
    Recipe,
    RecipeLine,
    RunStatus,
    Variation,
)
from hoard.stock import add_product, add_variation
from seneschal_core.db import WriteSession


async def _recipe(db: WriteSession, lines: list[tuple[int, str]], batch: str = "1", **output):
    recipe = Recipe(organization_id=db.organization_id, batch_quantity=Decimal(batch), **output)
    db.add(recipe)
    await db.flush()
    for position, (item_id, quantity) in enumerate(lines):
        db.add(
            RecipeLine(
                organization_id=db.organization_id,
                recipe_id=recipe.id,
                item_id=item_id,
                quantity=Decimal(quantity),
                position=position,
            )
        )
    await db.flush()
    return recipe


async def _lot(db: WriteSession, main: Location, item_id: int, number: str, **fields) -> Lot:
    lot = Lot(
        organization_id=db.organization_id,
        item_id=item_id,
        location_id=main.id,
        number=number,
        dated_on=date(2026, 11, 1),
        **fields,
    )
    db.add(lot)
    await db.flush()
    return lot


def test_the_us_nine_and_the_eu_fourteen_are_the_keys():
    assert (len(US_ALLERGENS), len(EU_ALLERGENS)) == (9, 14)
    assert US_ALLERGENS | EU_ALLERGENS == set(Allergen)


async def test_a_product_declares_its_materials_through_a_two_level_recipe(
    db: WriteSession, material, base
):
    tea = await material("Assam", ingredient_name="Black tea", allergens=[])
    ginger = await material("Ginger", allergens=[])
    almond = await material("Almond", allergens=[Allergen.TREE_NUTS])
    milk = await material("Oat drink")
    chai = await base("Chai spice base")
    # A batch of 100 of the spice base: 60 of ginger and 40 of almond.
    await _recipe(db, [(ginger.id, "60"), (almond.id, "40")], batch="100", base_id=chai.id)
    product, latte = await add_product(db, Product(name="Chai latte"))
    await _recipe(db, [(tea.id, "12"), (chai.id, "25"), (milk.id, "200")], variation_id=latte.id)

    declared = await Declarations(db).product(product)

    # Of the 25 of the base, 15 is ginger and 10 almond: either side of the 12 of tea.
    assert declared == Declaration(
        ingredients=["Oat drink", "Ginger", "Black tea", "Almond"],
        allergens=[Allergen.TREE_NUTS],
        undeclared=["Oat drink"],
        # Nobody has recorded what fragrance allergens these hold.
        fragrance_undeclared=["Almond", "Assam", "Ginger", "Oat drink"],
    )
    assert (await Declarations(db).item(chai.id)).ingredients == ["Ginger", "Almond"]


async def test_a_bundle_declares_the_variations_its_recipe_holds(db: WriteSession, material):
    flour = await material("Flour", allergens=[Allergen.WHEAT, Allergen.GLUTEN])
    _, cookie = await add_product(db, Product(name="Cookie"))
    await _recipe(db, [(flour.id, "30")], variation_id=cookie.id)
    _, tin = await add_product(db, Product(name="Tea tin"))
    box, _ = await add_product(db, Product(name="Gift box", bundle=True))
    await _recipe(db, [(cookie.id, "2"), (tin.id, "1")], product_id=box.id)

    declared = await Declarations(db).product(box)

    assert declared.ingredients == ["Flour"]
    assert declared.allergens == [Allergen.GLUTEN, Allergen.WHEAT]
    # The tin has no recipe, so what it holds is not known.
    assert declared.undeclared == ["Tea tin"]


async def test_a_run_declares_its_lots_and_its_output_lot_the_run(
    db: WriteSession, material, base, main: Location
):
    milk = await material("Milk powder", allergens=[Allergen.MILK])
    sugar = await material("Sugar")
    syrup = await base("Brown sugar syrup")
    # This lot of milk powder also carries soy, and this lot of sugar declares none.
    milk_lot = await _lot(db, main, milk.id, "M-1", allergens=[Allergen.MILK, Allergen.SOYBEANS])
    sugar_lot = await _lot(db, main, sugar.id, "S-1", allergens=[])
    output = await _lot(db, main, syrup.id, "R-1")
    run = ProductionRun(
        organization_id=db.organization_id,
        code="R-1",
        item_id=syrup.id,
        location_id=main.id,
        status=RunStatus.COMPLETED,
        completed_on=date(2026, 11, 2),
        actual_quantity=Decimal(4),
        output_lot_id=output.id,
    )
    db.add(run)
    await db.flush()
    for lot, quantity in ((milk_lot, "1"), (sugar_lot, "3")):
        db.add(
            ProductionRunLine(
                organization_id=db.organization_id,
                run_id=run.id,
                item_id=lot.item_id,
                lot_id=lot.id,
                quantity=Decimal(quantity),
            )
        )
    await db.flush()
    declarations = Declarations(db)

    expected = Declaration(
        ingredients=["Sugar", "Milk powder"],
        allergens=[Allergen.MILK, Allergen.SOYBEANS],
        undeclared=[],
        fragrance_undeclared=["Milk powder", "Sugar"],
    )
    assert await declarations.run(run) == expected
    assert await declarations.lot(output) == expected
    # A lot that records nothing of its own declares its material's.
    plain = await _lot(db, main, sugar.id, "S-2")
    assert (await declarations.lot(plain)).undeclared == ["Sugar"]


def test_the_eu_fragrance_allergens_are_the_2023_list():
    # The 24 declared before, 12 earlier entries the update replaced, and
    # entries 327 to 371.
    assert len(set(FRAGRANCE_ALLERGENS)) == len(FRAGRANCE_ALLERGENS) == 24 + 12 + 45
    assert (LEAVE_ON, RINSE_OFF) == (Decimal("0.001"), Decimal("0.01"))


async def test_fragrance_allergens_are_declared_past_the_products_threshold(
    db: WriteSession, material
):
    water = await material("Water", ingredient_name="Aqua", allergens=[], fragrance_allergens={})
    lavender = await material(
        "Lavender oil",
        allergens=[],
        fragrance_allergens={"linalool": "0.5", "limonene": "0.1", "not_a_key": "40"},
    )
    lotion, scented = await add_product(db, Product(name="Lotion"))
    plain = await add_variation(db, Variation(product_id=lotion.id))
    await _recipe(db, [(water.id, "995"), (lavender.id, "5")], variation_id=scented.id)
    await _recipe(db, [(water.id, "1000")], variation_id=plain.id)
    soap, bar = await add_product(db, Product(name="Soap", rinse_off=True))
    await _recipe(db, [(water.id, "995"), (lavender.id, "5")], variation_id=bar.id)
    declarations = Declarations(db)

    left_on = await declarations.product(lotion)
    washed_off = await declarations.product(soap)

    # 5 g of an oil that is 0.5% linalool in 1000 g: 0.0025%, at the scented
    # lotion's share rather than spread across the unscented one.
    shares = {"linalool": Decimal("0.0025"), "limonene": Decimal("0.0005")}
    assert (left_on.fragrance_allergens, left_on.fragrance_shares) == (["linalool"], shares)
    assert (washed_off.fragrance_allergens, washed_off.fragrance_shares) == ([], shares)
    assert (await declarations.item(bar.id)).fragrance_allergens == []
    assert (await declarations.item(lavender.id)).fragrance_allergens == ["linalool", "limonene"]
    assert left_on.fragrance_undeclared == []


async def test_a_lot_declares_its_own_fragrance_allergens_where_known(
    db: WriteSession, material, main: Location
):
    rose = await material("Rose oil", fragrance_allergens={"citronellol": "20"})
    unknown = await material("Absolute", fragrance_allergens={"geraniol": "a lot"})
    declarations = Declarations(db)

    own = await declarations.lot(
        await _lot(db, main, rose.id, "R-1", fragrance_allergens={"geraniol": "2"})
    )
    its_materials = await declarations.lot(await _lot(db, main, rose.id, "R-2"))

    assert own.fragrance_allergens == ["geraniol"]
    assert its_materials.fragrance_allergens == ["citronellol"]
    # A share that is not a number reads as not recorded.
    assert (await declarations.item(unknown.id)).fragrance_undeclared == ["Absolute"]
