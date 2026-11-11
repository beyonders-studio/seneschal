"""The properties people add to materials, products and lots, and each record's
values of them, in the shapes the UI kit draws.

Every member reads a kind's properties. Only a community admin adds, changes
or removes one, or adds an option to a select (:data:`~hoard.api.deps.AdminDep`).
A member sets a record's values while Hoard may change. A record's values come
back with the record itself (:func:`values_of`).
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, date, datetime
from enum import StrEnum
from math import isfinite
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import APIRouter, status
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlmodel import SQLModel, col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from hoard.api.deps import AdminDep
from hoard.api.settings import found, refused_as
from hoard.records import (
    CURRENCY_CODES,
    Lot,
    Material,
    Product,
    PropertyDefinition,
    PropertyType,
    PropertyValue,
    RecordKind,
)
from seneschal_core.api import OrganizationSessionDep, SessionDep, WriteSessionDep, refuse

router = APIRouter(prefix="/properties", tags=["properties"])

if TYPE_CHECKING:

    class HoardCurrency(StrEnum): ...

else:
    HoardCurrency = StrEnum("HoardCurrency", {code: code for code in CURRENCY_CODES})
    HoardCurrency.__doc__ = "The ISO 4217 code a money property's amounts are in."

#: Each kind's records, and the column a value names one of them by.
RECORDS: dict[RecordKind, tuple[type[SQLModel], str]] = {
    RecordKind.MATERIAL: (Material, "material_id"),
    RecordKind.PRODUCT: (Product, "product_id"),
    RecordKind.LOT: (Lot, "lot_id"),
}

_SELECTS = {PropertyType.SELECT, PropertyType.MULTI_SELECT}
_NUMBERS = {PropertyType.NUMBER, PropertyType.CURRENCY, PropertyType.SCALE, PropertyType.COUNTER}
_SLUG = r"^[A-Za-z0-9][A-Za-z0-9_\-]*$"
_MAX_TEXT = 10_000


class PropertyError(StrEnum):
    """Why a change to a property, or to a record's values, was refused."""

    #: Another property of the same kind already has the name.
    NAME_TAKEN = "NAME_TAKEN"
    #: A select needs options, each its own value; money needs its currency.
    PROPERTY_INVALID = "PROPERTY_INVALID"
    #: A value names a property the record's kind does not have.
    PROPERTY_UNKNOWN = "PROPERTY_UNKNOWN"
    #: A value is not one its property can hold.
    VALUE_INVALID = "VALUE_INVALID"


_READ: dict[int | str, dict[str, Any]] = {401: {"description": "NOT_AUTHENTICATED"}}
_CHANGE: dict[int | str, dict[str, Any]] = {
    **_READ,
    403: {"description": "ADMIN_ONLY, READ_ONLY"},
    404: {"description": "NOT_FOUND"},
    409: {"description": "NAME_TAKEN"},
    422: {"description": "PROPERTY_INVALID"},
}
_SET: dict[int | str, dict[str, Any]] = {
    **_READ,
    403: {"description": "READ_ONLY"},
    404: {"description": "NOT_FOUND"},
    422: {"description": "PROPERTY_UNKNOWN, VALUE_INVALID"},
}

Name = Annotated[str, Field(min_length=1, max_length=100)]


class HoardPropertyOption(BaseModel):
    """One of a select's options: its value, which records keep, and its label."""

    value: str = Field(min_length=1, max_length=64, pattern=_SLUG)
    label: str = Field(min_length=1, max_length=100)
    color: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")


