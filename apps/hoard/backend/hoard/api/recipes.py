"""Recipes: what a batch of a variation or a base is made from, and what a
bundle holds (``hoard.recipes``).

A recipe is read as it stands or scaled, to make a quantity of its output or
to use an amount of one of its lines, each line with its amount in the unit it
was written in, its ratio, its quantity in its item's tracking unit and its
share of the batch by weight. A draft is worked out the same way without
saving it (``POST /recipes/preview``), so a page shows what Hoard would make
of it and converts nothing itself.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any

from fastapi import APIRouter, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, or_
from sqlmodel import col, select

from hoard import recipes, units
from hoard.api import items
from hoard.api.adjustments import AdjustmentItem, describe_items
from hoard.api.settings import found, require_references
from hoard.api.shapes import Page, PageDep
from hoard.recipes import RecipeError, RecipeRefused
from hoard.records import Base, CategoryKind, Product, Recipe, Variation
from hoard.units import UnitRefused
from seneschal_core.api import ErrorCode, OrganizationSessionDep, WriteSessionDep, refuse
from seneschal_core.db import OrganizationSession

router = APIRouter(prefix="/recipes", tags=["recipes"])

#: The most lines a recipe holds.
MAX_LINES = 200

_READ: dict[int | str, dict[str, Any]] = {
    401: {"description": "NOT_AUTHENTICATED"},
    404: {"description": "NOT_FOUND"},
}
_CHANGE: dict[int | str, dict[str, Any]] = {
    **_READ,
    403: {"description": "READ_ONLY"},
    409: {"description": "RECIPE_EXISTS"},
    422: {
        "description": "RECIPE_LINE_KIND, RECIPE_LINE_REPEATED, RECIPE_CYCLE, "
        "RECIPE_BUNDLE, RECIPE_RATIO_TOTAL, UNIT_NOT_CONVERTIBLE, UNIT_UNKNOWN, "
        "AMOUNT_TOO_SMALL, REQUEST_INVALID"
    },
}
_WORKED: dict[int | str, dict[str, Any]] = {
    **_READ,
    422: {"description": "RECIPE_RATIO_TOTAL, UNIT_NOT_CONVERTIBLE, AMOUNT_TOO_SMALL"},
}


class RecipeOutput(StrEnum):
    """What a recipe makes."""

    VARIATION = "variation"
    BASE = "base"
    #: A bundle product, which holds the variations its lines name.
    BUNDLE = "bundle"


class RecipeLineRead(BaseModel):
    item: AdjustmentItem
    #: The unit it was written in; none for a variation, which counts in
    #: units of itself.
    unit_id: int | None
    #: The number as typed: its amount, or its parts of a ratio.
    written: Decimal
    #: Its amount in the unit it was written in.
    amount: Decimal
    #: Its ratio: its parts as typed, or, by amount, its amount relative to
    #: the smallest line's.
    ratio: Decimal
    #: The unit its item is tracked in, which the quantity is in.
    tracking_unit_id: int | None
    quantity: Decimal
    #: Its share of the batch by weight, as a percentage; none for a line
    #: that isn't measured by weight.
    percent: Decimal | None


class RecipeFigures(BaseModel):
    """A recipe's lines worked out: as one batch, or scaled."""

    #: Whether its lines are written as parts of a ratio.
    by_ratio: bool
    #: A ratio's ingredients total, which its parts split, and its unit.
    total: Decimal | None
    total_unit_id: int | None
    #: How much the lines make: one batch, or the quantity it was scaled to.
    batch_quantity: Decimal
    #: How many batches the lines are: one, unless it was scaled.
    batches: Decimal
    lines: list[RecipeLineRead]


class RecipeRead(RecipeFigures):
    id: int
    output: RecipeOutput
    #: The variation's, the base's or the bundle product's id.
    output_id: int
    #: The product a variation's or a bundle's recipe is of.
    product_id: int | None
    #: The base's or the product's name.
    name: str
    #: A variation's attributes; none for a base or a bundle.
    attributes: dict[str, str]
    #: The unit the batch quantity is in: the base's; none for a variation or
    #: a bundle, which count in units of themselves.
    unit_id: int | None
    #: How long one batch takes.
    minutes: int | None


class RecipeLineWrite(BaseModel):
    item_id: int
    #: Its amount, or its parts of a ratio.
    written: items.Positive
    #: The unit it is written in: its item's tracking unit when not sent.
    unit_id: int | None = None


