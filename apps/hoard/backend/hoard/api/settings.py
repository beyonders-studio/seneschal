"""The organization's settings: its currency, the rules it keeps records for,
what it calls a base and a recipe, what an hour of work costs and the overhead
on what goods cost, units,
categories with their picking rules and default types, the types of
material, base and product, locations and adjustment reasons. Every member
reads them, but for the labour rate and the overhead, which only a member who
sees costs reads; only a community admin changes them
(:data:`~hoard.api.deps.AdminDep`), and those only one who sees costs.

An organization starts with its main location, the default adjustment reasons,
Hoard's standard units (``hoard.units``) and each kind's default type, which
Hoard makes itself the first time each list is read. A standard unit is never
renamed or removed, and no unit of the organization's own is named like one.
A default type is renamed but never removed, and a type records have stays
until they are moved to another. A location other than the main one may be
marked a consignee's (``hoard.summary``), once it names its contact and while
no store's location is mapped to it.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterator, Sequence
from contextlib import contextmanager
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any

from fastapi import APIRouter, Query, status
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlmodel import SQLModel, col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from hoard import stock, units
from hoard.api.adjustments import AdjustmentError
from hoard.api.deps import AdminDep, HoardError, SeesCostsDep
from hoard.records import (
    SHAPES,
    AdjustmentReason,
    AutoManufacture,
    Category,
    CategoryKind,
    DefaultReason,
    ItemType,
    Location,
    OrganizationSettings,
    Picking,
    Regime,
    StoreLocation,
    TypeShape,
    Unit,
)
from seneschal_core.api import (
    OrganizationDep,
    OrganizationSessionDep,
    PluginDep,
    WriteSessionDep,
    refuse,
)
from seneschal_core.db import WriteSession
from seneschal_core.organizations import Organization

router = APIRouter(prefix="/settings", tags=["settings"])


class SettingsError(StrEnum):
    """Why a change to the settings, or to a record naming them, was refused."""

    #: Another of the same kind already has the name.
    NAME_TAKEN = "NAME_TAKEN"
    #: Every location but the main one needs a name.
    NAME_REQUIRED = "NAME_REQUIRED"
    #: Records still name it, so it stays.
    IN_USE = "IN_USE"
    #: The main location stays.
    MAIN_LOCATION = "MAIN_LOCATION"
    #: A unit named is not one of the organization's.
    UNIT_UNKNOWN = "UNIT_UNKNOWN"
    #: The category named is not one of the organization's for this kind of record.
    CATEGORY_UNKNOWN = "CATEGORY_UNKNOWN"
    #: A standard unit keeps its name, and stays.
    UNIT_STANDARD = "UNIT_STANDARD"
    #: Numbers are recorded in an item's tracking unit, so it stays: by its
    #: stock movements, its purchases, its stocktake counts, a recipe holding
    #: it, its own recipe, or a run making or using it.
    TRACKING_UNIT_IN_STOCK = "TRACKING_UNIT_IN_STOCK"
    TRACKING_UNIT_IN_PURCHASES = "TRACKING_UNIT_IN_PURCHASES"
    TRACKING_UNIT_IN_STOCKTAKES = "TRACKING_UNIT_IN_STOCKTAKES"
    TRACKING_UNIT_IN_RECIPES = "TRACKING_UNIT_IN_RECIPES"
    TRACKING_UNIT_IN_OWN_RECIPE = "TRACKING_UNIT_IN_OWN_RECIPE"
    TRACKING_UNIT_IN_RUNS = "TRACKING_UNIT_IN_RUNS"
    #: The type named is not one of the organization's for this kind of record.
    TYPE_UNKNOWN = "TYPE_UNKNOWN"
    #: The shape named is not one Hoard ships for this kind of record.
    SHAPE_UNKNOWN = "SHAPE_UNKNOWN"
    #: A kind's default type stays.
    TYPE_DEFAULT = "TYPE_DEFAULT"
    #: A consignee's location names the consignee's contact, and is never the
    #: main location.
    CONSIGNEE_CONTACT = "CONSIGNEE_CONTACT"
    #: A store sells the location's stock, so it is not a consignee's.
    CONSIGNEE_MAPPED = "CONSIGNEE_MAPPED"


_READ: dict[int | str, dict[str, Any]] = {401: {"description": "NOT_AUTHENTICATED"}}
_CHANGE: dict[int | str, dict[str, Any]] = {
    **_READ,
    403: {"description": "ADMIN_ONLY, READ_ONLY, COSTS_HIDDEN"},
    404: {"description": "NOT_FOUND"},
    409: {
        "description": "NAME_TAKEN, IN_USE, MAIN_LOCATION, UNIT_STANDARD, TYPE_DEFAULT, "
        "CONSIGNEE_MAPPED"
    },
    422: {"description": "NAME_REQUIRED, TYPE_UNKNOWN, SHAPE_UNKNOWN, CONSIGNEE_CONTACT"},
}

Name = Annotated[str, Field(min_length=1, max_length=200)]


class SettingsRead(BaseModel):
    #: The ISO 4217 code every cost and price is in.
    currency: str
    #: What the organization calls a base; null until it is named, when pages
    #: say the word for a base in the reader's language.
    base_word: str | None
    #: What the organization calls a recipe, such as "bill of materials"; null
    #: until it is named, when pages say the word for a recipe in the reader's
    #: language.
    recipe_word: str | None
    #: Whether a store order makes what it sells, where its product says nothing.
    auto_manufacture: AutoManufacture
    #: The rules the organization keeps records for.
    regimes: list[Regime]
    #: What an hour of work costs; null until it is set, and without ``costs``.
    labour_rate: Decimal | None
    #: The percentage added to what goods cost for overhead; null until it is
    #: set, and without ``costs``.
    overhead: Decimal | None


#: An amount of money or a percentage, never below zero.
NonNegative = Annotated[Decimal, Field(ge=0, max_digits=20, decimal_places=6)]

#: The settings only a member who sees costs reads or changes.
COST_SETTINGS = ("labour_rate", "overhead")

#: The organization's own words for what pages name, each cleared with null.
WORDS = ("base_word", "recipe_word")

Word = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]


class SettingsUpdate(BaseModel):
    """The settings to change: each left as it is unless sent. The words for
    a base and a recipe, the labour rate and the overhead may be cleared with
    null; the labour rate and the overhead are refused from a member who
    doesn't see costs."""

    currency: Annotated[str, Field(pattern=r"^[A-Z]{3}$")] | None = None
    base_word: Word | None = None
    recipe_word: Word | None = None
    auto_manufacture: AutoManufacture | None = None
    regimes: list[Regime] | None = None
    labour_rate: NonNegative | None = None
    overhead: NonNegative | None = None


