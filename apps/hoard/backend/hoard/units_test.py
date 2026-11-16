"""Standard units are found by their names and convert within a measure; a
recipe's lines scale by one multiplier, by amount or by ratio; amounts become
parts; a recipe scales to use an amount of one line; a quantity splits in
proportion; shares and declarations weigh only what is measured by weight."""

from __future__ import annotations

from decimal import Decimal

import pytest

from hoard import units
from hoard.records import Unit
from hoard.units import RecipeLine, UnitError, UnitRefused

GRAM = Unit(id=1, name="g", standard="gram")
KILOGRAM = Unit(id=2, name="kg", standard="kilogram")
MILLILITRE = Unit(id=3, name="mL", standard="millilitre")
OUNCE = Unit(id=4, name="oz", standard="ounce")
JAR = Unit(id=5, name="Jar")
EACH = Unit(id=6, name="each", standard="each")


def test_standard_units_are_found_by_their_names_and_aliases():
    assert units.match("g") == units.match("Grams") == units.match("gramme") == "gram"
    assert units.match("Fl. Oz.") == units.match("floz") == units.match("fl_oz") == "fluid_ounce"
    assert units.match("oz") == "ounce"
    assert (units.match("ton"), units.match("tonne")) == ("ton", "tonne")
    assert units.match("cup (US)") == "cup"
    assert [units.match(name) for name in ("t", "T", "jar", "")] == [None, None, None, None]
    assert {"kg", "kilo", "kilogram"} <= set(units.aliases("kilogram"))


def test_standard_units_of_one_measure_convert_and_others_do_not():
    assert units.factor(KILOGRAM, GRAM) == 1000
    assert units.convert(Decimal(1), OUNCE, GRAM).quantize(units.PLACES) == Decimal("28.349523")
    assert units.factor(JAR, JAR) == 1
    assert units.factor(GRAM, MILLILITRE) is None
    assert units.factor(JAR, GRAM) is None
    assert units.factor(None, None) == 1
    assert units.factor(None, GRAM) is None
    # A variation counts in units of itself, which convert as each.
    assert (units.factor(EACH, None), units.factor(None, JAR)) == (1, None)
    with pytest.raises(UnitRefused) as refused:
        units.convert(Decimal(1), MILLILITRE, GRAM)
    assert refused.value.code is UnitError.UNIT_NOT_CONVERTIBLE


def test_a_purchase_converts_between_standard_units_and_a_custom_one_is_typed():
    assert units.purchase_conversion(KILOGRAM, GRAM) == Decimal("1000.000000")
    assert units.purchase_conversion(JAR, GRAM) is None
    assert units.purchase_conversion(GRAM, GRAM) is None
    # A variation bought by the dozen is 12 of it; by the jar, the number typed.
    dozen = Unit(id=9, name="dozen", standard="dozen")
    assert units.purchase_conversion(dozen, None) == 12
    assert units.purchase_conversion(JAR, None) is None
    assert units.purchase_conversion(None, None) is None
    tonne = Unit(id=7, name="tonne", standard="tonne")
    milligram = Unit(id=8, name="mg", standard="milligram")
    with pytest.raises(UnitRefused) as refused:
        units.purchase_conversion(milligram, tonne)
    assert refused.value.code is UnitError.AMOUNT_TOO_SMALL


def test_a_line_written_in_kilograms_is_kept_in_grams():
    worked = units.recipe([RecipeLine(Decimal("0.5"), KILOGRAM, GRAM)])

    assert worked.amounts == [Decimal("0.5")]
    assert worked.quantities == [Decimal("500.000000")]
    with pytest.raises(UnitRefused) as refused:
        units.recipe([RecipeLine(Decimal("0.0000001"), GRAM, KILOGRAM)])
    assert refused.value.code is UnitError.AMOUNT_TOO_SMALL


def test_a_ratio_splits_its_total_among_the_lines_measured_like_it():
    alyx = [
        RecipeLine(Decimal(2), GRAM, GRAM),
        RecipeLine(Decimal(1), GRAM, GRAM),
        RecipeLine(Decimal(1), GRAM, GRAM),
        RecipeLine(Decimal("0.1"), GRAM, GRAM),
    ]

    worked = units.recipe(alyx, total=Decimal(931), total_unit=GRAM)

    assert [amount.quantize(Decimal("0.01")) for amount in worked.amounts] == [
        Decimal("454.15"),
        Decimal("227.07"),
        Decimal("227.07"),
        Decimal("22.71"),
    ]
    assert worked.ratios == [Decimal(2), Decimal(1), Decimal(1), Decimal("0.1")]
    in_kilograms = units.recipe(alyx, total=Decimal("0.931"), total_unit=KILOGRAM)
    assert in_kilograms.quantities == worked.quantities


