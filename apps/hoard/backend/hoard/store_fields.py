"""A store's fields, aligned with Hoard's: each of the fields a store defines
for its products and variants (``hoard.channels.StoreField``) mapped to what
it fills in Hoard (:class:`~hoard.records.StoreFieldMapping`).

**Suggestions** (:func:`suggest`) are explicit checks, in order:

1. Hoard's allergens, for a field named "Allergens", or one whose entries
   are mostly Hoard's allergen names; Hoard's ingredient list, for a field
   named "Ingredients".
2. A product property with the field's name and the type its values take.
3. A new product property of that type (:func:`property_type`): text,
   number, true-or-false, date, date-and-time and address fields take their
   own type; a field picking from a fixed list, as category metafields do,
   and a list of text, a select or a multi-select of the entries' names.
4. Nothing: the field is left out.

Each of an allergens field's values is paired with a Hoard allergen by name
(:func:`pair_allergens`); a value nothing pairs with waits to be paired by
hand.

**Which way each field flows** follows from what it fills:

* **A property** is kept in the store and edited in either. Reading a
  product's values (:func:`take`), at the store's sync, through its webhooks
  and the reconcile, fills the property; an edit made in Hoard is written to
  the store first, and the store's answer is saved (:func:`send_properties`).
* **Allergens and the ingredient list**, which Hoard works out from recipes,
  are Hoard's. Reading them records what the store holds of each
  (:class:`~hoard.records.StoreFieldValue`), as if Hoard had sent it, and
  writes nothing. When a recipe, or a material's declaration, changes what
  Hoard works out, Hoard sends the new value (:func:`queue_changed`, as a
  job); where Hoard's value and the store's differ otherwise, they are listed
  (:func:`differing`) and sent only when someone asks (:func:`queue_send`).

Hoard does not know a product's allergens while a material in it has none
recorded, nor its ingredients while it has no recipe: such a value is never
sent nor listed.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from hoard.channels import Connector, FieldValue, FieldValues, StoreField
from hoard.declarations import Declaration, Declarations
from hoard.properties import add_option
from hoard.records import (
    Allergen,
    FieldOwner,
    FieldTarget,
    Listing,
    Product,
    PropertyDefinition,
    PropertyType,
    PropertyValue,
    RecordKind,
    Store,
    StoreFieldMapping,
    StoreFieldValue,
    Variation,
)
from seneschal_core.db import WriteSession
from seneschal_core.jobs import Job, JobState, enqueue

#: The kind of job that reads mapped fields in and sends Hoard's values out.
STORE_FIELDS_JOB = "store_fields"
#: How many products' values one commit takes.
AT_ONCE = 50


class StoreFieldError(StrEnum):
    """Why a field's mapping, or a value for it, was refused: codes pages translate."""

    #: The store has no such field.
    STORE_FIELD_UNKNOWN = "STORE_FIELD_UNKNOWN"
    #: The field cannot fill what it was mapped to.
    STORE_FIELD_TARGET_INVALID = "STORE_FIELD_TARGET_INVALID"
    #: A value Hoard holds that the store's field cannot.
    STORE_FIELD_VALUE_INVALID = "STORE_FIELD_VALUE_INVALID"
    #: The store is not synced: its fields are neither read nor written.
    STORE_NOT_SYNCED = "STORE_NOT_SYNCED"


class StoreFieldRefused(Exception):
    def __init__(self, code: StoreFieldError) -> None:
        super().__init__(code.value)
        self.code = code


# Types.

#: The store's types a property holds as its own type.
_SCALARS: dict[str, PropertyType] = {
    "single_line_text_field": PropertyType.TEXT,
    "multi_line_text_field": PropertyType.TEXT,
    "number_integer": PropertyType.NUMBER,
    "number_decimal": PropertyType.NUMBER,
    "boolean": PropertyType.CHECKBOX,
    "date": PropertyType.DATE,
    "date_time": PropertyType.DATETIME,
    "url": PropertyType.URL,
}
_TEXT = frozenset({"single_line_text_field", "multi_line_text_field"})
_TEXT_LIST = "list.single_line_text_field"
_REFERENCE = "metaobject_reference"
_REFERENCES = "list.metaobject_reference"
#: The types whose values are lists, kept as JSON.
_LISTS = frozenset({_TEXT_LIST, _REFERENCES})
#: The types each derived field may be kept in.
_DERIVED_TYPES = {
    FieldTarget.ALLERGENS: _TEXT | _LISTS,
    FieldTarget.INGREDIENTS: _TEXT | {_TEXT_LIST},
}
#: The names, ignoring case, that suggest each derived field.
_DERIVED_NAMES = {
    FieldTarget.ALLERGENS: frozenset({"allergens", "allergen information"}),
    FieldTarget.INGREDIENTS: frozenset({"ingredients", "ingredient list"}),
}

