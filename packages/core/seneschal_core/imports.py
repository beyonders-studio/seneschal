"""Imports: an app's records brought in from the files another program
exported, checked first and then made by a job.

* **Importers** (:class:`Importer`): an app registers its own, each with an
  id, the files it takes (:class:`ImportFile`, each required or optional), its
  options (a model the upload's ``options`` field is read as), a ``check`` and
  a ``step``.
* **Checking**: ``check`` reads the files and answers a :class:`Checked`: the
  plan, a list of steps as JSON; what the plan will make and what already
  exists, counted by kind; and the problems it found, each with its file and
  row. It writes nothing.
* **Importing**: a plan with no problems is carried out by a job of kind
  :data:`IMPORT_JOB`, whose arguments are the importer's id and its plan; the
  files themselves are not kept. The job hands each step to the importer's
  ``step``, in a transaction of its own, and saves a checkpoint after it. A
  step matches before it makes, so a job resumed after its worker was lost
  makes nothing twice. Its result is what it made, by kind.
* **Routes** (:func:`router`, mounted under ``/api/v1`` beside ``/jobs``), for
  the community's admins: ``GET /imports``, the importers and the files each
  takes; ``POST /imports/{importer}/check``, the files and options in one
  multipart upload, answering the counts and problems and keeping nothing; and
  ``POST /imports/{importer}``, the same upload checked again, which queues
  the job when the check found no problems.

The app registers :func:`handler` as the handler of :data:`IMPORT_JOB`.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, ValidationError
from starlette.datastructures import UploadFile

from seneschal_core import jobs, organizations
from seneschal_core.api import (
    ErrorCode,
    OrganizationDep,
    OrganizationSessionDep,
    Plugin,
    PluginDep,
    SessionDep,
    WriteSessionDep,
    refuse,
)
from seneschal_core.db import OrganizationSession, WriteSession
from seneschal_core.jobs import JobContext, JobError, JobRead, JobResult
from seneschal_core.organizations import Organization
from seneschal_core.sessions import SessionClaims

#: The kind of job an import is.
IMPORT_JOB = "import"
#: The step an import's job reports while it carries out its plan.
IMPORT_STEP = "import"
#: The largest file an import reads.
MAX_FILE_BYTES = 10 * 1024 * 1024
#: The upload's field holding the options, as JSON.
OPTIONS_FIELD = "options"


class ImportRefusal(StrEnum):
    """Why an import was refused: codes pages translate."""

    #: Only a member who administers the community imports.
    IMPORT_ADMIN_ONLY = "IMPORT_ADMIN_ONLY"
    #: The app has no importer of that id.
    IMPORTER_UNKNOWN = "IMPORTER_UNKNOWN"
    #: A file the importer requires was not sent.
    IMPORT_FILE_MISSING = "IMPORT_FILE_MISSING"
    #: A file is larger than an import reads.
    IMPORT_FILE_TOO_LARGE = "IMPORT_FILE_TOO_LARGE"
    #: The options are not the importer's.
    IMPORT_OPTIONS_INVALID = "IMPORT_OPTIONS_INVALID"
    #: The check found problems, so nothing is imported.
    IMPORT_PROBLEMS = "IMPORT_PROBLEMS"


@dataclass(frozen=True)
class ImportFile:
    """A file an importer takes, by the upload's field name for it."""

    name: str
    required: bool = True


class ImportProblem(BaseModel):
    """Something a check found that stops the import: a code pages translate,
    the file and row it is on (a row counted as a spreadsheet shows it, its
    header the first), and what the row names, when that is the problem."""

    code: str
    file: str | None = None
    row: int | None = None
    name: str | None = None


class ImportCount(BaseModel):
    """What a plan holds of one kind of record: how many it makes, and how
    many already exist and are left as they are."""

    kind: str
    new: int
    existing: int


@dataclass(frozen=True)
class Checked:
    """What a check answers: the plan's steps, as JSON a job keeps, its
    counts by kind, and the problems found."""

    plan: list[dict[str, Any]]
    counts: list[ImportCount]
    problems: list[ImportProblem]


@dataclass(frozen=True)
class ImportCheck:
    """What a check is given: the organization's session, which it only
    reads, the member asking, and the files and options sent."""

    db: OrganizationSession
    plugin: Plugin
    organization: Organization
    session: SessionClaims
    #: Each file sent, by its field's name.
    files: dict[str, bytes]
    options: Any


