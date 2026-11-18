"""Stocksmith's CSV exports brought into Hoard: its materials export, and
optionally its recipes export, with its manufactures export read only to tell
products from bases.

**Materials.** Each row is a material: its category, made if missing; its
description as its notes; its units through ``hoard.units`` (Stocksmith's
``item`` is each); its purchase conversion, which Hoard keeps only for units
that don't convert by themselves; its low stock limit when above zero; its
preferred vendor as its supplier, the community contact of that name or a new
one; and its zone, section and position as its bin at the main location. With
a day chosen, its stock on hand starts at the main location at its actual unit
cost, dated that day, where it holds none there yet.

**Recipes.** The export has a header row per recipe and its lines after it,
each naming a material, a base or a product by name, in that item's tracking
unit. A recipe with a ``Variation ID``, or one the manufactures export made
under a SKU, is a product variation's, written by amount: the product is a
Made product named by ``Product Name``, its variations gathered by that name,
each with its SKU and its options read from ``Size: Full Size``. Any other
recipe is a base's, written by ratio: each line's amount its parts, adding up
to the ingredients total, and ``Batch Quantity`` what the batch makes. A base
is measured in grams when every line is weighed, and in each otherwise. A
base the manufactures export shows Stocksmith made by itself is made when a
run is short of it.

**Running it again** matches what exists before it makes anything: materials,
bases and products by name ignoring case, variations by SKU, suppliers by
contact name. A matched record is left as it is, and a matched output that
has a recipe keeps it.

Everything is made through Hoard's own write paths, which check it as they
check what a page sends. A row they refuse is rolled back alone and listed
with its file and row, and the import carries on.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any

from fastapi import status
from pydantic import BaseModel
from sqlalchemy import func
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from hoard import plugin_manifest, recipes, stock, summary, units
from hoard.api import bases, contacts, items, materials, products
from hoard.api.deps import HoardError
from hoard.imports.rows import COLUMN_MISSING, FILE_UNREADABLE, Row, read, stepper
from hoard.imports.rows import key as _key
from hoard.records import (
    PLACES,
    AutoManufacture,
    Base,
    Category,
    CategoryKind,
    ItemType,
    Location,
    Material,
    Product,
    Recipe,
    TypeShape,
    Unit,
    Variation,
)
from seneschal_core.api import refuse
from seneschal_core.db import WriteSession
from seneschal_core.imports import (
    Checked,
    ImportCheck,
    ImportCount,
    Importer,
    ImportFile,
    ImportProblem,
    ImportResult,
    ImportStep,
)

#: The files the importer takes, by their fields' names.
MATERIALS, RECIPES, MANUFACTURES = "materials", "recipes", "manufactures"

#: The columns each file is read by.
MATERIAL_COLUMNS = (
    "name",
    "category",
    "description",
    "zone",
    "section",
    "position",
    "inventoriable",
    "stock_on_hand",
    "tracking_unit",
    "purchase_unit",
    "purchase_to_tracking_conversion",
    "actual_unit_cost",
    "low_stock_limit",
    "preferred_vendor",
)
RECIPE_COLUMNS = (
    "Recipe ID",
    "Product SKU",
    "Product Name",
    "Variation Name",
    "Batch Quantity",
    "Manufacturing Minutes",
    "Recipe Notes",
    "Material Name",
    "Material Quantity",
    "Variation ID",
)
MANUFACTURE_COLUMNS = ("Product", "Variation", "SKU", "Auto Manufactured?")

#: Stocksmith's names of units that ``hoard.units`` doesn't find them by.
UNIT_NAMES = {"item": "each", "items": "each"}
#: What Stocksmith calls the variation of a product that has only one.
DEFAULT_VARIATION = "Default variation"
#: What a variation's options are read as when its name holds no option's name.
OPTION = "Variation"


class StocksmithProblem(StrEnum):
    """What a check found that stops the import: codes pages translate."""

    #: The file isn't the export it was given as: it lacks a column.
    COLUMN_MISSING = COLUMN_MISSING
    #: The file can't be read as a CSV export.
    FILE_UNREADABLE = FILE_UNREADABLE
    #: A unit Hoard doesn't know, which an admin adds in Settings › Units.
    UNIT_UNKNOWN = "UNIT_UNKNOWN"
    #: A recipe line names no material, base or product, in either file or in Hoard.
    LINE_NAMES_NOTHING = "LINE_NAMES_NOTHING"
    #: A recipe names one item on two lines.
    LINE_REPEATED = "LINE_REPEATED"
    #: A second recipe for one base or variation.
    RECIPE_REPEATED = "RECIPE_REPEATED"
    #: A recipe that would contain itself, directly or through a base.
    RECIPE_CONTAINS_ITSELF = "RECIPE_CONTAINS_ITSELF"


class StocksmithOptions(BaseModel):
    #: The day stock on hand is brought in as of; none leaves stock behind.
    stock_dated: date | None = None


#: The kinds a check counts and an import makes, in the order pages list them.
KINDS = (
    "materials",
    "categories",
    "suppliers",
    "stock",
    "bases",
    "products",
    "variations",
    "recipes",
)


def _decimal(text: str) -> Decimal | None:
    try:
        number = Decimal(text)
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


def _positive(text: str) -> Decimal | None:
    """A number above zero, kept to the places Hoard keeps; None otherwise."""
    number = _decimal(text)
    if number is None or number <= 0:
        return None
    kept = number.quantize(PLACES)
    return kept if kept > 0 else None


def _rows(
    name: str, content: bytes | None, columns: tuple[str, ...], problems: list[ImportProblem]
) -> list[tuple[int, Row]] | None:
    """A file's rows, or None when it is absent or isn't the export it was given as."""
    found = read(name, content, columns, problems)
    return None if found is None else found[1]


def _options(variation: str) -> dict[str, str]:
    """A variation's options from its name: ``Size: Full Size`` is the option
    Size with the value Full Size, and the default variation has none."""
    if not variation or variation.lower() == DEFAULT_VARIATION.lower():
        return {}
    option, colon, value = variation.partition(":")
    if colon and option.strip() and value.strip():
        return {option.strip(): value.strip()}
    return {OPTION: variation}


@dataclass
class _Held:
    """What Hoard holds already, read once for the check."""

    #: The organization's own units, by name.
    own_units: dict[str, Unit]
    units: dict[int, Unit]
    #: Materials and bases by name: their ids and tracking units.
    materials: dict[str, tuple[int, int]]
    bases: dict[str, tuple[int, int]]
    products: dict[str, int]
    #: Variations by SKU, and each product's variations' options.
    skus: dict[str, int]
    options: dict[int, list[tuple[int, dict[str, str]]]]
    categories: set[str]
    #: The outputs that have a recipe.
    made: set[int]
    #: The items holding stock at the main location.
    stocked: set[int]


async def _held(db: AsyncSession) -> _Held:
    held_units = await units.load(db)
    variations = (
        await db.exec(
            select(Variation.id, Variation.product_id, Variation.sku, Variation.attributes)
        )
    ).all()
    options: dict[int, list[tuple[int, dict[str, str]]]] = {}
    for variation_id, product_id, _, attributes in variations:
        options.setdefault(product_id, []).append((variation_id, attributes))
    materials_held = {
        _key(name): (item_id, unit_id)
        for item_id, name, unit_id in await db.exec(
            select(Material.id, Material.name, Material.tracking_unit_id)
        )
    }
    main = (await db.exec(select(Location.id).where(col(Location.main)))).first()
    held_stock = await summary.stock(db, [item_id for item_id, _ in materials_held.values()])
    made = set()
    for variation_id, base_id in await db.exec(select(Recipe.variation_id, Recipe.base_id)):
        made |= {variation_id or 0, base_id or 0}
    return _Held(
        own_units={_key(unit.name): unit for unit in held_units.values() if not unit.standard},
        units=held_units,
        materials=materials_held,
        bases={
            _key(name): (item_id, unit_id)
            for item_id, name, unit_id in await db.exec(
                select(Base.id, Base.name, Base.tracking_unit_id)
            )
        },
        products={
            _key(name): product_id
            for product_id, name in await db.exec(select(Product.id, Product.name))
        },
        skus={sku: variation_id for variation_id, _, sku, _ in variations if sku},
        options=options,
        categories={
            _key(name)
            for name in await db.exec(
                select(Category.name).where(Category.kind == CategoryKind.MATERIAL)
            )
        },
        made=made,
        stocked={
            item_id
            for item_id, at in held_stock.items()
            if main is not None and at.get(main, summary.Stock()).on_hand != 0
        },
    )


@dataclass
class _Recipe:
    """A recipe as the export holds it."""

    row: int
    #: A base's name, or the product's a variation is of.
    name: str
    base: bool
    sku: str | None
    options: dict[str, str]
    batch_quantity: Decimal
    minutes: int | None
    notes: str | None
    #: Each line's row, the name it gives, and its amount.
    lines: list[tuple[int, str, Decimal]] = field(default_factory=list)


@dataclass
class _Plan:
    """The plan a check makes, as it makes it."""

    held: _Held
    problems: list[ImportProblem] = field(default_factory=list)
    steps: list[dict[str, Any]] = field(default_factory=list)
    counts: dict[str, list[int]] = field(default_factory=dict)
    #: The measure each material in the file is tracked in, by name.
    measures: dict[str, units.Measure | None] = field(default_factory=dict)

    def count(self, kind: str, exists: bool) -> None:
        self.counts.setdefault(kind, [0, 0])[1 if exists else 0] += 1


def _unit(plan: _Plan, name: str, row: int) -> dict[str, str] | None:
    """The unit a material's column names: a standard unit by its key, or one
    of the organization's own by its name. One Hoard doesn't know is a problem."""
    key = UNIT_NAMES.get(_key(name)) or units.match(name)
    if key is not None:
        return {"standard": key}
    own = plan.held.own_units.get(_key(name))
    if own is not None:
        return {"own": own.name}
    plan.problems.append(
        ImportProblem(code=StocksmithProblem.UNIT_UNKNOWN, file=MATERIALS, row=row, name=name)
    )
    return None


def _plan_materials(
    plan: _Plan, rows: list[tuple[int, Row]], stock_dated: date | None, sees_costs: bool
) -> tuple[list[tuple[str, int]], list[tuple[str, int]]]:
    """A step per material, and the suppliers and categories they name, each
    with the first row naming it."""
    suppliers: dict[str, tuple[str, int]] = {}
    categories: dict[str, tuple[str, int]] = {}
    for row, record in rows:
        name = record["name"]
        if not name or _key(name) in plan.measures:
            continue
        tracking = _unit(plan, record["tracking_unit"], row)
        bought = (
            None
            if _key(record["purchase_unit"]) in {"", _key(record["tracking_unit"])}
            else _unit(plan, record["purchase_unit"], row)
        )
        plan.measures[_key(name)] = (
            units.STANDARD[tracking["standard"]].measure
            if tracking is not None and "standard" in tracking
            else None
        )
        if record["preferred_vendor"]:
            suppliers.setdefault(
                _key(record["preferred_vendor"]), (record["preferred_vendor"], row)
            )
        if record["category"]:
            categories.setdefault(_key(record["category"]), (record["category"], row))
        held = plan.held.materials.get(_key(name))
        plan.count("materials", held is not None)
        inventoriable = record["inventoriable"].lower() not in {"no", "false", "0"}
        quantity = _positive(record["stock_on_hand"])
        held_stock = None
        if stock_dated is not None and inventoriable and quantity is not None:
            plan.count("stock", held is not None and held[0] in plan.held.stocked)
            held_stock = {
                "quantity": str(quantity),
                "unit_cost": str(_positive(record["actual_unit_cost"]) or Decimal(0)),
                "dated_on": stock_dated.isoformat(),
                "sees_costs": sees_costs,
            }
        bin = {part: record[part] for part in ("zone", "section", "position") if record[part]}
        conversion = _positive(record["purchase_to_tracking_conversion"])
        limit = _positive(record["low_stock_limit"])
        plan.steps.append(
            {
                "step": "materials",
                "file": MATERIALS,
                "row": row,
                "name": name,
                "category": record["category"] or None,
                "notes": record["description"] or None,
                "tracking_unit": tracking,
                "purchase_unit": bought,
                "purchase_conversion": str(conversion or Decimal(1)),
                "low_stock_limit": None if limit is None else str(limit),
                "supplier": record["preferred_vendor"] or None,
                "bin": bin or None,
                "inventoriable": inventoriable,
                "stock": held_stock,
            }
        )
    return list(suppliers.values()), list(categories.values())


def _recipes(
    rows: list[tuple[int, Row]], manufactured: dict[str, list[tuple[str, str]]]
) -> list[_Recipe]:
    """Each recipe in the export, its header row first and its lines after it."""
    found: dict[str, _Recipe] = {}
    for row, record in rows:
        recipe = found.get(record["Recipe ID"])
        if recipe is None:
            name = record["Product Name"]
            variation, sku = record["Variation Name"], record["Product SKU"] or None
            made = manufactured.get(_key(name), [])
            base = not record["Variation ID"] and not made
            if not record["Variation ID"] and made:
                variation, sku = next(
                    ((named, made_sku) for named, made_sku in made if made_sku == sku), made[0]
                )
            minutes = _decimal(record["Manufacturing Minutes"])
            recipe = found[record["Recipe ID"]] = _Recipe(
                row=row,
                name=name,
                base=base,
                sku=None if base else sku,
                options={} if base else _options(variation),
                batch_quantity=_positive(record["Batch Quantity"]) or Decimal(1),
                minutes=int(minutes) if minutes else None,
                notes=record["Recipe Notes"] or None,
            )
        amount = _positive(record["Material Quantity"])
        if record["Material Name"] and amount is not None:
            recipe.lines.append((row, record["Material Name"], amount))
    return list(found.values())


def _output(recipe: _Recipe) -> tuple[str, ...]:
    """What a recipe makes, as two recipes for one output are told by."""
    if recipe.base:
        return ("base", _key(recipe.name))
    if recipe.sku:
        return ("sku", recipe.sku)
    return (
        "options",
        _key(recipe.name),
        *(f"{name}={value}" for name, value in sorted(recipe.options.items())),
    )


def _plan_recipes(plan: _Plan, found: list[_Recipe], auto_made: set[str]) -> None:
    """A step per base, per product with its variations, and per recipe. A
    base ``auto_made`` names is made when a run is short of it."""
    kept: list[_Recipe] = []
    seen: set[tuple[str, ...]] = set()
    for recipe in found:
        if _output(recipe) in seen:
            plan.problems.append(
                ImportProblem(
                    code=StocksmithProblem.RECIPE_REPEATED,
                    file=RECIPES,
                    row=recipe.row,
                    name=recipe.name,
                )
            )
            continue
        seen.add(_output(recipe))
        kept.append(recipe)
    made_bases = {_key(recipe.name): recipe for recipe in kept if recipe.base}
    made_products = {_key(recipe.name) for recipe in kept if not recipe.base}
    held = plan.held

    def known(name: str) -> bool:
        key = _key(name)
        return any(
            key in names
            for names in (
                plan.measures,
                made_bases,
                made_products,
                held.materials,
                held.bases,
                held.products,
            )
        )

    # What each base and product is made from, by name, to find a recipe
    # that would contain itself.
    made_from: dict[str, set[str]] = {}
    for recipe in kept:
        made_from.setdefault(_key(recipe.name), set()).update(
            _key(name) for _, name, _ in recipe.lines
        )

    def contains_itself(recipe: _Recipe) -> bool:
        reached, seen_names = {_key(name) for _, name, _ in recipe.lines}, set()
        while reached:
            if _key(recipe.name) in reached:
                return True
            seen_names |= reached
            reached = {name for each in reached for name in made_from.get(each, set())} - seen_names
        return False

    weighed: dict[str, bool] = {}

    def by_weight(name: str, visiting: frozenset[str] = frozenset()) -> bool:
        """Whether an item is measured by weight: a material or base tracked
        in a unit of mass, or a base in the file whose every line is."""
        key = _key(name)
        if key in plan.measures:
            return plan.measures[key] is units.Measure.MASS
        if key in made_bases and key not in held.bases:
            if key not in weighed:
                lines = made_bases[key].lines
                weighed[key] = (
                    key not in visiting
                    and bool(lines)
                    and all(by_weight(line, visiting | {key}) for _, line, _ in lines)
                )
            return weighed[key]
        for named in (held.materials, held.bases):
            if key in named:
                return units.measure(held.units.get(named[key][1])) is units.Measure.MASS
        return False

    products_planned: dict[str, dict[str, Any]] = {}
    variations_planned: dict[str, list[dict[str, Any]]] = {}
    recipe_steps: list[dict[str, Any]] = []
    for recipe in kept:
        named: set[str] = set()
        for row, name, _ in recipe.lines:
            if not known(name):
                plan.problems.append(
                    ImportProblem(
                        code=StocksmithProblem.LINE_NAMES_NOTHING, file=RECIPES, row=row, name=name
                    )
                )
            elif _key(name) in named:
                plan.problems.append(
                    ImportProblem(
                        code=StocksmithProblem.LINE_REPEATED, file=RECIPES, row=row, name=name
                    )
                )
            named.add(_key(name))
        if contains_itself(recipe):
            plan.problems.append(
                ImportProblem(
                    code=StocksmithProblem.RECIPE_CONTAINS_ITSELF,
                    file=RECIPES,
                    row=recipe.row,
                    name=recipe.name,
                )
            )
        if recipe.base:
            base = held.bases.get(_key(recipe.name))
            plan.count("bases", base is not None)
            plan.count("recipes", base is not None and base[0] in held.made)
            plan.steps.append(
                {
                    "step": "bases",
                    "file": RECIPES,
                    "row": recipe.row,
                    "name": recipe.name,
                    "unit": "gram" if by_weight(recipe.name) else "each",
                    "auto_manufacture": AutoManufacture.OUT_OF_STOCK
                    if _key(recipe.name) in auto_made
                    else None,
                }
            )
            output: dict[str, Any] = {"base": recipe.name}
        else:
            product = products_planned.get(_key(recipe.name))
            if product is None:
                product = products_planned[_key(recipe.name)] = {
                    "step": "products",
                    "file": RECIPES,
                    "row": recipe.row,
                    "name": recipe.name,
                    "variations": variations_planned.setdefault(_key(recipe.name), []),
                }
                plan.count("products", _key(recipe.name) in held.products)
            variation = {"sku": recipe.sku, "options": recipe.options}
            variations_planned[_key(recipe.name)].append(variation)
            variation_id = _held_variation(held, recipe.name, variation)
            plan.count("variations", variation_id is not None)
            plan.count("recipes", variation_id in held.made)
            output = {"product": recipe.name, **variation}
        recipe_steps.append(
            {
                "step": "recipes",
                "file": RECIPES,
                "row": recipe.row,
                "output": output,
                "by_ratio": recipe.base,
                "batch_quantity": str(recipe.batch_quantity),
                "minutes": recipe.minutes,
                "notes": recipe.notes,
                "lines": [
                    {"name": name, "written": str(amount)} for _, name, amount in recipe.lines
                ],
            }
        )
    plan.steps.extend(products_planned.values())
    plan.steps.extend(recipe_steps)


def _held_variation(held: _Held, product: str, variation: Mapping[str, Any]) -> int | None:
    """The variation Hoard holds already: by its SKU, or, without one, by its
    product's name and its options."""
    if variation["sku"]:
        return held.skus.get(variation["sku"])
    product_id = held.products.get(_key(product))
    return next(
        (
            variation_id
            for variation_id, options in held.options.get(product_id or 0, [])
            if options == variation["options"]
        ),
        None,
    )