#: Each allergen's names in English, the first the one Hoard writes.
ALLERGEN_NAMES: dict[Allergen, tuple[str, ...]] = {
    Allergen.CELERY: ("Celery",),
    Allergen.CRUSTACEANS: ("Crustaceans", "Crustacean shellfish", "Shellfish"),
    Allergen.EGGS: ("Eggs", "Egg"),
    Allergen.FISH: ("Fish",),
    Allergen.GLUTEN: ("Gluten", "Cereals containing gluten"),
    Allergen.LUPIN: ("Lupin", "Lupine"),
    Allergen.MILK: ("Milk", "Dairy"),
    Allergen.MOLLUSCS: ("Molluscs", "Mollusks"),
    Allergen.MUSTARD: ("Mustard",),
    Allergen.PEANUTS: ("Peanuts", "Peanut"),
    Allergen.SESAME: ("Sesame", "Sesame seeds"),
    Allergen.SOYBEANS: ("Soybeans", "Soy", "Soya"),
    Allergen.SULPHITES: ("Sulphites", "Sulfites", "Sulphur dioxide", "Sulfur dioxide"),
    Allergen.TREE_NUTS: ("Tree nuts", "Nuts", "Tree nut"),
    Allergen.WHEAT: ("Wheat",),
}


def _plain(text: str) -> str:
    """``text`` for comparing names: lower case, with underscores and hyphens
    as spaces, and single spaces."""
    return " ".join(text.casefold().replace("_", " ").replace("-", " ").split())


_BY_NAME = {
    _plain(name): allergen
    for allergen, names in ALLERGEN_NAMES.items()
    for name in (allergen.value, *names)
}


def allergen_named(name: str) -> Allergen | None:
    """The allergen ``name`` names, in English or by its key."""
    return _BY_NAME.get(_plain(name))


def pair_allergens(choices: Mapping[str, str]) -> dict[str, str | None]:
    """Each of a field's values paired with the allergen its name names, or
    None where none is."""
    return {
        value: (allergen.value if (allergen := allergen_named(name)) else None)
        for value, name in choices.items()
    }


def property_type(store_field: StoreField) -> PropertyType | None:
    """The type of a property holding ``store_field``'s values; None for a
    field no property holds."""
    kind = store_field.type
    if kind in (_REFERENCE, _REFERENCES):
        if not store_field.choices:
            return None
        return PropertyType.SELECT if kind == _REFERENCE else PropertyType.MULTI_SELECT
    if kind == _TEXT_LIST:
        return PropertyType.MULTI_SELECT
    if kind in _TEXT and store_field.choices:
        return PropertyType.SELECT
    return _SCALARS.get(kind)


def allows(
    store_field: StoreField, target: FieldTarget, definition: PropertyDefinition | None = None
) -> bool:
    """Whether ``store_field`` can fill ``target``: a derived field in a type
    its values take, or, for a product's field, a product property of the
    type its values take (``definition``, or a new one where None)."""
    if target is not FieldTarget.PROPERTY:
        return store_field.type in _DERIVED_TYPES[target]
    wanted = property_type(store_field)
    if store_field.owner is not FieldOwner.PRODUCT or wanted is None:
        return False
    return definition is None or (
        definition.kind is RecordKind.PRODUCT and definition.type is wanted
    )


