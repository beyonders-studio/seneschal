"""Long work an app runs for a person, such as an import, an export or a
store's sync: queued as a job, run by a worker beside the app's others, and
followed by the page that started it.

* **Queuing** (:func:`enqueue`): a job of a kind, with its arguments and the
  member who started it, waits in the organization's ``jobs`` table. Queued
  with a ``unique_key``, a job whose kind and key are already queued or
  running is answered instead of a second one. Once the session commits, the
  running :func:`work_forever` is woken for the organization.
* **Working** (:func:`work_forever`, or :func:`work` for one organization):
  each queued job is claimed with ``FOR UPDATE SKIP LOCKED``, so several
  replicas share the queue, and run by the handler the app registered for its
  kind. The handler gets a :class:`JobContext`: its arguments, the checkpoint
  it last saved, and ways to report progress and save a checkpoint. While it
  runs, its heartbeat is kept; a job whose heartbeat is older than
  :data:`STALE_AFTER` was lost with its worker, and is claimed again,
  resuming from its checkpoint. A handler that raises is tried again after a
  growing wait, up to :data:`MOST_ATTEMPTS` tries in all, and then the job
  fails with the error's code.
* **Results**: a handler answers a :class:`JobResult`, JSON and an optional
  file, kept on the job until :func:`prune` removes it, a week after it
  finished (:data:`KEEP_FINISHED`), from the app's upkeep.
* **Routes** (:data:`router`, mounted under ``/api/v1``): ``GET /jobs`` and
  ``GET /jobs/{id}`` for the organization's members, and
  ``GET /jobs/{id}/result`` for the file, which only the member who started
  the job downloads.

Migrations make the table with :func:`create_table`.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Collection, Mapping
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, cast

import sqlalchemy as sa
from alembic import op
from fastapi import APIRouter, Response, status
from sqlalchemy import Column, DateTime, Index, LargeBinary, and_, event, or_, text, update
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.orm import Session, defer
from sqlmodel import Field, SQLModel, col, select

from seneschal_core import organizations
from seneschal_core.api import (
    ErrorCode,
    OrganizationSessionDep,
    Plugin,
    SessionDep,
    refuse,
)
from seneschal_core.db import OrganizationSession, WriteSession
from seneschal_core.migrations import isolate
from seneschal_core.models import OrganizationRow, enum_column, identity

logger = logging.getLogger(__name__)

#: A job whose heartbeat is older than this was lost with its worker.
STALE_AFTER = timedelta(minutes=5)
#: How often a running job's heartbeat is kept.
HEARTBEAT_SECONDS = 60.0
#: The tries a job is given, the first and three more, before it fails.
MOST_ATTEMPTS = 4
#: The wait before a job is tried again, doubling with each try.
FIRST_WAIT = timedelta(seconds=30)
#: How long a finished job, and its result, is kept.
KEEP_FINISHED = timedelta(days=7)
#: How often every organization's due jobs are looked for, besides after a commit.
SWEEP_SECONDS = 60.0
#: The most jobs one worker runs at once.
MOST_RUNNING = 4
#: A failed job's error when its handler raised something other than a :class:`JobError`.
JOB_FAILED = "JOB_FAILED"


class JobState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


#: The jobs a ``unique_key`` stands for: those not finished.
_PENDING_KEY = "unique_key IS NOT NULL AND state IN ('queued', 'running')"


class Job(OrganizationRow, table=True):
    """A job: what it is and who started it, how far it got, and what it answered.

    ``step``, ``done`` and ``total`` are its progress: a code naming the step,
    which pages translate, and how much of it is done out of how much.
    ``attempts`` counts its tries, each claim one. ``file`` is its file result,
    named ``file_name``, of type ``file_type``."""

    __tablename__ = "jobs"
    __table_args__ = (
        Index("ix_jobs_due", "organization_id", "state", "next_attempt_at"),
        Index(
            "uq_jobs_pending_key",
            "organization_id",
            "kind",
            "unique_key",
            unique=True,
            postgresql_where=text(_PENDING_KEY),
        ),
        sa.CheckConstraint("attempts >= 0", name="ck_jobs_attempts"),
    )

    id: int = identity()
    kind: str
    #: The member's pairwise reference, or None for the app itself.
    started_by: str | None = None
    args: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    )
    unique_key: str | None = None
    state: JobState = Field(
        default=JobState.QUEUED,
        sa_column=enum_column(JobState, "job_state", server_default="queued"),
    )
    step: str | None = None
    done: int | None = None
    total: int | None = None
    checkpoint: Any = Field(default=None, sa_column=Column(JSONB(none_as_null=True)))
    attempts: int = 0
    next_attempt_at: datetime = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=text("now()")),
    )
    heartbeat_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    error: str | None = None
    result: Any = Field(default=None, sa_column=Column(JSONB(none_as_null=True)))
    file: bytes | None = Field(default=None, sa_column=Column(LargeBinary))
    file_type: str | None = None
    file_name: str | None = None
    created_at: datetime = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=text("now()")),
    )
    finished_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))


#: Loads a job without its file, which only its download reads.
_WITHOUT_FILE = defer(cast(Any, Job.file))


def create_table() -> None:
    """Make the ``jobs`` table, an organization's table, for :class:`Job`."""
    postgresql.ENUM(*(state.value for state in JobState), name="job_state").create(op.get_bind())
    moment = DateTime(timezone=True)
    op.create_table(
        "jobs",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("started_by", sa.Text(), nullable=True),
        sa.Column("args", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("unique_key", sa.Text(), nullable=True),
        sa.Column(
            "state",
            postgresql.ENUM(name="job_state", create_type=False),
            nullable=False,
            server_default="queued",
        ),
        sa.Column("step", sa.Text(), nullable=True),
        sa.Column("done", sa.Integer(), nullable=True),
        sa.Column("total", sa.Integer(), nullable=True),
        sa.Column("checkpoint", JSONB(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", moment, nullable=False, server_default=sa.func.now()),
        sa.Column("heartbeat_at", moment, nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("result", JSONB(), nullable=True),
        sa.Column("file", sa.LargeBinary(), nullable=True),
        sa.Column("file_type", sa.Text(), nullable=True),
        sa.Column("file_name", sa.Text(), nullable=True),
        sa.Column("created_at", moment, nullable=False, server_default=sa.func.now()),
        sa.Column("finished_at", moment, nullable=True),
        sa.CheckConstraint("attempts >= 0", name="ck_jobs_attempts"),
    )
    isolate("jobs")
    op.create_index("ix_jobs_due", "jobs", ["organization_id", "state", "next_attempt_at"])
    op.create_index(
        "uq_jobs_pending_key",
        "jobs",
        ["organization_id", "kind", "unique_key"],
        unique=True,
        postgresql_where=sa.text(_PENDING_KEY),
    )


def drop_table() -> None:
    """Undo :func:`create_table`."""
    op.drop_table("jobs")
    op.execute("DROP TYPE job_state")


# Queuing.

#: Where a session notes the organization it queued a job for.
_QUEUED = "seneschal.jobs.queued"


async def enqueue(
    db: OrganizationSession,
    kind: str,
    args: Mapping[str, Any],
    *,
    started_by: str | None,
    unique_key: str | None = None,
) -> Job:
    """Queue a job of ``kind`` with ``args`` in ``db``'s organization, started
    by ``started_by`` (a member's pairwise reference, or None for the app). With
    ``unique_key``, a job of ``kind`` with that key already queued or running
    is answered instead. The worker runs it once ``db`` commits."""
    organization_id = db.organization_id
    if organization_id is None:
        raise ValueError("a job is queued in a session opened for its organization")
    queue = insert(Job).values(
        organization_id=organization_id,
        kind=kind,
        args=dict(args),
        started_by=started_by,
        unique_key=unique_key,
    )
    if unique_key is not None:
        queue = queue.on_conflict_do_nothing(
            index_elements=["organization_id", "kind", "unique_key"],
            index_where=text(_PENDING_KEY),
        )
    while True:
        job_id = (await db.execute(queue.returning(col(Job.id)))).scalar_one_or_none()
        if job_id is None:
            # A pending job holds the key; one that finished meanwhile lets
            # the next try queue.
            job_id = (
                await db.exec(
                    select(Job.id).where(
                        Job.kind == kind,
                        Job.unique_key == unique_key,
                        col(Job.state).in_([JobState.QUEUED, JobState.RUNNING]),
                    )
                )
            ).first()
        if job_id is not None:
            break
    db.info[_QUEUED] = organization_id
    return await db.get_one(Job, job_id, options=[_WITHOUT_FILE])


# Working.


@dataclass(frozen=True)
class JobFile:
    """A file a job made, to be downloaded by the member who started it."""

    content: bytes
    content_type: str
    filename: str


@dataclass(frozen=True)
class JobResult:
    """What a handler answers: JSON kept as the job's ``result``, and a file."""

    data: Any = None
    file: JobFile | None = None


class JobError(Exception):
    """A handler's failure, with the code its job fails with. With ``retry``
    false, the job fails at once rather than being tried again."""

    def __init__(self, code: str, *, retry: bool = True) -> None:
        super().__init__(code)
        self.code = code
        self.retry = retry


class JobLost(Exception):
    """The job is no longer this worker's: its heartbeat went stale and another
    worker claimed it. Raised from :meth:`JobContext.progress` and
    :meth:`JobContext.save_checkpoint`, it stops the handler."""


def _now() -> datetime:
    return datetime.now(UTC)


async def _change(plugin: Plugin, job: Job, **values: Any) -> bool:
    """Change a job this worker holds, keeping its heartbeat; answers whether
    it still held it."""
    async with plugin.database.write_session(job.organization_id, actor=None) as db:
        changed = await db.execute(
            update(Job)
            .where(
                col(Job.id) == job.id,
                col(Job.state) == JobState.RUNNING,
                col(Job.attempts) == job.attempts,
            )
            .values({"heartbeat_at": _now(), **values})
            .returning(col(Job.id))
        )
        held = changed.first() is not None
        await db.commit()
    return held


@dataclass
class JobContext:
    """What a handler is given: the job, and ways to report on it."""

    plugin: Plugin
    organization_id: int
    job_id: int
    kind: str
    started_by: str | None
    args: dict[str, Any]
    #: What the handler last saved, or None on its first try.
    checkpoint: Any
    _job: Job = field(repr=False)

    async def progress(self, step: str, done: int | None = None, total: int | None = None) -> None:
        """Report the step the job is at, a code pages translate, and how much
        of it is done out of how much."""
        if not await _change(self.plugin, self._job, step=step, done=done, total=total):
            raise JobLost

    async def save_checkpoint(self, checkpoint: Any) -> None:
        """Save what the handler needs to resume from here, as JSON. A job
        claimed again gets it as :attr:`checkpoint`."""
        if not await _change(self.plugin, self._job, checkpoint=checkpoint):
            raise JobLost
        self.checkpoint = checkpoint


#: A job kind's handler.
Handler = Callable[[JobContext], Awaitable[JobResult | None]]


def _wait(attempts: int) -> timedelta:
    return FIRST_WAIT * 2 ** max(attempts - 1, 0)


async def claim(plugin: Plugin, organization_id: int, kinds: Collection[str]) -> Job | None:
    """Claim the organization's next due job of one of ``kinds``: queued and
    due, or running with a stale heartbeat. A lost job that has had its tries
    fails instead."""
    async with plugin.database.write_session(organization_id, actor=None) as db:
        while True:
            now = _now()
            job = (
                await db.exec(
                    select(Job)
                    .options(_WITHOUT_FILE)
                    .where(
                        col(Job.kind).in_(list(kinds)),
                        or_(
                            and_(
                                col(Job.state) == JobState.QUEUED,
                                col(Job.next_attempt_at) <= now,
                            ),
                            and_(
                                col(Job.state) == JobState.RUNNING,
                                col(Job.heartbeat_at) < now - STALE_AFTER,
                            ),
                        ),
                    )
                    .order_by(col(Job.next_attempt_at), col(Job.id))
                    .limit(1)
                    .with_for_update(skip_locked=True)
                )
            ).first()
            if job is None:
                return None
            if job.attempts >= MOST_ATTEMPTS:
                job.state, job.finished_at = JobState.FAILED, now
                job.error = job.error or JOB_FAILED
                await db.commit()
                continue
            job.state, job.heartbeat_at = JobState.RUNNING, now
            job.attempts += 1
            await db.commit()
            return job


@asynccontextmanager
async def _beating(plugin: Plugin, job: Job) -> AsyncIterator[None]:
    """Keep the job's heartbeat while the block runs."""

    async def beat() -> None:
        while True:
            await asyncio.sleep(HEARTBEAT_SECONDS)
            if not await _change(plugin, job):
                return

    beating = asyncio.create_task(beat())
    try:
        yield
    finally:
        beating.cancel()
        with suppress(asyncio.CancelledError):
            await beating


async def run(plugin: Plugin, job: Job, handler: Handler) -> None:
    """Run a claimed job with its kind's handler, and keep what came of it."""
    context = JobContext(
        plugin=plugin,
        organization_id=job.organization_id,
        job_id=job.id,
        kind=job.kind,
        started_by=job.started_by,
        args=dict(job.args),
        checkpoint=job.checkpoint,
        _job=job,
    )
    try:
        async with _beating(plugin, job):
            result = await handler(context) or JobResult()
    except JobLost:
        return
    except asyncio.CancelledError:
        # The worker is stopping: the job waits for the next one, its try
        # not counted.
        with suppress(Exception):
            await _change(
                plugin,
                job,
                state=JobState.QUEUED,
                attempts=job.attempts - 1,
                next_attempt_at=_now(),
                heartbeat_at=None,
            )
        raise
    except Exception as exc:
        code, retry = (exc.code, exc.retry) if isinstance(exc, JobError) else (JOB_FAILED, True)
        logger.warning("job %s (%s) failed: %s", job.id, job.kind, code, exc_info=True)
        if retry and job.attempts < MOST_ATTEMPTS:
            await _change(
                plugin,
                job,
                state=JobState.QUEUED,
                error=code,
                next_attempt_at=_now() + _wait(job.attempts),
            )
        else:
            await _change(plugin, job, state=JobState.FAILED, error=code, finished_at=_now())
        return
    file = result.file
    await _change(
        plugin,
        job,
        state=JobState.DONE,
        error=None,
        result=result.data,
        file=None if file is None else file.content,
        file_type=None if file is None else file.content_type,
        file_name=None if file is None else file.filename,
        finished_at=_now(),
    )


async def work(plugin: Plugin, organization_id: int, handlers: Mapping[str, Handler]) -> None:
    """Run the organization's due jobs of the kinds ``handlers`` names, one
    after another, until none is due."""
    while (job := await claim(plugin, organization_id, handlers)) is not None:
        await run(plugin, job, handlers[job.kind])


class _Waker:
    """The organizations whose commits queued jobs, and the signal that one did."""

    def __init__(self) -> None:
        self.due: set[int] = set()
        self.woken = asyncio.Event()

    def wake(self, organization_id: int) -> None:
        self.due.add(organization_id)
        self.woken.set()


#: The running :func:`work_forever`'s waker, if one runs.
_waker: _Waker | None = None


@event.listens_for(OrganizationSession.sync_session_class, "after_commit")
def _wake(session: Session) -> None:
    organization_id = session.info.pop(_QUEUED, None)
    if organization_id is not None and _waker is not None:
        _waker.wake(organization_id)


@event.listens_for(OrganizationSession.sync_session_class, "after_soft_rollback")
def _forget(session: Session, _transaction: Any) -> None:
    session.info.pop(_QUEUED, None)


async def work_forever(
    plugin: Plugin,
    handlers: Mapping[str, Handler],
    *,
    sweep_seconds: float = SWEEP_SECONDS,
    most_running: int = MOST_RUNNING,
) -> None:
    """Run every organization's due jobs, up to ``most_running`` at once,
    until cancelled: an organization's as soon as a commit queues one there,
    and every organization's each ``sweep_seconds``. ``handlers`` holds the
    handler of each kind, by kind."""
    global _waker
    waker = _waker = _Waker()
    running: set[asyncio.Task[None]] = set()

    def ran(organization_id: int, task: asyncio.Task[None]) -> None:
        running.discard(task)
        waker.wake(organization_id)

    swept: float | None = None
    try:
        while True:
            waker.woken.clear()
            due, waker.due = waker.due, set()
            if swept is None or time.monotonic() - swept >= sweep_seconds:
                swept = time.monotonic()
                try:
                    async with plugin.database.session() as db:
                        due |= await organizations.working(db)
                except Exception:
                    logger.exception("could not list the organizations to run jobs for")
            for organization_id in sorted(due):
                if len(running) >= most_running:
                    # Looked at again once a running job ends.
                    waker.due.add(organization_id)
                    continue
                try:
                    while len(running) < most_running:
                        job = await claim(plugin, organization_id, handlers)
                        if job is None:
                            break
                        task = asyncio.create_task(run(plugin, job, handlers[job.kind]))
                        running.add(task)
                        task.add_done_callback(functools.partial(ran, organization_id))
                except Exception:
                    logger.exception("could not claim organization %s's jobs", organization_id)
            with suppress(TimeoutError):
                async with asyncio.timeout(sweep_seconds):
                    await waker.woken.wait()
    finally:
        for task in running:
            task.cancel()
        await asyncio.gather(*running, return_exceptions=True)
        if _waker is waker:
            _waker = None


async def prune(db: WriteSession, now: datetime | None = None) -> None:
    """Remove the jobs, and their results, that finished more than
    :data:`KEEP_FINISHED` ago."""
    await db.execute(
        sa.delete(Job).where(
            col(Job.finished_at) < (now or _now()) - KEEP_FINISHED,
        )
    )


# Routes.

router = APIRouter(prefix="/jobs", tags=["jobs"])


class JobRead(SQLModel):
    """A job as pages read it: its progress, and what it answered. A job
    holding a file names it in ``file_name``."""

    id: int
    kind: str
    started_by: str | None
    args: dict[str, Any]
    state: JobState
    step: str | None
    done: int | None
    total: int | None
    attempts: int
    error: str | None
    result: Any
    file_name: str | None
    created_at: datetime
    finished_at: datetime | None


def _read(job: Job) -> JobRead:
    return JobRead.model_validate(job, from_attributes=True)


@router.get("", responses={401: {"description": "NOT_AUTHENTICATED"}})
async def list_jobs(db: OrganizationSessionDep, kind: str | None = None) -> list[JobRead]:
    """The organization's jobs, of ``kind`` if given, newest first."""
    query = select(Job).options(_WITHOUT_FILE).order_by(col(Job.id).desc())
    if kind is not None:
        query = query.where(Job.kind == kind)
    return [_read(job) for job in (await db.exec(query)).all()]


@router.get("/{job_id}", responses={404: {"description": "JOB_NOT_FOUND"}})
async def read_job(job_id: int, db: OrganizationSessionDep) -> JobRead:
    job = await db.get(Job, job_id, options=[_WITHOUT_FILE])
    if job is None:
        raise refuse(status.HTTP_404_NOT_FOUND, ErrorCode.JOB_NOT_FOUND)
    return _read(job)


@router.get(
    "/{job_id}/result",
    response_class=Response,
    responses={
        200: {"content": {"application/octet-stream": {}}},
        403: {"description": "JOB_STARTER_ONLY"},
        404: {"description": "JOB_NOT_FOUND, JOB_RESULT_NOT_FOUND"},
    },
)
async def read_job_result(job_id: int, db: OrganizationSessionDep, session: SessionDep) -> Response:
    """The job's file, for the member who started it."""
    job = await db.get(Job, job_id, options=[_WITHOUT_FILE])
    if job is None:
        raise refuse(status.HTTP_404_NOT_FOUND, ErrorCode.JOB_NOT_FOUND)
    if job.started_by != session.member_ref:
        raise refuse(status.HTTP_403_FORBIDDEN, ErrorCode.JOB_STARTER_ONLY)
    content = (await db.exec(select(Job.file).where(Job.id == job_id))).first()
    if job.state is not JobState.DONE or content is None:
        raise refuse(status.HTTP_404_NOT_FOUND, ErrorCode.JOB_RESULT_NOT_FOUND)
    return Response(
        content,
        media_type=job.file_type,
        headers={"Content-Disposition": f'attachment; filename="{job.file_name}"'},
    )
