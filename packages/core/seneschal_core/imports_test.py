"""Imports are for the community's admins: a check reads the files and keeps
nothing, an import with problems is refused, and one without is a job that
carries out its plan step by step, resuming after the last step it finished."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi import FastAPI
from pydantic import BaseModel
from sqlalchemy import text, update
from sqlmodel import col

from seneschal_core import imports, jobs, organizations
from seneschal_core.api import Plugin, install
from seneschal_core.auth import router as auth_router
from seneschal_core.imports import Checked, ImportCheck, ImportCount, ImportProblem, ImportStep
from seneschal_core.jobs import Job, JobState


class WidgetOptions(BaseModel):
    shout: bool = False


async def _names(db) -> set[str]:
    return set((await db.execute(text("SELECT name FROM widgets"))).scalars().all())


async def _check(check: ImportCheck) -> Checked:
    """Each line of ``widgets`` a widget, and of ``more`` too; a blank line is a problem."""
    rows = [
        (name, row, line.strip())
        for name, content in check.files.items()
        for row, line in enumerate(content.decode().splitlines(), start=1)
    ]
    held = await _names(check.db)
    plan = [{"name": line.upper() if check.options.shout else line} for _, _, line in rows if line]
    return Checked(
        plan=plan,
        counts=[
            ImportCount(
                kind="widgets",
                new=sum(step["name"] not in held for step in plan),
                existing=sum(step["name"] in held for step in plan),
            )
        ],
        problems=[
            ImportProblem(code="WIDGET_UNNAMED", file=name, row=row)
            for name, row, line in rows
            if not line
        ],
    )


#: The steps each run was handed, and the step that fails once.
handed: list[str] = []
fail_once: set[str] = set()


async def _step(run: ImportStep, step: dict[str, Any]) -> Mapping[str, int]:
    """A widget, matched by name before it is made."""
    handed.append(step["name"])
    if step["name"] in fail_once:
        fail_once.discard(step["name"])
        raise RuntimeError("lost")
    if step["name"] in await _names(run.db):
        return {}
    await run.db.execute(
        text("INSERT INTO widgets (organization_id, name) VALUES (:org, :name)"),
        {"org": run.db.organization_id, "name": step["name"]},
    )
    return {"widgets": 1}


WIDGETS = imports.Importer(
    id="widgets",
    files=(imports.ImportFile("widgets"), imports.ImportFile("more", required=False)),
    options=WidgetOptions,
    check=_check,
    step=_step,
)
HANDLERS = {imports.IMPORT_JOB: imports.handler([WIDGETS])}


@pytest.fixture
def app(plugin: Plugin) -> FastAPI:
    app = FastAPI()
    install(app, plugin)
    app.include_router(auth_router, prefix="/api/v1")
    app.include_router(jobs.router, prefix="/api/v1")
    app.include_router(imports.router([WIDGETS]), prefix="/api/v1")
    return app


@pytest.fixture
async def organization_id(plugin: Plugin, community_ref: str) -> int:
    async with plugin.database.session() as db:
        organization_id = await organizations.organization_for(
            db, community_ref=community_ref, plugin_install_id=1
        )
        await db.commit()
    return organization_id


@pytest.fixture
def sign_in(client, initiative_stand_in, community_ref):
    async def sign_in(admin: bool = True) -> None:
        token = initiative_stand_in.handoff(
            community_ref=community_ref, member_ref="admin-ref", admin=admin
        )
        exchanged = await client.post(
            "/api/v1/auth/handoff/exchange", json={"handoff_token": token}
        )
        assert exchanged.status_code == 200

    return sign_in


def _upload(options: dict | None = None, **files: str) -> dict[str, Any]:
    return {
        "files": {name: (f"{name}.txt", content.encode()) for name, content in files.items()},
        "data": {} if options is None else {"options": json.dumps(options)},
    }


async def _widgets(plugin: Plugin, organization_id: int) -> set[str]:
    async with plugin.database.organization_session(organization_id) as db:
        return await _names(db)


async def test_only_admins_import(client, sign_in, organization_id):
    await sign_in(admin=False)
    for answer in (
        await client.get("/api/v1/imports"),
        await client.post("/api/v1/imports/widgets/check", **_upload(widgets="bolt")),
        await client.post("/api/v1/imports/widgets", **_upload(widgets="bolt")),
    ):
        assert (answer.status_code, answer.json()["detail"]) == (403, "IMPORT_ADMIN_ONLY")

    await sign_in()
    listed = await client.get("/api/v1/imports")
    assert listed.json() == [
        {
            "id": "widgets",
            "files": [
                {"name": "widgets", "required": True},
                {"name": "more", "required": False},
            ],
        }
    ]


async def test_a_check_answers_counts_and_problems_and_keeps_nothing(
    client, sign_in, plugin: Plugin, organization_id
):
    await sign_in()
    checked = await client.post(
        "/api/v1/imports/widgets/check",
        **_upload({"shout": True}, widgets="bolt\n\nnut", more="washer"),
    )

    assert checked.status_code == 200, checked.text
    assert checked.json() == {
        "counts": [{"kind": "widgets", "new": 3, "existing": 0}],
        "problems": [{"code": "WIDGET_UNNAMED", "file": "widgets", "row": 2, "name": None}],
    }
    assert await _widgets(plugin, organization_id) == set()
    refused = await client.post("/api/v1/imports/widgets", **_upload(widgets="bolt\n\nnut"))
    assert (refused.status_code, refused.json()["detail"]) == (409, "IMPORT_PROBLEMS")
    async with plugin.database.organization_session(organization_id) as db:
        assert (await db.execute(text("SELECT count(*) FROM jobs"))).scalar_one() == 0


async def test_an_upload_the_importer_cannot_read_is_refused(
    client, sign_in, organization_id, monkeypatch
):
    await sign_in()
    for path, upload, refusal in (
        ("/imports/gadgets/check", _upload(widgets="bolt"), (404, "IMPORTER_UNKNOWN")),
        ("/imports/widgets/check", _upload(more="bolt"), (422, "IMPORT_FILE_MISSING")),
        ("/imports/widgets/check", _upload(widgets=""), (422, "IMPORT_FILE_MISSING")),
        (
            "/imports/widgets/check",
            _upload({"shout": "loudly"}, widgets="bolt"),
            (422, "IMPORT_OPTIONS_INVALID"),
        ),
        ("/imports/widgets", _upload(widgets="bolt" * 10), (413, "IMPORT_FILE_TOO_LARGE")),
    ):
        monkeypatch.setattr(imports, "MAX_FILE_BYTES", 20)
        answer = await client.post(f"/api/v1{path}", **upload)
        assert (answer.status_code, answer.json()["detail"]) == refusal, path


async def test_an_import_is_a_job_that_resumes_after_its_last_step(
    client, sign_in, plugin: Plugin, organization_id
):
    async with plugin.database.write_session(organization_id, actor=None) as db:
        await db.execute(
            text("INSERT INTO widgets (organization_id, name) VALUES (:org, 'nut')"),
            {"org": organization_id},
        )
        await db.commit()
    await sign_in()
    handed.clear()
    fail_once.add("washer")

    queued = await client.post(
        "/api/v1/imports/widgets", **_upload(widgets="bolt\nnut", more="washer")
    )
    assert queued.status_code == 202, queued.text
    job = queued.json()
    assert (job["kind"], job["state"], job["started_by"]) == ("import", "queued", "admin-ref")
    assert job["args"] == {
        "importer": "widgets",
        "plan": [{"name": "bolt"}, {"name": "nut"}, {"name": "washer"}],
    }

    await jobs.work(plugin, organization_id, HANDLERS)
    waiting = (await client.get(f"/api/v1/jobs/{job['id']}")).json()
    assert (waiting["state"], waiting["done"], waiting["total"]) == ("queued", 2, 3)
    async with plugin.database.write_session(organization_id, actor=None) as db:
        await db.execute(
            update(Job).where(col(Job.id) == job["id"]).values(next_attempt_at=datetime.now(UTC))
        )
        await db.commit()
    await jobs.work(plugin, organization_id, HANDLERS)

    done = (await client.get(f"/api/v1/jobs/{job['id']}")).json()
    assert (done["state"], done["result"]) == ("done", {"made": {"widgets": 2}})
    assert handed == ["bolt", "nut", "washer", "washer"]
    assert await _widgets(plugin, organization_id) == {"bolt", "nut", "washer"}


async def test_an_import_waits_while_the_community_is_read_only(plugin: Plugin, organization_id):
    async with plugin.database.session() as db:
        await organizations.set_status(db, organization_id, organizations.CommunityStatus.READ_ONLY)
        await db.commit()
    async with plugin.database.write_session(organization_id, actor=None) as db:
        queued = await jobs.enqueue(
            db, imports.IMPORT_JOB, {"importer": "widgets", "plan": []}, started_by=None
        )
        await db.commit()

    await jobs.work(plugin, organization_id, HANDLERS)

    async with plugin.database.organization_session(organization_id) as db:
        failed = await db.get_one(Job, queued.id)
    assert (failed.state, failed.error) == (JobState.FAILED, "READ_ONLY")
