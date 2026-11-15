"""Jobs are run once however many workers look, resume after a lost worker,
are tried again and then fail, are pruned a week after they finish, and
answer their file only to the member who started them."""

from __future__ import annotations

import asyncio
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import update
from sqlmodel import col, select

from seneschal_core import jobs, organizations
from seneschal_core.api import Plugin
from seneschal_core.jobs import Job, JobContext, JobError, JobFile, JobLost, JobResult, JobState


@pytest.fixture
async def organization_id(plugin: Plugin, community_ref: str) -> int:
    async with plugin.database.session() as db:
        organization_id = await organizations.organization_for(
            db, community_ref=community_ref, plugin_install_id=1
        )
        await db.commit()
    return organization_id


@pytest.fixture
def kind() -> str:
    """A kind of job no other test queues."""
    return f"kind-{secrets.token_hex(4)}"


async def _queue(
    plugin: Plugin,
    organization_id: int,
    kind: str,
    *,
    started_by: str | None = "member-ref",
    **args,
) -> Job:
    async with plugin.database.write_session(organization_id, actor=None) as db:
        job = await jobs.enqueue(db, kind, args, started_by=started_by)
        await db.commit()
    return job


async def _job(plugin: Plugin, organization_id: int, job_id: int) -> Job:
    async with plugin.database.organization_session(organization_id) as db:
        return await db.get_one(Job, job_id)


async def _set(plugin: Plugin, organization_id: int, job_id: int, **values: Any) -> None:
    async with plugin.database.write_session(organization_id, actor=None) as db:
        await db.execute(update(Job).where(col(Job.id) == job_id).values(**values))
        await db.commit()


async def test_two_workers_run_a_job_once(plugin: Plugin, organization_id: int, kind: str):
    ran: list[int] = []

    async def handler(job: JobContext) -> JobResult:
        ran.append(job.job_id)
        await asyncio.sleep(0.05)
        return JobResult(data={"rows": 3})

    queued = await _queue(plugin, organization_id, kind)
    handlers = {kind: handler}
    await asyncio.gather(
        jobs.work(plugin, organization_id, handlers), jobs.work(plugin, organization_id, handlers)
    )

    assert ran == [queued.id]
    done = await _job(plugin, organization_id, queued.id)
    assert (done.state, done.result, done.attempts) == (JobState.DONE, {"rows": 3}, 1)
    assert done.finished_at is not None


async def test_a_lost_job_is_claimed_again_and_resumes_from_its_checkpoint(
    plugin: Plugin, organization_id: int, kind: str
):
    saved, release = asyncio.Event(), asyncio.Event()
    seen: list[Any] = []
    lost: list[bool] = []

    async def handler(job: JobContext) -> JobResult:
        seen.append(job.checkpoint)
        if job.checkpoint is None:
            await job.progress("reading", 1, 3)
            await job.save_checkpoint({"page": 1})
            saved.set()
            await release.wait()
            try:
                await job.progress("reading", 2, 3)
            except JobLost:
                lost.append(True)
                raise
            return JobResult(data="first")
        await job.progress("reading", 3, 3)
        return JobResult(data={"resumed_from": job.checkpoint["page"]})

    handlers = {kind: handler}
    queued = await _queue(plugin, organization_id, kind)
    first = asyncio.create_task(jobs.work(plugin, organization_id, handlers))
    await asyncio.wait_for(saved.wait(), 5)

    # A job whose heartbeat is fresh is left to its worker.
    await jobs.work(plugin, organization_id, handlers)
    assert seen == [None]

    await _set(
        plugin,
        organization_id,
        queued.id,
        heartbeat_at=datetime.now(UTC) - jobs.STALE_AFTER - timedelta(seconds=1),
    )
    await jobs.work(plugin, organization_id, handlers)
    release.set()
    await first

    assert seen == [None, {"page": 1}]
    assert lost == [True]
    done = await _job(plugin, organization_id, queued.id)
    assert (done.state, done.result, done.attempts) == (JobState.DONE, {"resumed_from": 1}, 2)
    assert (done.step, done.done, done.total) == ("reading", 3, 3)