async def check(request: ImportCheck) -> Checked:
    """The plan of what Stocksmith's exports make, with what exists already
    and the problems that stop it. Nothing is written."""
    options: StocksmithOptions = request.options
    plan = _Plan(held=await _held(request.db))
    material_rows = _rows(MATERIALS, request.files.get(MATERIALS), MATERIAL_COLUMNS, plan.problems)
    recipe_rows = _rows(RECIPES, request.files.get(RECIPES), RECIPE_COLUMNS, plan.problems)
    manufacture_rows = _rows(
        MANUFACTURES, request.files.get(MANUFACTURES), MANUFACTURE_COLUMNS, plan.problems
    )
    plan.steps.append({"step": "prepare"})
    material_steps = len(plan.steps)
    suppliers, categories = _plan_materials(
        plan,
        material_rows or [],
        options.stock_dated,
        plugin_manifest.SEE_COSTS in request.session.permissions,
    )
    material_plan = plan.steps[material_steps:]
    del plan.steps[material_steps:]
    if suppliers:
        held_contacts = {
            _key(contact.name)
            for contact in await contacts.community_contacts(
                request.plugin, request.organization.community_ref
            )
        }
        for name, row in suppliers:
            plan.count("suppliers", _key(name) in held_contacts)
            plan.steps.append({"step": "suppliers", "file": MATERIALS, "row": row, "name": name})
    for name, row in categories:
        plan.count("categories", _key(name) in plan.held.categories)
        plan.steps.append({"step": "categories", "file": MATERIALS, "row": row, "name": name})
    plan.steps.extend(material_plan)
    manufactured: dict[str, list[tuple[str, str]]] = {}
    # What Stocksmith made by itself, for a run that needed it.
    auto_made: set[str] = set()
    for _, record in manufacture_rows or []:
        if record["Auto Manufactured?"].lower() == "yes":
            auto_made.add(_key(record["Product"]))
        if record["SKU"]:
            made = manufactured.setdefault(_key(record["Product"]), [])
            if (record["Variation"], record["SKU"]) not in made:
                made.append((record["Variation"], record["SKU"]))
    _plan_recipes(plan, _recipes(recipe_rows or [], manufactured), auto_made)
    return Checked(
        plan=plan.steps,
        counts=[
            ImportCount(kind=kind, new=plan.counts[kind][0], unchanged=plan.counts[kind][1])
            for kind in KINDS
            if kind in plan.counts
        ],
        problems=plan.problems,
    )


