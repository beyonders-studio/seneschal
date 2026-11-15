"""Each kind of record exports as CSV or JSON, through a job whose file only
its starter downloads, with what things cost only for those who see costs."""

from __future__ import annotations

import csv
import io
from collections.abc import Awaitable, Callable

import pytest
from fastapi import FastAPI
from httpx2 import AsyncClient, Response

from hoard import plugin_manifest
from hoard.api.export import KINDS, ExportKind
from hoard.app import JOB_HANDLERS
from seneschal_core import jobs
from seneschal_core.db import WriteSession


def _header(text: str) -> list[str]:
    return next(csv.reader(io.StringIO(text)))


Export = Callable[..., Awaitable[Response]]


@pytest.fixture
def export(client: AsyncClient, app: FastAPI, organization_id: int) -> Export:
    """Ask for an export of ``kind``, run the job it queued, and download its file."""

    async def export(kind: str, **params: str) -> Response:
        queued = await client.post(f"/api/v1/export/{kind}", params=params)
        assert queued.status_code == 202, queued.text
        job = queued.json()
        assert (job["kind"], job["state"]) == ("export", "queued")
        await jobs.work(app.state.plugin, organization_id, JOB_HANDLERS)
        done = (await client.get(f"/api/v1/jobs/{job['id']}")).json()
        assert done["state"] == "done", done
        return await client.get(f"/api/v1/jobs/{job['id']}/result")

    return export


async def test_cost_columns_export_only_to_a_member_who_sees_costs(client, sign_in, export):
    await sign_in(client)
    hidden = {kind: _header((await export(kind)).text) for kind in KINDS}
    await sign_in(client, (plugin_manifest.SEE_COSTS,))
    seen = {kind: _header((await export(kind)).text) for kind in KINDS}

    for kind, (_, costs) in KINDS.items():
        assert set(seen[kind]) - set(hidden[kind]) == costs, kind
        assert "organization_id" not in seen[kind], kind
    assert {"code", "dated_on", "paid"} <= set(hidden[ExportKind.PURCHASES])


async def test_records_export_as_csv_and_json(
    client, sign_in, export, db: WriteSession, material, buy
):
    flour = await material("=Flour", sku="F-1")
    purchase = await buy((flour, "4", "2.5"))
    await db.commit()
    await sign_in(client)

    materials = await export("materials")
    lines = await export("purchase_lines", format="json")

    assert materials.headers["content-disposition"] == 'attachment; filename="materials.csv"'
    assert materials.headers["content-type"] == "text/csv; charset=utf-8"
    assert [
        (row["id"], row["name"], row["sku"], row["inventoriable"], row["category_id"])
        for row in csv.DictReader(io.StringIO(materials.text))
    ] == [(str(flour.id), "'=Flour", "F-1", "true", "")]
    assert lines.headers["content-disposition"] == 'attachment; filename="purchase_lines.json"'
    [line] = lines.json()
    assert (line["purchase_id"], line["material_id"], line["tracking_quantity"]) == (
        purchase.id,
        flour.id,
        "4.000000",
    )
    assert "unit_cost" not in line


async def test_an_export_downloads_only_for_the_member_who_asked(client, sign_in, export):
    await sign_in(client, (plugin_manifest.SEE_COSTS,))
    exported = await export("products")
    assert exported.status_code == 200
    job_id = exported.request.url.path.split("/")[-2]

    await sign_in(client, member_ref="another-member")
    refused = await client.get(f"/api/v1/jobs/{job_id}/result")
    assert (refused.status_code, refused.json()["detail"]) == (403, "JOB_STARTER_ONLY")