class SettingsUnitRead(BaseModel):
    id: int
    #: A standard unit's is its standard name: pages call it by the words for
    #: its key.
    name: str
    #: Which standard unit it is, by key; null for a unit of the
    #: organization's own.
    standard: str | None
    #: What a standard unit measures.
    measure: units.Measure | None
    #: The names a standard unit is found by.
    aliases: list[str]


class UnitConversion(BaseModel):
    #: How many of the one unit make one of the other; null when they don't
    #: convert by themselves, and a number is typed in.
    factor: Decimal | None


class SettingsUnitWrite(BaseModel):
    name: Name


class SettingsCategoryRead(BaseModel):
    id: int
    kind: CategoryKind
    name: str
    #: Which of its items' lots goes first.
    picking: Picking
    #: The type a record made in it takes, unless another is chosen.
    default_type_id: int | None


class SettingsCategoryCreate(BaseModel):
    kind: CategoryKind
    name: Name
    picking: Picking = Picking.EARLIEST_EXPIRY
    default_type_id: int | None = None


class SettingsCategoryUpdate(BaseModel):
    """The category's name, picking rule and default type: each left as it is
    unless sent, the default type cleared with null. A new default type
    changes no record's."""

    name: Name | None = None
    picking: Picking | None = None
    default_type_id: int | None = None


