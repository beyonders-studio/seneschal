"""Materials: what Hoard buys and never makes, and their stock per location.

A material's average cost is sent only to a member who sees costs; none is
taken, since purchases and starting stock set it (``hoard.costing``).
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
from hoard.records import CategoryKind, Item, Material, RecordKind
from seneschal_core.api import OrganizationSessionDep, WriteSessionDep

router = APIRouter(prefix="/materials", tags=["materials"])

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


class MaterialRead(BaseModel):
    id: int
    name: str
    sku: str | None
    inventoriable: bool
    #: The unit stock is counted in.
    tracking_unit_id: int
    #: The unit it is bought in, when not the tracking unit.
    purchase_unit_id: int | None
    #: Tracking units in one purchase unit.
    purchase_conversion: Decimal
    low_stock_limit: Decimal | None
    category_id: int | None
    #: The preferred supplier: an Initiative contact's id.
    supplier_contact_id: int | None
    #: The supplier's code for it.
    supplier_code: str | None
    #: What is on hand at every location.
    on_hand: Decimal
    #: The moving average cost of one tracking unit; null without ``costs``.
    average_cost: Decimal | None
    #: What it holds of the properties people add to materials.
    properties: list[HoardPropertySummary]


class MaterialDetail(MaterialRead, items.ItemHoldings, items.ItemStamps):
    """A material, with its stock and lots at each location."""


class MaterialWrite(BaseModel):
    """A material as it is added, or as an edit leaves it."""

    name: items.Text
    sku: items.OptionalText = None
    inventoriable: bool = True
    tracking_unit_id: int
    purchase_unit_id: int | None = None
    purchase_conversion: items.Positive = Decimal(1)
    low_stock_limit: items.Amount | None = None
    category_id: int | None = None
    supplier_contact_id: int | None = None
    supplier_code: items.OptionalText = None


def _read(
    material: Material,
    item: Item,
    on_hand: Decimal,
    sees_costs: bool,
    properties: list[HoardPropertySummary],
) -> dict[str, Any]:
    return {
        **material.model_dump(),
        "on_hand": on_hand,
        "average_cost": item.average_cost if sees_costs else None,
        "properties": properties,
    }


async def _detail(db: AsyncSession, material_id: int, sees_costs: bool) -> MaterialDetail:
    """The material as the database holds it."""
    db.expunge_all()
    material = await found(db, Material, material_id)
    item = await found(db, Item, material_id)
    held = (await items.holdings(db, [material_id]))[material_id]
    values = (await values_of(db, RecordKind.MATERIAL, [material_id]))[material_id]
    return MaterialDetail.model_validate(
        {**_read(material, item, held.on_hand, sees_costs, values), **held.model_dump()}
    )


async def _write(db: AsyncSession, material: Material, body: MaterialWrite) -> None:
    await require_references(
        db,
        kind=CategoryKind.MATERIAL,
        units=(body.tracking_unit_id, body.purchase_unit_id),
        category_id=body.category_id,
    )
    material.sqlmodel_update(body.model_dump())


@router.get("", responses=_READ)
async def list_materials(
    db: OrganizationSessionDep,
    page: PageDep,
    sees_costs: SeesCostsDep,
    q: Annotated[str | None, Query(max_length=200)] = None,
) -> Page[MaterialRead]:
    """Materials by name: those whose name or SKU holds ``q``, when it is given."""
    query = select(Material, Item).join(Item, col(Item.id) == col(Material.id))
    if q:
        query = query.where(
            or_(
                col(Material.name).icontains(q, autoescape=True),
                col(Material.sku).icontains(q, autoescape=True),
            )
        )
    total = (await db.exec(select(func.count()).select_from(query.subquery()))).one()
    rows = (
        await db.exec(
            query.order_by(col(Material.name), col(Material.id))
            .offset(page.offset)
            .limit(page.limit)
        )
    ).all()
    ids = [material.id for material, _ in rows]
    totals = await items.on_hand(db, ids)
    values = await values_of(db, RecordKind.MATERIAL, ids)
    return Page(
        items=[
            MaterialRead.model_validate(
                _read(
                    material,
                    item,
                    totals.get(material.id, Decimal(0)),
                    sees_costs,
                    values[material.id],
                )
            )
            for material, item in rows
        ],
        total=total,
    )


@router.get("/{material_id}", responses=_READ)
async def read_material(
    material_id: int, db: OrganizationSessionDep, sees_costs: SeesCostsDep
) -> MaterialDetail:
    """A material, with its stock and lots at each location."""
    return await _detail(db, material_id, sees_costs)


@router.post("", status_code=status.HTTP_201_CREATED, responses=_CHANGE)
async def create_material(
    body: MaterialWrite, db: WriteSessionDep, sees_costs: SeesCostsDep
) -> MaterialDetail:
    material = Material(name=body.name, tracking_unit_id=body.tracking_unit_id)
    await _write(db, material, body)
    await stock.add_material(db, material)
    await db.commit()
    return await _detail(db, material.id, sees_costs)


@router.put("/{material_id}", responses=_CHANGE)
async def update_material(
    material_id: int, body: MaterialWrite, db: WriteSessionDep, sees_costs: SeesCostsDep
) -> MaterialDetail:
    material = await found(db, Material, material_id)
    await _write(db, material, body)
    db.add(material)
    await db.commit()
    return await _detail(db, material_id, sees_costs)


@router.delete("/{material_id}", status_code=status.HTTP_204_NO_CONTENT, responses=_CHANGE)
async def delete_material(material_id: int, db: WriteSessionDep) -> None:
    """Remove a material with its stock, lots and adjustments. One a purchase
    names stays."""
    await found(db, Material, material_id)
    with refused_as(SettingsError.IN_USE):
        await stock.remove_item(db, material_id)
        await db.commit()
