"""Recipes: what a batch of a variation or a base is made from, and what a
bundle holds (``hoard.recipes``).

A recipe is read as it stands or scaled to make a quantity of its output, each
line with its share of the batch by weight.
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any

from fastapi import APIRouter, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, or_
from sqlmodel import col, select

from hoard import recipes
from hoard.api import items
from hoard.api.adjustments import AdjustmentItem, describe_items
from hoard.api.settings import found
from hoard.api.shapes import Page, PageDep
from hoard.recipes import RecipeError, RecipeRefused
from hoard.records import Base, Product, Recipe, Variation
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
        "RECIPE_BUNDLE, REQUEST_INVALID"
    },
}


class RecipeOutput(StrEnum):
    """What a recipe makes."""

    VARIATION = "variation"
    BASE = "base"
    #: A bundle product, which holds the variations its lines name.
    BUNDLE = "bundle"


class RecipeLineRead(BaseModel):
    item: AdjustmentItem
    #: The unit the quantity is in: the material's or the base's tracking
    #: unit; none for a variation, which counts in units of itself.
    unit_id: int | None
    quantity: Decimal
    #: Its share of the batch by weight, as a percentage (``hoard.recipes.percents``).
    percent: Decimal | None


class RecipeRead(BaseModel):
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
    #: How much the lines make: one batch, or the quantity it was scaled to.
    batch_quantity: Decimal
    #: How many batches the lines are: one, unless it was scaled.
    batches: Decimal
    #: How long one batch takes.
    minutes: int | None
    lines: list[RecipeLineRead]


class RecipeLineWrite(BaseModel):
    item_id: int
    quantity: items.Positive


class RecipeWrite(BaseModel):
    """A recipe as an edit leaves it: its lines, in their order."""

    batch_quantity: items.Positive = Decimal(1)
    minutes: Annotated[int, Field(ge=0)] | None = None
    lines: Annotated[list[RecipeLineWrite], Field(max_length=MAX_LINES)]


class RecipeCreate(RecipeWrite):
    """A recipe as it is added: of exactly one variation, base or bundle product."""

    variation_id: int | None = None
    base_id: int | None = None
    #: A product, which the recipe makes a bundle.
    product_id: int | None = None


def _refused(refused: RecipeRefused) -> Exception:
    exists = refused.code is RecipeError.RECIPE_EXISTS
    return refuse(
        status.HTTP_409_CONFLICT if exists else status.HTTP_422_UNPROCESSABLE_CONTENT,
        refused.code,
    )


async def _read(
    db: OrganizationSession, found_recipes: list[Recipe], quantity: Decimal | None = None
) -> list[RecipeRead]:
    """Each recipe as it reads, its lines scaled to make ``quantity``."""
    lines = await recipes.lines_of(db, [recipe.id for recipe in found_recipes])
    line_items = [line.item_id for held in lines.values() for line in held]
    outputs = [recipe.variation_id or recipe.base_id or 0 for recipe in found_recipes]
    described = await describe_items(db, [*line_items, *outputs])
    units = await recipes.tracking_units(db, [*line_items, *outputs])
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
        batches = recipes.scale(recipe.batch_quantity, quantity)
        held = lines[recipe.id]
        percents = recipes.percents([(units.get(line.item_id), line.quantity) for line in held])
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
                unit_id=units.get(recipe.base_id or 0),
                batch_quantity=recipes.scaled(recipe.batch_quantity, batches),
                batches=batches,
                minutes=recipe.minutes,
                lines=[
                    RecipeLineRead(
                        item=described[line.item_id],
                        unit_id=units.get(line.item_id),
                        quantity=recipes.scaled(line.quantity, batches),
                        percent=percent,
                    )
                    for line, percent in zip(held, percents, strict=True)
                ],
            )
        )
    return read


async def _read_one(
    db: OrganizationSession, recipe_id: int, quantity: Decimal | None = None
) -> RecipeRead:
    db.expunge_all()
    return (await _read(db, [await found(db, Recipe, recipe_id)], quantity))[0]


def _lines(body: RecipeWrite) -> list[recipes.Line]:
    return [recipes.Line(line.item_id, line.quantity) for line in body.lines]


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


@router.get("/{recipe_id}", responses=_READ)
async def read_recipe(
    recipe_id: int,
    db: OrganizationSessionDep,
    quantity: Annotated[items.Positive | None, Query()] = None,
) -> RecipeRead:
    """A recipe: as one batch, or scaled to make ``quantity`` of its output."""
    return await _read_one(db, recipe_id, quantity)


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
    recipe = Recipe(
        variation_id=body.variation_id,
        base_id=body.base_id,
        product_id=body.product_id,
        batch_quantity=body.batch_quantity,
        minutes=body.minutes,
    )
    try:
        await recipes.save(db, recipe, _lines(body))
    except RecipeRefused as refused:
        raise _refused(refused) from None
    await db.commit()
    return await _read_one(db, recipe.id)


@router.put("/{recipe_id}", responses=_CHANGE)
async def update_recipe(recipe_id: int, body: RecipeWrite, db: WriteSessionDep) -> RecipeRead:
    recipe = await found(db, Recipe, recipe_id)
    recipe.batch_quantity = body.batch_quantity
    recipe.minutes = body.minutes
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