@dataclass(frozen=True)
class FieldChoice:
    """What one of the store's fields, by its owner and ``namespace.key``,
    fills: a derived field, or a property; a property with no id is a new one,
    named as the store names the field. ``pairs`` pairs an allergens field's
    values by hand."""

    owner: FieldOwner
    handle: str
    target: FieldTarget
    property_id: int | None = None
    pairs: Mapping[str, str | None] = field(default_factory=dict)

    def args(self) -> dict[str, Any]:
        return {
            "owner": self.owner.value,
            "handle": self.handle,
            "target": self.target.value,
            "property_id": self.property_id,
            "pairs": dict(self.pairs),
        }

    @classmethod
    def from_args(cls, args: Mapping[str, Any]) -> FieldChoice:
        return cls(
            owner=FieldOwner(args["owner"]),
            handle=str(args["handle"]),
            target=FieldTarget(args["target"]),
            property_id=args.get("property_id"),
            pairs=dict(args.get("pairs") or {}),
        )


def suggest(
    store_field: StoreField, properties: Sequence[PropertyDefinition]
) -> FieldChoice | None:
    """What Hoard suggests ``store_field`` fills, among the product
    ``properties``; None to leave it out."""

    def choice(target: FieldTarget, property_id: int | None = None) -> FieldChoice:
        pairs = pair_allergens(store_field.choices) if target is FieldTarget.ALLERGENS else {}
        return FieldChoice(store_field.owner, store_field.handle, target, property_id, pairs)

    name = _plain(store_field.name)
    for target in (FieldTarget.ALLERGENS, FieldTarget.INGREDIENTS):
        if allows(store_field, target) and name in _DERIVED_NAMES[target]:
            return choice(target)
    if allows(store_field, FieldTarget.ALLERGENS) and len(store_field.choices) >= 2:
        paired = [value for value in pair_allergens(store_field.choices).values() if value]
        if len(paired) * 2 >= len(store_field.choices):
            return choice(FieldTarget.ALLERGENS)
    if not allows(store_field, FieldTarget.PROPERTY):
        return None
    for definition in properties:
        if _plain(definition.name) == name and allows(
            store_field, FieldTarget.PROPERTY, definition
        ):
            return choice(FieldTarget.PROPERTY, definition.id)
    return choice(FieldTarget.PROPERTY)


def check(
    store_field: StoreField | None, choice: FieldChoice, definition: PropertyDefinition | None
) -> None:
    """Refuse a choice the field cannot take."""
    if store_field is None:
        raise StoreFieldRefused(StoreFieldError.STORE_FIELD_UNKNOWN)
    if (choice.target is FieldTarget.PROPERTY and choice.property_id is not None) and (
        definition is None
    ):
        raise StoreFieldRefused(StoreFieldError.STORE_FIELD_TARGET_INVALID)
    if not allows(store_field, choice.target, definition):
        raise StoreFieldRefused(StoreFieldError.STORE_FIELD_TARGET_INVALID)
    if any(value is not None and value not in Allergen for value in choice.pairs.values()):
        raise StoreFieldRefused(StoreFieldError.STORE_FIELD_TARGET_INVALID)


# Keeping mappings.


async def mappings(db: AsyncSession, store: Store) -> list[StoreFieldMapping]:
    found = await db.exec(
        select(StoreFieldMapping)
        .where(StoreFieldMapping.store_id == store.id)
        .order_by(col(StoreFieldMapping.id))
    )
    return list(found.all())


async def _new_property(db: WriteSession, store_field: StoreField) -> PropertyDefinition:
    """The product property a new mapping fills: the one named as the field
    and of its type, else one made so, named with its ``namespace.key`` where
    another type holds the name."""
    wanted = property_type(store_field)
    assert wanted is not None
    for name in (store_field.name, f"{store_field.name} ({store_field.handle})"):
        held = (
            await db.exec(
                select(PropertyDefinition).where(
                    PropertyDefinition.kind == RecordKind.PRODUCT,
                    func.lower(PropertyDefinition.name) == name.lower(),
                )
            )
        ).first()
        if held is not None and held.type is wanted:
            return held
        if held is None:
            made = PropertyDefinition(
                organization_id=db.organization_id,
                kind=RecordKind.PRODUCT,
                name=name[:100],
                type=wanted,
            )
            if wanted in (PropertyType.SELECT, PropertyType.MULTI_SELECT):
                made.options = []
            db.add(made)
            await db.flush()
            return made
    raise StoreFieldRefused(StoreFieldError.STORE_FIELD_TARGET_INVALID)


