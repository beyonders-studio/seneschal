"""Declarations: ingredient lists, allergens and EU fragrance allergens,
rolled up through recipes (``hoard.declarations``). Every member reads a
material's, a product's or a lot's; a member who may change stock records a
material's ingredient name, allergens and fragrance allergens, and a bought
lot's own.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Annotated, Any, Literal

from fastapi import APIRouter, status
from pydantic import BaseModel, Field

from hoard.api.settings import found
from hoard.declarations import (
    FRAGRANCE_ALLERGENS,
    LEAVE_ON,
    RINSE_OFF,
    Declaration,
    Declarations,
)
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


#: The EU fragrance allergens, by key, in the regulation's order.
FragranceAllergen = StrEnum("FragranceAllergen", [(key, key) for key in FRAGRANCE_ALLERGENS])
#: A fragrance allergen's share of a material or a lot, in percent.
Share = Annotated[Decimal, Field(ge=0, le=100, max_digits=12, decimal_places=6)]


class DeclarationRead(BaseModel):
    #: Whether it records allergens of its own: a material, or a lot of one.
    records_own: bool
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
    #: The fragrance allergens a material or a lot records itself, each with
    #: its share in percent; null where nothing is recorded, and for a product.
    recorded_fragrance: dict[FragranceAllergen, Decimal] | None
    #: Whether any material in it has its fragrance allergens recorded.
    fragrance_recorded: bool
    #: Whether it is washed off, and so takes the rinse-off threshold.
    rinse_off: bool
    #: The share, in percent, past which a fragrance allergen is declared.
    fragrance_threshold: Decimal
    #: Each fragrance allergen in it, with its share in percent.
    fragrance_shares: dict[FragranceAllergen, Decimal]
    #: The fragrance allergens past the threshold, which its label declares.
    fragrance_allergens: list[FragranceAllergen]
    #: The materials in it whose fragrance allergens are not recorded.
    fragrance_undeclared: list[str]


class DeclarationLotWrite(BaseModel):
    #: Null when this lot's supplier declares nothing of its own.
    allergens: list[Allergen] | None
    #: Each fragrance allergen's share in percent; null when this lot's
    #: supplier declares nothing of its own.
    fragrance_allergens: dict[FragranceAllergen, Share] | None


class DeclarationMaterialWrite(DeclarationLotWrite):
    ingredient_name: Annotated[str, Field(min_length=1, max_length=200)] | None


def _read(
    declared: Declaration,
    records_own: bool = False,
    recorded: list[str] | None = None,
    fragrance: dict[str, str] | None = None,
    ingredient_name: str | None = None,
) -> DeclarationRead:
    return DeclarationRead(
        records_own=records_own,
        recorded_allergens=None if recorded is None else [Allergen(key) for key in recorded],
        ingredient_name=ingredient_name,
        ingredients=declared.ingredients,
        allergens=declared.allergens,
        undeclared=declared.undeclared,
        recorded_fragrance=_recorded(fragrance),
        fragrance_recorded=declared.fragrance_recorded,
        rinse_off=declared.rinse_off,
        fragrance_threshold=RINSE_OFF if declared.rinse_off else LEAVE_ON,
        fragrance_shares={
            FragranceAllergen(key): share for key, share in declared.fragrance_shares.items()
        },
        fragrance_allergens=[FragranceAllergen(key) for key in declared.fragrance_allergens],
        fragrance_undeclared=declared.fragrance_undeclared,
    )


def _recorded(fragrance: dict[str, str] | None) -> dict[FragranceAllergen, Decimal] | None:
    """What a material or a lot records of its fragrance allergens; None where
    nothing is, or a share is not a number."""
    if fragrance is None:
        return None
    try:
        return {
            FragranceAllergen(key): Decimal(share)
            for key, share in fragrance.items()
            if key in FRAGRANCE_ALLERGENS
        }
    except InvalidOperation:
        return None


def _keys(allergens: list[Allergen] | None) -> list[str] | None:
    """Each allergen once, in :class:`Allergen`'s order."""
    if allergens is None:
        return None
    return [allergen.value for allergen in Allergen if allergen in allergens]


def _shares(fragrance: dict[FragranceAllergen, Decimal] | None) -> dict[str, str] | None:
    """Each fragrance allergen's share as text, in the regulation's order."""
    if fragrance is None:
        return None
    return {
        allergen.value: f"{fragrance[allergen].normalize():f}"
        for allergen in FragranceAllergen
        if allergen in fragrance
    }


@router.get("/{of}/{record_id}", responses=_READ)
async def read_declaration(
    of: Literal["material", "product", "lot"], record_id: int, db: OrganizationSessionDep
) -> DeclarationRead:
    """What a material, a product or a lot declares."""
    declarations = Declarations(db)
    if of == "material":
        material = await found(db, Material, record_id)
        return _read(
            await declarations.item(material.id),
            True,
            material.allergens,
            material.fragrance_allergens,
            material.ingredient_name,
        )
    if of == "product":
        return _read(await declarations.product(await found(db, Product, record_id)))
    lot = await found(db, Lot, record_id)
    bought = await db.get(Material, lot.item_id) is not None
    return _read(await declarations.lot(lot), bought, lot.allergens, lot.fragrance_allergens)


@router.put("/material/{material_id}", responses=_CHANGE)
async def update_material_declaration(
    material_id: int, body: DeclarationMaterialWrite, db: WriteSessionDep
) -> DeclarationRead:
    """Record what a material's supplier declares."""
    material = await found(db, Material, material_id)
    material.ingredient_name = body.ingredient_name
    material.allergens = _keys(body.allergens)
    material.fragrance_allergens = _shares(body.fragrance_allergens)
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
    lot.fragrance_allergens = _shares(body.fragrance_allergens)
    db.add(lot)
    await db.commit()
    return await read_declaration("lot", lot_id, db)
