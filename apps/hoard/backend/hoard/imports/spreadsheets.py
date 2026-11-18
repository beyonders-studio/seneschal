"""Hoard's own catalogue as spreadsheets: four CSV files, ``materials``,
``bases``, ``products`` (a row per variation) and ``recipes`` (a row per
line), which Reports › Export writes and this importer reads back.

**The files.** Each row's first column is Hoard's ``id``. References are
written by name: a category, a type (empty for its kind's default), a unit,
a supplier (the community contact's name). A custom property is a column
headed ``Property: <name>``. A list, such as allergens, is separated by
semicolons; for allergens and fragrance allergens an empty cell is not known
and ``none`` is none. A variation is named by its product's name and its
options (``Size: Full Size; Tin: Red``), with no options for a product's one
variation that has none. What something costs or sells for is written and
read only for a member who sees costs. Where a synced store holds a
variation's price, the store's price is the one written, and the price read
is left as it is.

**Matching.** A row with an ``id`` is that record. Without one, a material,
base or product matches by name ignoring case, and a variation by SKU, then
by its options among its product's; with no match it is new.

**What a row changes.** A column in the file sets its field, an empty cell
clearing it; a column not in the file leaves its field as it is, and a record
not in the file is left as it is. A recipe in the file is replaced by its
rows, in their order. A category or supplier named but not found is made.

**The check** writes nothing: it counts what is new, changed and unchanged by
kind, lists each change field by field, and lists the problems. **The job**
writes through Hoard's own write paths: categories and suppliers, then
materials, bases, products and their variations, then recipes. A product a
synced store lists is written to the store first, as its page writes it.
"""

from __future__ import annotations

import csv
import io
import operator
from collections.abc import Callable, Hashable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any

from fastapi import HTTPException, status
from pydantic import BaseModel
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from hoard import plugin_manifest, recipes, stock, summary, units
from hoard.api import bases, contacts, declarations, items, materials, products, properties
from hoard.api.deps import HoardError
from hoard.api.recipes import RecipeWrite
from hoard.api.settings import found
from hoard.declarations import FRAGRANCE_ALLERGENS
from hoard.imports.rows import (
    COLUMN_MISSING,
    FILE_UNREADABLE,
    Row,
    category,
    category_id,
    community_contacts,
    key,
    named,
    read,
    stepper,
    supplier,
)
from hoard.records import (
    Allergen,
    AutoManufacture,
    Base,
    Category,
    CategoryKind,
    ItemStatus,
    ItemType,
    Material,
    Product,
    PropertyDefinition,
    PropertyType,
    PropertyValue,
    Recipe,
    RecipeLine,
    RecordKind,
    Unit,
    Variation,
)
from seneschal_core.api import Plugin, refuse
from seneschal_core.imports import (
    Checked,
    ImportChange,
    ImportCheck,
    ImportCount,
    Importer,
    ImportFile,
    ImportProblem,
    ImportResult,
    ImportStep,
)

#: The files, by their fields' names.
MATERIALS, BASES, PRODUCTS, RECIPES = "materials", "bases", "products", "recipes"

#: The heading of a custom property's column, before its name.
PROPERTY = "Property: "
#: What an allergens cell holds for a record that has none.
NONE = "none"
#: What a list's items are separated by in a cell.
SEPARATOR = "; "
#: The characters a spreadsheet reads a cell starting with as a formula, which
#: an export writes after an apostrophe.
FORMULA_STARTS = ("=", "+", "-", "@", "\t", "\r")
#: The largest number Hoard keeps, and the most places it keeps.
_LARGEST = Decimal(10) ** 14
_PLACES = 6

#: The columns of products.csv that are the variation's, beside its own id.
VARIATION_ID = "variation_id"
VARIATION_COLUMNS = (
    "options",
    "sku",
    "price",
    "variation_auto_manufacture",
    "supplier",
    "supplier_code",
    "purchase_unit",
    "purchase_conversion",
)
#: The columns of recipes.csv that name what a recipe makes, and its lines.
OUTPUT_COLUMNS = ("makes", "makes_options")
LINE_COLUMNS = ("item", "item_options", "amount", "unit")
#: A material's columns its declaration keeps.
DECLARED = ("ingredient_name", "allergens", "fragrance_allergens")
#: The write paths' names of columns named otherwise in a file.
WRITTEN_AS = {
    "tracking_unit": "tracking_unit_id",
    "purchase_unit": "purchase_unit_id",
    "total_unit": "total_unit_id",
    "options": "attributes",
    "variation_auto_manufacture": "auto_manufacture",
}

#: Each file's kind of category and type, and of property.
CATEGORY_KINDS = {
    MATERIALS: CategoryKind.MATERIAL,
    BASES: CategoryKind.BASE,
    PRODUCTS: CategoryKind.PRODUCT,
    RECIPES: CategoryKind.BASE,
}
RECORD_KINDS = {
    MATERIALS: RecordKind.MATERIAL,
    BASES: RecordKind.BASE,
    PRODUCTS: RecordKind.PRODUCT,
}


class SpreadsheetProblem(StrEnum):
    """What a check found that stops the import: codes pages translate."""

    COLUMN_MISSING = COLUMN_MISSING
    FILE_UNREADABLE = FILE_UNREADABLE
    #: A column heading that isn't one of the file's or a property's.
    COLUMN_UNKNOWN = "COLUMN_UNKNOWN"
    #: A column of what something costs or sells for, from a member who
    #: doesn't see costs.
    COSTS_HIDDEN = "COSTS_HIDDEN"
    #: An ``id`` that isn't the community's record of the file's kind.
    ID_UNKNOWN = "ID_UNKNOWN"
    #: A second row for a record.
    ROW_REPEATED = "ROW_REPEATED"
    #: One SKU on two variations.
    SKU_REPEATED = "SKU_REPEATED"
    #: A name matching more than one record; its ``id`` settles which.
    NAME_AMBIGUOUS = "NAME_AMBIGUOUS"
    #: An empty cell its field can't be without.
    VALUE_REQUIRED = "VALUE_REQUIRED"
    #: A value that isn't one of its column's choices, or is too long.
    VALUE_INVALID = "VALUE_INVALID"
    #: A number that isn't a number, or is written with separators.
    NUMBER_INVALID = "NUMBER_INVALID"
    #: A number its field doesn't take.
    NUMBER_OUT_OF_RANGE = "NUMBER_OUT_OF_RANGE"
    TYPE_UNKNOWN = "TYPE_UNKNOWN"
    UNIT_UNKNOWN = "UNIT_UNKNOWN"
    #: A property's option that isn't one of its options.
    OPTION_UNKNOWN = "OPTION_UNKNOWN"
    #: Options not written as ``Name: value``, separated by semicolons.
    OPTIONS_INVALID = "OPTIONS_INVALID"
    #: A product's or a recipe's columns differ between its rows.
    COLUMNS_DISAGREE = "COLUMNS_DISAGREE"
    #: What a recipe makes isn't a base, a product's variation or a bundle.
    RECIPE_OUTPUT_UNKNOWN = "RECIPE_OUTPUT_UNKNOWN"
    #: A recipe's ``id`` names a recipe that makes something else.
    RECIPE_OUTPUT_DIFFERS = "RECIPE_OUTPUT_DIFFERS"
    #: A line names no material, base or variation, in the files or in Hoard.
    LINE_NAMES_NOTHING = "LINE_NAMES_NOTHING"
    #: A recipe names one item on two lines.
    LINE_REPEATED = "LINE_REPEATED"
    #: A recipe that would contain itself, directly or through a base.
    RECIPE_CONTAINS_ITSELF = "RECIPE_CONTAINS_ITSELF"


class SpreadsheetOptions(BaseModel):
    """This importer takes no options."""


#: The kinds a check counts and an import writes, in the order pages list them.
KINDS = ("categories", "suppliers", "materials", "bases", "products", "variations", "recipes")


# Cells.


class _Bad(Exception):
    """A cell its column can't read, and why."""

    def __init__(self, code: SpreadsheetProblem) -> None:
        super().__init__(code)
        self.code = code


