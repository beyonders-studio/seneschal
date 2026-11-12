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

EU fragrance allergens (:data:`FRAGRANCE_ALLERGENS`) are recorded the same
way, as each one's share of a material or a lot in percent, and roll up by
weight into each one's share of what is made. One is declared once its share
exceeds the threshold for the product: :data:`LEAVE_ON` for a product left on
the skin, :data:`RINSE_OFF` for one washed off (``Product.rinse_off``). A
material, a base or a lot nobody sells as a product takes the leave-on
threshold, the lower. A product with variations declares what any one of them
would, since each is its own product on the shelf.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

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

#: The share, in percent, past which a fragrance allergen is declared in a
#: product left on the skin, and in one washed off.
LEAVE_ON = Decimal("0.001")
RINSE_OFF = Decimal("0.01")

#: The fragrance allergens the EU's cosmetics regulation has declared in the
#: list of ingredients, by key, in the order of its Annex III entries, each
#: named as the list of ingredients names it. Regulation (EU) 2023/1545 added
#: entries 327 to 371 and the condition to the earlier entries it replaced.
FRAGRANCE_ALLERGENS: tuple[str, ...] = (
    # Entries 45 and 67 to 92, declared before the 2023 update.
    "benzyl_alcohol",
    "amyl_cinnamal",
    "cinnamyl_alcohol",
    "citral",
    "eugenol",
    "hydroxycitronellal",
    "isoeugenol",
    "amylcinnamyl_alcohol",
    "benzyl_salicylate",
    "cinnamal",
    "coumarin",
    "geraniol",
    "anise_alcohol",
    "benzyl_cinnamate",
    "farnesol",
    "linalool",
    "benzyl_benzoate",
    "citronellol",
    "hexyl_cinnamal",
    "limonene",
    "methyl_2_octynoate",
    "alpha_isomethyl_ionone",
    "evernia_prunastri_extract",
    "evernia_furfuracea_extract",
    # Earlier entries the 2023 update replaced: 46, 109, 114, 122, 124, 131,
    # 133, 154, 157, 175, 196 and 324.
    "6_methylcoumarin",
    "pinus_mugo",
    "pinus_pumila",
    "cedrus_atlantica_oil_extract",
    "turpentine",
    "alpha_terpinene",
    "terpinolene",
    "myroxylon_pereirae_oil_extract",
    "rose_ketones",
    "3_propylidenephthalide",
    "lippia_citriodora_absolute",
    "methyl_salicylate",
    # Entries 327 to 371.
    "acetyl_cedrene",
    "amyl_salicylate",
    "anethole",
    "benzaldehyde",
    "camphor",
    "beta_caryophyllene",
    "carvone",
    "dimethyl_phenethyl_acetate",
    "hexadecanolactone",
    "hexamethylindanopyran",
    "linalyl_acetate",
    "menthol",
    "trimethylcyclopentenyl_methylisopentenol",
    "salicylaldehyde",
    "santalol",
    "sclareol",
    "terpineol",
    "tetramethyl_acetyloctahydronaphthalenes",
    "trimethylbenzenepropanol",
    "vanillin",
    "cananga_odorata_oil_extract",
    "cinnamomum_cassia_leaf_oil",
    "cinnamomum_zeylanicum_bark_oil",
    "citrus_aurantium_flower_oil",
    "citrus_aurantium_peel_oil",
    "citrus_aurantium_bergamia_peel_oil",
    "citrus_limon_peel_oil",
    "lemongrass_oil",
    "eucalyptus_globulus_oil",
    "eugenia_caryophyllus_oil",
    "jasmine_oil_extract",
    "juniperus_virginiana_oil",
    "laurus_nobilis_leaf_oil",
    "lavandula_oil_extract",
    "mentha_piperita_oil",
    "mentha_viridis_leaf_oil",
    "narcissus_extract",
    "pelargonium_graveolens_flower_oil",
    "pogostemon_cablin_oil",
    "rose_flower_oil_extract",
    "santalum_album_oil",
    "eugenyl_acetate",
    "geranyl_acetate",
    "isoeugenyl_acetate",
    "pinene",
)


@dataclass(frozen=True)
class Declaration:
    #: What it is made of, the heaviest first.
    ingredients: list[str]
    #: The allergens it contains, in :class:`Allergen`'s order.
    allergens: list[Allergen]
    #: The materials in it whose allergens are not recorded, by name.
    undeclared: list[str]
    #: The fragrance allergens past its threshold, in :data:`FRAGRANCE_ALLERGENS`' order.
    fragrance_allergens: list[str] = field(default_factory=list)
    #: Each fragrance allergen in it, with its share in percent.
    fragrance_shares: dict[str, Decimal] = field(default_factory=dict)
    #: The materials in it whose fragrance allergens are not recorded, by name.
    fragrance_undeclared: list[str] = field(default_factory=list)