async def keep(
    db: WriteSession, store: Store, found: Sequence[StoreField], choice: FieldChoice
) -> StoreFieldMapping | None:
    """Keep ``choice`` as the store's mapping for its field, making the new
    property it asks for; None for a field the store no longer has. Raises
    :class:`StoreFieldRefused` for one the field cannot take."""
    store_field = next(
        (each for each in found if (each.owner, each.handle) == (choice.owner, choice.handle)),
        None,
    )
    definition = None
    if choice.target is FieldTarget.PROPERTY and choice.property_id is not None:
        definition = await db.get(PropertyDefinition, choice.property_id)
    check(store_field, choice, definition)
    assert store_field is not None
    if choice.target is FieldTarget.PROPERTY and definition is None:
        definition = await _new_property(db, store_field)
    held = (
        await db.exec(
            select(StoreFieldMapping).where(
                StoreFieldMapping.store_id == store.id,
                StoreFieldMapping.owner == choice.owner,
                StoreFieldMapping.handle == choice.handle,
            )
        )
    ).first()
    mapping = held or StoreFieldMapping(
        organization_id=db.organization_id,
        store_id=store.id,
        owner=choice.owner,
        handle=choice.handle,
        name="",
        type="",
        target=choice.target,
    )
    mapping.name, mapping.type, mapping.target = store_field.name, store_field.type, choice.target
    mapping.property_id = None if definition is None else definition.id
    if choice.target is FieldTarget.ALLERGENS:
        before = dict(mapping.pairs) if held and held.target is choice.target else {}
        mapping.pairs = {
            **before,
            **{
                value: allergen
                for value, allergen in pair_allergens(store_field.choices).items()
                if allergen is not None or value not in before
            },
            **choice.pairs,
        }
    elif definition is not None and definition.type in (
        PropertyType.SELECT,
        PropertyType.MULTI_SELECT,
    ):
        mapping.pairs = {
            value: add_option(definition, name)["value"]
            for value, name in store_field.choices.items()
        }
        db.add(definition)
    else:
        mapping.pairs = {}
    db.add(mapping)
    await db.flush()
    return mapping


# Values: as the store keeps them, and as Hoard does.


def _items(mapping: StoreFieldMapping, raw: str | None) -> list[str]:
    """A value's entries: a list's items, or text's comma-separated parts."""
    if raw is None:
        return []
    if mapping.type in _LISTS:
        try:
            listed = json.loads(raw)
        except ValueError:
            return []
        return [str(item) for item in listed] if isinstance(listed, list) else []
    if mapping.target is FieldTarget.PROPERTY:
        return [raw]
    return [part.strip() for part in raw.split(",") if part.strip()]


def _joined(mapping: StoreFieldMapping, items: Sequence[str]) -> str:
    return json.dumps(list(items)) if mapping.type in _LISTS else ", ".join(items)


def _is_reference(mapping: StoreFieldMapping) -> bool:
    return mapping.type in (_REFERENCE, _REFERENCES)


def _allergen_of(mapping: StoreFieldMapping, item: str) -> str | None:
    """The allergen a store's value is paired with, or, for one not paired
    yet, the one its name names; None where none is."""
    if item in mapping.pairs:
        return mapping.pairs[item]
    allergen = None if _is_reference(mapping) else allergen_named(item)
    return None if allergen is None else allergen.value


def _pair(mapping: StoreFieldMapping, item: str) -> None:
    """Pair a store's value not paired yet with the allergen its name names."""
    if item not in mapping.pairs:
        mapping.pairs = {**mapping.pairs, item: _allergen_of(mapping, item)}


def derived(mapping: StoreFieldMapping, declared: Declaration | None) -> str | None:
    """Hoard's value of a derived field, as the store keeps it; None where
    Hoard does not know it."""
    if declared is None:
        return None
    if mapping.target is FieldTarget.INGREDIENTS:
        return _joined(mapping, declared.ingredients) if declared.ingredients else None
    if declared.undeclared:
        return None
    paired: dict[str, str] = {}
    for value, allergen in mapping.pairs.items():
        if allergen is not None:
            paired.setdefault(allergen, value)
    items = []
    for allergen in declared.allergens:
        value = paired.get(allergen.value)
        if value is None and not _is_reference(mapping):
            value = ALLERGEN_NAMES[allergen][0]
        if value is not None:
            items.append(value)
    return _joined(mapping, items)


