"""The organization's settings: its currency, the rules it keeps records for,
units, categories with their picking rules, locations and adjustment reasons. Every member reads them; only a community admin changes
them (:data:`~hoard.api.deps.AdminDep`).

An organization starts with its main location and the default adjustment
reasons, which Hoard makes itself the first time each list is read.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterator, Sequence
from contextlib import contextmanager
from enum import StrEnum
from typing import Annotated, Any

from fastapi import APIRouter, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlmodel import SQLModel, col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from hoard import stock
from hoard.api.deps import AdminDep, HoardError
from hoard.records import (
    AdjustmentReason,
    AutoManufacture,
    Category,
    CategoryKind,
    DefaultReason,
    Location,
    OrganizationSettings,
    Picking,
    Regime,
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


_READ: dict[int | str, dict[str, Any]] = {401: {"description": "NOT_AUTHENTICATED"}}
_CHANGE: dict[int | str, dict[str, Any]] = {
    **_READ,
    403: {"description": "ADMIN_ONLY, READ_ONLY"},
    404: {"description": "NOT_FOUND"},
    409: {"description": "NAME_TAKEN, IN_USE, MAIN_LOCATION"},
    422: {"description": "NAME_REQUIRED"},
}

Name = Annotated[str, Field(min_length=1, max_length=200)]


class SettingsRead(BaseModel):
    #: The ISO 4217 code every cost and price is in.
    currency: str
    #: Whether a store order makes what it sells, where its product says nothing.
    auto_manufacture: AutoManufacture
    #: The rules the organization keeps records for.
    regimes: list[Regime]


class SettingsUpdate(BaseModel):
    """The settings to change: each left as it is unless sent."""

    currency: Annotated[str, Field(pattern=r"^[A-Z]{3}$")] | None = None
    auto_manufacture: AutoManufacture | None = None
    regimes: list[Regime] | None = None


class SettingsUnitRead(BaseModel):
    id: int
    name: str


class SettingsUnitWrite(BaseModel):
    name: Name


class SettingsCategoryRead(BaseModel):
    id: int
    kind: CategoryKind
    name: str
    #: Which of its items' lots goes first.
    picking: Picking


class SettingsCategoryCreate(BaseModel):
    kind: CategoryKind
    name: Name
    picking: Picking = Picking.EARLIEST_EXPIRY


class SettingsCategoryUpdate(BaseModel):
    """The category's name and picking rule: each left as it is unless sent."""

    name: Name | None = None
    picking: Picking | None = None


class SettingsLocationRead(BaseModel):
    id: int
    #: Null for the main location until someone names it: pages call it by
    #: the words for the main location.
    name: str | None
    main: bool
    #: The Initiative contact holding its address and phone.
    contact_id: int | None


class SettingsLocationCreate(BaseModel):
    name: Name
    contact_id: int | None = None


class SettingsLocationUpdate(BaseModel):
    #: Null only for the main location.
    name: Name | None
    #: Left as it is unless sent.
    contact_id: int | None = None


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
) -> None:
    """Refuse (422) a unit, or a category of ``kind``, the organization does
    not have."""
    wanted = {unit for unit in units if unit is not None}
    if wanted:
        count = select(func.count()).select_from(Unit).where(col(Unit.id).in_(wanted))
        if (await db.exec(count)).one() != len(wanted):
            raise refuse(status.HTTP_422_UNPROCESSABLE_CONTENT, SettingsError.UNIT_UNKNOWN)
    if category_id is not None:
        category = await db.get(Category, category_id)
        if category is None or category.kind != kind:
            raise refuse(status.HTTP_422_UNPROCESSABLE_CONTENT, SettingsError.CATEGORY_UNKNOWN)


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
async def read_settings(db: OrganizationSessionDep) -> SettingsRead:
    """The organization's settings, each its default until it is changed."""
    settings = (await db.exec(select(OrganizationSettings))).first()
    return _settings(settings or OrganizationSettings())


@router.put("", responses=_CHANGE)
async def update_settings(
    body: SettingsUpdate, _admin: AdminDep, db: WriteSessionDep
) -> SettingsRead:
    """Change the organization's settings."""
    settings = (await db.exec(select(OrganizationSettings))).first() or OrganizationSettings(
        organization_id=db.organization_id
    )
    changes = body.model_dump(exclude_none=True)
    if body.regimes is not None:
        changes["regimes"] = [regime for regime in Regime if regime in body.regimes]
    settings.sqlmodel_update(changes)
    db.add(settings)
    await db.commit()
    return _settings(settings)


def _settings(settings: OrganizationSettings) -> SettingsRead:
    return SettingsRead.model_validate(settings, from_attributes=True)


@router.get("/units", responses=_READ)
async def list_settings_units(db: OrganizationSessionDep) -> list[SettingsUnitRead]:
    """The units stock is counted and bought in, by name."""
    units = await db.exec(select(Unit).order_by(col(Unit.name)))
    return [SettingsUnitRead.model_validate(unit, from_attributes=True) for unit in units]


@router.post("/units", status_code=status.HTTP_201_CREATED, responses=_CHANGE)
async def create_settings_unit(
    body: SettingsUnitWrite, _admin: AdminDep, db: WriteSessionDep
) -> SettingsUnitRead:
    unit = Unit(organization_id=db.organization_id, name=body.name)
    await _save(db, unit)
    return SettingsUnitRead.model_validate(unit, from_attributes=True)


@router.patch("/units/{unit_id}", responses=_CHANGE)
async def update_settings_unit(
    unit_id: int, body: SettingsUnitWrite, _admin: AdminDep, db: WriteSessionDep
) -> SettingsUnitRead:
    unit = await found(db, Unit, unit_id)
    await _save(db, unit, name=body.name)
    return SettingsUnitRead.model_validate(unit, from_attributes=True)


@router.delete("/units/{unit_id}", status_code=status.HTTP_204_NO_CONTENT, responses=_CHANGE)
async def delete_settings_unit(unit_id: int, _admin: AdminDep, db: WriteSessionDep) -> None:
    await _remove(db, await found(db, Unit, unit_id))


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
    category = Category(organization_id=db.organization_id, **body.model_dump())
    await _save(db, category)
    return SettingsCategoryRead.model_validate(category, from_attributes=True)


@router.patch("/categories/{category_id}", responses=_CHANGE)
async def update_settings_category(
    category_id: int, body: SettingsCategoryUpdate, _admin: AdminDep, db: WriteSessionDep
) -> SettingsCategoryRead:
    category = await found(db, Category, category_id)
    await _save(db, category, **body.model_dump(exclude_none=True))
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


@router.post("/locations", status_code=status.HTTP_201_CREATED, responses=_CHANGE)
async def create_settings_location(
    body: SettingsLocationCreate, _admin: AdminDep, db: WriteSessionDep
) -> SettingsLocationRead:
    location = Location(organization_id=db.organization_id, **body.model_dump())
    await _save(db, location)
    return SettingsLocationRead.model_validate(location, from_attributes=True)


@router.patch("/locations/{location_id}", responses=_CHANGE)
async def update_settings_location(
    location_id: int, body: SettingsLocationUpdate, _admin: AdminDep, db: WriteSessionDep
) -> SettingsLocationRead:
    location = await found(db, Location, location_id)
    if body.name is None and not location.main:
        raise refuse(status.HTTP_422_UNPROCESSABLE_CONTENT, SettingsError.NAME_REQUIRED)
    await _save(
        db, location, **body.model_dump(exclude_unset=True, include={"contact_id"}), name=body.name
    )
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
