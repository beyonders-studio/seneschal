"""Declarations: what an item, a product, a lot or a production run declares,
rolled up through recipes to the materials.

A material declares its ingredient name and the allergens its supplier lists;
a lot of it declares its own allergens where they are known, and otherwise
its material's. A base or a variation declares what its recipe's lines do,
each in proportion to how much of it one unit takes. A bundle product declares
what its recipe's variations do, and any other product what its variations do
together. A lot a production run made declares what the run consumed, lot by
lot, and so does the run.

Ingredients are listed by weight, the heaviest first: each line's quantity is
taken as a weight, as a recipe made by weight is. Allergens are the keys of
:class:`~hoard.records.Allergen`, the US's nine and the EU's fourteen. A
material whose allergens nobody has recorded is listed as undeclared, so a
declaration never reads as free of what it was never checked for.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal

from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from hoard.events import item_name
from hoard.records import (
    Allergen,
    Item,
    ItemKind,
    Lot,
    Material,
    Product,
    ProductionRun,
    ProductionRunLine,
    Recipe,
    RecipeLine,
    Variation,
)

#: The allergens US law names: the "major food allergens".
US_ALLERGENS = frozenset(
    {
        Allergen.CRUSTACEANS,
        Allergen.EGGS,
        Allergen.FISH,
        Allergen.MILK,
        Allergen.PEANUTS,
        Allergen.SESAME,
        Allergen.SOYBEANS,
        Allergen.TREE_NUTS,
        Allergen.WHEAT,
    }
)
#: The allergens EU law names, with wheat among the cereals containing gluten.
EU_ALLERGENS = frozenset(set(Allergen) - {Allergen.WHEAT})


@dataclass(frozen=True)
class Declaration:
    #: What it is made of, the heaviest first.
    ingredients: list[str]
    #: The allergens it contains, in :class:`Allergen`'s order.
    allergens: list[Allergen]
    #: The materials in it whose allergens are not recorded, by name.
    undeclared: list[str]


@dataclass
class _Mix:
    """What one unit of something holds: each ingredient's weight, its
    allergens, and the materials whose allergens are unknown."""

    weights: dict[str, Decimal] = field(default_factory=lambda: defaultdict(Decimal))
    allergens: set[str] = field(default_factory=set)
    undeclared: set[str] = field(default_factory=set)

    def add(self, other: _Mix, scale: Decimal) -> None:
        for ingredient, weight in other.weights.items():
            self.weights[ingredient] += weight * scale
        self.allergens |= other.allergens
        self.undeclared |= other.undeclared

    def declaration(self) -> Declaration:
        return Declaration(
            ingredients=[
                name
                for name, _ in sorted(self.weights.items(), key=lambda entry: (-entry[1], entry[0]))
            ],
            allergens=[allergen for allergen in Allergen if allergen in self.allergens],
            undeclared=sorted(self.undeclared),
        )


def _material(material: Material, allergens: list[str] | None) -> _Mix:
    mix = _Mix()
    mix.weights[material.ingredient_name or material.name] = Decimal(1)
    if allergens is None:
        mix.undeclared.add(material.name)
    else:
        mix.allergens.update(allergens)
    return mix


class Declarations:
    """Rolls declarations up within one session, reading each item's once."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self._items: dict[int, _Mix] = {}

    async def _recipe(self, recipe: Recipe, seen: frozenset[int]) -> _Mix:
        mix = _Mix()
        lines = await self.db.exec(select(RecipeLine).where(RecipeLine.recipe_id == recipe.id))
        for line in lines.all():
            mix.add(await self._item(line.item_id, seen), line.quantity / recipe.batch_quantity)
        return mix

    async def _item(self, item_id: int, seen: frozenset[int] = frozenset()) -> _Mix:
        """One unit of an item, as its recipe makes it."""
        if item_id in self._items:
            return self._items[item_id]
        item = await self.db.get(Item, item_id)
        mix = _Mix()
        if item is None or item_id in seen:
            return mix
        if item.kind is ItemKind.MATERIAL:
            material = await self.db.get(Material, item_id)
            if material is not None:
                mix = _material(material, material.allergens)
        else:
            column = Recipe.base_id if item.kind is ItemKind.BASE else Recipe.variation_id
            recipe = (await self.db.exec(select(Recipe).where(column == item_id))).first()
            if recipe is None:
                mix.undeclared.add(await item_name(self.db, item_id))
            else:
                mix = await self._recipe(recipe, seen | {item_id})
        self._items[item_id] = mix
        return mix

    async def _lot(self, lot: Lot) -> _Mix:
        """One unit of a lot: a material's with the lot's own allergens where
        known; one a run made, as the run made it; otherwise its item's."""
        material = await self.db.get(Material, lot.item_id)
        if material is not None:
            known = lot.allergens if lot.allergens is not None else material.allergens
            return _material(material, known)
        run = (
            await self.db.exec(select(ProductionRun).where(ProductionRun.output_lot_id == lot.id))
        ).first()
        if run is not None:
            return await self._run(run)
        return await self._item(lot.item_id)

    async def _run(self, run: ProductionRun) -> _Mix:
        """One unit of what a run made, from the lots its lines consumed."""
        mix = _Mix()
        made = run.actual_quantity or Decimal(1)
        lines = await self.db.exec(
            select(ProductionRunLine)
            .where(ProductionRunLine.run_id == run.id)
            .order_by(col(ProductionRunLine.id))
        )
        for line in lines.all():
            lot = await self.db.get(Lot, line.lot_id) if line.lot_id is not None else None
            consumed = await self._lot(lot) if lot is not None else await self._item(line.item_id)
            mix.add(consumed, line.quantity / made)
        return mix

    async def item(self, item_id: int) -> Declaration:
        """A material's, a base's or a variation's declaration."""
        return (await self._item(item_id)).declaration()

    async def product(self, product: Product) -> Declaration:
        """A product's: a bundle's from what its recipe holds, any other's
        from its variations together."""
        recipe = (await self.db.exec(select(Recipe).where(Recipe.product_id == product.id))).first()
        if recipe is not None:
            return (await self._recipe(recipe, frozenset())).declaration()
        mix = _Mix()
        variations = await self.db.exec(
            select(Variation.id).where(Variation.product_id == product.id)
        )
        for variation_id in variations.all():
            mix.add(await self._item(variation_id), Decimal(1))
        return mix.declaration()

    async def lot(self, lot: Lot) -> Declaration:
        return (await self._lot(lot)).declaration()

    async def run(self, run: ProductionRun) -> Declaration:
        return (await self._run(run)).declaration()