@dataclass
class _Mix:
    """What one unit of something holds: each ingredient's weight, its
    allergens, each fragrance allergen's weight, and the materials whose
    allergens are unknown."""

    weights: dict[str, Decimal] = field(default_factory=lambda: defaultdict(Decimal))
    allergens: set[str] = field(default_factory=set)
    undeclared: set[str] = field(default_factory=set)
    fragrance: dict[str, Decimal] = field(default_factory=lambda: defaultdict(Decimal))
    fragrance_undeclared: set[str] = field(default_factory=set)

    def add(self, other: _Mix, scale: Decimal) -> None:
        for ingredient, weight in other.weights.items():
            self.weights[ingredient] += weight * scale
        for allergen, weight in other.fragrance.items():
            self.fragrance[allergen] += weight * scale
        self.allergens |= other.allergens
        self.undeclared |= other.undeclared
        self.fragrance_undeclared |= other.fragrance_undeclared

    def shares(self) -> dict[str, Decimal]:
        """Each fragrance allergen's share of the whole, in percent."""
        total = sum(self.weights.values(), Decimal(0))
        if total <= 0:
            return {}
        return {
            allergen: self.fragrance[allergen] / total * 100
            for allergen in FRAGRANCE_ALLERGENS
            if self.fragrance.get(allergen)
        }

    def declaration(
        self, rinse_off: bool = False, shares: dict[str, Decimal] | None = None
    ) -> Declaration:
        shares = self.shares() if shares is None else shares
        threshold = RINSE_OFF if rinse_off else LEAVE_ON
        return Declaration(
            ingredients=[
                name
                for name, _ in sorted(self.weights.items(), key=lambda entry: (-entry[1], entry[0]))
            ],
            allergens=[allergen for allergen in Allergen if allergen in self.allergens],
            undeclared=sorted(self.undeclared),
            fragrance_allergens=[
                allergen for allergen, share in shares.items() if share > threshold
            ],
            fragrance_shares=shares,
            fragrance_undeclared=sorted(self.fragrance_undeclared),
        )


def _fragrance(declared: dict[str, str]) -> dict[str, Decimal] | None:
    """Each known fragrance allergen's weight in one unit, from its share in
    percent; None when a share is not a number from 0 to 100."""
    weights: dict[str, Decimal] = {}
    for allergen, share in declared.items():
        try:
            weight = Decimal(share) / 100
        except (InvalidOperation, TypeError, ValueError):
            return None
        if not weight.is_finite() or not 0 <= weight <= 1:
            return None
        if allergen in FRAGRANCE_ALLERGENS:
            weights[allergen] = weight
    return weights


def _material(
    material: Material, allergens: list[str] | None, fragrance: dict[str, str] | None
) -> _Mix:
    mix = _Mix()
    mix.weights[material.ingredient_name or material.name] = Decimal(1)
    if allergens is None:
        mix.undeclared.add(material.name)
    else:
        mix.allergens.update(allergens)
    weights = None if fragrance is None else _fragrance(fragrance)
    if weights is None:
        mix.fragrance_undeclared.add(material.name)
    else:
        mix.fragrance.update(weights)
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
                mix = _material(material, material.allergens, material.fragrance_allergens)
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
        """One unit of a lot: a material's with the lot's own allergens and
        fragrance allergens where known; one a run made, as the run made it;
        otherwise its item's."""
        material = await self.db.get(Material, lot.item_id)
        if material is not None:
            known = lot.allergens if lot.allergens is not None else material.allergens
            fragrance = (
                lot.fragrance_allergens
                if lot.fragrance_allergens is not None
                else material.fragrance_allergens
            )
            return _material(material, known, fragrance)
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

    async def _rinse_off(self, item_id: int) -> bool:
        """Whether a variation's product is washed off; False for anything else."""
        found = (
            select(Product.rinse_off)
            .join(Variation, col(Variation.product_id) == Product.id)
            .where(Variation.id == item_id)
        )
        return bool((await self.db.exec(found)).first())

    async def item(self, item_id: int) -> Declaration:
        """A material's, a base's or a variation's declaration."""
        return (await self._item(item_id)).declaration(await self._rinse_off(item_id))

    async def product(self, product: Product) -> Declaration:
        """A product's: a bundle's from what its recipe holds, any other's
        from its variations together, each fragrance allergen at its share in
        the variation that holds the most of it."""
        recipe = (await self.db.exec(select(Recipe).where(Recipe.product_id == product.id))).first()
        if recipe is not None:
            return (await self._recipe(recipe, frozenset())).declaration(product.rinse_off)
        mix = _Mix()
        most: dict[str, Decimal] = {}
        variations = await self.db.exec(
            select(Variation.id).where(Variation.product_id == product.id)
        )
        for variation_id in variations.all():
            variation = await self._item(variation_id)
            mix.add(variation, Decimal(1))
            for allergen, share in variation.shares().items():
                most[allergen] = max(most.get(allergen, share), share)
        shares = {allergen: most[allergen] for allergen in FRAGRANCE_ALLERGENS if allergen in most}
        return mix.declaration(product.rinse_off, shares)

    async def lot(self, lot: Lot) -> Declaration:
        return (await self._lot(lot)).declaration(await self._rinse_off(lot.item_id))

    async def run(self, run: ProductionRun) -> Declaration:
        return (await self._run(run)).declaration(await self._rinse_off(run.item_id))