def test_a_line_in_another_measure_scales_along_out_of_the_total():
    lychee_in_ml = [
        RecipeLine(Decimal(2), GRAM, GRAM),
        RecipeLine(Decimal(1), GRAM, GRAM),
        RecipeLine(Decimal(1), GRAM, GRAM),
        RecipeLine(Decimal("0.1"), MILLILITRE, MILLILITRE),
    ]

    worked = units.recipe(lychee_in_ml, total=Decimal(931), total_unit=GRAM)

    assert worked.quantities == [
        Decimal("465.500000"),
        Decimal("232.750000"),
        Decimal("232.750000"),
        Decimal("23.275000"),
    ]
    with pytest.raises(UnitRefused) as refused:
        units.recipe(lychee_in_ml[3:], total=Decimal(10), total_unit=GRAM)
    assert refused.value.code is UnitError.RECIPE_RATIO_TOTAL


def test_amounts_read_relative_to_the_smallest_and_become_parts_by_a_common_factor():
    amounts = [Decimal(454), Decimal(227), Decimal(227), Decimal(23)]

    assert units.relative(amounts) == [
        Decimal("19.739130"),
        Decimal("9.869565"),
        Decimal("9.869565"),
        Decimal("1.000000"),
    ]
    assert units.parts([Decimal(454), Decimal(227), Decimal(227)]) == [
        Decimal("2.000000"),
        Decimal("1.000000"),
        Decimal("1.000000"),
    ]
    assert units.parts(amounts) == [amount.quantize(units.PLACES) for amount in amounts]
    untidy = [Decimal("454.15"), Decimal("227.07"), Decimal("227.07"), Decimal("22.71")]
    assert units.parts(untidy) == [amount.quantize(units.PLACES) for amount in untidy]


def test_a_recipe_scales_to_a_quantity_or_to_use_an_amount_of_one_line():
    batches = units.scale(Decimal(4), Decimal(10))

    assert batches == Decimal("2.5")
    assert units.scaled(Decimal("1.5"), batches) == Decimal("3.750000")
    assert units.scale(Decimal(4), None) == 1
    assert units.scaled(Decimal("0.333333"), Decimal("0.5")) == Decimal("0.166666")
    assert units.scaled(Decimal("0.333333"), Decimal("0.5"), exact=True) == Decimal("0.1666665")
    assert units.each(Decimal(3), Decimal(4)) == Decimal("0.75")
    alyx = units.recipe(
        [
            RecipeLine(Decimal(2), GRAM, GRAM),
            RecipeLine(Decimal(1), GRAM, GRAM),
            RecipeLine(Decimal(1), GRAM, GRAM),
            RecipeLine(Decimal("0.1"), GRAM, GRAM),
        ],
        total=Decimal(931),
        total_unit=GRAM,
    )
    rose_hips = units.use(alyx, 2, Decimal(300))
    assert rose_hips.quantities == [
        Decimal("600.000000"),
        Decimal("300.000000"),
        Decimal("300.000000"),
        Decimal("30.000000"),
    ]
    assert units.scaled(Decimal(931), rose_hips.batches) == Decimal("1230.000000")


def test_a_quantity_splits_in_proportion_into_two_that_add_up():
    # 200 g of a pound bought as one, counted as 453.592 g.
    assert units.split(Decimal(1), Decimal(200), Decimal("453.592")) == (
        Decimal("0.440925"),
        Decimal("0.559075"),
    )
    assert units.split(Decimal("453.592"), Decimal(200), Decimal("453.592")) == (
        Decimal(200),
        Decimal("253.592"),
    )
    for part in (Decimal("0.0000001"), Decimal("0.9999999")):
        with pytest.raises(UnitRefused) as refused:
            units.split(Decimal("0.000001"), part, Decimal(1))
        assert refused.value.code == UnitError.AMOUNT_TOO_SMALL


def test_shares_weigh_only_the_lines_measured_by_weight():
    weights = [
        units.grams(Decimal("0.5"), KILOGRAM),
        units.grams(Decimal(2), GRAM),
        units.grams(Decimal(10), MILLILITRE),
        units.grams(Decimal(3), EACH, Decimal(4)),
        units.grams(Decimal(1), None),
    ]

    assert weights == [Decimal("500.0"), Decimal(2), None, Decimal(12), None]
    assert units.percents(weights) == [
        Decimal("97.28"),
        Decimal("0.39"),
        None,
        Decimal("2.33"),
        None,
    ]
    assert units.percents([None]) == [None]