# The steps of a plan, each matching before it makes.


async def _named(
    db: AsyncSession, model: type[Material] | type[Base] | type[Product], name: str
) -> int | None:
    """The id of the material, base or product named ``name``, ignoring case."""
    found = await db.exec(
        select(model.id)
        .where(func.lower(col(model.name)) == _key(name))
        .order_by(col(model.id))
        .limit(1)
    )
    return found.first()


async def _unit_of(db: AsyncSession, ref: Mapping[str, str]) -> int:
    """The id of a unit a step names: standard by its key, or the organization's own by name."""
    query = (
        select(Unit.id).where(Unit.standard == ref["standard"])
        if "standard" in ref
        else select(Unit.id).where(func.lower(col(Unit.name)) == _key(ref["own"]))
    )
    unit_id = (await db.exec(query)).first()
    if unit_id is None:
        raise refuse(status.HTTP_422_UNPROCESSABLE_CONTENT, StocksmithProblem.UNIT_UNKNOWN)
    return unit_id


async def _contacts(run: ImportStep) -> dict[str, int]:
    """The community's contacts by name, read once a run."""
    if "contacts" not in run.memo:
        run.memo["contacts"] = {
            _key(contact.name): contact.id
            for contact in await contacts.community_contacts(
                run.job.plugin, run.organization.community_ref
            )
        }
    return run.memo["contacts"]


