"""Declarations: ingredient lists and allergens, rolled up through recipes
(``hoard.declarations``). Every member reads a material's, a product's or a
lot's; a member who may change stock records a material's ingredient name and
allergens, and a bought lot's own allergens.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, Literal

from fastapi import APIRouter, status
from pydantic import BaseModel, Field

from hoard.api.settings import found
from hoard.declarations import Declaration, Declarations
from hoard.records import Allergen, Lot, Material, Product
from seneschal_core.api import OrganizationSessionDep, WriteSessionDep, refuse

router = APIRouter(prefix="/declarations", tags=["declarations"])

_READ: dict[int | str, dict[str, Any]] = {
    401: {"description": "NOT_AUTHENTICATED"},
    404: {"description": "NOT_FOUND"},
}
_CHANGE: dict[int | str, dict[str, Any]] = {
    **_READ,
    403: {"description": "READ_ONLY"},
    422: {"description": "NOT_BOUGHT"},
}


class DeclarationError(StrEnum):
    """Why a declaration was refused."""

    #: Only a lot of a material records allergens of its own; a lot a run made
    #: declares what the run consumed.
    NOT_BOUGHT = "NOT_BOUGHT"


class DeclarationRead(BaseModel):
    #: What a material or a lot records itself; null where nothing is
    #: recorded, and for a product.
    recorded_allergens: list[Allergen] | None
    #: What an ingredient list calls a material, when not its name.
    ingredient_name: str | None
    #: What it is made of, the heaviest first.
    ingredients: list[str]
    #: The allergens it contains, rolled up.
    allergens: list[Allergen]
    #: The materials in it whose allergens are not recorded.
    undeclared: list[str]


class DeclarationLotWrite(BaseModel):
    #: Null when this lot's supplier declares nothing of its own.
    allergens: list[Allergen] | None


class DeclarationMaterialWrite(DeclarationLotWrite):
    ingredient_name: Annotated[str, Field(min_length=1, max_length=200)] | None


def _read(
    declared: Declaration, recorded: list[str] | None = None, ingredient_name: str | None = None
) -> DeclarationRead:
    return DeclarationRead(
        recorded_allergens=None if recorded is None else [Allergen(key) for key in recorded],
        ingredient_name=ingredient_name,
        ingredients=declared.ingredients,
        allergens=declared.allergens,
        undeclared=declared.undeclared,
    )


def _keys(allergens: list[Allergen] | None) -> list[str] | None:
    """Each allergen once, in :class:`Allergen`'s order."""
    if allergens is None:
        return None
    return [allergen.value for allergen in Allergen if allergen in allergens]


@router.get("/{of}/{record_id}", responses=_READ)
async def read_declaration(
    of: Literal["material", "product", "lot"], record_id: int, db: OrganizationSessionDep
) -> DeclarationRead:
    """What a material, a product or a lot declares."""
    declarations = Declarations(db)
    if of == "material":
        material = await found(db, Material, record_id)
        return _read(
            await declarations.item(material.id), material.allergens, material.ingredient_name
        )
    if of == "product":
        return _read(await declarations.product(await found(db, Product, record_id)))
    lot = await found(db, Lot, record_id)
    return _read(await declarations.lot(lot), lot.allergens)


@router.put("/material/{material_id}", responses=_CHANGE)
async def update_material_declaration(
    material_id: int, body: DeclarationMaterialWrite, db: WriteSessionDep
) -> DeclarationRead:
    """Record what a material's supplier declares."""
    material = await found(db, Material, material_id)
    material.ingredient_name = body.ingredient_name
    material.allergens = _keys(body.allergens)
    db.add(material)
    await db.commit()
    return await read_declaration("material", material_id, db)


@router.put("/lot/{lot_id}", responses=_CHANGE)
async def update_lot_declaration(
    lot_id: int, body: DeclarationLotWrite, db: WriteSessionDep
) -> DeclarationRead:
    """Record what a bought lot's supplier declares of it."""
    lot = await found(db, Lot, lot_id)
    if await db.get(Material, lot.item_id) is None:
        raise refuse(status.HTTP_422_UNPROCESSABLE_CONTENT, DeclarationError.NOT_BOUGHT)
    lot.allergens = _keys(body.allergens)
    db.add(lot)
    await db.commit()
    return await read_declaration("lot", lot_id, db)
