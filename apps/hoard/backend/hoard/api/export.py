"""Each kind of record, exported as CSV or JSON: every record of the kind in the
organization, one row each, with every column but the organization's. Without
the ``costs`` permission, no column of what something cost or sells for is
sent."""

from __future__ import annotations

import csv
import io
import json
from datetime import date
from decimal import Decimal
from enum import Enum, StrEnum
from typing import Annotated, Any

from fastapi import APIRouter, Query, Response
from sqlalchemy import inspect
from sqlmodel import SQLModel, select

from hoard.api.deps import SeesCostsDep
from hoard.records import (
    Adjustment,
    Base,
    Lot,
    Material,
    Product,
    Purchase,
    PurchaseLine,
    Recipe,
    RecipeLine,
    Stocktake,
    StocktakeRow,
    Variation,
)
from seneschal_core.api import OrganizationSessionDep

router = APIRouter(prefix="/export", tags=["export"])


class ExportKind(StrEnum):
    """The kinds of record that export."""

    MATERIALS = "materials"
    BASES = "bases"
    PRODUCTS = "products"
    VARIATIONS = "variations"
    PURCHASES = "purchases"
    PURCHASE_LINES = "purchase_lines"
    LOTS = "lots"
    ADJUSTMENTS = "adjustments"
    STOCKTAKES = "stocktakes"
    STOCKTAKE_ROWS = "stocktake_rows"
    RECIPES = "recipes"
    RECIPE_LINES = "recipe_lines"


class ExportFormat(StrEnum):
    CSV = "csv"
    JSON = "json"


#: Each kind's records, and their columns of what something cost or sells for.
KINDS: dict[ExportKind, tuple[type[SQLModel], frozenset[str]]] = {
    ExportKind.MATERIALS: (Material, frozenset()),
    ExportKind.BASES: (Base, frozenset()),
    ExportKind.PRODUCTS: (Product, frozenset({"default_price"})),
    ExportKind.VARIATIONS: (Variation, frozenset({"price"})),
    ExportKind.PURCHASES: (Purchase, frozenset({"discount", "shipping", "tax"})),
    ExportKind.PURCHASE_LINES: (PurchaseLine, frozenset({"unit_cost"})),
    ExportKind.LOTS: (Lot, frozenset()),
    ExportKind.ADJUSTMENTS: (Adjustment, frozenset({"unit_cost"})),
    ExportKind.STOCKTAKES: (Stocktake, frozenset()),
    ExportKind.STOCKTAKE_ROWS: (StocktakeRow, frozenset()),
    ExportKind.RECIPES: (Recipe, frozenset()),
    ExportKind.RECIPE_LINES: (RecipeLine, frozenset()),
}

#: The characters a spreadsheet reads a cell starting with as a formula.
FORMULA_STARTS = ("=", "+", "-", "@", "\t", "\r")


def _json_value(value: Any) -> Any:
    """A column's value as JSON holds it: exact decimals and dates as text."""
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, date):
        return value.isoformat()
    return value


def _csv_value(value: Any) -> str:
    """A column's value as a CSV cell. Text a spreadsheet would read as a
    formula starts with an apostrophe, so it opens as the text it is."""
    if isinstance(value, str) and value.startswith(FORMULA_STARTS):
        return f"'{value}"
    value = _json_value(value)
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _csv(columns: list[str], records: list[Any]) -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(columns)
    for record in records:
        writer.writerow([_csv_value(getattr(record, column)) for column in columns])
    return out.getvalue()


@router.get(
    "/{kind}",
    response_class=Response,
    responses={
        200: {"content": {"text/csv": {}, "application/json": {}}},
        401: {"description": "NOT_AUTHENTICATED"},
    },
)
async def export_records(
    kind: ExportKind,
    db: OrganizationSessionDep,
    sees_costs: SeesCostsDep,
    file_format: Annotated[ExportFormat, Query(alias="format")] = ExportFormat.CSV,
) -> Response:
    """Every record of ``kind``, oldest first, as a file to download."""
    model, costs = KINDS[kind]
    mapper = inspect(model)
    columns = [
        column.key
        for column in mapper.columns
        if column.key != "organization_id" and (sees_costs or column.key not in costs)
    ]
    records = list(await db.exec(select(model).order_by(*mapper.primary_key)))
    if file_format is ExportFormat.CSV:
        content = _csv(columns, records)
        media_type = "text/csv; charset=utf-8"
    else:
        content = json.dumps(
            [
                {column: _json_value(getattr(record, column)) for column in columns}
                for record in records
            ],
            ensure_ascii=False,
        )
        media_type = "application/json"
    return Response(
        content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{kind.value}.{file_format.value}"'},
    )