async def test_a_failing_job_is_tried_again_then_fails_with_its_code(
    plugin: Plugin, organization_id: int, kind: str
):
    tries: list[int] = []

    async def handler(job: JobContext) -> None:
        tries.append(job.job_id)
        raise JobError("STORE_REFUSED")

    queued = await _queue(plugin, organization_id, kind)
    for attempt in range(1, jobs.MOST_ATTEMPTS):
        await jobs.work(plugin, organization_id, {kind: handler})
        waiting = await _job(plugin, organization_id, queued.id)
        assert (waiting.state, waiting.attempts, waiting.error) == (
            JobState.QUEUED,
            attempt,
            "STORE_REFUSED",
        )
        assert waiting.next_attempt_at > datetime.now(UTC)
        # Nothing runs before its wait is over.
        await jobs.work(plugin, organization_id, {kind: handler})
        assert len(tries) == attempt
        await _set(plugin, organization_id, queued.id, next_attempt_at=datetime.now(UTC))
    await jobs.work(plugin, organization_id, {kind: handler})

    assert len(tries) == jobs.MOST_ATTEMPTS
    failed = await _job(plugin, organization_id, queued.id)
    assert (failed.state, failed.error) == (JobState.FAILED, "STORE_REFUSED")
    assert failed.finished_at is not None


async def test_a_job_that_need_not_be_tried_again_fails_at_once(
    plugin: Plugin, organization_id: int, kind: str
):
    async def refused(job: JobContext) -> None:
        raise JobError("STORE_REFUSED", retry=False)

    async def broken(job: JobContext) -> None:
        raise RuntimeError("broken")

    first = await _queue(plugin, organization_id, kind)
    await jobs.work(plugin, organization_id, {kind: refused})
    second = await _queue(plugin, organization_id, kind)
    await jobs.work(plugin, organization_id, {kind: broken})

    refused_job = await _job(plugin, organization_id, first.id)
    broken_job = await _job(plugin, organization_id, second.id)
    assert (refused_job.state, refused_job.error) == (JobState.FAILED, "STORE_REFUSED")
    assert (broken_job.state, broken_job.error) == (JobState.QUEUED, jobs.JOB_FAILED)


async def test_a_unique_key_answers_the_job_already_waiting(
    plugin: Plugin, organization_id: int, kind: str
):
    async with plugin.database.write_session(organization_id, actor=None) as db:
        first = await jobs.enqueue(db, kind, {"store": 1}, started_by=None, unique_key="store-1")
        again = await jobs.enqueue(db, kind, {"store": 1}, started_by=None, unique_key="store-1")
        other = await jobs.enqueue(db, kind, {"store": 2}, started_by=None, unique_key="store-2")
        await db.commit()
    assert again.id == first.id
    assert other.id != first.id

    await _set(plugin, organization_id, first.id, state=JobState.RUNNING)
    async with plugin.database.write_session(organization_id, actor=None) as db:
        running = await jobs.enqueue(db, kind, {}, started_by=None, unique_key="store-1")
        await db.commit()
    assert running.id == first.id

    await _set(plugin, organization_id, first.id, state=JobState.DONE)
    async with plugin.database.write_session(organization_id, actor=None) as db:
        after = await jobs.enqueue(db, kind, {}, started_by=None, unique_key="store-1")
        await db.commit()
    assert after.id != first.id


async def test_jobs_are_pruned_a_week_after_they_finish(
    plugin: Plugin, organization_id: int, kind: str
):
    now = datetime.now(UTC)
    old, recent, waiting = [await _queue(plugin, organization_id, kind) for _ in range(3)]
    for job, finished in (
        (old, jobs.KEEP_FINISHED + timedelta(hours=1)),
        (recent, timedelta(days=6)),
    ):
        await _set(plugin, organization_id, job.id, state=JobState.DONE, finished_at=now - finished)

    async with plugin.database.write_session(organization_id, actor=None) as db:
        await jobs.prune(db, now)
        await db.commit()
        left = await db.exec(select(Job.id).where(Job.kind == kind).order_by(col(Job.id)))
        assert list(left.all()) == [recent.id, waiting.id]


