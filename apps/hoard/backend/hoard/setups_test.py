"""Starting setups: adding one adds only what is missing, so adding it twice
changes nothing the second time."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from sqlmodel import col, select

from hoard import setups, units
from hoard.records import (
    AdjustmentReason,
    Category,
    CategoryKind,
    HoldReason,
    ItemType,
    OrganizationSettings,
    Picking,
    PropertyDefinition,
    RecordKind,
    TypeShape,
    Unit,
)
from hoard.stock import add_default_hold_reasons
from seneschal_core.db import WriteSession

LOCALE = Path(__file__).parents[2] / "frontend" / "public" / "locales" / "en" / "setups.json"


def _words(setup: setups.Setup) -> dict[str, str]:
    return {word: f"<{word}>" for word in setup.words()}


async def _everything(db: WriteSession) -> list[Any]:
    rows: list[Any] = []
    for model in (
        Unit,
        ItemType,
        Category,
        PropertyDefinition,
        AdjustmentReason,
        HoldReason,
        OrganizationSettings,
    ):
        found = await db.exec(select(model).order_by(col(model.id)))
        rows.extend(row.model_dump(exclude={"created_at", "updated_at"}) for row in found.all())
    return rows


def _said(words: Any, path: str) -> Any:
    """What a setup's locale ``words`` hold at ``path``."""
    for part in path.split("."):
        words = words[part]
    return words


def test_the_page_holds_words_for_every_key_of_every_setup():
    words = json.loads(LOCALE.read_text(encoding="utf-8"))["setups"]
    for key, setup in setups.setups().items():
        for path in setup.words():
            assert isinstance(_said(words[key], path), str), (key, path)
        assert isinstance(_said(words[key], "name"), str)


async def test_adding_drinks_twice_changes_nothing_the_second_time(db: WriteSession):
    drinks = setups.setups()["drinks"]
    assert not await setups.added(db, drinks)
    assert not await setups.started(db)

    await setups.add(db, drinks, _words(drinks))
    first = await _everything(db)
    await setups.add(db, drinks, _words(drinks))

    assert await _everything(db) == first
    assert await setups.added(db, drinks)
    assert await setups.started(db)
    categories = await db.exec(select(Category.name).where(Category.kind == CategoryKind.BASE))
    assert "<categories.prep>" in categories.all()
    packaging = await db.exec(
        select(Category.picking).where(col(Category.name).startswith("<categories.packaging"))
    )
    assert packaging.all() == [Picking.FIRST_IN]
    reasons = (await db.exec(select(AdjustmentReason.name))).all()
    assert "<reasons.spill>" in reasons
    # Drinks calls a base a base, which needs no word of its own.
    assert (await db.exec(select(OrganizationSettings))).first() is None


def test_the_first_open_dropdown_offers_each_industry():
    assert [*setups.setups()] == [
        "bakery_food",
        "cosmetics_soap",
        "crafts_assembly",
        "drinks",
        "restaurant_bar",
    ]


@pytest.mark.parametrize(
    "key", ["bakery_food", "cosmetics_soap", "crafts_assembly", "drinks", "restaurant_bar"]
)
async def test_adding_an_industrys_setup_twice_changes_nothing_the_second_time(
    db: WriteSession, key: str
):
    setup = setups.setups()[key]

    await setups.add(db, setup, _words(setup))
    first = await _everything(db)
    await setups.add(db, setup, _words(setup))

    assert await _everything(db) == first
    assert await setups.added(db, setup)
    held = await db.exec(select(HoldReason.key).where(col(HoldReason.name).is_not(None)))
    assert sorted(held.all()) == sorted(setup.hold_reasons)
    # Packaging and the like, which never expires, go first in, first out.
    first_in = await db.exec(select(Category.name).where(Category.picking == Picking.FIRST_IN))
    assert first_in.all()
    # Its types: packaging and bought products among them.
    types = {
        name: (kind, shape)
        for name, kind, shape in await db.exec(select(ItemType.name, ItemType.kind, ItemType.shape))
    }
    assert types == {f"<types.{each.key}>": (each.kind, each.shape) for each in setup.types}
    assert {TypeShape.PACKAGING, TypeShape.BOUGHT} <= {shape for _kind, shape in types.values()}
    # Each category of its kind takes the type it names; one naming none takes
    # its kind's default.
    defaults = await db.exec(
        select(Category.kind, Category.name, ItemType.name).join(
            ItemType, col(ItemType.id) == col(Category.default_type_id), isouter=True
        )
    )
    assert sorted(defaults.all()) == sorted(
        (
            kind,
            f"<categories.{category.key}>",
            f"<types.{category.type}>"
            if any(each.key == category.type and each.kind == kind for each in setup.types)
            else None,
        )
        for category in setup.categories
        for kind in category.kinds
    )
    settings = (await db.exec(select(OrganizationSettings))).first()
    named = (settings.base_word, settings.recipe_word) if settings else (None, None)
    assert named == (
        "<base_word>" if setup.base_word != setups.BASE else None,
        "<recipe_word>" if setup.recipe_word != setups.RECIPE else None,
    )