class RecipeWrite(BaseModel):
    """A recipe as an edit leaves it: its lines, in their order. Written by
    ratio, its ingredients total and that total's unit are sent too."""

    batch_quantity: items.Positive = Decimal(1)
    minutes: Annotated[int, Field(ge=0)] | None = None
    by_ratio: bool = False
    total: items.Positive | None = None
    total_unit_id: int | None = None
    lines: Annotated[list[RecipeLineWrite], Field(max_length=MAX_LINES)]


class RecipeCreate(RecipeWrite):
    """A recipe as it is added: of exactly one variation, base or bundle product."""

    variation_id: int | None = None
    base_id: int | None = None
    #: A product, which the recipe makes a bundle.
    product_id: int | None = None


class RecipePreview(RecipeFigures):
    """A draft worked out, and the same draft written the other way: by
    amount for one by ratio, keeping its amounts; by ratio for one by
    amount, its amounts divided by a common factor where they have one and
    its ingredients total that of the lines measured like its output. Null
    for a bundle, which counts whole variations."""

    switched: RecipeWrite | None


def _refused(refused: RecipeRefused) -> Exception:
    exists = refused.code is RecipeError.RECIPE_EXISTS
    return refuse(
        status.HTTP_409_CONFLICT if exists else status.HTTP_422_UNPROCESSABLE_CONTENT,
        refused.code,
    )


def _lines(body: RecipeWrite) -> list[recipes.Line]:
    return [recipes.Line(line.item_id, line.written, line.unit_id) for line in body.lines]


async def _require_units(db: OrganizationSession, body: RecipeWrite) -> None:
    """Refuse (422 ``UNIT_UNKNOWN``) a unit the organization does not have."""
    await require_references(
        db,
        kind=CategoryKind.BASE,
        units=[body.total_unit_id, *(line.unit_id for line in body.lines)],
    )


async def _figures(
    db: OrganizationSession,
    recipe: Recipe,
    lines: Sequence[recipes.Line],
    scale: Decimal | units.Use = Decimal(1),
) -> dict[str, Any]:
    """``lines`` of ``recipe`` worked out, over ``scale`` batches or as an
    amount of one line uses (``hoard.units.use``)."""
    try:
        worked = await recipes.work_out(db, recipe, lines)
    except RecipeRefused as refused:
        raise _refused(refused) from None
    figures = worked.figures
    batches = scale if isinstance(scale, Decimal) else scale.batches
    quantities = (
        [units.scaled(quantity, batches) for quantity in figures.quantities]
        if isinstance(scale, Decimal)
        else scale.quantities
    )
    described = await describe_items(db, [line.item_id for line in lines])
    return {
        "by_ratio": recipe.by_ratio,
        "total": None
        if recipe.total is None or not recipe.by_ratio
        else units.scaled(recipe.total, batches),
        "total_unit_id": recipe.total_unit_id if recipe.by_ratio else None,
        "batch_quantity": units.scaled(recipe.batch_quantity, batches),
        "batches": batches,
        "lines": [
            RecipeLineRead(
                item=described[line.item_id],
                unit_id=None if unit is None else unit.id,
                written=line.written,
                amount=units.scaled(amount, batches),
                ratio=ratio,
                tracking_unit_id=None if tracking is None else tracking.id,
                quantity=quantity,
                percent=percent,
            )
            for line, unit, tracking, amount, ratio, quantity, percent in zip(
                lines,
                worked.units,
                worked.tracking,
                figures.amounts,
                figures.ratios,
                quantities,
                worked.percents,
                strict=True,
            )
        ],
    }


async def _scale(
    db: OrganizationSession,
    recipe: Recipe,
    lines: Sequence[recipes.Line],
    quantity: Decimal | None,
    ingredient_id: int | None,
    amount: Decimal | None,
    unit_id: int | None,
) -> Decimal | units.Use:
    """The batches that make ``quantity`` of a recipe's output, or the scale
    that uses ``amount`` of its line of ``ingredient_id``, in ``unit_id`` or
    that item's tracking unit."""
    if ingredient_id is None or amount is None:
        return units.scale(recipe.batch_quantity, quantity)
    index = next((at for at, line in enumerate(lines) if line.item_id == ingredient_id), None)
    if index is None:
        raise refuse(status.HTTP_422_UNPROCESSABLE_CONTENT, ErrorCode.REQUEST_INVALID)
    try:
        tracked = await units.in_tracking_unit(db, ingredient_id, amount, unit_id)
        worked = await recipes.work_out(db, recipe, lines)
    except (UnitRefused, RecipeRefused) as refused:
        raise refuse(status.HTTP_422_UNPROCESSABLE_CONTENT, refused.code) from None
    return units.use(worked.figures, index, tracked)


