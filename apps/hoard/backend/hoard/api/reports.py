"""Reports on stock: what to reorder, what stock is worth, what the goods
cost over a period, and the mass balance over one. What stock is worth and
what the goods cost are read only by a member who sees costs."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import StrEnum

from fastapi import APIRouter, status
from pydantic import BaseModel

from hoard import cogs, reports
from hoard.api.deps import SeesCostsDep
from hoard.records import (
    CategoryKind,
    ItemKind,
    PropertyDefinition,
    PropertyType,
    RecordKind,
)
from seneschal_core.api import OrganizationSessionDep, refuse

router = APIRouter(prefix="/reports", tags=["reports"])


class ReportError(StrEnum):
    """Why a report was refused: codes pages translate."""

    #: Only a member who sees costs reads what stock is worth.
    COSTS_ONLY = "COSTS_ONLY"
    #: The period ends before it starts.
    PERIOD_INVALID = "PERIOD_INVALID"
    #: A mass balance splits only by a lot property that is a select.
    SPLIT_INVALID = "SPLIT_INVALID"


class ReportReorderRow(BaseModel):
    """A material, base or product at or below its low-stock limit."""

    kind: CategoryKind
    #: The material's, the base's or the product's id.
    id: int
    name: str
    #: The material's or the base's tracking unit; none for a product.
    unit: str | None
    on_hand: Decimal
    low_stock_limit: Decimal


class ReportValuationRow(BaseModel):
    """What one item's stock is worth."""

    item_id: int
    kind: ItemKind
    #: The material's or the base's name, or the variation's product's.
    name: str
    #: A variation's attributes; none for a material or a base.
    attributes: dict[str, str]
    on_hand: Decimal
    average_cost: Decimal
    value: Decimal


class ReportValuation(BaseModel):
    """What the stock is worth, in the organization's currency."""

    currency: str
    items: list[ReportValuationRow]
    total: Decimal


class ReportCostOfGoods(BaseModel):
    """What the goods cost over a period, in the organization's currency:
    opening + starting + purchases − personal use − closing."""

    currency: str
    #: The stock's value at the start of the period's first day.
    opening: Decimal
    #: Starting stock entered during the period, at the cost typed in.
    starting: Decimal
    #: Purchases received during the period, at their landed cost.
    purchases: Decimal
    #: Stock taken for personal use during the period.
    personal_use: Decimal
    #: The stock's value at the end of the period's last day.
    closing: Decimal
    cost_of_goods: Decimal
    #: What the goods shipped during the period cost, less what came back.
    shipped: Decimal
    #: Production runs completed during the period that consumed something
    #: with no cost.
    uncosted_runs: int


class ReportBalanceRow(BaseModel):
    """One item's quantities over a period, under one option of the property
    the balance is split by: opening + received + made − used − sold − lost =
    closing."""

    item_id: int
    kind: ItemKind
    #: The material's or the base's name, or the variation's product's.
    name: str
    #: A variation's attributes; none for a material or a base.
    attributes: dict[str, str]
    #: The material's or the base's tracking unit; none for a variation.
    unit: str | None
    #: The option its lots hold; none where they hold none, and when not split.
    option: str | None
    opening: Decimal
    received: Decimal
    made: Decimal
    used: Decimal
    sold: Decimal
    lost: Decimal
    closing: Decimal


def _period(start: date, end: date) -> None:
    if end < start:
        raise refuse(status.HTTP_422_UNPROCESSABLE_CONTENT, ReportError.PERIOD_INVALID)


@router.get("/reorder", responses={401: {"description": "NOT_AUTHENTICATED"}})
async def read_reorder_report(db: OrganizationSessionDep) -> list[ReportReorderRow]:
    """The materials, bases and products at or below their low-stock limit, by name."""
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


@router.get(
    "/cost-of-goods",
    responses={
        401: {"description": "NOT_AUTHENTICATED"},
        403: {"description": "COSTS_ONLY"},
        422: {"description": "PERIOD_INVALID"},
    },
)
async def read_cost_of_goods_report(
    start: date, end: date, db: OrganizationSessionDep, sees_costs: SeesCostsDep
) -> ReportCostOfGoods:
    """What the goods cost from ``start`` to ``end``, both days included, at
    moving average cost: materials only, without labour or purchase tax."""
    if not sees_costs:
        raise refuse(status.HTTP_403_FORBIDDEN, ReportError.COSTS_ONLY)
    _period(start, end)
    return ReportCostOfGoods.model_validate(
        await cogs.cost_of_goods(db, start, end), from_attributes=True
    )


@router.get(
    "/mass-balance",
    responses={
        401: {"description": "NOT_AUTHENTICATED"},
        422: {"description": "PERIOD_INVALID, SPLIT_INVALID"},
    },
)
async def read_mass_balance_report(
    start: date, end: date, db: OrganizationSessionDep, split_by: int | None = None
) -> list[ReportBalanceRow]:
    """Each item's quantities from ``start`` to ``end``, both days included,
    by name; split by the options of the lot property ``split_by``, a select,
    when one is named."""
    _period(start, end)
    if split_by is not None:
        split = await db.get(PropertyDefinition, split_by)
        if split is None or (split.kind, split.type) != (RecordKind.LOT, PropertyType.SELECT):
            raise refuse(status.HTTP_422_UNPROCESSABLE_CONTENT, ReportError.SPLIT_INVALID)
    return [
        ReportBalanceRow.model_validate(balance, from_attributes=True)
        for balance in await cogs.mass_balance(db, start, end, split_by)
    ]
