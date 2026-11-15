"""Recipes: what one batch of a variation or a base is made from, and how much
a batch makes; and what a bundle holds.

An ordinary recipe's lines are materials that carry stock, and bases. A bundle
is a product whose recipe holds variations, of other products or other
bundles, and whose own variations take no recipe of their own. Nothing is made,
at any depth, from itself.

A line is written by amount or as parts of a ratio, in any unit that converts
to its item's tracking unit, and keeps its quantity in the tracking unit,
which ``hoard.units`` derives from what was written. A recipe scales to any
quantity of its output, and shows each line's share of the batch by weight.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import delete, or_
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from hoard import store_fields, units
from hoard.records import (
    Item,
    ItemKind,
    Material,
    Product,
    Recipe,
    RecipeLine,
    Unit,
    Variation,
)
from hoard.units import UnitError, UnitRefused
from seneschal_core.db import WriteSession


class RecipeError(StrEnum):
    """Why a recipe was refused: codes pages translate."""

    #: A line holds what its recipe may not: an ordinary recipe holds materials
    #: that carry stock, and bases; a bundle's holds variations.
    RECIPE_LINE_KIND = "RECIPE_LINE_KIND"
    #: Two lines hold the same item.
    RECIPE_LINE_REPEATED = "RECIPE_LINE_REPEATED"
    #: Its output would be made, at some depth, from itself.
    RECIPE_CYCLE = "RECIPE_CYCLE"
    #: A bundle's variations take no recipe of their own, and a product whose
    #: variations have one is not a bundle.
    RECIPE_BUNDLE = "RECIPE_BUNDLE"
    #: Its output already has a recipe.
    RECIPE_EXISTS = "RECIPE_EXISTS"


class RecipeRefused(Exception):
    def __init__(self, code: RecipeError | UnitError) -> None:
        super().__init__(code.value)
        self.code = code


@dataclass(frozen=True)
class Line:
    """An item a batch takes, and the number written for it: its amount, or
    its parts of a ratio, in ``unit_id``, or its tracking unit when none is
    given."""

    item_id: int
    written: Decimal
    unit_id: int | None = None


async def _output_items(db: AsyncSession, recipe: Recipe) -> set[int]:
    """The items ``recipe`` makes: its variation or base, or every variation of
    its bundle."""
    if recipe.product_id is None:
        return {recipe.variation_id or recipe.base_id or 0}
    found = await db.exec(select(Variation.id).where(Variation.product_id == recipe.product_id))
    return set(found.all())


async def _made_from(db: AsyncSession, items: set[int], leaving: int | None) -> set[int]:
    """The items the recipes making ``items`` hold, but the recipe ``leaving``'s."""
    products = select(Variation.product_id).where(col(Variation.id).in_(items))
    query = (
        select(RecipeLine.item_id)
        .join(Recipe, col(Recipe.id) == col(RecipeLine.recipe_id))
        .where(
            or_(
                col(Recipe.variation_id).in_(items),
                col(Recipe.base_id).in_(items),
                col(Recipe.product_id).in_(products),
            )
        )
    )
    if leaving is not None:
        query = query.where(Recipe.id != leaving)
    return set((await db.exec(query)).all())


async def _check_cycle(db: AsyncSession, recipe: Recipe, lines: Sequence[Line]) -> None:
    outputs = await _output_items(db, recipe)
    seen: set[int] = set()
    reached = {line.item_id for line in lines}
    while reached:
        if reached & outputs:
            raise RecipeRefused(RecipeError.RECIPE_CYCLE)
        seen |= reached
        reached = await _made_from(db, reached, recipe.id) - seen


async def _check_kinds(db: AsyncSession, recipe: Recipe, lines: Sequence[Line]) -> None:
    ids = [line.item_id for line in lines]
    if not ids:
        return
    found = await db.exec(
        select(Item.kind, Material.inventoriable)
        .outerjoin(Material, col(Material.id) == col(Item.id))
        .where(col(Item.id).in_(ids))
    )
    kinds = found.all()
    if recipe.product_id is not None:
        allowed = all(kind is ItemKind.VARIATION for kind, _ in kinds)
    else:
        allowed = all(
            kind is ItemKind.BASE or (kind is ItemKind.MATERIAL and inventoriable)
            for kind, inventoriable in kinds
        )
    if len(kinds) != len(ids) or not allowed:
        raise RecipeRefused(RecipeError.RECIPE_LINE_KIND)


async def _check_bundle(db: AsyncSession, recipe: Recipe) -> None:
    if recipe.variation_id is not None:
        variation = await db.get_one(Variation, recipe.variation_id)
        bundled = await db.exec(select(Recipe.id).where(Recipe.product_id == variation.product_id))
        clash = bundled.first() is not None
    elif recipe.product_id is not None:
        variations = select(Variation.id).where(Variation.product_id == recipe.product_id)
        made = await db.exec(select(Recipe.id).where(col(Recipe.variation_id).in_(variations)))
        clash = made.first() is not None
    else:
        clash = False
    if clash:
        raise RecipeRefused(RecipeError.RECIPE_BUNDLE)