async def _prepare(run: ImportStep, _step: dict[str, Any]) -> ImportResult:
    """What the steps after it find: the standard units, the main location
    and each kind's default type."""
    await units.add_standard(run.db)
    await stock.main_location(run.db)
    await stock.add_default_types(run.db)
    return ImportResult()


async def _supplier(run: ImportStep, step: dict[str, Any]) -> ImportResult:
    held = await _contacts(run)
    if _key(step["name"]) in held:
        return ImportResult(matched={"suppliers": 1})
    contact = await contacts.add_contact(
        run.job.plugin, run.organization.community_ref, contacts.ContactCreate(name=step["name"])
    )
    held[_key(contact.name)] = contact.id
    return ImportResult(created={"suppliers": 1})


async def _category_id(db: WriteSession, name: str) -> int | None:
    found = await db.exec(
        select(Category.id).where(
            Category.kind == CategoryKind.MATERIAL, func.lower(col(Category.name)) == _key(name)
        )
    )
    return found.first()


async def _category(run: ImportStep, step: dict[str, Any]) -> ImportResult:
    if await _category_id(run.db, step["name"]) is not None:
        return ImportResult(matched={"categories": 1})
    run.db.add(
        Category(
            organization_id=run.db.organization_id, kind=CategoryKind.MATERIAL, name=step["name"]
        )
    )
    await run.db.flush()
    return ImportResult(created={"categories": 1})


