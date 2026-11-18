"""Each kind of record, exported: every record of the kind in the
organization. The catalogue's kinds (materials, bases, products with their
variations, and recipes with their lines) export as CSV that reads as a
spreadsheet and imports back (``hoard.imports.spreadsheets``); the others as
CSV or JSON, one row each, with every column but the organization's. Without
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

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import inspect
from sqlmodel import SQLModel, select

from hoard.api.deps import HoardError, SeesCostsDep
from hoard.imports import spreadsheets
from hoard.records import (
    Adjustment,
    Lot,
    Purchase,
    PurchaseLine,
    Stocktake,
    StocktakeRow,
)
from seneschal_core import jobs, organizations
from seneschal_core.api import OrganizationSessionDep, SessionDep, refuse
from seneschal_core.jobs import JobContext, JobError, JobFile, JobRead, JobResult

router = APIRouter(prefix="/export", tags=["export"])


class ExportKind(StrEnum):
    """The kinds of record that export."""

    MATERIALS = "materials"
    BASES = "bases"
    PRODUCTS = "products"
    RECIPES = "recipes"
    PURCHASES = "purchases"
    PURCHASE_LINES = "purchase_lines"
    LOTS = "lots"
    ADJUSTMENTS = "adjustments"
    STOCKTAKES = "stocktakes"
    STOCKTAKE_ROWS = "stocktake_rows"


class ExportFormat(StrEnum):
    CSV = "csv"
    JSON = "json"


class ExportError(StrEnum):
    """Why an export was refused."""

    #: The catalogue's kinds export as CSV only.
    CSV_ONLY = "CSV_ONLY"


#: The catalogue's kinds, which export as spreadsheets that import back.
CATALOGUE = (ExportKind.MATERIALS, ExportKind.BASES, ExportKind.PRODUCTS, ExportKind.RECIPES)

#: Each other kind's records, and their columns of what something cost or sells for.
KINDS: dict[ExportKind, tuple[type[SQLModel], frozenset[str]]] = {
    ExportKind.PURCHASES: (Purchase, frozenset({"discount", "shipping", "tax"})),
    ExportKind.PURCHASE_LINES: (PurchaseLine, frozenset({"unit_cost"})),
    ExportKind.LOTS: (Lot, frozenset()),
    ExportKind.ADJUSTMENTS: (Adjustment, frozenset({"unit_cost"})),
    ExportKind.STOCKTAKES: (Stocktake, frozenset()),
    ExportKind.STOCKTAKE_ROWS: (StocktakeRow, frozenset()),
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
    responses={
        401: {"description": "NOT_AUTHENTICATED"},
        422: {"description": "CSV_ONLY"},
    },
)
async def export_records(
    kind: ExportKind,
    db: OrganizationSessionDep,
    session: SessionDep,
    sees_costs: SeesCostsDep,
    file_format: Annotated[ExportFormat, Query(alias="format")] = ExportFormat.CSV,
) -> JobRead:
    """Queue an export of every record of ``kind``; its job's file is the export."""
    if kind in CATALOGUE and file_format is not ExportFormat.CSV:
        raise refuse(status.HTTP_422_UNPROCESSABLE_CONTENT, ExportError.CSV_ONLY)
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
    if kind in CATALOGUE:
        return await _catalogue(job, kind)
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


async def _catalogue(job: JobContext, kind: ExportKind) -> JobResult:
    """One of the catalogue's spreadsheets, with its suppliers by name."""
    await job.progress("reading")
    async with job.plugin.database.session() as db:
        organization = await organizations.get(db, job.organization_id)
    if organization is None:
        raise JobError(HoardError.NOT_FOUND, retry=False)
    async with job.plugin.database.organization_session(job.organization_id) as db:
        try:
            content = await spreadsheets.export(
                db, job.plugin, organization.community_ref, kind.value, job.args["sees_costs"]
            )
        except HTTPException as refused:
            raise JobError(str(refused.detail)) from None
    return JobResult(file=JobFile(content.encode(), "text/csv; charset=utf-8", f"{kind.value}.csv"))