class SettingsTypeRead(BaseModel):
    id: int
    kind: CategoryKind
    #: Null for a default type until someone names it: pages call it by the
    #: words for its kind's default type.
    name: str | None
    #: What its page is drawn as until its layout is changed.
    shape: TypeShape
    #: Its kind's default type, which stays.
    is_default: bool


class SettingsTypeCreate(BaseModel):
    kind: CategoryKind
    name: Name
    #: One of its kind's shapes; its kind's first unless sent.
    shape: TypeShape | None = None


class SettingsTypeUpdate(BaseModel):
    """The type's name and shape: each left as it is unless sent. A new shape
    changes its page only while its layout is unchanged."""

    name: Name | None = None
    shape: TypeShape | None = None


class SettingsLocationRead(BaseModel):
    id: int
    #: Null for the main location until someone names it: pages call it by
    #: the words for the main location.
    name: str | None
    main: bool
    #: The Initiative contact holding its address and phone.
    contact_id: int | None
    #: Whether it is a consignee's, whose stock is consigned.
    consignee: bool


class SettingsLocationCreate(BaseModel):
    name: Name
    contact_id: int | None = None
    consignee: bool = False


class SettingsLocationUpdate(BaseModel):
    #: Null only for the main location.
    name: Name | None
    #: Left as it is unless sent.
    contact_id: int | None = None
    #: Left as it is unless sent.
    consignee: bool | None = None


class SettingsReasonRead(BaseModel):
    id: int
    #: Set on a default reason: until someone names it, pages call it by the
    #: words for its key.
    key: DefaultReason | None
    #: Null for a default reason until someone names it.
    name: str | None


class SettingsReasonWrite(BaseModel):
    name: Name


@contextmanager
def refused_as(code: StrEnum) -> Iterator[None]:
    """Refuse with ``code`` (409) when the database refuses the write inside."""
    try:
        yield
    except IntegrityError:
        raise refuse(status.HTTP_409_CONFLICT, code) from None


async def found[T: SQLModel](db: AsyncSession, model: type[T], record_id: int) -> T:
    """The organization's ``model`` row ``record_id``, or 404 ``NOT_FOUND``."""
    record = await db.get(model, record_id)
    if record is None:
        raise refuse(status.HTTP_404_NOT_FOUND, HoardError.NOT_FOUND)
    return record


async def require_references(
    db: AsyncSession,
    *,
    kind: CategoryKind,
    units: Sequence[int | None] = (),
    category_id: int | None = None,
    type_id: int | None = None,
) -> None:
    """Refuse (422) a unit, or a category or a type of ``kind``, the
    organization does not have."""
    wanted = {unit for unit in units if unit is not None}
    if wanted:
        count = select(func.count()).select_from(Unit).where(col(Unit.id).in_(wanted))
        if (await db.exec(count)).one() != len(wanted):
            raise refuse(status.HTTP_422_UNPROCESSABLE_CONTENT, SettingsError.UNIT_UNKNOWN)
    if category_id is not None:
        category = await db.get(Category, category_id)
        if category is None or category.kind != kind:
            raise refuse(status.HTTP_422_UNPROCESSABLE_CONTENT, SettingsError.CATEGORY_UNKNOWN)
    if type_id is not None:
        held = await db.get(ItemType, type_id)
        if held is None or held.kind != kind:
            raise refuse(status.HTTP_422_UNPROCESSABLE_CONTENT, SettingsError.TYPE_UNKNOWN)


async def type_of(
    db: AsyncSession,
    kind: CategoryKind,
    type_id: int | None,
    category_id: int | None,
    held: int | None,
) -> int | None:
    """The type a record of ``kind`` is written with: the one sent; else the
    one it has; else, as it is made, its category's default type. None leaves
    a new record its kind's default (``hoard.stock``). Refuses (422) a type or
    a category of another kind."""
    await require_references(db, kind=kind, category_id=category_id, type_id=type_id)
    if type_id is not None or held is not None:
        return type_id or held
    category = await db.get(Category, category_id) if category_id is not None else None
    return category.default_type_id if category is not None else None