async def _material(run: ImportStep, step: dict[str, Any]) -> ImportResult:
    """A material, made unless one of its name exists, and its starting stock
    where it holds none at the main location."""
    db, made = run.db, ImportResult(matched={"materials": 1})
    material_id = await _named(db, Material, step["name"])
    if material_id is None:
        tracking = await _unit_of(db, step["tracking_unit"])
        bought = (
            None if step["purchase_unit"] is None else await _unit_of(db, step["purchase_unit"])
        )
        category = None if step["category"] is None else await _category_id(db, step["category"])
        supplier = (
            None if step["supplier"] is None else (await _contacts(run)).get(_key(step["supplier"]))
        )
        material = await materials.add_material(
            db,
            materials.MaterialCreate(
                name=step["name"],
                inventoriable=step["inventoriable"],
                tracking_unit_id=tracking,
                purchase_unit_id=None if bought == tracking else bought,
                purchase_conversion=Decimal(step["purchase_conversion"]),
                low_stock_limit=step["low_stock_limit"],
                category_id=category,
                supplier_contact_id=supplier,
                notes=step["notes"],
                start=None
                if step["bin"] is None
                else items.ItemStart(bin=items.ItemBin.model_validate(step["bin"])),
            ),
            sees_costs=False,
        )
        material_id, made = material.id, ImportResult(created={"materials": 1})
    starting = step["stock"]
    if starting is None:
        return made
    material = await db.get_one(Material, material_id)
    main = await stock.main_location(db)
    if not material.inventoriable or await stock.on_hand(db, material_id, main.id) != 0:
        return made
    await items.start(
        db,
        material_id,
        items.ItemStart(
            location_id=main.id,
            quantity=Decimal(starting["quantity"]),
            unit_cost=Decimal(starting["unit_cost"]),
        ),
        starting["sees_costs"],
        dated_on=date.fromisoformat(starting["dated_on"]),
    )
    made.created["stock"] = 1
    return made