@dataclass
class ImportStep:
    """What a step is given: its job, a session writing for the member who
    started it, which the job commits after the step, and the organization."""

    job: JobContext
    db: WriteSession
    organization: Organization
    #: Whatever the importer keeps between one run's steps, such as what it
    #: read once for all of them; empty again when a job is resumed.
    memo: dict[str, Any] = field(default_factory=dict)


#: A check: the plan, counts and problems of what was sent.
Check = Callable[[ImportCheck], Awaitable[Checked]]
#: A step of a plan carried out: what it made, by kind. It matches before it
#: makes, so it makes nothing a run that was lost made already.
Step = Callable[[ImportStep, dict[str, Any]], Awaitable[Mapping[str, int]]]


@dataclass(frozen=True)
class Importer:
    """One way into an app: the files it reads, its options, and how it
    checks them and carries out the plan."""

    id: str
    files: tuple[ImportFile, ...]
    #: The model the upload's options are read as.
    options: type[BaseModel]
    check: Check
    step: Step


# The job.


def handler(importers: Sequence[Importer]) -> jobs.Handler:
    """The handler of :data:`IMPORT_JOB` for ``importers``: each step of the
    plan carried out by its importer, in its own transaction, from the step
    after the last checkpoint."""
    by_id = {importer.id: importer for importer in importers}

    async def run(job: JobContext) -> JobResult:
        importer = by_id.get(job.args.get("importer", ""))
        if importer is None:
            raise JobError(ImportRefusal.IMPORTER_UNKNOWN, retry=False)
        async with job.plugin.database.session() as db:
            organization = await organizations.get(db, job.organization_id)
        if organization is None or not organization.available:
            raise JobError(ErrorCode.COMMUNITY_UNAVAILABLE, retry=False)
        if organization.read_only:
            raise JobError(ErrorCode.READ_ONLY, retry=False)
        steps: list[dict[str, Any]] = job.args["plan"]
        saved: dict[str, Any] = job.checkpoint or {"next": 0, "made": {}}
        made: Counter[str] = Counter(saved["made"])
        memo: dict[str, Any] = {}
        for index in range(saved["next"], len(steps)):
            await job.progress(IMPORT_STEP, index, len(steps))
            async with job.plugin.database.write_session(
                job.organization_id, actor=job.started_by
            ) as db:
                made.update(
                    await importer.step(ImportStep(job, db, organization, memo), steps[index])
                )
                await db.commit()
            await job.save_checkpoint({"next": index + 1, "made": dict(made)})
        await job.progress(IMPORT_STEP, len(steps), len(steps))
        return JobResult(data={"made": dict(made)})

    return run


# Routes.


class ImportFileRead(BaseModel):
    name: str
    required: bool


class ImporterRead(BaseModel):
    """An importer, and the files it takes."""

    id: str
    files: list[ImportFileRead]


class ImportCheckRead(BaseModel):
    """What a check found: what the import makes and what exists already, by
    kind, and the problems that stop it."""

    counts: list[ImportCount]
    problems: list[ImportProblem]


def _admin(session: SessionDep) -> SessionClaims:
    if not session.admin:
        raise refuse(status.HTTP_403_FORBIDDEN, ImportRefusal.IMPORT_ADMIN_ONLY)
    return session


AdminSessionDep = Annotated[SessionClaims, Depends(_admin)]


def _upload(importers: Sequence[Importer]) -> dict[str, Any]:
    """The upload a check and an import take: each file under its field's
    name, any of ``importers``' files, and the options as JSON."""
    files = {
        file.name: {"type": "string", "format": "binary"} for i in importers for file in i.files
    }
    return {
        "requestBody": {
            "required": True,
            "content": {
                "multipart/form-data": {
                    "schema": {
                        "type": "object",
                        "properties": {**files, OPTIONS_FIELD: {"type": "string"}},
                    }
                }
            },
        }
    }


_REFUSALS: dict[int | str, dict[str, Any]] = {
    401: {"description": "NOT_AUTHENTICATED"},
    403: {"description": "IMPORT_ADMIN_ONLY"},
    404: {"description": "IMPORTER_UNKNOWN"},
    413: {"description": "IMPORT_FILE_TOO_LARGE"},
    422: {"description": "IMPORT_FILE_MISSING, IMPORT_OPTIONS_INVALID"},
}