async def _save(db: WriteSession, record: SQLModel, **fields: Any) -> None:
    """Write ``fields`` to ``record``, refusing a name another has."""
    for field, value in fields.items():
        setattr(record, field, value)
    db.add(record)
    with refused_as(SettingsError.NAME_TAKEN):
        await db.commit()


async def _remove(db: WriteSession, record: SQLModel) -> None:
    """Remove ``record``, unless records still name it."""
    await db.delete(record)
    with refused_as(SettingsError.IN_USE):
        await db.commit()


async def _made_by_hoard(
    organization: Organization, plugin: PluginDep, make: Callable[[WriteSession], Awaitable[Any]]
) -> bool:
    """Make what an organization starts with, as Hoard's own write, and answer
    whether it wrote: nothing is written while the organization is read-only."""
    if organization.read_only:
        return False
    async with plugin.database.write_session(organization.id, actor=None) as db:
        await make(db)
        await db.commit()
    return True


@router.get("", responses=_READ)
async def read_settings(db: OrganizationSessionDep, sees_costs: SeesCostsDep) -> SettingsRead:
    """The organization's settings, each its default until it is changed."""
    settings = (await db.exec(select(OrganizationSettings))).first()
    return _settings(settings or OrganizationSettings(), sees_costs)


@router.put("", responses=_CHANGE)
async def update_settings(
    body: SettingsUpdate, _admin: AdminDep, db: WriteSessionDep, sees_costs: SeesCostsDep
) -> SettingsRead:
    """Change the organization's settings."""
    settings = (await db.exec(select(OrganizationSettings))).first() or OrganizationSettings(
        organization_id=db.organization_id
    )
    changes = body.model_dump(exclude_none=True)
    if body.regimes is not None:
        changes["regimes"] = [regime for regime in Regime if regime in body.regimes]
    for name in body.model_fields_set & set(COST_SETTINGS):
        _sees_costs(sees_costs)
        changes[name] = getattr(body, name)
    for name in body.model_fields_set & set(WORDS):
        changes[name] = getattr(body, name)
    settings.sqlmodel_update(changes)
    db.add(settings)
    await db.commit()
    await db.refresh(settings)
    return _settings(settings, sees_costs)


def _settings(settings: OrganizationSettings, sees_costs: bool) -> SettingsRead:
    read = SettingsRead.model_validate(settings, from_attributes=True)
    return read if sees_costs else read.model_copy(update=dict.fromkeys(COST_SETTINGS))


def _unit(unit: Unit) -> SettingsUnitRead:
    return SettingsUnitRead(
        id=unit.id,
        name=unit.name,
        standard=unit.standard,
        measure=units.measure(unit),
        aliases=units.aliases(unit.standard) if unit.standard else [],
    )


async def _units(db: AsyncSession) -> list[Unit]:
    """The standard units in their order, then the organization's own by name."""
    order = {unit.key: position for position, unit in enumerate(units.STANDARD_UNITS)}
    found = await db.exec(select(Unit).order_by(col(Unit.name)))
    return sorted(found, key=lambda unit: order.get(unit.standard or "", len(order)))


@router.get("/units", responses=_READ)
async def list_settings_units(
    db: OrganizationSessionDep, organization: OrganizationDep, plugin: PluginDep
) -> list[SettingsUnitRead]:
    """The units stock is counted and bought in: Hoard's standard units, which
    an organization that lacks any of them is given, then its own by name."""
    found = await _units(db)
    standard = sum(unit.standard is not None for unit in found)
    if standard < len(units.STANDARD_UNITS) and await _made_by_hoard(
        organization, plugin, units.add_standard
    ):
        found = await _units(db)
    return [_unit(unit) for unit in found]


@router.get("/units/conversion", responses=_READ)
async def read_unit_conversion(
    db: OrganizationSessionDep, from_unit_id: int, to_unit_id: int | None = None
) -> UnitConversion:
    """How many ``to_unit_id`` units one ``from_unit_id`` unit is, when they
    are standard units of one measure: as a purchase converts. Without
    ``to_unit_id``, how many of a variation it is, which counts in units of
    itself."""
    source = await found(db, Unit, from_unit_id)
    target = None if to_unit_id is None else await found(db, Unit, to_unit_id)
    return UnitConversion(factor=units.purchase_conversion(source, target))


