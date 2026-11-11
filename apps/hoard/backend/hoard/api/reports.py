"""Reports on stock: what to reorder, and what stock is worth. What stock is
worth is read only by a member who sees costs."""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum

from fastapi import APIRouter, status
from pydantic import BaseModel

from hoard import reports
from hoard.api.deps import SeesCostsDep
from hoard.records import CategoryKind, ItemKind
from seneschal_core.api import OrganizationSessionDep, refuse

router = APIRouter(prefix="/reports", tags=["reports"])


class ReportError(StrEnum):
    """Why a report was refused: codes pages translate."""

    #: Only a member who sees costs reads what stock is worth.
    COSTS_ONLY = "COSTS_ONLY"


class ReportReorderRow(BaseModel):
    """A material or product at or below its low-stock limit."""

    kind: CategoryKind
    #: The material's or the product's id.
    id: int
    name: str
    #: The material's tracking unit; none for a product.
    unit: str | None
    on_hand: Decimal
    low_stock_limit: Decimal


class ReportValuationRow(BaseModel):
    """What one item's stock is worth."""

    item_id: int
    kind: ItemKind
    #: The material's name, or the variation's product's.
    name: str
    #: A variation's attributes; none for a material.
    attributes: dict[str, str]
    on_hand: Decimal
    average_cost: Decimal
    value: Decimal


class ReportValuation(BaseModel):
    """What the stock is worth, in the organization's currency."""

    currency: str
    items: list[ReportValuationRow]
    total: Decimal


@router.get("/reorder", responses={401: {"description": "NOT_AUTHENTICATED"}})
async def read_reorder_report(db: OrganizationSessionDep) -> list[ReportReorderRow]:
    """The materials and products at or below their low-stock limit, by name."""
    return [
        ReportReorderRow.model_validate(shortfall, from_attributes=True)
        for shortfall in await reports.reorder(db)
    ]


@router.get(
    "/valuation",
    responses={
        401: {"description": "NOT_AUTHENTICATED"},
        403: {"description": "COSTS_ONLY"},
    },
)
async def read_valuation_report(
    db: OrganizationSessionDep, sees_costs: SeesCostsDep
) -> ReportValuation:
    """What the stock on hand is worth at each item's average cost, by name,
    and in total."""
    if not sees_costs:
        raise refuse(status.HTTP_403_FORBIDDEN, ReportError.COSTS_ONLY)
    return ReportValuation.model_validate(await reports.valuation(db), from_attributes=True)