async def test_the_crafts_setup_names_recipes_bill_of_materials(db: WriteSession):
    words = json.loads(LOCALE.read_text(encoding="utf-8"))["setups"]["crafts_assembly"]
    crafts = setups.setups()["crafts_assembly"]

    await setups.add(db, crafts, {word: _said(words, word) for word in crafts.words()})

    settings = (await db.exec(select(OrganizationSettings))).one()
    assert (settings.recipe_word, settings.base_word) == ("bill of materials", "sub-assembly")
    products = await db.exec(
        select(ItemType.name, ItemType.shape).where(ItemType.kind == CategoryKind.PRODUCT)
    )
    assert {
        "Pins": TypeShape.MADE,
        "Games": TypeShape.MADE,
        "Merch": TypeShape.BOUGHT,
    }.items() <= dict(products.all()).items()


async def test_a_word_named_already_stays(db: WriteSession):
    crafts = setups.setups()["crafts_assembly"]
    db.add(OrganizationSettings(organization_id=db.organization_id, recipe_word="pattern"))
    await db.flush()

    await setups.add(db, crafts, _words(crafts))

    settings = (await db.exec(select(OrganizationSettings))).one()
    assert (settings.recipe_word, settings.base_word) == ("pattern", "<base_word>")


async def test_cosmetics_cures_its_lots_and_writes_off_a_failed_cure(db: WriteSession):
    cosmetics = setups.setups()["cosmetics_soap"]

    await setups.add(db, cosmetics, _words(cosmetics))

    curing = await db.exec(select(HoldReason.name).where(HoldReason.key == "curing"))
    assert curing.all() == ["<hold_reasons.curing>"]
    reasons = await db.exec(select(AdjustmentReason.name))
    assert "<reasons.failed_cure>" in reasons.all()


async def test_a_renamed_property_is_found_by_its_key(db: WriteSession):
    drinks = setups.setups()["drinks"]
    await setups.add(db, drinks, _words(drinks))
    organic = (
        await db.exec(
            select(PropertyDefinition).where(PropertyDefinition.key == "organic_category")
        )
    ).one()
    assert (organic.kind, [option["value"] for option in organic.options or []]) == (
        RecordKind.LOT,
        ["organic_100", "organic", "made_with_organic", "not_organic"],
    )
    organic.name = "Organic status"
    db.add(organic)
    await db.flush()

    await setups.add(db, drinks, _words(drinks))

    found = await db.exec(
        select(PropertyDefinition.name).where(PropertyDefinition.key == "organic_category")
    )
    assert found.all() == ["Organic status"]


async def test_a_setup_wants_every_word(db: WriteSession):
    drinks = setups.setups()["drinks"]
    words = _words(drinks)
    del words["units.bottle_750ml"]

    with pytest.raises(setups.SetupRefused):
        await setups.add(db, drinks, words)


async def test_a_setups_hold_reasons_keep_their_keys_beside_the_defaults(db: WriteSession):
    drinks = setups.setups()["drinks"]
    held = replace(drinks, hold_reasons=("curing", "tasting"))
    await add_default_hold_reasons(db)

    await setups.add(db, held, _words(held))

    reasons = await db.exec(select(HoldReason.key, HoldReason.name).order_by(col(HoldReason.id)))
    # A default the organization has keeps its own name; another is added named.
    assert [*reasons.all()][-2:] == [("recall", None), ("tasting", "<hold_reasons.tasting>")]
    assert "hold_reasons.curing" in held.words()


async def test_the_standard_units_are_no_start_and_no_setup_brings_one(db: WriteSession):
    await units.add_standard(db)

    assert not await setups.started(db)
    for setup in setups.setups().values():
        assert [unit for unit in setup.units if units.match(unit)] == []


async def test_a_setup_names_its_types_and_the_categories_that_default_to_them(
    db: WriteSession,
):
    drinks = setups.setups()["drinks"]
    typed = replace(
        drinks,
        types=(
            setups.SetupType("bought", CategoryKind.PRODUCT, TypeShape.BOUGHT),
            setups.SetupType("packaging", CategoryKind.MATERIAL, TypeShape.PACKAGING),
        ),
        categories=(
            setups.SetupCategory(
                "packaging", (CategoryKind.MATERIAL,), Picking.FIRST_IN, type="packaging"
            ),
            setups.SetupCategory("packed_product", (CategoryKind.PRODUCT,), Picking.FIRST_IN),
        ),
    )
    assert {"types.bought", "types.packaging"} <= set(typed.words())

    await setups.add(db, typed, _words(typed))
    first = await _everything(db)
    await setups.add(db, typed, _words(typed))

    assert await _everything(db) == first
    made = {
        name: (kind, shape)
        for name, kind, shape in await db.exec(select(ItemType.name, ItemType.kind, ItemType.shape))
    }
    assert made == {
        "<types.bought>": (CategoryKind.PRODUCT, "bought"),
        "<types.packaging>": (CategoryKind.MATERIAL, "packaging"),
    }
    defaults = await db.exec(
        select(Category.name, ItemType.name).join(
            ItemType, col(ItemType.id) == col(Category.default_type_id), isouter=True
        )
    )
    assert sorted(defaults.all()) == [
        ("<categories.packaging>", "<types.packaging>"),
        ("<categories.packed_product>", None),
    ]
    with pytest.raises(ValueError):
        setups.SetupType("box", CategoryKind.MATERIAL, TypeShape.BUNDLE)