async def test_the_worker_runs_what_a_commit_queues_and_hands_back_what_it_stops(
    plugin: Plugin, organization_id: int, kind: str
):
    started, finished = asyncio.Event(), asyncio.Event()

    async def handler(job: JobContext) -> None:
        if job.args.get("wait"):
            started.set()
            await asyncio.Event().wait()
        finished.set()

    worker = asyncio.create_task(jobs.work_forever(plugin, {kind: handler}, sweep_seconds=3600))
    await asyncio.sleep(0.1)
    await _queue(plugin, organization_id, kind)
    await asyncio.wait_for(finished.wait(), 5)

    waiting = await _queue(plugin, organization_id, kind, wait=True)
    await asyncio.wait_for(started.wait(), 5)
    worker.cancel()
    with pytest.raises(asyncio.CancelledError):
        await worker

    stopped = await _job(plugin, organization_id, waiting.id)
    assert (stopped.state, stopped.attempts) == (JobState.QUEUED, 0)


async def _signed_in(client, initiative_stand_in, community_ref: str, member_ref: str) -> None:
    token = initiative_stand_in.handoff(community_ref=community_ref, member_ref=member_ref)
    exchanged = await client.post("/api/v1/auth/handoff/exchange", json={"handoff_token": token})
    assert exchanged.status_code == 200


async def test_a_jobs_file_downloads_only_for_the_member_who_started_it(
    client, initiative_stand_in, community_ref, plugin: Plugin, organization_id: int, kind: str
):
    async def handler(job: JobContext) -> JobResult:
        return JobResult(file=JobFile(b"id,name\r\n", "text/csv; charset=utf-8", "widgets.csv"))

    queued = await _queue(plugin, organization_id, kind, started_by="starter")
    await jobs.work(plugin, organization_id, {kind: handler})

    await _signed_in(client, initiative_stand_in, community_ref, "starter")
    read = await client.get(f"/api/v1/jobs/{queued.id}")
    assert (read.json()["state"], read.json()["file_name"]) == ("done", "widgets.csv")
    listed = await client.get("/api/v1/jobs", params={"kind": kind})
    assert [job["id"] for job in listed.json()] == [queued.id]
    downloaded = await client.get(f"/api/v1/jobs/{queued.id}/result")
    assert downloaded.status_code == 200
    assert downloaded.content == b"id,name\r\n"
    assert downloaded.headers["content-type"] == "text/csv; charset=utf-8"
    assert downloaded.headers["content-disposition"] == 'attachment; filename="widgets.csv"'

    await _signed_in(client, initiative_stand_in, community_ref, "another")
    assert (await client.get(f"/api/v1/jobs/{queued.id}")).status_code == 200
    refused = await client.get(f"/api/v1/jobs/{queued.id}/result")
    assert (refused.status_code, refused.json()["detail"]) == (403, "JOB_STARTER_ONLY")
    missing = await client.get("/api/v1/jobs/0")
    assert (missing.status_code, missing.json()["detail"]) == (404, "JOB_NOT_FOUND")


async def test_a_job_reaches_only_its_own_organization(
    client, initiative_stand_in, community_ref, plugin: Plugin, kind: str
):
    async with plugin.database.session() as db:
        elsewhere = await organizations.organization_for(
            db, community_ref=f"community-{secrets.token_hex(6)}", plugin_install_id=1
        )
        await db.commit()
    queued = await _queue(plugin, elsewhere, kind)

    await _signed_in(client, initiative_stand_in, community_ref, "member-ref")
    answer = await client.get(f"/api/v1/jobs/{queued.id}")
    assert (answer.status_code, answer.json()["detail"]) == (404, "JOB_NOT_FOUND")