def _own_name(name: str) -> str:
    """``name`` for a unit of the organization's own, refused when it names a
    standard unit."""
    if units.match(name) is not None:
        raise refuse(status.HTTP_409_CONFLICT, SettingsError.NAME_TAKEN)
    return name


async def _own_unit(db: AsyncSession, unit_id: int) -> Unit:
    """The organization's own unit ``unit_id``, refused for a standard unit."""
    unit = await found(db, Unit, unit_id)
    if unit.standard is not None:
        raise refuse(status.HTTP_409_CONFLICT, SettingsError.UNIT_STANDARD)
    return unit


@router.post("/units", status_code=status.HTTP_201_CREATED, responses=_CHANGE)
async def create_settings_unit(
    body: SettingsUnitWrite, _admin: AdminDep, db: WriteSessionDep
) -> SettingsUnitRead:
    unit = Unit(organization_id=db.organization_id, name=_own_name(body.name))
    await _save(db, unit)
    return _unit(unit)


@router.patch("/units/{unit_id}", responses=_CHANGE)
async def update_settings_unit(
    unit_id: int, body: SettingsUnitWrite, _admin: AdminDep, db: WriteSessionDep
) -> SettingsUnitRead:
    unit = await _own_unit(db, unit_id)
    await _save(db, unit, name=_own_name(body.name))
    return _unit(unit)


@router.delete("/units/{unit_id}", status_code=status.HTTP_204_NO_CONTENT, responses=_CHANGE)
async def delete_settings_unit(unit_id: int, _admin: AdminDep, db: WriteSessionDep) -> None:
    await _remove(db, await _own_unit(db, unit_id))


async def _types(db: AsyncSession, kind: CategoryKind | None) -> list[SettingsTypeRead]:
    query = select(ItemType).order_by(
        col(ItemType.kind), col(ItemType.is_default).desc(), col(ItemType.name)
    )
    if kind is not None:
        query = query.where(ItemType.kind == kind)
    return [
        SettingsTypeRead.model_validate(each, from_attributes=True) for each in await db.exec(query)
    ]


def _shape(kind: CategoryKind, shape: TypeShape | None) -> TypeShape:
    """``shape``, or ``kind``'s first; refused (422) when not one of ``kind``'s."""
    if shape is None:
        return SHAPES[kind][0]
    if shape not in SHAPES[kind]:
        raise refuse(status.HTTP_422_UNPROCESSABLE_CONTENT, SettingsError.SHAPE_UNKNOWN)
    return shape


@router.get("/types", responses=_READ)
async def list_settings_types(
    db: OrganizationSessionDep,
    organization: OrganizationDep,
    plugin: PluginDep,
    kind: Annotated[CategoryKind | None, Query()] = None,
) -> list[SettingsTypeRead]:
    """The types of one kind of record, or of every kind: each kind's default
    first, then the rest by name. An organization that lacks a kind's default
    type is given it."""
    found = await _types(db, kind)
    defaults = {each.kind for each in found if each.is_default}
    wanted = {kind} if kind is not None else set(SHAPES)
    if not wanted <= defaults and await _made_by_hoard(
        organization, plugin, stock.add_default_types
    ):
        found = await _types(db, kind)
    return found


@router.post("/types", status_code=status.HTTP_201_CREATED, responses=_CHANGE)
async def create_settings_type(
    body: SettingsTypeCreate, _admin: AdminDep, db: WriteSessionDep
) -> SettingsTypeRead:
    made = ItemType(
        organization_id=db.organization_id,
        kind=body.kind,
        name=body.name,
        shape=_shape(body.kind, body.shape),
    )
    await _save(db, made)
    return SettingsTypeRead.model_validate(made, from_attributes=True)


