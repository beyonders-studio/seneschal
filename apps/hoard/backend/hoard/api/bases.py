"""Bases: made from materials and other bases, never bought or sold, and their
stock per location.

A base's average cost is sent only to a member who sees costs; none is taken,
since the runs that make it set it (``hoard.costing``), but for the unit cost
of the stock a new base starts with. Its recipe is kept under ``/recipes``,
and its bins and picture under ``/items``. Its tracking unit is fixed once
anything records a number in it. A base that cures is not made by the runs
that use it (``hoard.runs``), so it is never set to be. An archived base is
listed only when asked for.
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any

from fastapi import APIRouter, Query, status
from pydantic import BaseModel
from sqlalchemy import func, or_
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from hoard import stock
from hoard.api import items
from hoard.api.deps import SeesCostsDep
from hoard.api.properties import HoardPropertySummary, values_of
from hoard.api.settings import SettingsError, found, refused_as, require_references, type_of
from hoard.api.shapes import Page, PageDep
from hoard.records import AutoManufacture, Base, CategoryKind, Item, ItemStatus, RecordKind
from seneschal_core.api import OrganizationSessionDep, WriteSessionDep, refuse

router = APIRouter(prefix="/bases", tags=["bases"])


class BaseError(StrEnum):
    """Why a change to a base was refused."""

    #: A base that cures is never set to auto-manufacture, nor given a cure
    #: time while it is.
    AUTO_MANUFACTURE_CURES = "AUTO_MANUFACTURE_CURES"


_READ: dict[int | str, dict[str, Any]] = {
    401: {"description": "NOT_AUTHENTICATED"},
    404: {"description": "NOT_FOUND"},
}
_CHANGE: dict[int | str, dict[str, Any]] = {
    **_READ,
    403: {"description": "READ_ONLY"},
    409: {
        "description": "IN_USE, TRACKING_UNIT_IN_STOCK, TRACKING_UNIT_IN_PURCHASES, "
        "TRACKING_UNIT_IN_STOCKTAKES, TRACKING_UNIT_IN_RECIPES, TRACKING_UNIT_IN_OWN_RECIPE, "
        "TRACKING_UNIT_IN_RUNS, AUTO_MANUFACTURE_CURES"
    },
    422: {"description": "UNIT_UNKNOWN, CATEGORY_UNKNOWN, TYPE_UNKNOWN"},
}


class BaseRead(BaseModel):
    id: int
    name: str
    status: ItemStatus
    sku: str | None
    #: The unit stock is counted in.
    tracking_unit_id: int
    low_stock_limit: Decimal | None
    category_id: int | None
    #: Its type, which decides the page it is shown on.
    type_id: int
    #: How many days what a run makes cures before it is used; null for none.
    cure_days: int | None
    #: Whether a run that uses it makes it first; null for never.
    auto_manufacture: AutoManufacture | None
    #: What is on hand at every location.
    on_hand: Decimal
    #: The moving average cost of one tracking unit; null without ``costs``.
    average_cost: Decimal | None
    notes: str | None
    #: What it holds of the properties people add to bases.
    properties: list[HoardPropertySummary]


class BaseDetail(BaseRead, items.ItemHoldings, items.ItemStamps):
    """A base, with its stock, bin and lots at each location."""

    #: Its picture's id, which reads it (``/items/{id}/picture/{picture_id}``).
    picture_id: int | None


class BaseWrite(BaseModel):
    """A base as it is added, or as an edit leaves it."""

    name: items.Text
    status: ItemStatus = ItemStatus.ACTIVE
    sku: items.OptionalText = None
    tracking_unit_id: int
    low_stock_limit: items.Amount | None = None
    category_id: int | None = None
    #: Its type: as it is made, its category's default type unless sent, and
    #: its kind's default without one; left as it is unless sent.
    type_id: int | None = None
    cure_days: items.CureDays | None = None
    #: Left as it is unless sent; null for never.
    auto_manufacture: AutoManufacture | None = None
    notes: items.Notes | None = None


class BaseCreate(BaseWrite):
    """A base as it is added, with where it starts."""

    start: items.ItemStart | None = None


def _read(
    base: Base,
    item: Item,
    on_hand: Decimal,
    sees_costs: bool,
    properties: list[HoardPropertySummary],
) -> dict[str, Any]:
    return {
        **base.model_dump(),
        "on_hand": on_hand,
        "average_cost": item.average_cost if sees_costs else None,
        "properties": properties,
    }


async def _detail(db: AsyncSession, base_id: int, sees_costs: bool) -> BaseDetail:
    """The base as the database holds it."""
    db.expunge_all()
    base = await found(db, Base, base_id)
    item = await found(db, Item, base_id)
    held = (await items.holdings(db, [base_id]))[base_id]
    values = (await values_of(db, RecordKind.BASE, [base_id]))[base_id]
    return BaseDetail.model_validate(
        {
            **_read(base, item, held.on_hand, sees_costs, values),
            **held.model_dump(),
            "picture_id": await items.picture_id(db, base_id),
        }
    )


async def _write(db: AsyncSession, base: Base, body: BaseWrite) -> None:
    await require_references(db, kind=CategoryKind.BASE, units=(body.tracking_unit_id,))
    if base.id is not None:
        await items.require_tracking_unit(db, base.id, base.tracking_unit_id, body.tracking_unit_id)
    unsent = {"auto_manufacture"} - body.model_fields_set
    made = body.auto_manufacture if not unsent else base.auto_manufacture
    if body.cure_days and made not in (None, AutoManufacture.OFF):
        raise refuse(status.HTTP_409_CONFLICT, BaseError.AUTO_MANUFACTURE_CURES)
    base.sqlmodel_update(
        body.model_dump(exclude={"start", "type_id"} | unsent)
        | {
            "type_id": await type_of(
                db, CategoryKind.BASE, body.type_id, body.category_id, base.type_id
            )
        }
    )


@router.get("", responses=_READ)
async def list_bases(
    db: OrganizationSessionDep,
    page: PageDep,
    sees_costs: SeesCostsDep,
    q: Annotated[str | None, Query(max_length=200)] = None,
    archived: Annotated[bool, Query()] = False,
    type_id: Annotated[int | None, Query()] = None,
) -> Page[BaseRead]:
    """Bases by name: those whose name or SKU holds ``q``, when it is given,
    of the type ``type_id`` when it is given, and archived ones only when
    asked for."""
    query = select(Base, Item).join(Item, col(Item.id) == col(Base.id))
    if not archived:
        query = query.where(Base.status == ItemStatus.ACTIVE)
    if type_id is not None:
        query = query.where(Base.type_id == type_id)
    if q:
        query = query.where(
            or_(
                col(Base.name).icontains(q, autoescape=True),
                col(Base.sku).icontains(q, autoescape=True),
            )
        )
    total = (await db.exec(select(func.count()).select_from(query.subquery()))).one()
    rows = (
        await db.exec(
            query.order_by(col(Base.name), col(Base.id)).offset(page.offset).limit(page.limit)
        )
    ).all()
    ids = [base.id for base, _ in rows]
    totals = await items.on_hand(db, ids)
    values = await values_of(db, RecordKind.BASE, ids)
    return Page(
        items=[
            BaseRead.model_validate(
                _read(base, item, totals.get(base.id, Decimal(0)), sees_costs, values[base.id])
            )
            for base, item in rows
        ],
        total=total,
    )


@router.get("/{base_id}", responses=_READ)
async def read_base(
    base_id: int, db: OrganizationSessionDep, sees_costs: SeesCostsDep
) -> BaseDetail:
    """A base, with its stock and lots at each location."""
    return await _detail(db, base_id, sees_costs)


@router.post("", status_code=status.HTTP_201_CREATED, responses=_CHANGE)
async def create_base(
    body: BaseCreate, db: WriteSessionDep, sees_costs: SeesCostsDep
) -> BaseDetail:
    """Add a base, with its bin and starting stock where it starts."""
    base = Base(name=body.name, tracking_unit_id=body.tracking_unit_id)
    await _write(db, base, body)
    await stock.add_base(db, base)
    if body.start is not None:
        await items.start(db, base.id, body.start, sees_costs)
    await db.commit()
    return await _detail(db, base.id, sees_costs)


@router.put("/{base_id}", responses=_CHANGE)
async def update_base(
    base_id: int, body: BaseWrite, db: WriteSessionDep, sees_costs: SeesCostsDep
) -> BaseDetail:
    base = await found(db, Base, base_id)
    await _write(db, base, body)
    db.add(base)
    await db.commit()
    return await _detail(db, base_id, sees_costs)


@router.delete("/{base_id}", status_code=status.HTTP_204_NO_CONTENT, responses=_CHANGE)
async def delete_base(base_id: int, db: WriteSessionDep) -> None:
    """Remove a base with its recipe, stock, lots and adjustments. One a recipe
    or a production run uses stays."""
    await found(db, Base, base_id)
    with refused_as(SettingsError.IN_USE):
        await stock.remove_item(db, base_id)
        await db.commit()