async def _read(
    db: OrganizationSession,
    found_recipes: list[Recipe],
    quantity: Decimal | None = None,
    use: tuple[int, Decimal, int | None] | None = None,
) -> list[RecipeRead]:
    """Each recipe as it reads, scaled to make ``quantity`` or to use an
    amount of one line (``use``: its item, the amount and its unit)."""
    lines = await recipes.lines_of(db, [recipe.id for recipe in found_recipes])
    outputs = [recipe.variation_id or recipe.base_id or 0 for recipe in found_recipes]
    described = await describe_items(db, outputs)
    counted = await units.tracking_units(db, outputs)
    bundles = [recipe.product_id for recipe in found_recipes if recipe.product_id is not None]
    bundle_names = dict(
        (await db.exec(select(Product.id, Product.name).where(col(Product.id).in_(bundles)))).all()
    )
    variations = [recipe.variation_id for recipe in found_recipes if recipe.variation_id]
    products = dict(
        (
            await db.exec(
                select(Variation.id, Variation.product_id).where(col(Variation.id).in_(variations))
            )
        ).all()
    )
    read = []
    for recipe in found_recipes:
        held = [recipes.Line(line.item_id, line.written, line.unit_id) for line in lines[recipe.id]]
        ingredient_id, amount, unit_id = use or (None, None, None)
        scale = await _scale(db, recipe, held, quantity, ingredient_id, amount, unit_id)
        if recipe.product_id is not None:
            output, output_id, product_id = (
                RecipeOutput.BUNDLE,
                recipe.product_id,
                recipe.product_id,
            )
            name, attributes = bundle_names[recipe.product_id], {}
        else:
            output_id = recipe.variation_id or recipe.base_id or 0
            output = RecipeOutput.BASE if recipe.base_id else RecipeOutput.VARIATION
            product_id = products.get(output_id)
            name, attributes = described[output_id].name, described[output_id].attributes
        read.append(
            RecipeRead(
                id=recipe.id,
                output=output,
                output_id=output_id,
                product_id=product_id,
                name=name,
                attributes=attributes,
                unit_id=counted.get(recipe.base_id or 0),
                minutes=recipe.minutes,
                **await _figures(db, recipe, held, scale),
            )
        )
    return read


async def _read_one(
    db: OrganizationSession,
    recipe_id: int,
    quantity: Decimal | None = None,
    use: tuple[int, Decimal, int | None] | None = None,
) -> RecipeRead:
    db.expunge_all()
    return (await _read(db, [await found(db, Recipe, recipe_id)], quantity, use))[0]


def _recipe(body: RecipeWrite, recipe: Recipe) -> Recipe:
    """``recipe`` as ``body`` writes it."""
    recipe.batch_quantity = body.batch_quantity
    recipe.minutes = body.minutes
    recipe.by_ratio = body.by_ratio
    recipe.total = body.total if body.by_ratio else None
    recipe.total_unit_id = body.total_unit_id if body.by_ratio else None
    return recipe


@router.get("", responses=_READ)
async def list_recipes(
    db: OrganizationSessionDep,
    page: PageDep,
    q: Annotated[str | None, Query(max_length=200)] = None,
    product_id: Annotated[int | None, Query()] = None,
    base_id: Annotated[int | None, Query()] = None,
) -> Page[RecipeRead]:
    """Recipes by the name of what they make: those of one product (its
    variations' or its bundle's) or one base, and those whose output's name
    holds ``q``, when given."""
    product = func.coalesce(Recipe.product_id, Variation.product_id)
    name = func.coalesce(Base.name, Product.name)
    query = (
        select(Recipe)
        .outerjoin(Base, col(Base.id) == col(Recipe.base_id))
        .outerjoin(Variation, col(Variation.id) == col(Recipe.variation_id))
        .outerjoin(Product, col(Product.id) == product)
    )
    if q:
        query = query.where(
            or_(
                col(Base.name).icontains(q, autoescape=True),
                col(Product.name).icontains(q, autoescape=True),
            )
        )
    if product_id is not None:
        query = query.where(product == product_id)
    if base_id is not None:
        query = query.where(Recipe.base_id == base_id)
    total = (await db.exec(select(func.count()).select_from(query.subquery()))).one()
    found_recipes = (
        await db.exec(
            query.order_by(func.lower(name), col(Recipe.id)).offset(page.offset).limit(page.limit)
        )
    ).all()
    return Page(items=await _read(db, list(found_recipes)), total=total)