@router.patch("/types/{type_id}", responses=_CHANGE)
async def update_settings_type(
    type_id: int, body: SettingsTypeUpdate, _admin: AdminDep, db: WriteSessionDep
) -> SettingsTypeRead:
    held = await found(db, ItemType, type_id)
    changes = body.model_dump(exclude_none=True)
    if body.shape is not None:
        changes["shape"] = _shape(held.kind, body.shape)
    await _save(db, held, **changes)
    return SettingsTypeRead.model_validate(held, from_attributes=True)


@router.delete("/types/{type_id}", status_code=status.HTTP_204_NO_CONTENT, responses=_CHANGE)
async def delete_settings_type(type_id: int, _admin: AdminDep, db: WriteSessionDep) -> None:
    """Remove a type, with its page and from the categories naming it as
    their default. A kind's default type stays, and so does a type records
    have."""
    held = await found(db, ItemType, type_id)
    if held.is_default:
        raise refuse(status.HTTP_409_CONFLICT, SettingsError.TYPE_DEFAULT)
    await _remove(db, held)


@router.get("/categories", responses=_READ)
async def list_settings_categories(
    db: OrganizationSessionDep, kind: Annotated[CategoryKind | None, Query()] = None
) -> list[SettingsCategoryRead]:
    """The categories of one kind of record, or of every kind, by name."""
    query = select(Category).order_by(col(Category.kind), col(Category.name))
    if kind is not None:
        query = query.where(Category.kind == kind)
    return [
        SettingsCategoryRead.model_validate(category, from_attributes=True)
        for category in await db.exec(query)
    ]


@router.post("/categories", status_code=status.HTTP_201_CREATED, responses=_CHANGE)
async def create_settings_category(
    body: SettingsCategoryCreate, _admin: AdminDep, db: WriteSessionDep
) -> SettingsCategoryRead:
    await require_references(db, kind=body.kind, type_id=body.default_type_id)
    category = Category(organization_id=db.organization_id, **body.model_dump())
    await _save(db, category)
    return SettingsCategoryRead.model_validate(category, from_attributes=True)


@router.patch("/categories/{category_id}", responses=_CHANGE)
async def update_settings_category(
    category_id: int, body: SettingsCategoryUpdate, _admin: AdminDep, db: WriteSessionDep
) -> SettingsCategoryRead:
    category = await found(db, Category, category_id)
    await require_references(db, kind=category.kind, type_id=body.default_type_id)
    changes = body.model_dump(exclude_none=True) | body.model_dump(
        include={"default_type_id"} & body.model_fields_set
    )
    await _save(db, category, **changes)
    return SettingsCategoryRead.model_validate(category, from_attributes=True)


@router.delete(
    "/categories/{category_id}", status_code=status.HTTP_204_NO_CONTENT, responses=_CHANGE
)
async def delete_settings_category(category_id: int, _admin: AdminDep, db: WriteSessionDep) -> None:
    await _remove(db, await found(db, Category, category_id))


async def _locations(db: AsyncSession) -> list[SettingsLocationRead]:
    query = select(Location).order_by(col(Location.main).desc(), col(Location.name))
    return [
        SettingsLocationRead.model_validate(location, from_attributes=True)
        for location in await db.exec(query)
    ]


@router.get("/locations", responses=_READ)
async def list_settings_locations(
    db: OrganizationSessionDep, organization: OrganizationDep, plugin: PluginDep
) -> list[SettingsLocationRead]:
    """Where stock is kept: the main location first, then the rest by name."""
    locations = await _locations(db)
    if not any(location.main for location in locations) and await _made_by_hoard(
        organization, plugin, stock.main_location
    ):
        locations = await _locations(db)
    return locations


async def _consignee(db: AsyncSession, location: Location) -> None:
    """Refuse a consignee's location that names no contact, is the main one,
    or is the location a store's location is mapped to."""
    if not location.consignee:
        return
    if location.contact_id is None or location.main:
        raise refuse(status.HTTP_422_UNPROCESSABLE_CONTENT, SettingsError.CONSIGNEE_CONTACT)
    mapped = select(StoreLocation.id).where(StoreLocation.location_id == location.id)
    if location.id is not None and (await db.exec(mapped)).first() is not None:
        raise refuse(status.HTTP_409_CONFLICT, SettingsError.CONSIGNEE_MAPPED)


