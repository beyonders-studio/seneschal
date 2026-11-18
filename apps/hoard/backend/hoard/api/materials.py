"""Materials: what Hoard buys and never makes, and their stock per location.

A material's average cost is sent only to a member who sees costs; none is
taken, since purchases and starting stock set it (``hoard.costing``), but for
the unit cost of the stock a new material starts with. Bought and tracked in
standard units of one measure, its purchase conversion is theirs
(``hoard.units``), and only otherwise the number sent. Its tracking unit is
fixed once anything records a number in it. Its bins and picture are kept
under ``/items``. An archived material is listed only when asked for.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Query, status
from pydantic import BaseModel
from sqlalchemy import func, or_
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from hoard import stock, units
from hoard.api import items
from hoard.api.deps import SeesCostsDep
from hoard.api.properties import HoardPropertySummary, values_of
from hoard.api.settings import SettingsError, found, refused_as, require_references, type_of
from hoard.api.shapes import Page, PageDep
from hoard.records import CategoryKind, Item, ItemStatus, Material, RecordKind, Unit
from hoard.units import UnitRefused
from seneschal_core.api import OrganizationSessionDep, WriteSessionDep, refuse
from seneschal_core.db import WriteSession

router = APIRouter(prefix="/materials", tags=["materials"])

_READ: dict[int | str, dict[str, Any]] = {
    401: {"description": "NOT_AUTHENTICATED"},
    404: {"description": "NOT_FOUND"},
}
_CHANGE: dict[int | str, dict[str, Any]] = {
    **_READ,
    403: {"description": "READ_ONLY"},
    409: {
        "description": "IN_USE, TRACKING_UNIT_IN_STOCK, TRACKING_UNIT_IN_PURCHASES, "
        "TRACKING_UNIT_IN_STOCKTAKES, TRACKING_UNIT_IN_RECIPES, TRACKING_UNIT_IN_RUNS, "
        "NOT_INVENTORIABLE, GTIN_TAKEN"
    },
    422: {
        "description": "UNIT_UNKNOWN, CATEGORY_UNKNOWN, TYPE_UNKNOWN, AMOUNT_TOO_SMALL, "
        "GTIN_INVALID"
    },
}


class MaterialRead(BaseModel):
    id: int
    name: str
    status: ItemStatus
    sku: str | None
    #: Its GTIN, as 14 digits: the barcode number on it.
    gtin: str | None
    inventoriable: bool
    #: The unit stock is counted in.
    tracking_unit_id: int
    #: The unit it is bought in, when not the tracking unit.
    purchase_unit_id: int | None
    #: Tracking units in one purchase unit.
    purchase_conversion: Decimal
    #: What one tracking unit weighs, in grams, for a material not tracked by
    #: weight; only declarations read it.
    unit_weight: Decimal | None
    low_stock_limit: Decimal | None
    category_id: int | None
    #: Its type, which decides the page it is shown on.
    type_id: int
    #: The preferred supplier: an Initiative contact's id.
    supplier_contact_id: int | None
    #: The supplier's code for it.
    supplier_code: str | None
    #: What is on hand at every location.
    on_hand: Decimal
    #: The moving average cost of one tracking unit; null without ``costs``.
    average_cost: Decimal | None
    notes: str | None
    #: What it holds of the properties people add to materials.
    properties: list[HoardPropertySummary]


class MaterialDetail(MaterialRead, items.ItemHoldings, items.ItemStamps):
    """A material, with its stock, bin and lots at each location."""

    #: Its picture's id, which reads it (``/items/{id}/picture/{picture_id}``).
    picture_id: int | None


class MaterialWrite(BaseModel):
    """A material as it is added, or as an edit leaves it."""

    name: items.Text
    status: ItemStatus = ItemStatus.ACTIVE
    sku: items.OptionalText = None
    #: Its GTIN: 8, 12, 13 or 14 digits, kept as 14. Left as it is unless sent.
    gtin: items.OptionalText = None
    inventoriable: bool = True
    tracking_unit_id: int
    purchase_unit_id: int | None = None
    #: Taken only for units that don't convert by themselves.
    purchase_conversion: items.Positive = Decimal(1)
    #: Kept only for a material not tracked by weight.
    unit_weight: items.Positive | None = None
    low_stock_limit: items.Amount | None = None
    category_id: int | None = None
    #: Its type: as it is made, its category's default type unless sent, and
    #: its kind's default without one; left as it is unless sent.
    type_id: int | None = None
    supplier_contact_id: int | None = None
    supplier_code: items.OptionalText = None
    notes: items.Notes | None = None


class MaterialCreate(MaterialWrite):
    """A material as it is added, with where it starts."""

    start: items.ItemStart | None = None


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
        {
            **_read(material, item, held.on_hand, sees_costs, values),
            **held.model_dump(),
            "picture_id": await items.picture_id(db, material_id),
        }
    )


async def write_material(db: AsyncSession, material: Material, body: MaterialWrite) -> None:
    """Write ``material`` as ``body`` leaves it, as an edit does."""
    await require_references(
        db, kind=CategoryKind.MATERIAL, units=(body.tracking_unit_id, body.purchase_unit_id)
    )
    if material.id is not None:
        await items.require_tracking_unit(
            db, material.id, material.tracking_unit_id, body.tracking_unit_id
        )
    tracking = await db.get_one(Unit, body.tracking_unit_id)
    bought = (
        None if body.purchase_unit_id is None else await db.get_one(Unit, body.purchase_unit_id)
    )
    try:
        conversion = units.purchase_conversion(bought, tracking)
    except UnitRefused as refused:
        raise refuse(status.HTTP_422_UNPROCESSABLE_CONTENT, refused.code) from None
    gtin = await items.gtin_written(db, body, material)
    material.sqlmodel_update(
        body.model_dump(exclude={"start", "type_id", "gtin"})
        | gtin
        | {
            "type_id": await type_of(
                db, CategoryKind.MATERIAL, body.type_id, body.category_id, material.type_id
            ),
            "purchase_conversion": conversion or body.purchase_conversion,
            "unit_weight": None
            if units.measure(tracking) is units.Measure.MASS
            else body.unit_weight,
        }
    )


@router.get("", responses=_READ)
async def list_materials(
    db: OrganizationSessionDep,
    page: PageDep,
    sees_costs: SeesCostsDep,
    q: Annotated[str | None, Query(max_length=200)] = None,
    archived: Annotated[bool, Query()] = False,
    type_id: Annotated[int | None, Query()] = None,
) -> Page[MaterialRead]:
    """Materials by name: those whose name or SKU holds ``q``, when it is
    given, of the type ``type_id`` when it is given, and archived ones only
    when asked for."""
    query = select(Material, Item).join(Item, col(Item.id) == col(Material.id))
    if not archived:
        query = query.where(Material.status == ItemStatus.ACTIVE)
    if type_id is not None:
        query = query.where(Material.type_id == type_id)
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
    body: MaterialCreate, db: WriteSessionDep, sees_costs: SeesCostsDep
) -> MaterialDetail:
    """Add a material, with its bin and starting stock where it starts."""
    material = await add_material(db, body, sees_costs)
    await db.commit()
    return await _detail(db, material.id, sees_costs)


async def add_material(db: WriteSession, body: MaterialCreate, sees_costs: bool) -> Material:
    """Add a material as ``body`` writes it, with its bin and starting stock
    where it starts."""
    material = Material(name=body.name, tracking_unit_id=body.tracking_unit_id)
    await write_material(db, material, body)
    await stock.add_material(db, material)
    if body.start is not None:
        await items.start(db, material.id, body.start, sees_costs)
    return material


@router.put("/{material_id}", responses=_CHANGE)
async def update_material(
    material_id: int, body: MaterialWrite, db: WriteSessionDep, sees_costs: SeesCostsDep
) -> MaterialDetail:
    material = await found(db, Material, material_id)
    await write_material(db, material, body)
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