def same(mapping: StoreFieldMapping, hoard: str | None, store: str | None) -> bool:
    """Whether Hoard's value of a derived field and the store's say the same:
    the same allergens, or the same ingredients in the same order."""
    if mapping.target is FieldTarget.INGREDIENTS:
        return [_plain(item) for item in _items(mapping, hoard)] == [
            _plain(item) for item in _items(mapping, store)
        ]

    def allergens(raw: str | None) -> set[str]:
        return {_allergen_of(mapping, item) or f"?{item}" for item in _items(mapping, raw)}

    return allergens(hoard) == allergens(store)


def _number(raw: str) -> float | None:
    try:
        number = Decimal(raw)
    except InvalidOperation:
        return None
    return float(number) if number.is_finite() else None


def _parses(parse: Callable[[str], object], raw: str) -> bool:
    try:
        parse(raw)
    except ValueError:
        return False
    return True


def to_hoard(mapping: StoreFieldMapping, definition: PropertyDefinition, raw: str) -> Any:
    """A store's value as the property ``definition`` holds it; None for one
    it cannot hold. A list's entry no option holds yet becomes one, but for
    a reference, whose name only the field's entries give."""
    kind = definition.type
    if kind in (PropertyType.TEXT, PropertyType.URL):
        return raw or None
    if kind is PropertyType.NUMBER:
        return _number(raw)
    if kind is PropertyType.CHECKBOX:
        return {"true": True, "false": False}.get(raw)
    if kind is PropertyType.DATE:
        return raw if _parses(date.fromisoformat, raw) else None
    if kind is PropertyType.DATETIME:
        return raw if _parses(datetime.fromisoformat, raw) else None
    options = []
    for item in _items(mapping, raw):
        if item not in mapping.pairs and not _is_reference(mapping):
            mapping.pairs = {**mapping.pairs, item: add_option(definition, item)["value"]}
        if mapping.pairs.get(item) is not None:
            options.append(mapping.pairs[item])
    if kind is PropertyType.SELECT:
        return options[0] if options else None
    return list(dict.fromkeys(options)) or None


def to_store(mapping: StoreFieldMapping, definition: PropertyDefinition, value: Any) -> str | None:
    """A property's value as the store's field keeps it; None for none.
    Raises :class:`StoreFieldRefused` for one the field cannot hold."""
    if value is None:
        return None
    kind = definition.type
    if kind is PropertyType.CHECKBOX:
        return "true" if value else "false"
    if kind is PropertyType.NUMBER:
        number = Decimal(str(value))
        if mapping.type == "number_integer":
            if number != number.to_integral_value():
                raise StoreFieldRefused(StoreFieldError.STORE_FIELD_VALUE_INVALID)
            return str(int(number))
        return format(number.normalize(), "f")
    if kind not in (PropertyType.SELECT, PropertyType.MULTI_SELECT):
        return str(value)
    reverse: dict[str, str] = {}
    for item, option in mapping.pairs.items():
        if option is not None:
            reverse.setdefault(option, item)
    labels = {option["value"]: option["label"] for option in definition.options or []}
    items = []
    for option in value if isinstance(value, list) else [value]:
        item = reverse.get(option)
        if item is None and not _is_reference(mapping) and option in labels:
            item = labels[option]
        if item is None:
            raise StoreFieldRefused(StoreFieldError.STORE_FIELD_VALUE_INVALID)
        items.append(item)
    if mapping.type in _LISTS:
        return json.dumps(items)
    return items[0] if items else None


# What each of the store's products and variants is in Hoard.


async def _products(db: AsyncSession, store: Store) -> dict[str, int]:
    """The Hoard product each of the store's products is, by the store's id:
    the product its first linked listing's variation is of."""
    rows = await db.exec(
        select(Listing.product_id, Variation.product_id)
        .join(Variation, col(Variation.id) == Listing.item_id)
        .where(Listing.store_id == store.id)
        .order_by(col(Listing.id))
    )
    found: dict[str, int] = {}
    for store_product, product_id in rows.all():
        found.setdefault(store_product, product_id)
    return found