@router.get("/{recipe_id}", responses=_WORKED)
async def read_recipe(
    recipe_id: int,
    db: OrganizationSessionDep,
    quantity: Annotated[items.Positive | None, Query()] = None,
    ingredient_id: Annotated[int | None, Query()] = None,
    amount: Annotated[items.Positive | None, Query()] = None,
    unit_id: Annotated[int | None, Query()] = None,
) -> RecipeRead:
    """A recipe: as one batch, scaled to make ``quantity`` of its output, or
    scaled to use ``amount`` of its line of ``ingredient_id``, in ``unit_id``
    or that item's tracking unit. That line then takes exactly the amount."""
    use = None if ingredient_id is None or amount is None else (ingredient_id, amount, unit_id)
    return await _read_one(db, recipe_id, quantity, use)


@router.post("/preview", responses=_WORKED)
async def preview_recipe(body: RecipeCreate, db: OrganizationSessionDep) -> RecipePreview:
    """A draft recipe worked out for one batch, and written the other way,
    saving nothing. Its output, when sent, is what a switch to a ratio totals
    the lines measured like."""
    await _require_units(db, body)
    draft = _recipe(body, Recipe(product_id=body.product_id))
    lines = _lines(body)
    figures = await _figures(db, draft, lines)
    return RecipePreview(
        **figures, switched=None if body.product_id else await _switched(db, body, figures)
    )


async def _switched(
    db: OrganizationSession, body: RecipeCreate, figures: dict[str, Any]
) -> RecipeWrite:
    """``body`` written the other way, from its ``figures``."""
    worked: list[RecipeLineRead] = figures["lines"]
    if body.by_ratio:
        return body.model_copy(
            update={
                "by_ratio": False,
                "total": None,
                "total_unit_id": None,
                "lines": [
                    RecipeLineWrite(item_id=line.item.id, written=line.amount, unit_id=line.unit_id)
                    for line in worked
                ],
            }
        )
    known = await units.load(db)
    output = (await units.tracking_units(db, [body.base_id or 0])).get(body.base_id or 0)
    written = [
        units.RecipeLine(line.written, known.get(line.unit_id or 0), None) for line in worked
    ]
    total_unit = next(
        (
            unit
            for unit in [known.get(output or 0), *(line.unit for line in written)]
            if unit is not None and units.measured(written, unit) is not None
        ),
        None,
    )
    total = None if total_unit is None else units.measured(written, total_unit)
    return body.model_copy(
        update={
            "by_ratio": True,
            "total": None if total is None else units.kept(total),
            "total_unit_id": None if total_unit is None else total_unit.id,
            "lines": [
                RecipeLineWrite(item_id=line.item.id, written=parts, unit_id=line.unit_id)
                for line, parts in zip(
                    worked, units.parts([line.written for line in worked]), strict=True
                )
            ],
        }
    )


@router.post("", status_code=status.HTTP_201_CREATED, responses=_CHANGE)
async def create_recipe(body: RecipeCreate, db: WriteSessionDep) -> RecipeRead:
    outputs = [body.variation_id, body.base_id, body.product_id]
    if sum(output is not None for output in outputs) != 1:
        raise refuse(status.HTTP_422_UNPROCESSABLE_CONTENT, ErrorCode.REQUEST_INVALID)
    if body.variation_id is not None:
        await found(db, Variation, body.variation_id)
    if body.base_id is not None:
        await found(db, Base, body.base_id)
    if body.product_id is not None:
        await found(db, Product, body.product_id)
    await _require_units(db, body)
    recipe = _recipe(
        body,
        Recipe(variation_id=body.variation_id, base_id=body.base_id, product_id=body.product_id),
    )
    try:
        await recipes.save(db, recipe, _lines(body))
    except RecipeRefused as refused:
        raise _refused(refused) from None
    await db.commit()
    return await _read_one(db, recipe.id)


@router.put("/{recipe_id}", responses=_CHANGE)
async def update_recipe(recipe_id: int, body: RecipeWrite, db: WriteSessionDep) -> RecipeRead:
    await _require_units(db, body)
    recipe = _recipe(body, await found(db, Recipe, recipe_id))
    try:
        await recipes.save(db, recipe, _lines(body))
    except RecipeRefused as refused:
        raise _refused(refused) from None
    await db.commit()
    return await _read_one(db, recipe_id)


@router.delete("/{recipe_id}", status_code=status.HTTP_204_NO_CONTENT, responses=_CHANGE)
async def delete_recipe(recipe_id: int, db: WriteSessionDep) -> None:
    """Remove a recipe with its lines. A bundle's product is a bundle no more."""
    await recipes.remove(db, await found(db, Recipe, recipe_id))
    await db.commit()
