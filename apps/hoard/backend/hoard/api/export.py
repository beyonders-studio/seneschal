"""Each kind of record, exported as CSV or JSON: every record of the kind in the
organization, one row each, with every column but the organization's. Without
the ``costs`` permission, no column of what something cost or sells for is
sent.

An export runs as a job (``seneschal_core.jobs``) of kind :data:`EXPORT_JOB`:
asking for one queues it, with whether the member who asked sees costs, and
its file is downloaded from the job once it is done."""

from __future__ import annotations

import csv
import io
import json
from datetime import date
from decimal import Decimal
from enum import Enum, StrEnum
from typing import Annotated, Any

from fastapi import APIRouter, Query, status
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
from seneschal_core import jobs
from seneschal_core.api import OrganizationSessionDep, SessionDep
from seneschal_core.jobs import JobContext, JobFile, JobRead, JobResult

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

#: The kind of job an export is.
EXPORT_JOB = "export"

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


@router.post(
    "/{kind}",
    status_code=status.HTTP_202_ACCEPTED,
    responses={401: {"description": "NOT_AUTHENTICATED"}},
)
async def export_records(
    kind: ExportKind,
    db: OrganizationSessionDep,
    session: SessionDep,
    sees_costs: SeesCostsDep,
    file_format: Annotated[ExportFormat, Query(alias="format")] = ExportFormat.CSV,
) -> JobRead:
    """Queue an export of every record of ``kind``; its job's file is the export."""
    job = await jobs.enqueue(
        db,
        EXPORT_JOB,
        {"kind": kind.value, "format": file_format.value, "sees_costs": sees_costs},
        started_by=session.member_ref,
    )
    await db.commit()
    return JobRead.model_validate(job, from_attributes=True)


async def export(job: JobContext) -> JobResult:
    """The export a job asks for: every record of its kind, oldest first, as a file."""
    kind, file_format = ExportKind(job.args["kind"]), ExportFormat(job.args["format"])
    model, costs = KINDS[kind]
    mapper = inspect(model)
    columns = [
        column.key
        for column in mapper.columns
        if column.key != "organization_id" and (job.args["sees_costs"] or column.key not in costs)
    ]
    await job.progress("reading")
    async with job.plugin.database.organization_session(job.organization_id) as db:
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
    return JobResult(
        file=JobFile(content.encode(), media_type, f"{kind.value}.{file_format.value}")
    )