async def _base(run: ImportStep, step: dict[str, Any]) -> ImportResult:
    if await _named(run.db, Base, step["name"]) is not None:
        return ImportResult(matched={"bases": 1})
    await bases.add_base(
        run.db,
        bases.BaseCreate(
            name=step["name"],
            tracking_unit_id=await _unit_of(run.db, {"standard": step["unit"]}),
            auto_manufacture=step.get("auto_manufacture"),
        ),
        sees_costs=False,
    )
    return ImportResult(created={"bases": 1})


async def _made_type(db: WriteSession) -> int:
    """The product type a Made product takes: the default type when it is
    drawn as one, else the first type that is."""
    found = await db.exec(
        select(ItemType.id)
        .where(ItemType.kind == CategoryKind.PRODUCT, ItemType.shape == TypeShape.MADE)
        .order_by(col(ItemType.is_default).desc(), col(ItemType.id))
    )
    return found.first() or await stock.default_type(db, CategoryKind.PRODUCT)


async def _variation(db: AsyncSession, product: str, variation: Mapping[str, Any]) -> int | None:
    """The variation a step names: by its SKU, or by its product's name and its options."""
    if variation["sku"]:
        found = await db.exec(
            select(Variation.id)
            .where(Variation.sku == variation["sku"])
            .order_by(col(Variation.id))
        )
        return found.first()
    product_id = await _named(db, Product, product)
    held = await db.exec(
        select(Variation.id, Variation.attributes)
        .where(Variation.product_id == (product_id or 0))
        .order_by(col(Variation.id))
    )
    return next(
        (variation_id for variation_id, options in held if options == variation["options"]), None
    )