async def _variants(db: AsyncSession, store: Store) -> dict[str, int]:
    """The variation each of the store's variants is, by the store's id."""
    rows = await db.exec(
        select(Listing.variant_id, Variation.id)
        .join(Variation, col(Variation.id) == Listing.item_id)
        .where(Listing.store_id == store.id, col(Listing.variant_id).is_not(None))
    )
    return {variant_id: variation_id for variant_id, variation_id in rows.all() if variant_id}


class _Hoard:
    """Hoard's values of derived fields, each declaration read once."""

    def __init__(self, db: AsyncSession, products: dict[str, int], variants: dict[str, int]):
        self.declarations = Declarations(db)
        self.db, self.products, self.variants = db, products, variants

    async def declared(self, mapping: StoreFieldMapping, owner_id: str) -> Declaration | None:
        if mapping.owner is FieldOwner.VARIANT:
            variation = self.variants.get(owner_id)
            return None if variation is None else await self.declarations.item(variation)
        product_id = self.products.get(owner_id)
        product = None if product_id is None else await self.db.get(Product, product_id)
        return None if product is None else await self.declarations.product(product)

    async def value(self, mapping: StoreFieldMapping, owner_id: str) -> str | None:
        return derived(mapping, await self.declared(mapping, owner_id))


async def _set_property(db: WriteSession, product_id: int, definition_id: int, value: Any) -> None:
    """Hold ``value`` as a product's value of a property, or none for None."""
    held = (
        await db.exec(
            select(PropertyValue).where(
                PropertyValue.product_id == product_id,
                PropertyValue.definition_id == definition_id,
            )
        )
    ).first()
    if value is None:
        if held is not None:
            await db.delete(held)
        return
    row = held or PropertyValue(
        organization_id=db.organization_id,
        definition_id=definition_id,
        product_id=product_id,
        value=value,
    )
    row.value = value
    db.add(row)


async def take(db: WriteSession, store: Store, values: FieldValues, *, clear: bool) -> None:
    """Keep what the store holds of its mapped fields on the products and
    variants in ``values``: each property filled, a value the store does not
    hold taking Hoard's off where ``clear``; each derived field's value
    recorded as the store's, and, where Hoard had not compared it yet,
    Hoard's own beside it. Nothing is sent."""
    kept = await mappings(db, store)
    if not kept or not values:
        return
    products, variants = await _products(db, store), await _variants(db, store)
    hoard = _Hoard(db, products, variants)
    definitions = {
        definition.id: definition
        for definition in await db.exec(
            select(PropertyDefinition).where(
                col(PropertyDefinition.id).in_(
                    [mapping.property_id for mapping in kept if mapping.property_id]
                )
            )
        )
    }
    for owner_id, held in values.items():
        owner = (
            FieldOwner.PRODUCT
            if owner_id in products
            else FieldOwner.VARIANT
            if owner_id in variants
            else None
        )
        for mapping in kept:
            if mapping.owner is not owner:
                continue
            raw = held.get(mapping.handle)
            if mapping.target is FieldTarget.PROPERTY:
                definition = definitions.get(mapping.property_id or 0)
                if definition is None:
                    continue
                value = None if raw is None else to_hoard(mapping, definition, raw)
                if value is not None or clear:
                    await _set_property(db, products[owner_id], definition.id, value)
                db.add(definition)
                continue
            if mapping.target is FieldTarget.ALLERGENS:
                for item in _items(mapping, raw):
                    _pair(mapping, item)
            await db.execute(
                insert(StoreFieldValue)
                .values(
                    organization_id=db.organization_id,
                    field_id=mapping.id,
                    owner_id=owner_id,
                    store_value=raw,
                    hoard_value=await hoard.value(mapping, owner_id),
                )
                .on_conflict_do_update(
                    index_elements=["organization_id", "field_id", "owner_id"],
                    set_={"store_value": raw},
                )
            )
    for mapping in kept:
        db.add(mapping)
    await db.flush()


def handles(kept: Iterable[StoreFieldMapping]) -> dict[FieldOwner, list[str]]:
    """The fields ``kept`` names, by owner."""
    named: dict[FieldOwner, list[str]] = defaultdict(list)
    for mapping in kept:
        named[mapping.owner].append(mapping.handle)
    return dict(named)


