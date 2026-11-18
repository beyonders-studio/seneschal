"""What Hoard's importers share: a CSV file's rows, names matched as Hoard
matches them, and the steps of a plan carried out, a write a row is refused
listing that row (``seneschal_core.imports.RowRefused``) for the job to carry
on past."""

from __future__ import annotations

import csv
import io
from collections.abc import Awaitable, Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from typing import Any

from fastapi import HTTPException, status
from pydantic import ValidationError

from hoard.recipes import RecipeRefused
from seneschal_core.api import ErrorCode
from seneschal_core.imports import (
    ImportProblem,
    ImportResult,
    ImportRowFailure,
    ImportStep,
    RowRefused,
    Step,
)
from seneschal_core.jobs import JobError

#: A row as a file holds it: each column's cell, stripped.
Row = dict[str, str]

#: The problem a file that can't be read as CSV is.
FILE_UNREADABLE = "FILE_UNREADABLE"
#: The problem a file lacking a column it needs is.
COLUMN_MISSING = "COLUMN_MISSING"


def key(name: str) -> str:
    """A name as names are matched: ignoring case, as the database's ``lower`` does."""
    return name.strip().lower()


def read(
    name: str, content: bytes | None, columns: Sequence[str], problems: list[ImportProblem]
) -> tuple[list[str], list[tuple[int, Row]]] | None:
    """A file's header and rows, each row with its number as a spreadsheet
    counts it, the header being 1; or None when it is absent, can't be read,
    or lacks one of ``columns``. A byte-order mark and either line ending are
    read."""
    if content is None:
        return None
    try:
        reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig"), newline=""))
        header = [column.strip() for column in reader.fieldnames or []]
        rows = [
            (
                number,
                {
                    column: value.strip() if isinstance(value, str) else ""
                    for column, value in zip(header, record.values(), strict=False)
                },
            )
            for number, record in enumerate(reader, start=2)
        ]
    except (UnicodeDecodeError, csv.Error):
        problems.append(ImportProblem(code=FILE_UNREADABLE, file=name))
        return None
    missing = [column for column in columns if column not in header]
    problems.extend(
        ImportProblem(code=COLUMN_MISSING, file=name, row=1, name=column) for column in missing
    )
    return None if missing else (header, rows)


#: What a step's write may be refused with.
_REFUSALS = (HTTPException, RecipeRefused, ValidationError)


class _Blamed(Exception):
    def __init__(self, field: str, refused: Exception) -> None:
        super().__init__(field)
        self.field = field
        self.refused = refused


@contextmanager
def blame(field: str) -> Iterator[None]:
    """Name ``field`` as what stopped the row when a write inside is refused."""
    try:
        yield
    except _REFUSALS as refused:
        raise _Blamed(field, refused) from None


def _code(refused: Exception) -> str:
    """The code a write was refused with. One Initiative or Hoard couldn't
    answer for now fails the step, to be tried again; a store's refusal is
    the row's."""
    if isinstance(refused, RecipeRefused):
        return refused.code
    if isinstance(refused, ValidationError):
        return ErrorCode.REQUEST_INVALID
    assert isinstance(refused, HTTPException)
    if refused.status_code >= 500 and refused.status_code != status.HTTP_502_BAD_GATEWAY:
        raise JobError(str(refused.detail), retry=True) from None
    return str(refused.detail)


def _field(refused: Exception) -> str | None:
    if isinstance(refused, ValidationError):
        errors = refused.errors()
        return str(errors[0]["loc"][0]) if errors and errors[0]["loc"] else None
    return None


#: One kind of step: what it made, matched and changed.
StepOf = Callable[[ImportStep, dict[str, Any]], Awaitable[ImportResult]]


def stepper(steps: Mapping[str, StepOf]) -> Step:
    """Carry out a planned step by its ``step``, which is the kind of record
    it writes. A write refused lists the step's ``file`` and ``row``."""

    async def step(run: ImportStep, planned: dict[str, Any]) -> ImportResult:
        field: str | None = None
        try:
            return await steps[planned["step"]](run, planned)
        except _Blamed as blamed:
            field, refused = blamed.field, blamed.refused
        except _REFUSALS as caught:
            refused = caught
        raise RowRefused(
            planned["step"],
            ImportRowFailure(
                file=planned.get("file"),
                row=planned.get("row"),
                field=field or _field(refused),
                code=_code(refused),
            ),
        )

    return step