def router(importers: Sequence[Importer]) -> APIRouter:
    """The routes of ``importers``, under ``/imports``."""
    by_id = {importer.id: importer for importer in importers}
    routes = APIRouter(prefix="/imports", tags=["imports"])
    upload = _upload(importers)

    def importer_of(importer_id: str) -> Importer:
        importer = by_id.get(importer_id)
        if importer is None:
            raise refuse(status.HTTP_404_NOT_FOUND, ImportRefusal.IMPORTER_UNKNOWN)
        return importer

    async def checked(
        importer: Importer,
        request: Request,
        db: OrganizationSession,
        plugin: Plugin,
        organization: Organization,
        session: SessionClaims,
    ) -> Checked:
        """The upload read and checked by ``importer``."""
        files: dict[str, bytes] = {}
        async with request.form(max_files=len(importer.files), max_fields=1) as form:
            for wanted in importer.files:
                sent = form.get(wanted.name)
                if isinstance(sent, UploadFile):
                    content = await sent.read(MAX_FILE_BYTES + 1)
                    if len(content) > MAX_FILE_BYTES:
                        raise refuse(
                            status.HTTP_413_CONTENT_TOO_LARGE, ImportRefusal.IMPORT_FILE_TOO_LARGE
                        )
                    if content:
                        files[wanted.name] = content
                if wanted.required and wanted.name not in files:
                    raise refuse(
                        status.HTTP_422_UNPROCESSABLE_CONTENT, ImportRefusal.IMPORT_FILE_MISSING
                    )
            sent_options = form.get(OPTIONS_FIELD)
        try:
            options = importer.options.model_validate(
                json.loads(sent_options) if isinstance(sent_options, str) and sent_options else {}
            )
        except (ValueError, ValidationError):
            raise refuse(
                status.HTTP_422_UNPROCESSABLE_CONTENT, ImportRefusal.IMPORT_OPTIONS_INVALID
            ) from None
        return await importer.check(
            ImportCheck(
                db=db,
                plugin=plugin,
                organization=organization,
                session=session,
                files=files,
                options=options,
            )
        )

    @routes.get("", responses={401: _REFUSALS[401], 403: _REFUSALS[403]})
    async def list_importers(_admin: AdminSessionDep) -> list[ImporterRead]:
        """The app's importers, and the files each takes."""
        return [
            ImporterRead(
                id=importer.id,
                files=[
                    ImportFileRead(name=file.name, required=file.required)
                    for file in importer.files
                ],
            )
            for importer in importers
        ]

    @routes.post("/{importer_id}/check", responses=_REFUSALS, openapi_extra=upload)
    async def check_import(
        importer_id: str,
        request: Request,
        session: AdminSessionDep,
        db: OrganizationSessionDep,
        plugin: PluginDep,
        organization: OrganizationDep,
    ) -> ImportCheckRead:
        """What importing the files would make, what exists already, and the
        problems that stop it. Nothing is kept."""
        found = await checked(importer_of(importer_id), request, db, plugin, organization, session)
        return ImportCheckRead(counts=found.counts, problems=found.problems)

    @routes.post(
        "/{importer_id}",
        status_code=status.HTTP_202_ACCEPTED,
        responses={
            **_REFUSALS,
            403: {"description": "IMPORT_ADMIN_ONLY, READ_ONLY"},
            409: {"description": "IMPORT_PROBLEMS"},
        },
        openapi_extra=upload,
    )
    async def start_import(
        importer_id: str,
        request: Request,
        session: AdminSessionDep,
        db: WriteSessionDep,
        plugin: PluginDep,
        organization: OrganizationDep,
    ) -> JobRead:
        """Check the files again and, with no problems, queue the import's
        job. The files are not kept: the job holds the plan."""
        importer = importer_of(importer_id)
        found = await checked(importer, request, db, plugin, organization, session)
        if found.problems:
            raise refuse(status.HTTP_409_CONFLICT, ImportRefusal.IMPORT_PROBLEMS)
        job = await jobs.enqueue(
            db,
            IMPORT_JOB,
            {"importer": importer.id, "plan": found.plan},
            started_by=session.member_ref,
        )
        await db.commit()
        return JobRead.model_validate(job, from_attributes=True)

    return routes