async def _product(run: ImportStep, step: dict[str, Any]) -> ImportResult:
    """A Made product with its variations, each made unless it exists."""
    db, made = run.db, ImportResult()
    product_id = await _named(db, Product, step["name"])
    spare: Variation | None = None
    if product_id is None:
        product, spare = await products.add_product(
            db, products.ProductWrite(name=step["name"], type_id=await _made_type(db)), False
        )
        product_id = product.id
    _counted(made, "products", spare is not None)
    for variation in step["variations"]:
        if await _variation(db, step["name"], variation) is not None:
            _counted(made, "variations", False)
            continue
        await products.write_variation(
            db,
            spare or Variation(product_id=product_id),
            products.ProductVariationWrite(attributes=variation["options"], sku=variation["sku"]),
            False,
        )
        spare = None
        _counted(made, "variations", True)
    return made


def _counted(result: ImportResult, kind: str, created: bool) -> None:
    """Count one record of ``kind`` as made, or as matched."""
    counted = result.created if created else result.matched
    counted[kind] = counted.get(kind, 0) + 1


async def _line_item(db: AsyncSession, name: str) -> int:
    """The item a recipe line names: a material, a base, or a product's default variation."""
    for model in (Material, Base):
        item_id = await _named(db, model, name)
        if item_id is not None:
            return item_id
    product_id = await _named(db, Product, name)
    variation_id = (
        await db.exec(
            select(Variation.id)
            .where(Variation.product_id == (product_id or 0))
            .order_by(col(Variation.is_default).desc(), col(Variation.id))
        )
    ).first()
    if variation_id is None:
        raise refuse(status.HTTP_404_NOT_FOUND, HoardError.NOT_FOUND)
    return variation_id