async def refresh(
    db: WriteSession, store: Store, connector: Connector, product_ids: Sequence[str]
) -> None:
    """Read again the mapped fields of the store's products ``product_ids``,
    which the store changed, and keep them (:func:`take`)."""
    kept = await mappings(db, store)
    if not kept or not product_ids:
        return
    values = await connector.field_values(handles(kept), list(product_ids))
    await take(db, store, values, clear=True)


#: Opens a session for the job's organization, as its starter.
Sessions = Callable[[], WriteSession]
#: Told how many products' values were kept, out of how many.
Progress = Callable[[int, int], Awaitable[None]]


async def read_in(
    sessions: Sessions,
    store_id: int,
    connector: Connector,
    field_ids: Sequence[int],
    progress: Progress,
) -> None:
    """Read every product's values of the fields ``field_ids`` maps, in
    bulk, and keep them (:func:`take`), :data:`AT_ONCE` products a commit."""
    async with sessions() as db:
        store = await db.get_one(Store, store_id)
        reading = [mapping for mapping in await mappings(db, store) if mapping.id in field_ids]
    if not reading:
        return
    values = await connector.field_values(handles(reading), None)
    owners = sorted(values)
    await progress(0, len(owners))
    for start in range(0, len(owners), AT_ONCE):
        async with sessions() as db:
            store = await db.get_one(Store, store_id)
            batch = {owner: values[owner] for owner in owners[start : start + AT_ONCE]}
            await take(db, store, batch, clear=False)
            await db.commit()
        await progress(min(start + AT_ONCE, len(owners)), len(owners))


# Sending Hoard's values.


Connect = Callable[[Store], Awaitable[Connector]]


async def send_properties(
    db: WriteSession, connect: Connect, product_id: int, changes: Mapping[int, Any]
) -> dict[int, Any]:
    """Write a product's changed property values, by property, to each synced
    store whose fields they fill, first; answer the changes with each value
    as the store answered it. Raises :class:`ConnectRefused` where a store
    refuses, and :class:`StoreFieldRefused` for a value a field cannot hold."""
    answered = dict(changes)
    found = await db.exec(
        select(StoreFieldMapping, Store)
        .join(Store, col(Store.id) == StoreFieldMapping.store_id)
        .where(
            col(StoreFieldMapping.property_id).in_(list(changes)),
            col(Store.connected).is_(True),
            col(Store.imported_at).is_not(None),
        )
    )
    by_store: dict[int, tuple[Store, list[StoreFieldMapping]]] = {}
    for mapping, store in found.all():
        by_store.setdefault(store.id, (store, []))[1].append(mapping)
    for store, kept in by_store.values():
        owners = [
            owner for owner, held in (await _products(db, store)).items() if held == product_id
        ]
        if not owners:
            continue
        sending: list[tuple[FieldValue, StoreFieldMapping, PropertyDefinition]] = []
        for mapping in kept:
            definition = await db.get_one(PropertyDefinition, mapping.property_id)
            raw = to_store(mapping, definition, changes[definition.id])
            sending.extend(
                (FieldValue(owner, mapping.handle, mapping.type, raw), mapping, definition)
                for owner in owners
            )
        written = await (await connect(store)).set_field_values([each for each, _, _ in sending])
        for value, (_, mapping, definition) in zip(written, sending, strict=True):
            answered[definition.id] = (
                None if value.value is None else to_hoard(mapping, definition, value.value)
            )
            db.add(mapping)
            db.add(definition)
    return answered