class HoardCounterSettings(BaseModel):
    """How a counter moves: by ``step`` a tap, never past ``min`` or ``max``,
    and back to ``initial`` on a reset. A counter with no value holds
    ``initial``."""

    model_config = ConfigDict(json_schema_serialization_defaults_required=True)

    step: float = Field(default=1, gt=0, allow_inf_nan=False)
    min: float | None = Field(default=None, allow_inf_nan=False)
    max: float | None = Field(default=None, allow_inf_nan=False)
    initial: float = Field(default=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def _ordered(self) -> HoardCounterSettings:
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError("min is above max")
        return self


class HoardPropertyShape(BaseModel):
    """What a property holds beside its type: a select's options, a money
    property's currency and a counter's settings. Each other type has none."""

    options: list[HoardPropertyOption] | None = Field(default=None, max_length=200)
    currency: HoardCurrency | None = None
    counter: HoardCounterSettings | None = None


class HoardPropertyRead(HoardPropertyShape):
    model_config = ConfigDict(json_schema_serialization_defaults_required=True)

    id: int
    kind: RecordKind
    name: str
    type: PropertyType


class HoardPropertyCreate(HoardPropertyShape):
    kind: RecordKind
    name: Name
    type: PropertyType


class HoardPropertyUpdate(HoardPropertyShape):
    """What changes: the name, and the shape its type has. Its type stays."""

    name: Name | None = None


class HoardOptionCreate(BaseModel):
    label: str = Field(min_length=1, max_length=100)


class HoardPropertySummary(HoardPropertyShape):
    """A record's value of one property, as the UI kit draws it. The kit's
    summary names an owner and an install, which Hoard's properties never
    have."""

    model_config = ConfigDict(json_schema_serialization_defaults_required=True)

    property_id: int
    name: str
    type: PropertyType
    project_id: None
    collection_id: None
    install_id: None
    key: None
    #: Shaped by the type: a member's reference for a person; for a signature,
    #: who signed (``user``), the name they typed and when (``signed_at``).
    value: Any


class HoardPropertyValueInput(BaseModel):
    property_id: int
    #: Nothing takes the value off.
    value: Any = None


class HoardPropertyStep(BaseModel):
    property_id: int
    up: bool


class HoardPropertyValuesSet(BaseModel):
    """The values a change sets, takes off and steps. Those it leaves out stay
    as they are."""

    values: list[HoardPropertyValueInput] = Field(default_factory=list, max_length=100)
    removed: list[int] = Field(default_factory=list, max_length=100)
    steps: list[HoardPropertyStep] = Field(default_factory=list, max_length=100)


def _read(definition: PropertyDefinition) -> HoardPropertyRead:
    return HoardPropertyRead.model_validate(definition, from_attributes=True)


def _shaped(definition: PropertyDefinition, shape: HoardPropertyShape) -> None:
    """Give ``definition`` what its type holds from ``shape``, and nothing else."""
    kind = definition.type
    options = shape.options if kind in _SELECTS else None
    if kind in _SELECTS and (
        not options or len({option.value for option in options}) != len(options)
    ):
        raise refuse(status.HTTP_422_UNPROCESSABLE_CONTENT, PropertyError.PROPERTY_INVALID)
    if kind is PropertyType.CURRENCY and shape.currency is None:
        raise refuse(status.HTTP_422_UNPROCESSABLE_CONTENT, PropertyError.PROPERTY_INVALID)
    definition.options = [option.model_dump() for option in options] if options else None
    definition.currency = shape.currency if kind is PropertyType.CURRENCY else None
    definition.counter = (
        (shape.counter or HoardCounterSettings()).model_dump()
        if kind is PropertyType.COUNTER
        else None
    )


def _summary(definition: PropertyDefinition, value: Any) -> HoardPropertySummary:
    return HoardPropertySummary.model_validate(
        {
            **_read(definition).model_dump(),
            "property_id": definition.id,
            **dict.fromkeys(("project_id", "collection_id", "install_id", "key")),
            "value": value,
        }
    )


async def values_of(
    db: AsyncSession, kind: RecordKind, record_ids: Iterable[int]
) -> dict[int, list[HoardPropertySummary]]:
    """What each record of ``kind`` holds of its kind's properties, in the
    order they were added. A property a record holds nothing of is left out."""
    ids = list(record_ids)
    held: dict[int, list[HoardPropertySummary]] = {record_id: [] for record_id in ids}
    if not ids:
        return held
    column = col(getattr(PropertyValue, RECORDS[kind][1]))
    rows = await db.exec(
        select(PropertyDefinition, PropertyValue)
        .join(PropertyValue, col(PropertyValue.definition_id) == col(PropertyDefinition.id))
        .where(column.in_(ids))
        .order_by(col(PropertyDefinition.id))
    )
    for definition, value in rows:
        held[getattr(value, RECORDS[kind][1])].append(_summary(definition, value.value))
    return held


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        try:
            value = float(value)
        except ValueError:
            return None
    return float(value) if isinstance(value, int | float) and isfinite(value) else None


def _option_values(definition: PropertyDefinition) -> set[str]:
    return {option["value"] for option in definition.options or []}


def _counter(definition: PropertyDefinition) -> HoardCounterSettings:
    return HoardCounterSettings.model_validate(definition.counter or {})


def _within(settings: HoardCounterSettings, count: float) -> float:
    if settings.max is not None:
        count = min(settings.max, count)
    return max(settings.min, count) if settings.min is not None else count


def _parses(parse: Any, value: Any) -> bool:
    try:
        parse(value)
    except (TypeError, ValueError):
        return False
    return True


def _held(definition: PropertyDefinition, value: Any, signer: str) -> Any:
    """``value`` as ``definition`` keeps it, or None where it holds nothing.
    Refuses (422) a value it cannot hold."""
    kind = definition.type
    if value is None or value == "" or value == []:
        return None
    held: Any = None
    if kind in (PropertyType.TEXT, PropertyType.URL, PropertyType.USER_REFERENCE):
        held = value if isinstance(value, str) and len(value) <= _MAX_TEXT else None
    elif kind is PropertyType.SIGNATURE:
        if isinstance(value, str) and len(value) <= 200:
            signed_at = datetime.now(UTC).isoformat()
            held = {"user": {"id": signer}, "name": value.strip(), "signed_at": signed_at}
    elif kind is PropertyType.CHECKBOX:
        held = value if isinstance(value, bool) else None
    elif kind in _NUMBERS:
        held = _number(value)
        if held is not None and kind is PropertyType.SCALE and not 0 <= held <= 100:
            held = None
        if held is not None and kind is PropertyType.COUNTER:
            held = _within(_counter(definition), held)
    elif kind is PropertyType.DATE:
        held = value if isinstance(value, str) and _parses(date.fromisoformat, value) else None
    elif kind is PropertyType.DATETIME:
        held = value if isinstance(value, str) and _parses(datetime.fromisoformat, value) else None
    elif kind is PropertyType.SELECT:
        held = value if value in _option_values(definition) else None
    elif kind is PropertyType.MULTI_SELECT:
        if isinstance(value, list) and set(value) <= _option_values(definition):
            held = list(dict.fromkeys(value))
    elif kind is PropertyType.FILE:
        held = value if isinstance(value, int) and not isinstance(value, bool) else None
    if held is None:
        raise refuse(status.HTTP_422_UNPROCESSABLE_CONTENT, PropertyError.VALUE_INVALID)
    return held


@router.get("", responses=_READ)
async def list_properties(kind: RecordKind, db: OrganizationSessionDep) -> list[HoardPropertyRead]:
    """A kind's properties, in the order they were added."""
    definitions = await db.exec(
        select(PropertyDefinition)
        .where(PropertyDefinition.kind == kind)
        .order_by(col(PropertyDefinition.id))
    )
    return [_read(definition) for definition in definitions]


@router.post("", status_code=status.HTTP_201_CREATED, responses=_CHANGE)
async def create_property(
    body: HoardPropertyCreate, _admin: AdminDep, db: WriteSessionDep
) -> HoardPropertyRead:
    definition = PropertyDefinition(
        organization_id=db.organization_id, kind=body.kind, name=body.name.strip(), type=body.type
    )
    _shaped(definition, body)
    db.add(definition)
    with refused_as(PropertyError.NAME_TAKEN):
        await db.commit()
    return _read(definition)


@router.patch("/{property_id}", responses=_CHANGE)
async def update_property(
    property_id: int, body: HoardPropertyUpdate, _admin: AdminDep, db: WriteSessionDep
) -> HoardPropertyRead:
    definition = await found(db, PropertyDefinition, property_id)
    if body.name is not None:
        definition.name = body.name.strip()
    shape = _read(definition).model_dump(include={"options", "currency", "counter"})
    changed = body.model_dump(exclude_unset=True, exclude={"name"})
    _shaped(definition, HoardPropertyShape.model_validate({**shape, **changed}))
    db.add(definition)
    with refused_as(PropertyError.NAME_TAKEN):
        await db.commit()
    return _read(definition)


@router.delete("/{property_id}", status_code=status.HTTP_204_NO_CONTENT, responses=_CHANGE)
async def delete_property(property_id: int, _admin: AdminDep, db: WriteSessionDep) -> None:
    """Remove a property, and every record's value of it."""
    await db.delete(await found(db, PropertyDefinition, property_id))
    await db.commit()


@router.post("/{property_id}/options", responses=_CHANGE)
async def add_property_option(
    property_id: int, body: HoardOptionCreate, _admin: AdminDep, db: WriteSessionDep
) -> HoardPropertyOption:
    """Add an option labelled ``label`` to a select, or answer with the one
    already labelled so."""
    definition = await found(db, PropertyDefinition, property_id)
    if definition.type not in _SELECTS:
        raise refuse(status.HTTP_422_UNPROCESSABLE_CONTENT, PropertyError.PROPERTY_INVALID)
    options = [HoardPropertyOption.model_validate(each) for each in definition.options or []]
    label = body.label.strip()
    for option in options:
        if option.label.strip().lower() == label.lower():
            return option
    base = "".join(c if c.isascii() and (c.isalnum() or c in "_-") else "_" for c in label.lower())
    base = base.strip("_")[:64] or "option"
    taken = {option.value for option in options}
    value = next(
        slug for slug in (base, *(f"{base[:58]}-{n}" for n in range(2, 1000))) if slug not in taken
    )
    option = HoardPropertyOption(value=value, label=label)
    definition.options = [*(each.model_dump() for each in options), option.model_dump()]
    db.add(definition)
    await db.commit()
    return option


@router.put("/values/{kind}/{record_id}", responses=_SET)
async def set_property_values(
    kind: RecordKind,
    record_id: int,
    body: HoardPropertyValuesSet,
    session: SessionDep,
    db: WriteSessionDep,
) -> list[HoardPropertySummary]:
    """Set, take off and step a record's values, answering with every value
    it then holds."""
    model, column = RECORDS[kind]
    await found(db, model, record_id)
    named = {
        *(each.property_id for each in body.values),
        *body.removed,
        *(each.property_id for each in body.steps),
    }
    definitions = {
        definition.id: definition
        for definition in await db.exec(
            select(PropertyDefinition).where(
                col(PropertyDefinition.id).in_(named), PropertyDefinition.kind == kind
            )
        )
    }
    if named - definitions.keys():
        raise refuse(status.HTTP_422_UNPROCESSABLE_CONTENT, PropertyError.PROPERTY_UNKNOWN)
    held = {
        value.definition_id: value
        for value in await db.exec(
            select(PropertyValue).where(getattr(PropertyValue, column) == record_id)
        )
    }
    changes: dict[int, Any] = dict.fromkeys(body.removed)
    for each in body.values:
        changes[each.property_id] = _held(
            definitions[each.property_id], each.value, session.member_ref
        )
    for step in body.steps:
        definition = definitions[step.property_id]
        if definition.type is not PropertyType.COUNTER:
            raise refuse(status.HTTP_422_UNPROCESSABLE_CONTENT, PropertyError.VALUE_INVALID)
        settings = _counter(definition)
        current = held.get(step.property_id)
        count = current.value if current else settings.initial
        changes[step.property_id] = _within(
            settings, count + (settings.step if step.up else -settings.step)
        )
    for property_id, value in changes.items():
        row = held.get(property_id)
        if value is None:
            if row:
                await db.delete(row)
        elif row:
            row.value = value
            db.add(row)
        else:
            row = PropertyValue(
                organization_id=db.organization_id, definition_id=property_id, value=value
            )
            setattr(row, column, record_id)
            db.add(row)
    await db.commit()
    return (await values_of(db, kind, [record_id]))[record_id]