@router.post("/locations", status_code=status.HTTP_201_CREATED, responses=_CHANGE)
async def create_settings_location(
    body: SettingsLocationCreate, _admin: AdminDep, db: WriteSessionDep
) -> SettingsLocationRead:
    location = Location(organization_id=db.organization_id, **body.model_dump())
    await _consignee(db, location)
    await _save(db, location)
    return SettingsLocationRead.model_validate(location, from_attributes=True)


@router.patch("/locations/{location_id}", responses=_CHANGE)
async def update_settings_location(
    location_id: int, body: SettingsLocationUpdate, _admin: AdminDep, db: WriteSessionDep
) -> SettingsLocationRead:
    location = await found(db, Location, location_id)
    if body.name is None and not location.main:
        raise refuse(status.HTTP_422_UNPROCESSABLE_CONTENT, SettingsError.NAME_REQUIRED)
    location.sqlmodel_update(
        body.model_dump(exclude_unset=True, include={"contact_id"})
        | body.model_dump(exclude_none=True, include={"consignee"})
    )
    await _consignee(db, location)
    await _save(db, location, name=body.name)
    return SettingsLocationRead.model_validate(location, from_attributes=True)


@router.delete(
    "/locations/{location_id}", status_code=status.HTTP_204_NO_CONTENT, responses=_CHANGE
)
async def delete_settings_location(location_id: int, _admin: AdminDep, db: WriteSessionDep) -> None:
    location = await found(db, Location, location_id)
    if location.main:
        raise refuse(status.HTTP_409_CONFLICT, SettingsError.MAIN_LOCATION)
    await _remove(db, location)


async def _reasons(db: AsyncSession) -> list[SettingsReasonRead]:
    query = select(AdjustmentReason).order_by(col(AdjustmentReason.name), col(AdjustmentReason.id))
    reasons = await db.exec(query)
    return [SettingsReasonRead.model_validate(reason, from_attributes=True) for reason in reasons]


@router.get("/reasons", responses=_READ)
async def list_settings_reasons(
    db: OrganizationSessionDep, organization: OrganizationDep, plugin: PluginDep
) -> list[SettingsReasonRead]:
    """Why stock is adjusted by hand: those named, by name, then the default
    reasons nobody has named. An organization that has none yet starts with
    the default reasons."""
    reasons = await _reasons(db)
    if not reasons and await _made_by_hoard(organization, plugin, stock.add_default_reasons):
        reasons = await _reasons(db)
    return reasons


@router.post("/reasons", status_code=status.HTTP_201_CREATED, responses=_CHANGE)
async def create_settings_reason(
    body: SettingsReasonWrite, _admin: AdminDep, db: WriteSessionDep
) -> SettingsReasonRead:
    reason = AdjustmentReason(organization_id=db.organization_id, name=body.name)
    await _save(db, reason)
    return SettingsReasonRead.model_validate(reason, from_attributes=True)


@router.patch("/reasons/{reason_id}", responses=_CHANGE)
async def update_settings_reason(
    reason_id: int, body: SettingsReasonWrite, _admin: AdminDep, db: WriteSessionDep
) -> SettingsReasonRead:
    reason = await found(db, AdjustmentReason, reason_id)
    await _save(db, reason, name=body.name)
    return SettingsReasonRead.model_validate(reason, from_attributes=True)


@router.delete("/reasons/{reason_id}", status_code=status.HTTP_204_NO_CONTENT, responses=_CHANGE)
async def delete_settings_reason(reason_id: int, _admin: AdminDep, db: WriteSessionDep) -> None:
    await _remove(db, await found(db, AdjustmentReason, reason_id))


def _sees_costs(sees_costs: bool) -> None:
    """Refuse (403 ``COSTS_HIDDEN``) a member who doesn't see costs."""
    if not sees_costs:
        raise refuse(status.HTTP_403_FORBIDDEN, AdjustmentError.COSTS_HIDDEN)