async def send(sessions: Sessions, store_id: int, connector: Connector, every: bool) -> None:
    """Send each derived field's value Hoard works out where the store holds
    another: every one where ``every``, else those Hoard's own value changed
    since it was last compared. Each sent value is recorded as the store's."""
    async with sessions() as db:
        store = await db.get_one(Store, store_id)
        kept = {
            mapping.id: mapping
            for mapping in await mappings(db, store)
            if mapping.target is not FieldTarget.PROPERTY
        }
        if not kept:
            return
        hoard = _Hoard(db, await _products(db, store), await _variants(db, store))
        rows = await db.exec(
            select(StoreFieldValue)
            .where(col(StoreFieldValue.field_id).in_(list(kept)))
            .order_by(col(StoreFieldValue.id))
        )
        sending: list[tuple[StoreFieldValue, FieldValue]] = []
        for row in rows.all():
            mapping = kept[row.field_id]
            now = await hoard.value(mapping, row.owner_id)
            changed = now != row.hoard_value
            row.hoard_value = now
            db.add(row)
            if now is None or same(mapping, now, row.store_value):
                continue
            if every or changed:
                sending.append((row, FieldValue(row.owner_id, mapping.handle, mapping.type, now)))
        for mapping in kept.values():
            db.add(mapping)
        await db.commit()
    for start in range(0, len(sending), AT_ONCE):
        chunk = sending[start : start + AT_ONCE]
        written = await connector.set_field_values([value for _, value in chunk])
        async with sessions() as db:
            for (row, _), value in zip(chunk, written, strict=True):
                held = await db.get_one(StoreFieldValue, row.id)
                held.store_value = value.value
                db.add(held)
            await db.commit()


@dataclass(frozen=True)
class Difference:
    """A derived field whose value differs between Hoard and the store, on
    one of the store's products or variants."""

    mapping: StoreFieldMapping
    owner_id: str
    product: Product | None
    hoard: list[str]
    store: list[str]


async def differing(db: AsyncSession, store: Store) -> list[Difference]:
    """Where Hoard's value of a derived field differs from the store's."""
    kept = {
        mapping.id: mapping
        for mapping in await mappings(db, store)
        if mapping.target is not FieldTarget.PROPERTY
    }
    if not kept:
        return []
    products, variants = await _products(db, store), await _variants(db, store)
    hoard = _Hoard(db, products, variants)
    rows = await db.exec(
        select(StoreFieldValue)
        .where(col(StoreFieldValue.field_id).in_(list(kept)))
        .order_by(col(StoreFieldValue.field_id), col(StoreFieldValue.id))
    )
    found = []
    for row in rows.all():
        mapping = kept[row.field_id]
        now = await hoard.value(mapping, row.owner_id)
        if now is None or same(mapping, now, row.store_value):
            continue
        product_id = products.get(row.owner_id)
        if product_id is None and row.owner_id in variants:
            variation = await db.get(Variation, variants[row.owner_id])
            product_id = None if variation is None else variation.product_id
        found.append(
            Difference(
                mapping=mapping,
                owner_id=row.owner_id,
                product=None if product_id is None else await db.get(Product, product_id),
                hoard=_items(mapping, now),
                store=_items(mapping, row.store_value),
            )
        )
    return found


# The job.


def job_args(store: Store, *, read: Sequence[int] = (), send: bool = False) -> dict[str, Any]:
    """A fields job's arguments: the fields to read in, and whether to send
    every value that differs rather than those Hoard's changed."""
    return {"store_id": store.id, "read": list(read), "send": send}


async def queue_send(db: WriteSession, store: Store, started_by: str | None) -> Job:
    """Queue sending Hoard's values of every derived field that differs."""
    return await enqueue(
        db,
        STORE_FIELDS_JOB,
        job_args(store, send=True),
        started_by=started_by,
        unique_key=f"{store.id}:send",
    )


async def queue_read(
    db: WriteSession, store: Store, mapping: StoreFieldMapping, started_by: str | None
) -> Job:
    """Queue reading a field just mapped in, as a store's sync does."""
    return await enqueue(
        db, STORE_FIELDS_JOB, job_args(store, read=[mapping.id]), started_by=started_by
    )


async def queue_changed(db: WriteSession) -> None:
    """Queue sending what Hoard works out where a change to recipes or
    declarations changed it, for each synced store with a derived field
    mapped, unless one is waiting already."""
    stores = await db.exec(
        select(Store)
        .join(StoreFieldMapping, col(StoreFieldMapping.store_id) == Store.id)
        .where(
            col(Store.imported_at).is_not(None),
            col(StoreFieldMapping.target) != FieldTarget.PROPERTY,
        )
        .distinct()
    )
    for store in stores.all():
        args = job_args(store)
        waiting = await db.exec(
            select(Job.id).where(
                Job.kind == STORE_FIELDS_JOB, Job.state == JobState.QUEUED, Job.args == args
            )
        )
        if waiting.first() is None:
            await enqueue(db, STORE_FIELDS_JOB, args, started_by=None)