def _unescaped(cell: str) -> str:
    """A cell as it was before an export kept a spreadsheet from reading it
    as a formula."""
    return cell[1:] if cell.startswith("'") and cell[1:2].startswith(FORMULA_STARTS) else cell


def _escaped(cell: str) -> str:
    return f"'{cell}" if cell.startswith(FORMULA_STARTS) else cell


def _number_text(number: Decimal | float | None) -> str:
    """A number as a cell holds it: no exponent, and no trailing zeros."""
    if number is None:
        return ""
    return f"{Decimal(str(number)).normalize():f}"


def _decimal(cell: str) -> Decimal:
    """A cell's number. One with a separator is refused, since ``1,000``
    could mean a thousand or one."""
    if any(mark in cell for mark in ",_ "):
        raise _Bad(SpreadsheetProblem.NUMBER_INVALID)
    try:
        number = Decimal(cell)
    except InvalidOperation:
        raise _Bad(SpreadsheetProblem.NUMBER_INVALID) from None
    if not number.is_finite():
        raise _Bad(SpreadsheetProblem.NUMBER_INVALID)
    return number


def _filled(cell: str, required: bool) -> bool:
    """Whether a cell holds anything; an empty one its field needs is refused."""
    if not cell and required:
        raise _Bad(SpreadsheetProblem.VALUE_REQUIRED)
    return bool(cell)


@dataclass(frozen=True)
class _Column:
    """A column of a file: a record's value of it, a cell read as that value
    (raising :class:`_Bad`), the value written as a cell, and whether two
    values are the same."""

    value: Callable[[Any], Any]
    read: Callable[[str], Any]
    text: Callable[[Any], str]
    same: Callable[[Any, Any], bool] = operator.eq
    #: A column of what something costs or sells for.
    costs: bool = False
    #: The custom property it holds.
    property_id: int | None = None


def _plain(value: Any) -> str:
    return "" if value is None else str(value)


def _same_name(was: str | None, now: str | None) -> bool:
    return key(was or "") == key(now or "")


def _read_text(limit: int = 200, required: bool = False) -> Callable[[str], str | None]:
    def read_text(cell: str) -> str | None:
        if not _filled(cell, required):
            return None
        if len(cell) > limit:
            raise _Bad(SpreadsheetProblem.VALUE_INVALID)
        return cell

    return read_text


def _text(name: str, limit: int = 200, required: bool = False) -> _Column:
    return _Column(operator.attrgetter(name), _read_text(limit, required), _plain)


def _number(
    name: str,
    *,
    positive: bool = False,
    required: bool = False,
    whole: bool = False,
    most: int | None = None,
    costs: bool = False,
) -> _Column:
    def read_number(cell: str) -> Decimal | int | None:
        if not _filled(cell, required):
            return None
        number = _decimal(cell)
        exponent = number.normalize().as_tuple().exponent
        if (
            number < 0
            or (positive and number == 0)
            or number >= _LARGEST
            or (most is not None and number > most)
            or (isinstance(exponent, int) and exponent < (0 if whole else -_PLACES))
        ):
            raise _Bad(SpreadsheetProblem.NUMBER_OUT_OF_RANGE)
        return int(number) if whole else number

    return _Column(operator.attrgetter(name), read_number, _number_text, costs=costs)


def _choice(name: str, choices: type[StrEnum], required: bool) -> _Column:
    def read_choice(cell: str) -> StrEnum | None:
        if not _filled(cell, required):
            return None
        try:
            return choices(cell.lower())
        except ValueError:
            raise _Bad(SpreadsheetProblem.VALUE_INVALID) from None

    return _Column(operator.attrgetter(name), read_choice, _plain)


def _flag(cell: str) -> bool:
    _filled(cell, True)
    answer = {"true": True, "yes": True, "false": False, "no": False}.get(cell.lower())
    if answer is None:
        raise _Bad(SpreadsheetProblem.VALUE_INVALID)
    return answer


def _flag_text(value: bool | None) -> str:
    return "" if value is None else "true" if value else "false"


def options_text(options: Mapping[str, str]) -> str:
    """A variation's options as a cell holds them: ``Size: Full Size; Tin: Red``."""
    return SEPARATOR.join(f"{name}: {value}" for name, value in options.items())


def _options(cell: str) -> dict[str, str]:
    read_options: dict[str, str] = {}
    for part in cell.split(";"):
        if not part.strip():
            continue
        name, colon, value = (each.strip() for each in part.partition(":"))
        if not colon or not name or not value or name in read_options or len(value) > 200:
            raise _Bad(SpreadsheetProblem.OPTIONS_INVALID)
        read_options[name] = value
    return read_options


def _allergens(cell: str) -> list[str] | None:
    if not cell:
        return None
    if cell.lower() == NONE:
        return []
    keys = {key(part).replace(" ", "_") for part in cell.split(";") if part.strip()}
    if not keys <= {allergen.value for allergen in Allergen}:
        raise _Bad(SpreadsheetProblem.VALUE_INVALID)
    return [allergen.value for allergen in Allergen if allergen.value in keys]


def _allergens_text(allergens: list[str] | None) -> str:
    if allergens is None:
        return ""
    return SEPARATOR.join(allergens) or NONE


def _fragrance(cell: str) -> dict[str, Decimal] | None:
    if not cell:
        return None
    if cell.lower() == NONE:
        return {}
    shares: dict[str, Decimal] = {}
    for part in cell.split(";"):
        if not part.strip():
            continue
        name, colon, share = part.partition(":")
        allergen = key(name).replace(" ", "_")
        if not colon or allergen not in FRAGRANCE_ALLERGENS or allergen in shares:
            raise _Bad(SpreadsheetProblem.VALUE_INVALID)
        number = _decimal(share.strip())
        if not 0 <= number <= 100:
            raise _Bad(SpreadsheetProblem.NUMBER_OUT_OF_RANGE)
        shares[allergen] = number
    return {allergen: shares[allergen] for allergen in FRAGRANCE_ALLERGENS if allergen in shares}


def _fragrance_held(material: Material) -> dict[str, Decimal] | None:
    if material.fragrance_allergens is None:
        return None
    return {allergen: Decimal(share) for allergen, share in material.fragrance_allergens.items()}


def _fragrance_text(shares: dict[str, Decimal] | None) -> str:
    if shares is None:
        return ""
    return (
        SEPARATOR.join(f"{allergen}: {_number_text(share)}" for allergen, share in shares.items())
        or NONE
    )


def _variation_name(product: str, options: Mapping[str, str]) -> str:
    """A variation as a change names it: its product's name and its options."""
    return f"{product} ({options_text(options)})" if options else product


# What Hoard holds.


@dataclass
class _Held:
    """What Hoard holds of the catalogue, read once for a check or an export."""

    units: dict[int, Unit]
    types: dict[int, ItemType]
    categories: dict[int, Category]
    #: The community's contacts' names, by id; empty unless asked for.
    contacts: dict[int, str]
    materials: dict[int, Material]
    bases: dict[int, Base]
    products: dict[int, Product]
    #: Each product's variations, its default first.
    variations: dict[int, list[Variation]]
    recipes: list[Recipe]
    lines: dict[int, list[RecipeLine]]
    definitions: dict[RecordKind, list[PropertyDefinition]]
    #: Each record's property values, by its kind, its id and the property's id.
    values: dict[RecordKind, dict[int, dict[int, Any]]]
    #: The variations a synced store prices, and that price.
    store_prices: dict[int, Decimal]
    #: Every variation, by id.
    by_id: dict[int, Variation] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.by_id = {each.id: each for held in self.variations.values() for each in held}

    def unit_id(self, name: str) -> int | None:
        """The unit a cell names: one of the community's by its name, or a
        standard unit by any name it is found by."""
        for unit in self.units.values():
            if _same_name(unit.name, name):
                return unit.id
        standard = units.match(name)
        if standard is None:
            return None
        return next((unit.id for unit in self.units.values() if unit.standard == standard), None)

    def item_name(self, item_id: int) -> tuple[str, dict[str, str]]:
        """The name and options a line or a recipe names an item by."""
        for held in (self.materials, self.bases):
            if item_id in held:
                return held[item_id].name, {}
        variation = self.by_id[item_id]
        return self.products[variation.product_id].name, variation.attributes