async def _check_new(db: AsyncSession, recipe: Recipe) -> None:
    if recipe.id is not None:
        return
    column, output = next(
        (column, output)
        for column, output in (
            (Recipe.variation_id, recipe.variation_id),
            (Recipe.base_id, recipe.base_id),
            (Recipe.product_id, recipe.product_id),
        )
        if output is not None
    )
    held = await db.exec(select(Recipe.id).where(column == output))
    if held.first() is not None:
        raise RecipeRefused(RecipeError.RECIPE_EXISTS)


@dataclass(frozen=True)
class Worked:
    """A recipe's lines worked out for one batch (``hoard.units.recipe``), with
    the unit each was written in, its item's tracking unit, and its share of
    the batch by weight."""

    figures: units.Figures
    units: list[Unit | None]
    tracking: list[Unit | None]
    percents: list[Decimal | None]


async def work_out(db: AsyncSession, recipe: Recipe, lines: Sequence[Line]) -> Worked:
    """``lines`` of ``recipe`` worked out for one batch, by amount or by ratio
    as the recipe is written. Refused for a line in a unit that doesn't
    convert to its item's tracking unit, a ratio with no line measured like
    its total, and a line that converts to less than Hoard keeps."""
    known = await units.load(db)
    tracked = await units.tracking_units(db, [line.item_id for line in lines])
    tracking = [known.get(tracked.get(line.item_id, 0)) for line in lines]
    written = [
        known.get(line.unit_id) if line.unit_id is not None else unit
        for line, unit in zip(lines, tracking, strict=True)
    ]
    if any(not units.converts(*pair) for pair in zip(written, tracking, strict=True)):
        raise RecipeRefused(UnitError.UNIT_NOT_CONVERTIBLE)
    total_unit = known.get(recipe.total_unit_id or 0)
    if recipe.by_ratio and (recipe.total is None or total_unit is None):
        raise RecipeRefused(UnitError.RECIPE_RATIO_TOTAL)
    try:
        figures = units.recipe(
            [
                units.RecipeLine(line.written, unit, track)
                for line, unit, track in zip(lines, written, tracking, strict=True)
            ],
            total=recipe.total if recipe.by_ratio else None,
            total_unit=total_unit,
        )
    except UnitRefused as refused:
        raise RecipeRefused(refused.code) from None
    weights = [
        units.grams(quantity, track)
        for quantity, track in zip(figures.quantities, tracking, strict=True)
    ]
    return Worked(figures, written, tracking, units.percents(weights))


async def save(db: WriteSession, recipe: Recipe, lines: Sequence[Line]) -> Recipe:
    """Save ``recipe`` holding ``lines``, in their order, in place of any it
    held, each line's quantity derived from what was written. A bundle's
    recipe marks its product as a bundle. What Hoard works out of a product,
    such as its allergens, is sent to each store mapping it
    (``hoard.store_fields``)."""
    if len({line.item_id for line in lines}) != len(lines):
        raise RecipeRefused(RecipeError.RECIPE_LINE_REPEATED)
    await _check_new(db, recipe)
    await _check_bundle(db, recipe)
    await _check_kinds(db, recipe, lines)
    await _check_cycle(db, recipe, lines)
    worked = await work_out(db, recipe, lines)
    if not recipe.by_ratio:
        recipe.total = recipe.total_unit_id = None
    recipe.organization_id = db.organization_id
    db.add(recipe)
    await db.flush()
    await db.execute(delete(RecipeLine).where(col(RecipeLine.recipe_id) == recipe.id))
    db.add_all(
        RecipeLine(
            organization_id=db.organization_id,
            recipe_id=recipe.id,
            item_id=line.item_id,
            quantity=quantity,
            written=line.written,
            unit_id=None if unit is None else unit.id,
            position=position,
        )
        for position, (line, quantity, unit) in enumerate(
            zip(lines, worked.figures.quantities, worked.units, strict=True)
        )
    )
    if recipe.product_id is not None:
        product = await db.get_one(Product, recipe.product_id)
        product.bundle = True
        db.add(product)
    await db.flush()
    await store_fields.queue_changed(db)
    return recipe


async def remove(db: WriteSession, recipe: Recipe) -> None:
    """Remove ``recipe`` with its lines. A bundle's product is a bundle no more."""
    if recipe.product_id is not None:
        product = await db.get_one(Product, recipe.product_id)
        product.bundle = False
        db.add(product)
    await db.delete(recipe)
    await db.flush()
    await store_fields.queue_changed(db)


async def lines_of(db: AsyncSession, recipe_ids: Iterable[int]) -> dict[int, list[RecipeLine]]:
    """Each recipe's lines, in their order."""
    ids = list(recipe_ids)
    held: dict[int, list[RecipeLine]] = {recipe_id: [] for recipe_id in ids}
    found = await db.exec(
        select(RecipeLine)
        .where(col(RecipeLine.recipe_id).in_(ids))
        .order_by(col(RecipeLine.position), col(RecipeLine.id))
    )
    for line in found:
        held[line.recipe_id].append(line)
    return held