async def _ratio(db: AsyncSession, recipe: Recipe, lines: list[recipes.Line]) -> None:
    """A base's recipe by ratio: its lines' amounts are their parts, and the
    ingredients total adds up those measured like the base, in its unit; or,
    with none, those measured like the first line that has a unit. With no
    line that has a unit, it stays written by amount."""
    known = await units.load(db)
    tracked = await units.tracking_units(db, [line.item_id for line in lines])
    written = [
        units.RecipeLine(line.written, unit, unit)
        for line in lines
        for unit in [known.get(tracked.get(line.item_id, 0))]
    ]
    base = await db.get_one(Base, recipe.base_id)
    for unit in (known.get(base.tracking_unit_id), *(line.unit for line in written)):
        total = None if unit is None else units.measured(written, unit)
        if unit is not None and total:
            recipe.by_ratio, recipe.total, recipe.total_unit_id = True, units.kept(total), unit.id
            return


async def _recipe(run: ImportStep, step: dict[str, Any]) -> ImportResult:
    """A base's or a variation's recipe, unless it has one."""
    db, output = run.db, step["output"]
    if "base" in output:
        output_id = await _named(db, Base, output["base"])
        recipe = Recipe(base_id=output_id)
        held = select(Recipe.id).where(Recipe.base_id == output_id)
    else:
        output_id = await _variation(db, output["product"], output)
        recipe = Recipe(variation_id=output_id)
        held = select(Recipe.id).where(Recipe.variation_id == output_id)
    if output_id is None:
        raise refuse(status.HTTP_404_NOT_FOUND, HoardError.NOT_FOUND)
    if (await db.exec(held)).first() is not None:
        return ImportResult(matched={"recipes": 1})
    lines = [
        recipes.Line(await _line_item(db, line["name"]), Decimal(line["written"]))
        for line in step["lines"]
    ]
    recipe.batch_quantity = Decimal(step["batch_quantity"])
    recipe.minutes = step["minutes"]
    recipe.notes = step["notes"]
    if step["by_ratio"]:
        await _ratio(db, recipe, lines)
    await recipes.save(db, recipe, lines)
    return ImportResult(created={"recipes": 1})


IMPORTER = Importer(
    id="stocksmith",
    files=(
        ImportFile(MATERIALS),
        ImportFile(RECIPES, required=False),
        ImportFile(MANUFACTURES, required=False),
    ),
    options=StocksmithOptions,
    check=check,
    step=stepper(
        {
            "prepare": _prepare,
            "suppliers": _supplier,
            "categories": _category,
            "materials": _material,
            "bases": _base,
            "products": _product,
            "recipes": _recipe,
        }
    ),
)