async def _held(db: AsyncSession, plugin: Plugin, community_ref: str, suppliers: bool) -> _Held:
    variations: dict[int, list[Variation]] = {}
    for variation in await db.exec(
        select(Variation).order_by(col(Variation.is_default).desc(), col(Variation.id))
    ):
        variations.setdefault(variation.product_id, []).append(variation)
    definitions: dict[RecordKind, list[PropertyDefinition]] = {kind: [] for kind in RecordKind}
    for definition in await db.exec(
        select(PropertyDefinition).order_by(col(PropertyDefinition.id))
    ):
        definitions[definition.kind].append(definition)
    values: dict[RecordKind, dict[int, dict[int, Any]]] = {kind: {} for kind in RecordKind}
    for value in await db.exec(select(PropertyValue)):
        for kind, (_, column) in properties.RECORDS.items():
            record_id = getattr(value, column)
            if record_id is not None:
                values[kind].setdefault(record_id, {})[value.definition_id] = value.value
    held_recipes = list(await db.exec(select(Recipe).order_by(col(Recipe.id))))
    named_contacts = await contacts.community_contacts(plugin, community_ref) if suppliers else []
    return _Held(
        units=await units.load(db),
        types={each.id: each for each in await db.exec(select(ItemType))},
        categories={each.id: each for each in await db.exec(select(Category))},
        contacts={contact.id: contact.name for contact in named_contacts},
        materials={
            each.id: each for each in await db.exec(select(Material).order_by(col(Material.id)))
        },
        bases={each.id: each for each in await db.exec(select(Base).order_by(col(Base.id)))},
        products={
            each.id: each for each in await db.exec(select(Product).order_by(col(Product.id)))
        },
        variations=variations,
        recipes=held_recipes,
        lines=await recipes.lines_of(db, [recipe.id for recipe in held_recipes]),
        definitions=definitions,
        values=values,
        store_prices=await summary.store_prices(
            db, [variation.id for held in variations.values() for variation in held]
        ),
    )


# The columns.


def _unit(held: _Held, name: str, required: bool = False) -> _Column:
    def read_unit(cell: str) -> int | None:
        if not _filled(cell, required):
            return None
        unit_id = held.unit_id(cell)
        if unit_id is None:
            raise _Bad(SpreadsheetProblem.UNIT_UNKNOWN)
        return unit_id

    return _Column(
        operator.attrgetter(name),
        read_unit,
        lambda unit_id: "" if unit_id is None else held.units[unit_id].name,
    )


def _type(held: _Held, kind: CategoryKind) -> _Column:
    """A type by its name: empty, or its default type's name, for the kind's default."""
    types = {each.id: each for each in held.types.values() if each.kind == kind}

    def read_type(cell: str) -> int | None:
        if not cell:
            return None
        type_id = next(
            (type_id for type_id, each in types.items() if _same_name(each.name, cell)), None
        )
        if type_id is None:
            raise _Bad(SpreadsheetProblem.TYPE_UNKNOWN)
        return None if types[type_id].is_default else type_id

    def held_type(record: Material | Base | Product) -> int | None:
        return None if types[record.type_id].is_default else record.type_id

    return _Column(
        held_type, read_type, lambda type_id: "" if type_id is None else types[type_id].name or ""
    )


def _category(held: _Held, kind: CategoryKind) -> _Column:
    names = {each.id: each.name for each in held.categories.values() if each.kind == kind}
    return _Column(
        lambda record: names.get(record.category_id or 0), _read_text(), _plain, _same_name
    )


def _supplier(held: _Held) -> _Column:
    return _Column(
        lambda record: held.contacts.get(record.supplier_contact_id or 0),
        _read_text(255),
        _plain,
        _same_name,
    )


_NUMBERS = (PropertyType.NUMBER, PropertyType.CURRENCY, PropertyType.SCALE, PropertyType.COUNTER)


def _property(held: _Held, kind: RecordKind, definition: PropertyDefinition) -> _Column:
    """A custom property's column: a select's options by their labels, a
    signature by the name signed."""
    shape = definition.type
    options = definition.options or []
    labels = {option["value"]: option["label"] for option in options}

    def option(cell: str) -> str:
        for each in options:
            if _same_name(each["label"], cell) or _same_name(each["value"], cell):
                return each["value"]
        raise _Bad(SpreadsheetProblem.OPTION_UNKNOWN)

    def ordered(chosen: Iterable[str]) -> list[str]:
        wanted = set(chosen)
        return [each["value"] for each in options if each["value"] in wanted]

    def held_value(record: Material | Base | Product) -> Any:
        value = held.values[kind].get(record.id, {}).get(definition.id)
        if value is None:
            return None
        if shape is PropertyType.SIGNATURE:
            return value.get("name") if isinstance(value, dict) else None
        if shape is PropertyType.MULTI_SELECT:
            return ordered(value)
        return float(value) if shape in _NUMBERS else value

    def read_value(cell: str) -> Any:
        if not cell:
            return None
        if shape is PropertyType.CHECKBOX:
            return _flag(cell)
        if shape in _NUMBERS:
            number = _decimal(cell)
            if shape is PropertyType.SCALE and not 0 <= number <= 100:
                raise _Bad(SpreadsheetProblem.NUMBER_OUT_OF_RANGE)
            return float(number)
        if shape is PropertyType.SELECT:
            return option(cell)
        if shape is PropertyType.MULTI_SELECT:
            return ordered(option(part.strip()) for part in cell.split(";") if part.strip())
        if shape is PropertyType.FILE:
            if not cell.isdigit():
                raise _Bad(SpreadsheetProblem.VALUE_INVALID)
            return int(cell)
        parse = {
            PropertyType.DATE: date.fromisoformat,
            PropertyType.DATETIME: datetime.fromisoformat,
        }
        try:
            parse.get(shape, str)(cell)
        except ValueError:
            raise _Bad(SpreadsheetProblem.VALUE_INVALID) from None
        if len(cell) > (200 if shape is PropertyType.SIGNATURE else 10_000):
            raise _Bad(SpreadsheetProblem.VALUE_INVALID)
        return cell

    def text(value: Any) -> str:
        if value is None:
            return ""
        if shape is PropertyType.CHECKBOX:
            return _flag_text(value)
        if shape in _NUMBERS:
            return _number_text(value)
        if shape is PropertyType.SELECT:
            return labels.get(value, value)
        if shape is PropertyType.MULTI_SELECT:
            return SEPARATOR.join(labels.get(each, each) for each in value)
        return str(value)

    return _Column(held_value, read_value, text, property_id=definition.id)


