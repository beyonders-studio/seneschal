"""Bases: made from materials and other bases, never bought or sold, and their
stock per location.

A base's average cost is sent only to a member who sees costs; none is taken,
since the runs that make it set it (``hoard.costing``). Its recipe is kept
under ``/recipes``.
"""

from __future__ import annotations

from decimal import Decimal
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
from hoard.api.settings import SettingsError, found, refused_as, require_references
from hoard.api.shapes import Page, PageDep
from hoard.records import Base, CategoryKind, Item, RecordKind
from seneschal_core.api import OrganizationSessionDep, WriteSessionDep

router = APIRouter(prefix="/bases", tags=["bases"])

_READ: dict[int | str, dict[str, Any]] = {
    401: {"description": "NOT_AUTHENTICATED"},
    404: {"description": "NOT_FOUND"},
}
_CHANGE: dict[int | str, dict[str, Any]] = {
    **_READ,
    403: {"description": "READ_ONLY"},
    409: {"description": "IN_USE"},
    422: {"description": "UNIT_UNKNOWN, CATEGORY_UNKNOWN"},
}


class BaseRead(BaseModel):
    id: int
    name: str
    sku: str | None
    #: The unit stock is counted in.
    tracking_unit_id: int
    low_stock_limit: Decimal | None
    category_id: int | None
    #: What is on hand at every location.
    on_hand: Decimal
    #: The moving average cost of one tracking unit; null without ``costs``.
    average_cost: Decimal | None
    #: What it holds of the properties people add to bases.
    properties: list[HoardPropertySummary]


class BaseDetail(BaseRead, items.ItemHoldings, items.ItemStamps):
    """A base, with its stock and lots at each location."""


class BaseWrite(BaseModel):
    """A base as it is added, or as an edit leaves it."""

    name: items.Text
    sku: items.OptionalText = None
    tracking_unit_id: int
    low_stock_limit: items.Amount | None = None
    category_id: int | None = None


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
        {**_read(base, item, held.on_hand, sees_costs, values), **held.model_dump()}
    )


async def _write(db: AsyncSession, base: Base, body: BaseWrite) -> None:
    await require_references(
        db, kind=CategoryKind.BASE, units=(body.tracking_unit_id,), category_id=body.category_id
    )
    base.sqlmodel_update(body.model_dump())


@router.get("", responses=_READ)
async def list_bases(
    db: OrganizationSessionDep,
    page: PageDep,
    sees_costs: SeesCostsDep,
    q: Annotated[str | None, Query(max_length=200)] = None,
) -> Page[BaseRead]:
    """Bases by name: those whose name or SKU holds ``q``, when it is given."""
    query = select(Base, Item).join(Item, col(Item.id) == col(Base.id))
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
async def create_base(body: BaseWrite, db: WriteSessionDep, sees_costs: SeesCostsDep) -> BaseDetail:
    base = Base(name=body.name, tracking_unit_id=body.tracking_unit_id)
    await _write(db, base, body)
    await stock.add_base(db, base)
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