def _columns(held: _Held, file: str, sees_costs: bool = True) -> dict[str, _Column]:
    """Each column a file holds besides its ids and what a recipe names, in
    the order an export writes them, its properties last. A product's are
    its own, then its variation's (:data:`VARIATION_COLUMNS`)."""
    kind = CATEGORY_KINDS[file]
    every: dict[str, _Column]
    if file == RECIPES:
        every = {
            "batch_quantity": _number("batch_quantity", positive=True, required=True),
            "by_ratio": _Column(operator.attrgetter("by_ratio"), _flag, _flag_text),
            "total": _number("total", positive=True),
            "total_unit": _unit(held, "total_unit_id"),
            "minutes": _number("minutes", whole=True),
            "notes": _text("notes", limit=2000),
        }
    elif file == PRODUCTS:
        every = {
            "name": _text("name", required=True),
            "status": _choice("status", ItemStatus, required=True),
            "category": _category(held, kind),
            "type": _type(held, kind),
            "low_stock_limit": _number("low_stock_limit"),
            "default_price": _number("default_price", costs=True),
            "auto_manufacture": _choice("auto_manufacture", AutoManufacture, required=False),
            "cure_days": _number("cure_days", positive=True, whole=True, most=3650),
            "options": _Column(operator.attrgetter("attributes"), _options, options_text),
            "sku": _text("sku"),
            "price": _number("price", costs=True),
            "variation_auto_manufacture": _choice(
                "auto_manufacture", AutoManufacture, required=False
            ),
            "supplier": _supplier(held),
            "supplier_code": _text("supplier_code"),
            "purchase_unit": _unit(held, "purchase_unit_id"),
            "purchase_conversion": _number("purchase_conversion", positive=True, required=True),
        }
    else:
        every = {
            "name": _text("name", required=True),
            "status": _choice("status", ItemStatus, required=True),
            "sku": _text("sku"),
            "category": _category(held, kind),
            "type": _type(held, kind),
            "tracking_unit": _unit(held, "tracking_unit_id", required=True),
        }
        if file == MATERIALS:
            every |= {
                "purchase_unit": _unit(held, "purchase_unit_id"),
                "purchase_conversion": _number("purchase_conversion", positive=True, required=True),
                "unit_weight": _number("unit_weight", positive=True),
                "low_stock_limit": _number("low_stock_limit"),
                "supplier": _supplier(held),
                "supplier_code": _text("supplier_code"),
                "ingredient_name": _text("ingredient_name"),
                "allergens": _Column(operator.attrgetter("allergens"), _allergens, _allergens_text),
                "fragrance_allergens": _Column(_fragrance_held, _fragrance, _fragrance_text),
            }
        else:
            every |= {
                "low_stock_limit": _number("low_stock_limit"),
                "cure_days": _number("cure_days", positive=True, whole=True, most=3650),
                "auto_manufacture": _choice("auto_manufacture", AutoManufacture, required=False),
            }
        every["notes"] = _text("notes", limit=2000)
    if file in RECORD_KINDS:
        record_kind = RECORD_KINDS[file]
        every |= {
            f"{PROPERTY}{definition.name}": _property(held, record_kind, definition)
            for definition in held.definitions[record_kind]
        }
    return {name: column for name, column in every.items() if sees_costs or not column.costs}


# The export.


def _csv(rows: list[list[str]]) -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerows([[_escaped(cell) for cell in row] for row in rows])
    return out.getvalue()


def _cells(columns: Mapping[str, _Column], record: Any) -> dict[str, str]:
    return {name: column.text(column.value(record)) for name, column in columns.items()}


async def export(
    db: AsyncSession, plugin: Plugin, community_ref: str, file: str, sees_costs: bool
) -> str:
    """One of the catalogue's files, as this importer reads it back."""
    held = await _held(db, plugin, community_ref, suppliers=file in (MATERIALS, PRODUCTS))
    columns = _columns(held, file, sees_costs)
    if file in (MATERIALS, BASES):
        records = held.materials if file == MATERIALS else held.bases
        return _csv(
            [
                ["id", *columns],
                *(
                    [str(record_id), *_cells(columns, record).values()]
                    for record_id, record in records.items()
                ),
            ]
        )
    if file == PRODUCTS:
        own = {name: column for name, column in columns.items() if name not in VARIATION_COLUMNS}
        variation_columns = {name: columns[name] for name in VARIATION_COLUMNS if name in columns}
        fixed = [name for name, column in own.items() if column.property_id is None]
        header = [
            "id",
            *fixed,
            VARIATION_ID,
            *variation_columns,
            *(name for name in own if name not in fixed),
        ]
        rows = [header]
        for product_id, product in held.products.items():
            product_cells = _cells(own, product)
            for variation in held.variations.get(product_id, []):
                cells = (
                    product_cells
                    | _cells(variation_columns, variation)
                    | {"id": str(product_id), VARIATION_ID: str(variation.id)}
                )
                if "price" in cells and variation.id in held.store_prices:
                    cells["price"] = _number_text(held.store_prices[variation.id])
                rows.append([cells[name] for name in header])
        return _csv(rows)
    rows = [["id", *OUTPUT_COLUMNS, *columns, *LINE_COLUMNS]]
    for recipe in held.recipes:
        if recipe.product_id is not None:
            makes, makes_options = held.products[recipe.product_id].name, {}
        else:
            makes, makes_options = held.item_name(recipe.base_id or recipe.variation_id or 0)
        recipe_cells = [
            str(recipe.id),
            makes,
            options_text(makes_options),
            *_cells(columns, recipe).values(),
        ]
        lines = held.lines[recipe.id]
        if not lines:
            rows.append([*recipe_cells, "", "", "", ""])
        for line in lines:
            item, item_options = held.item_name(line.item_id)
            rows.append(
                [
                    *recipe_cells,
                    item,
                    options_text(item_options),
                    _number_text(line.written),
                    "" if line.unit_id is None else held.units[line.unit_id].name,
                ]
            )
    return _csv(rows)


# The check.

#: A record a file names: its id, or a key for one the import makes.
Key = Hashable


@dataclass(frozen=True)
class _Bundle:
    """What a bundle's recipe makes: its product."""

    product_id: int


def _json(value: Any) -> Any:
    """A value as a plan keeps it: exact decimals as text."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {name: _json(each) for name, each in value.items()}
    if isinstance(value, list):
        return [_json(each) for each in value]
    return value


@dataclass
class _Plan:
    """The plan a check makes, as it makes it, with the catalogue as the
    import leaves it, for recipes to name what they hold by."""

    held: _Held
    db: AsyncSession
    problems: list[ImportProblem] = field(default_factory=list)
    changes: list[ImportChange] = field(default_factory=list)
    #: Each kind's new, changed and unchanged records.
    counts: dict[str, list[int]] = field(default_factory=dict)
    #: Categories and suppliers named, each with the first row naming it, and
    #: whether Hoard holds it.
    categories: dict[tuple[CategoryKind, str], tuple[str, str, int, bool]] = field(
        default_factory=dict
    )
    suppliers: dict[str, tuple[str, str, int, bool]] = field(default_factory=dict)
    steps: list[dict[str, Any]] = field(default_factory=list)
    #: Each material's and base's name, as the import leaves it, and which it is.
    names: dict[Key, str] = field(default_factory=dict)
    bases: set[Key] = field(default_factory=set)
    #: Each product's name, and each variation's product and options.
    product_names: dict[Key, str] = field(default_factory=dict)
    variations: dict[Key, tuple[Key, dict[str, str]]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        held = self.held
        self.names = {
            **{item_id: each.name for item_id, each in held.materials.items()},
            **{item_id: each.name for item_id, each in held.bases.items()},
        }
        self.bases = set(held.bases)
        self.product_names = {product_id: each.name for product_id, each in held.products.items()}
        self.variations = {
            each.id: (each.product_id, each.attributes) for each in held.by_id.values()
        }

    def problem(
        self,
        code: str,
        file: str,
        row: int | None = None,
        column: str | None = None,
        name: str | None = None,
    ) -> None:
        self.problems.append(ImportProblem(code=code, file=file, row=row, field=column, name=name))

    def count(self, kind: str, record: object, changed: bool) -> None:
        """Count a record of ``kind`` as new (without ``record``), changed or unchanged."""
        self.counts.setdefault(kind, [0, 0, 0])[0 if record is None else 1 if changed else 2] += 1

    def name_reference(self, file: str, row: int, column: str, value: Any) -> None:
        """Note the category or supplier a row names, which the import makes
        if Hoard doesn't hold it."""
        if value is None:
            return
        if column == "category":
            kind = CATEGORY_KINDS[file]
            held = any(
                each.kind == kind and _same_name(each.name, value)
                for each in self.held.categories.values()
            )
            self.categories.setdefault((kind, key(value)), (value, file, row, held))
        elif column == "supplier":
            held = any(_same_name(name, value) for name in self.held.contacts.values())
            self.suppliers.setdefault(key(value), (value, file, row, held))

    def parse(
        self, file: str, row: int, cells: Row, columns: Mapping[str, _Column]
    ) -> dict[str, Any]:
        """A row's cells of ``columns`` that it holds, each read, a problem
        noted for each it can't read."""
        parsed: dict[str, Any] = {}
        for name, column in columns.items():
            if name not in cells:
                continue
            try:
                parsed[name] = column.read(cells[name])
            except _Bad as bad:
                self.problem(bad.code, file, row, name, cells[name] or None)
                continue
            self.name_reference(file, row, name, parsed[name])
        return parsed

    def changed(
        self,
        file: str,
        row: int,
        name: str,
        columns: Mapping[str, _Column],
        parsed: dict[str, Any],
        record: object,
    ) -> dict[str, Any]:
        """What a row changes of ``record``, each change listed; everything
        it holds for a new record."""
        if record is None:
            return parsed
        changes: dict[str, Any] = {}
        for column_name, now in parsed.items():
            column = columns[column_name]
            was = column.value(record)
            if not column.same(was, now):
                changes[column_name] = now
                self.changes.append(
                    ImportChange(
                        file=file,
                        row=row,
                        name=name,
                        field=column_name,
                        was=column.text(was),
                        now=column.text(now),
                    )
                )
        return changes

    def written(
        self, columns: Mapping[str, _Column], changes: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """A step's fields and its properties' values, by property id."""
        values = {
            str(columns[name].property_id): _json(value)
            for name, value in changes.items()
            if columns[name].property_id is not None
        }
        fields = {
            name: _json(value)
            for name, value in changes.items()
            if columns[name].property_id is None
        }
        return fields, values

    def reference(self, item: Key) -> dict[str, Any]:
        """How a step finds an item: by its id, or as the import made it."""
        if isinstance(item, int):
            return {"id": item}
        if item in self.variations:
            product, options = self.variations[item]
            return {"product": self.product_names[product], "options": options}
        return {"bases" if item in self.bases else "materials": self.names[item]}


def _header(
    plan: _Plan,
    file: str,
    header: list[str],
    columns: Mapping[str, _Column],
    every: Mapping[str, _Column],
    known: Iterable[str],
) -> None:
    """Note each column a file shouldn't hold: one it has none of, and one of
    costs from a member who doesn't see them."""
    for name in header:
        if name in columns or name in known:
            continue
        code = (
            SpreadsheetProblem.COSTS_HIDDEN if name in every else SpreadsheetProblem.COLUMN_UNKNOWN
        )
        plan.problem(code, file, 1, name, name)


def _record_id(plan: _Plan, file: str, row: int, cell: str, held: Mapping[int, Any]) -> int | None:
    """The record an ``id`` cell names, or None, noting it, when it names none."""
    if cell.isdigit() and int(cell) in held:
        return int(cell)
    plan.problem(SpreadsheetProblem.ID_UNKNOWN, file, row, "id", cell)
    return None


def _match(
    plan: _Plan, file: str, row: int, cells: Row, held: Mapping[int, Any]
) -> tuple[Key | None, bool]:
    """The record a row names, by its ``id`` or its name, or a key for the
    new one it makes; and whether the row is good to go on with."""
    if cells.get("id"):
        record_id = _record_id(plan, file, row, cells["id"], held)
        return record_id, record_id is not None
    name = cells.get("name", "")
    if not name:
        plan.problem(SpreadsheetProblem.VALUE_REQUIRED, file, row, "name")
        return None, False
    matches = [record_id for record_id, record in held.items() if _same_name(record.name, name)]
    if len(matches) > 1:
        plan.problem(SpreadsheetProblem.NAME_AMBIGUOUS, file, row, "name", name)
        return None, False
    return (matches[0] if matches else (file, key(name))), True


def _rows(
    plan: _Plan, file: str, content: bytes | None, columns: tuple[str, ...] = ()
) -> tuple[list[str], list[tuple[int, Row]]] | None:
    found_rows = read(file, content, columns, plan.problems)
    if found_rows is None:
        return None
    header, rows = found_rows
    return header, [
        (row, {name: _unescaped(cell) for name, cell in cells.items()}) for row, cells in rows
    ]


async def _plan_items(plan: _Plan, file: str, content: bytes | None, sees_costs: bool) -> None:
    """A step per material or base a file adds or changes."""
    found_rows = _rows(plan, file, content)
    if found_rows is None:
        return
    header, rows = found_rows
    every = _columns(plan.held, file)
    columns = _columns(plan.held, file, sees_costs)
    _header(plan, file, header, columns, every, ("id",))
    held: Mapping[int, Material | Base] = (
        plan.held.materials if file == MATERIALS else plan.held.bases
    )
    seen: set[Key] = set()
    for row, cells in rows:
        item, good = _match(plan, file, row, cells, held)
        if not good:
            continue
        if item in seen:
            plan.problem(SpreadsheetProblem.ROW_REPEATED, file, row, "id", cells.get("name"))
            continue
        seen.add(item)
        record = held.get(item) if isinstance(item, int) else None
        parsed = plan.parse(file, row, cells, columns)
        if record is None and parsed.get("tracking_unit") is None:
            plan.problem(SpreadsheetProblem.VALUE_REQUIRED, file, row, "tracking_unit")
        name = record.name if record is not None else cells.get("name", "")
        changes = plan.changed(file, row, name, columns, parsed, record)
        if record is not None and "tracking_unit" in changes:
            try:
                await items.require_tracking_unit(
                    plan.db, record.id, record.tracking_unit_id, changes["tracking_unit"]
                )
            except HTTPException as refused:
                plan.problem(str(refused.detail), file, row, "tracking_unit")
        plan.names[item] = parsed.get("name") or plan.names.get(item, "")
        if file == BASES:
            plan.bases.add(item)
        plan.count(file, record, bool(changes))
        if record is None or changes:
            fields, values = plan.written(columns, changes)
            plan.steps.append(
                {
                    "step": file,
                    "file": file,
                    "row": row,
                    "id": None if record is None else record.id,
                    "fields": fields,
                    "properties": values,
                }
            )


def _agree(
    plan: _Plan,
    file: str,
    rows: list[tuple[int, dict[str, Any]]],
    columns: Mapping[str, _Column],
) -> dict[str, Any]:
    """The values a record's rows hold of its own columns, the first row's,
    noting each later row that holds another."""
    first = rows[0][1]
    for row, parsed in rows[1:]:
        for name, value in parsed.items():
            if name in first and not columns[name].same(first[name], value):
                plan.problem(SpreadsheetProblem.COLUMNS_DISAGREE, file, row, name)
    return first


async def _plan_products(plan: _Plan, content: bytes | None, sees_costs: bool) -> None:
    """A step per product products.csv adds or changes, and one per
    variation, each new product's first variation made with it."""
    found_rows = _rows(plan, PRODUCTS, content)
    if found_rows is None:
        return
    header, rows = found_rows
    held = plan.held
    every = _columns(held, PRODUCTS)
    columns = _columns(held, PRODUCTS, sees_costs)
    _header(plan, PRODUCTS, header, columns, every, ("id", VARIATION_ID))
    own = {name: column for name, column in columns.items() if name not in VARIATION_COLUMNS}
    of_variation = {name: columns[name] for name in VARIATION_COLUMNS if name in columns}
    has_variations = VARIATION_ID in header or any(name in header for name in of_variation)
    groups: dict[Key, list[tuple[int, Row]]] = {}
    for row, cells in rows:
        product, good = _match(plan, PRODUCTS, row, cells, held.products)
        if not good:
            continue
        if product in groups and not has_variations:
            plan.problem(SpreadsheetProblem.ROW_REPEATED, PRODUCTS, row, "id", cells.get("name"))
            continue
        groups.setdefault(product, []).append((row, cells))
    skus: dict[Key, tuple[str | None, int | None]] = {
        variation.id: (variation.sku, None) for variation in held.by_id.values()
    }
    for product, product_rows in groups.items():
        record = held.products.get(product) if isinstance(product, int) else None
        first_row, first_cells = product_rows[0]
        parsed = _agree(
            plan,
            PRODUCTS,
            [(row, plan.parse(PRODUCTS, row, cells, own)) for row, cells in product_rows],
            own,
        )
        name = record.name if record is not None else first_cells.get("name", "")
        changes = plan.changed(PRODUCTS, first_row, name, own, parsed, record)
        plan.product_names[product] = parsed.get("name") or plan.product_names.get(product, "")
        plan.count(PRODUCTS, record, bool(changes))
        fields, values = plan.written(own, changes)
        step: dict[str, Any] = {
            "step": PRODUCTS,
            "file": PRODUCTS,
            "row": first_row,
            "id": None if record is None else record.id,
            "fields": fields,
            "properties": values,
            "variation": None,
            "sees_costs": sees_costs,
        }
        if record is None or changes:
            plan.steps.append(step)
        if not has_variations:
            if record is None:
                plan.variations[(PRODUCTS, product, first_row)] = (product, {})
            continue
        held_variations = held.variations.get(product, []) if record is not None else []
        seen: set[Key] = set()
        # Each of the product's variations' options as the import leaves them,
        # for a new variation to be told from the others by.
        named_by: dict[Key, dict[str, str]] = {each.id: each.attributes for each in held_variations}
        for row, cells in product_rows:
            variation_parsed = plan.parse(PRODUCTS, row, cells, of_variation)
            variation, good = _variation(plan, row, cells, variation_parsed, held_variations)
            if not good:
                continue
            variation_key: Key = variation.id if variation else (PRODUCTS, product, row)
            if variation_key in seen:
                plan.problem(SpreadsheetProblem.ROW_REPEATED, PRODUCTS, row, VARIATION_ID)
                continue
            seen.add(variation_key)
            if variation and variation.id in held.store_prices:
                # The store's price shows in place of Hoard's, which stays as it is.
                variation_parsed.pop("price", None)
            options = variation_parsed.get("options", variation.attributes if variation else {})
            if not variation and (options or {}) in named_by.values():
                plan.problem(SpreadsheetProblem.ROW_REPEATED, PRODUCTS, row, "options")
                continue
            named_by[variation_key] = options or {}
            plan.variations[variation_key] = (product, options or {})
            if "sku" in variation_parsed:
                skus[variation_key] = (variation_parsed["sku"], row)
            variation_changes = plan.changed(
                PRODUCTS,
                row,
                _variation_name(name, variation.attributes if variation else options or {}),
                of_variation,
                variation_parsed,
                variation,
            )
            plan.count("variations", variation, bool(variation_changes))
            variation_fields = _json(variation_changes)
            if record is None and step["variation"] is None:
                step["variation"] = variation_fields
            elif not variation or variation_changes:
                plan.steps.append(
                    {
                        "step": "variations",
                        "file": PRODUCTS,
                        "row": row,
                        "id": variation.id if variation else None,
                        "product": {"id": product}
                        if isinstance(product, int)
                        else {"name": plan.product_names[product]},
                        "fields": variation_fields,
                        "sees_costs": sees_costs,
                    }
                )
    by_sku: dict[str, list[int | None]] = {}
    for sku, row in skus.values():
        if sku is not None:
            by_sku.setdefault(sku, []).append(row)
    for sku, sku_rows in by_sku.items():
        if len(sku_rows) > 1:
            for row in sku_rows:
                if row is not None:
                    plan.problem(SpreadsheetProblem.SKU_REPEATED, PRODUCTS, row, "sku", sku)


def _variation(
    plan: _Plan,
    row: int,
    cells: Row,
    parsed: dict[str, Any],
    held_variations: list[Variation],
) -> tuple[Variation | None, bool]:
    """The variation a row names, by its id, its SKU or its options among its
    product's, or None for a new one; and whether the row is good to go on
    with, which an id naming none of the product's variations isn't."""
    if cells.get(VARIATION_ID):
        cell = cells[VARIATION_ID]
        variation = next((each for each in held_variations if str(each.id) == cell), None)
        if variation is None:
            plan.problem(SpreadsheetProblem.ID_UNKNOWN, PRODUCTS, row, VARIATION_ID, cell)
        return variation, variation is not None
    if parsed.get("sku"):
        for each in held_variations:
            if each.sku == parsed["sku"]:
                return each, True
    options = parsed.get("options", {})
    return next((each for each in held_variations if each.attributes == options), None), True


def _index(plan: _Plan) -> dict[str, list[tuple[Key, dict[str, str] | None]]]:
    """Each item the catalogue holds once imported, by its name: a material
    or a base with no options, a variation with its options."""
    index: dict[str, list[tuple[Key, dict[str, str] | None]]] = {}
    for item, name in plan.names.items():
        index.setdefault(key(name), []).append((item, None))
    for variation, (product, options) in plan.variations.items():
        index.setdefault(key(plan.product_names[product]), []).append((variation, options))
    return index


@dataclass(frozen=True)
class _Line:
    """A recipe's line as recipes.csv holds it."""

    row: int
    item: Key
    written: Decimal
    unit: int | None

    @property
    def held(self) -> tuple[Key, Decimal, int | None]:
        return self.item, self.written, self.unit


@dataclass
class _Recipe:
    """A recipe as recipes.csv holds it."""

    row: int
    cells: Row
    rows: list[tuple[int, Row]] = field(default_factory=list)


def _recipes_held(
    held: _Held,
) -> tuple[dict[Key, Recipe], dict[Key, set[Key]], dict[int, Key]]:
    """Each held recipe by what it makes, what each output is made from, and
    each recipe's output. A bundle's output is its product, and what it holds
    is what each of its variations is made from."""
    by_output: dict[Key, Recipe] = {}
    made_from: dict[Key, set[Key]] = {}
    output_of: dict[int, Key] = {}
    for recipe in held.recipes:
        output: Key = (
            _Bundle(recipe.product_id)
            if recipe.product_id is not None
            else recipe.base_id or recipe.variation_id or 0
        )
        by_output[output] = recipe
        output_of[recipe.id] = output
        for item in _outputs(held, output):
            made_from[item] = {line.item_id for line in held.lines[recipe.id]}
    return by_output, made_from, output_of


def _outputs(held: _Held, output: Key) -> list[Key]:
    """The items a recipe's output is: a bundle's variations, or itself."""
    if isinstance(output, _Bundle):
        return [variation.id for variation in held.variations.get(output.product_id, [])]
    return [output]


async def _plan_recipes(plan: _Plan, content: bytes | None) -> None:
    """A step per recipe recipes.csv adds or changes, each replaced by its rows."""
    found_rows = _rows(plan, RECIPES, content, ("item", "amount"))
    if found_rows is None:
        return
    header, rows = found_rows
    held = plan.held
    columns = _columns(held, RECIPES)
    _header(plan, RECIPES, header, columns, columns, ("id", *OUTPUT_COLUMNS, *LINE_COLUMNS))
    if "id" not in header and "makes" not in header:
        plan.problem(SpreadsheetProblem.COLUMN_MISSING, RECIPES, 1, "makes", "makes")
        return
    found: dict[tuple[str, ...], _Recipe] = {}
    for row, cells in rows:
        group = (
            ("id", cells["id"])
            if cells.get("id")
            else ("makes", key(cells.get("makes", "")), key(cells.get("makes_options", "")))
        )
        found.setdefault(group, _Recipe(row, cells)).rows.append((row, cells))
    index = _index(plan)
    by_output, made_from, output_of = _recipes_held(held)
    bundles = {
        key(plan.product_names[output.product_id]): output
        for output in by_output
        if isinstance(output, _Bundle)
    }

    def named_item(row: int, column: str, name: str, cell: str, outputs: bool) -> Key | None:
        try:
            options = _options(cell)
        except _Bad as bad:
            plan.problem(bad.code, RECIPES, row, f"{column}_options", cell)
            return None
        if outputs and not options and key(name) in bundles:
            return bundles[key(name)]
        matches = [
            item
            for item, held_options in index.get(key(name), [])
            if (held_options is None and not options) or held_options == options
        ]
        if outputs:
            matches = [item for item in matches if item in plan.bases or item in plan.variations]
        if len(matches) > 1:
            plan.problem(SpreadsheetProblem.NAME_AMBIGUOUS, RECIPES, row, column, name)
            return None
        if not matches:
            code = (
                SpreadsheetProblem.RECIPE_OUTPUT_UNKNOWN
                if outputs
                else SpreadsheetProblem.LINE_NAMES_NOTHING
            )
            plan.problem(code, RECIPES, row, column, name)
            return None
        return matches[0]

    amounts = _number("written", positive=True, required=True)
    line_units = _unit(held, "unit_id")
    planned: list[tuple[_Recipe, Key, Recipe | None, dict[str, Any], list[_Line]]] = []
    seen: set[Key] = set()
    for recipe in found.values():
        row, cells = recipe.row, recipe.cells
        held_recipe: Recipe | None = None
        output: Key | None = None
        makes = cells.get("makes", "")
        if cells.get("id"):
            recipe_id = _record_id(
                plan, RECIPES, row, cells["id"], {each.id: each for each in held.recipes}
            )
            if recipe_id is None:
                continue
            held_recipe = next(each for each in held.recipes if each.id == recipe_id)
            output = output_of[recipe_id]
            if makes:
                named_output = named_item(row, "makes", makes, cells.get("makes_options", ""), True)
                if named_output is not None and named_output != output:
                    plan.problem(
                        SpreadsheetProblem.RECIPE_OUTPUT_DIFFERS, RECIPES, row, "makes", makes
                    )
        else:
            if not makes:
                plan.problem(SpreadsheetProblem.VALUE_REQUIRED, RECIPES, row, "makes")
                continue
            output = named_item(row, "makes", makes, cells.get("makes_options", ""), True)
            if output is None:
                continue
            held_recipe = by_output.get(output)
        if output in seen:
            plan.problem(SpreadsheetProblem.ROW_REPEATED, RECIPES, row, "makes", makes or None)
            continue
        seen.add(output)
        parsed = _agree(
            plan,
            RECIPES,
            [
                (each_row, plan.parse(RECIPES, each_row, each, columns))
                for each_row, each in recipe.rows
            ],
            columns,
        )
        lines: list[_Line] = []
        for line_row, line in recipe.rows:
            if not any(line.get(name) for name in ("item", "amount", "unit")):
                continue
            read_line = plan.parse(RECIPES, line_row, line, {"amount": amounts, "unit": line_units})
            if not line.get("item"):
                plan.problem(SpreadsheetProblem.VALUE_REQUIRED, RECIPES, line_row, "item")
                continue
            item = named_item(line_row, "item", line["item"], line.get("item_options", ""), False)
            if item is None or read_line.get("amount") is None:
                continue
            if item in {each.item for each in lines}:
                plan.problem(
                    SpreadsheetProblem.LINE_REPEATED, RECIPES, line_row, "item", line["item"]
                )
            lines.append(_Line(line_row, item, read_line["amount"], read_line.get("unit")))
        planned.append((recipe, output, held_recipe, parsed, lines))
        for each in _outputs(held, output):
            made_from[each] = {line.item for line in lines}
    for recipe, output, held_recipe, parsed, lines in planned:
        if _contains_itself(held, made_from, output):
            plan.problem(
                SpreadsheetProblem.RECIPE_CONTAINS_ITSELF,
                RECIPES,
                recipe.row,
                "makes",
                recipe.cells.get("makes") or None,
            )
        _plan_recipe(plan, columns, recipe, output, held_recipe, parsed, lines)


def _contains_itself(held: _Held, made_from: dict[Key, set[Key]], output: Key) -> bool:
    outputs = set(_outputs(held, output))
    reached: set[Key] = set().union(*(made_from.get(each, set()) for each in outputs))
    seen: set[Key] = set()
    while reached:
        if reached & outputs:
            return True
        seen |= reached
        reached = set().union(*(made_from.get(each, set()) for each in reached)) - seen
    return False


def _plan_recipe(
    plan: _Plan,
    columns: Mapping[str, _Column],
    recipe: _Recipe,
    output: Key,
    held_recipe: Recipe | None,
    parsed: dict[str, Any],
    lines: list[_Line],
) -> None:
    """The step that writes a recipe, unless Hoard holds it as the file has it."""
    held = plan.held
    makes = _variation_name(*_named(plan, output))
    changes = plan.changed(RECIPES, recipe.row, makes, columns, parsed, held_recipe)
    now = [line.held for line in lines]
    was = (
        []
        if held_recipe is None
        else [(line.item_id, line.written, line.unit_id) for line in held.lines[held_recipe.id]]
    )
    if held_recipe is not None:
        for index in range(max(len(now), len(was))):
            if index < len(now) and index < len(was) and now[index] == was[index]:
                continue
            plan.changes.append(
                ImportChange(
                    file=RECIPES,
                    row=lines[index].row if index < len(lines) else recipe.row,
                    name=makes,
                    field="item",
                    was=_line_text(plan, was[index]) if index < len(was) else "",
                    now=_line_text(plan, now[index]) if index < len(now) else "",
                )
            )
    changed = bool(changes) or now != was
    plan.count(RECIPES, held_recipe, changed)
    if held_recipe is not None and not changed:
        return
    if isinstance(output, _Bundle):
        column, made = "product_id", {"id": output.product_id}
    else:
        column = "base_id" if output in plan.bases else "variation_id"
        made = plan.reference(output)
    plan.steps.append(
        {
            "step": RECIPES,
            "file": RECIPES,
            "row": recipe.row,
            "id": None if held_recipe is None else held_recipe.id,
            "output": {"column": column, "item": made},
            "fields": _json(changes),
            "lines": [
                {"item": plan.reference(line.item), "written": str(line.written), "unit": line.unit}
                for line in lines
            ],
        }
    )


def _named(plan: _Plan, item: Key) -> tuple[str, dict[str, str]]:
    """The name and options an item is named by once imported."""
    if isinstance(item, _Bundle):
        return plan.product_names[item.product_id], {}
    if item in plan.variations:
        product, options = plan.variations[item]
        return plan.product_names[product], options
    return plan.names[item], {}


def _line_text(plan: _Plan, line: tuple[Key, Decimal, int | None]) -> str:
    """A recipe's line as a change shows it: its amount, its unit and its item."""
    item, written, unit = line
    unit_name = "" if unit is None else f" {plan.held.units[unit].name}"
    return f"{_number_text(written)}{unit_name} {_variation_name(*_named(plan, item))}"


async def check(request: ImportCheck) -> Checked:
    """The plan of what the spreadsheets change, with what Hoard holds already
    and the problems that stop it. Nothing is written."""
    sees_costs = plugin_manifest.SEE_COSTS in request.session.permissions
    # The community's contacts are read only for a file naming suppliers.
    suppliers = any(
        b"supplier" in (request.files.get(name) or b"").split(b"\n", 1)[0]
        for name in (MATERIALS, PRODUCTS)
    )
    plan = _Plan(
        held=await _held(request.db, request.plugin, request.organization.community_ref, suppliers),
        db=request.db,
    )
    for file in (MATERIALS, BASES):
        await _plan_items(plan, file, request.files.get(file), sees_costs)
    await _plan_products(plan, request.files.get(PRODUCTS), sees_costs)
    await _plan_recipes(plan, request.files.get(RECIPES))
    matched: dict[str, int] = {
        kind: counted[2] for kind, counted in plan.counts.items() if counted[2]
    }
    references: list[dict[str, Any]] = []
    for (kind, _), (name, file, row, held) in plan.categories.items():
        plan.count("categories", True if held else None, False)
        if not held:
            references.append(
                {"step": "categories", "file": file, "row": row, "kind": kind, "name": name}
            )
    for name, file, row, held in plan.suppliers.values():
        plan.count("suppliers", True if held else None, False)
        if not held:
            references.append({"step": "suppliers", "file": file, "row": row, "name": name})
    for kind in ("categories", "suppliers"):
        if plan.counts.get(kind, [0, 0, 0])[2]:
            matched[kind] = plan.counts[kind][2]
    return Checked(
        plan=[{"step": "matched", "matched": matched}, *references, *plan.steps],
        counts=[
            ImportCount(
                kind=kind,
                new=plan.counts[kind][0],
                changed=plan.counts[kind][1],
                unchanged=plan.counts[kind][2],
            )
            for kind in KINDS
            if kind in plan.counts
        ],
        problems=plan.problems,
        changes=plan.changes,
    )


# The steps of a plan.


async def _matched(_run: ImportStep, step: dict[str, Any]) -> ImportResult:
    """What the files name that Hoard holds as they have it."""
    return ImportResult(matched=step["matched"])


async def _fields(run: ImportStep, kind: CategoryKind, fields: dict[str, Any]) -> dict[str, Any]:
    """A step's fields as its write path takes them: a category, a supplier
    and a type by id, the kind's default type for none."""
    written: dict[str, Any] = {}
    for name, value in fields.items():
        if name == "category":
            written["category_id"] = (
                None if value is None else await category_id(run.db, kind, value)
            )
        elif name == "supplier":
            written["supplier_contact_id"] = (
                None if value is None else (await community_contacts(run)).get(key(value))
            )
        elif name == "type":
            written["type_id"] = value or await stock.default_type(run.db, kind)
        elif name not in DECLARED:
            written[WRITTEN_AS.get(name, name)] = value
    return written


async def _properties(
    run: ImportStep, kind: RecordKind, record_id: int, values: dict[str, Any]
) -> None:
    """Set and take off a record's property values as a step says."""
    if not values:
        return
    await properties.set_values(
        run.db,
        run.job.plugin,
        run.organization.community_ref,
        run.job.started_by or "",
        kind,
        record_id,
        properties.HoardPropertyValuesSet(
            values=[
                properties.HoardPropertyValueInput(property_id=int(property_id), value=value)
                for property_id, value in values.items()
                if value is not None
            ],
            removed=[int(property_id) for property_id, value in values.items() if value is None],
        ),
    )


def _result(kind: str, made: bool) -> ImportResult:
    return ImportResult(created={kind: 1}) if made else ImportResult(changed={kind: 1})


async def _material(run: ImportStep, step: dict[str, Any]) -> ImportResult:
    """A material, made unless one of its name exists, or changed."""
    db, fields = run.db, step["fields"]
    written = await _fields(run, CategoryKind.MATERIAL, fields)
    if step["id"] is None:
        if await named(db, Material, fields["name"]) is not None:
            return ImportResult(matched={MATERIALS: 1})
        material = await materials.add_material(
            db, materials.MaterialCreate.model_validate(written), sees_costs=False
        )
    else:
        material = await found(db, Material, step["id"])
        await materials.write_material(
            db, material, materials.MaterialWrite.model_validate(material.model_dump() | written)
        )
        db.add(material)
    if any(name in fields for name in DECLARED):
        await declarations.declare(
            db,
            material,
            declarations.DeclarationMaterialWrite.model_validate(
                {
                    "ingredient_name": material.ingredient_name,
                    "allergens": material.allergens,
                    "fragrance_allergens": material.fragrance_allergens,
                }
                | {name: fields[name] for name in DECLARED if name in fields}
            ),
        )
    await _properties(run, RecordKind.MATERIAL, material.id, step["properties"])
    return _result(MATERIALS, step["id"] is None)


async def _base(run: ImportStep, step: dict[str, Any]) -> ImportResult:
    """A base, made unless one of its name exists, or changed."""
    db, fields = run.db, step["fields"]
    written = await _fields(run, CategoryKind.BASE, fields)
    if step["id"] is None:
        if await named(db, Base, fields["name"]) is not None:
            return ImportResult(matched={BASES: 1})
        base = await bases.add_base(db, bases.BaseCreate.model_validate(written), sees_costs=False)
    else:
        base = await found(db, Base, step["id"])
        await bases.write_base(
            db, base, bases.BaseWrite.model_validate(base.model_dump() | written)
        )
        db.add(base)
    await _properties(run, RecordKind.BASE, base.id, step["properties"])
    return _result(BASES, step["id"] is None)


async def _product(run: ImportStep, step: dict[str, Any]) -> ImportResult:
    """A product, made with its first variation unless one of its name
    exists, or changed: a new name or status written first to the synced
    stores listing it."""
    db, fields, sees_costs = run.db, step["fields"], step["sees_costs"]
    written = await _fields(run, CategoryKind.PRODUCT, fields)
    result = _result(PRODUCTS, step["id"] is None)
    if step["id"] is None:
        if await named(db, Product, fields["name"]) is not None:
            return ImportResult(matched={PRODUCTS: 1})
        product, spare = await products.add_product(
            db, products.ProductWrite.model_validate(written), sees_costs
        )
        if step["variation"] is not None:
            await products.write_variation(
                db,
                spare,
                products.ProductVariationWrite.model_validate(
                    await _fields(run, CategoryKind.PRODUCT, step["variation"])
                ),
                sees_costs,
            )
            result.created["variations"] = 1
    else:
        product = await found(db, Product, step["id"])
        if fields:
            await products.change_product(
                db,
                run.job.plugin,
                run.organization.community_ref,
                product,
                products.ProductWrite.model_validate(product.model_dump() | written),
                sees_costs,
                None,
            )
    await _properties(run, RecordKind.PRODUCT, product.id, step["properties"])
    return result


async def _product_variation(run: ImportStep, step: dict[str, Any]) -> ImportResult:
    """A variation, made unless its product has one of its SKU or options,
    or changed: a new SKU or new options written first to the synced stores
    listing it."""
    db, sees_costs = run.db, step["sees_costs"]
    written = await _fields(run, CategoryKind.PRODUCT, step["fields"])
    product_id = step["product"].get("id") or await named(db, Product, step["product"]["name"])
    if product_id is None:
        raise refuse(status.HTTP_404_NOT_FOUND, HoardError.NOT_FOUND)
    if step["id"] is None:
        held = await db.exec(
            select(Variation).where(Variation.product_id == product_id).order_by(col(Variation.id))
        )
        if any(
            (written.get("sku") and each.sku == written["sku"])
            or each.attributes == written.get("attributes", {})
            for each in held
        ):
            return ImportResult(matched={"variations": 1})
        await products.write_variation(
            db,
            Variation(product_id=product_id),
            products.ProductVariationWrite.model_validate(written),
            sees_costs,
        )
        return ImportResult(created={"variations": 1})
    variation = await found(db, Variation, step["id"])
    await products.change_variation(
        db,
        run.job.plugin,
        run.organization.community_ref,
        variation,
        products.ProductVariationWrite.model_validate(variation.model_dump() | written),
        sees_costs,
        None,
    )
    return ImportResult(changed={"variations": 1})


async def _item_id(db: AsyncSession, reference: dict[str, Any]) -> int:
    """The item a step names: by its id, or by its name and options."""
    if "id" in reference:
        return reference["id"]
    if "materials" in reference or "bases" in reference:
        model = Material if "materials" in reference else Base
        item_id = await named(db, model, reference.get("materials") or reference["bases"])
    else:
        product_id = await named(db, Product, reference["product"])
        held = await db.exec(
            select(Variation)
            .where(Variation.product_id == (product_id or 0))
            .order_by(col(Variation.id))
        )
        item_id = next((each.id for each in held if each.attributes == reference["options"]), None)
    if item_id is None:
        raise refuse(status.HTTP_404_NOT_FOUND, HoardError.NOT_FOUND)
    return item_id


async def _recipe(run: ImportStep, step: dict[str, Any]) -> ImportResult:
    """A recipe replaced by its rows: its own columns the file holds, and its
    lines in their order."""
    db, output = run.db, step["output"]
    output_id = await _item_id(db, output["item"])
    recipe = (
        await found(db, Recipe, step["id"])
        if step["id"] is not None
        else (
            await db.exec(select(Recipe).where(getattr(Recipe, output["column"]) == output_id))
        ).first()
    )
    made = recipe is None
    if recipe is None:
        recipe = Recipe()
        setattr(recipe, output["column"], output_id)
    body = RecipeWrite.model_validate(
        recipe.model_dump() | await _fields(run, CategoryKind.BASE, step["fields"]) | {"lines": []}
    )
    recipe.sqlmodel_update(body.model_dump(exclude={"lines"}))
    lines = [
        recipes.Line(await _item_id(db, line["item"]), Decimal(line["written"]), line["unit"])
        for line in step["lines"]
    ]
    if not recipe.by_ratio:
        recipe.total = recipe.total_unit_id = None
    await recipes.save(db, recipe, lines)
    return _result(RECIPES, made)


IMPORTER = Importer(
    id="spreadsheets",
    files=tuple(ImportFile(name, required=False) for name in (MATERIALS, BASES, PRODUCTS, RECIPES)),
    options=SpreadsheetOptions,
    check=check,
    step=stepper(
        {
            "matched": _matched,
            "categories": category,
            "suppliers": supplier,
            MATERIALS: _material,
            BASES: _base,
            PRODUCTS: _product,
            "variations": _product_variation,
            RECIPES: _recipe,
        }
    ),
)
