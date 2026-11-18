"""Each kind of record exports, through a job whose file only its starter
downloads, with what things cost only for those who see costs: the
catalogue's kinds as spreadsheets, the others as CSV or JSON."""

from __future__ import annotations

import csv
import io

from hoard import plugin_manifest
from hoard.api.export import CATALOGUE, KINDS, ExportKind
from seneschal_core.db import WriteSession


def _header(text: str) -> list[str]:
    return next(csv.reader(io.StringIO(text)))


async def test_cost_columns_export_only_to_a_member_who_sees_costs(
    client, sign_in, export, suppliers
):
    await sign_in(client)
    hidden = {kind: _header((await export(kind)).text) for kind in ExportKind}
    await sign_in(client, (plugin_manifest.SEE_COSTS,))
    seen = {kind: _header((await export(kind)).text) for kind in ExportKind}

    costs = {kind: costs for kind, (_, costs) in KINDS.items()} | {
        ExportKind.PRODUCTS: {"default_price", "price"}
    }
    for kind in ExportKind:
        assert set(seen[kind]) - set(hidden[kind]) == costs.get(kind, set()), kind
        assert "organization_id" not in seen[kind], kind
    assert {"code", "dated_on", "paid"} <= set(hidden[ExportKind.PURCHASES])


async def test_records_export_as_csv_and_json(
    client, sign_in, export, suppliers, db: WriteSession, material, buy
):
    flour = await material("=Flour", sku="F-1")
    purchase = await buy((flour, "4", "2.5"))
    await db.commit()
    await sign_in(client)

    materials = await export("materials")
    lines = await export("purchase_lines", format="json")

    assert materials.headers["content-disposition"] == 'attachment; filename="materials.csv"'
    assert materials.headers["content-type"] == "text/csv; charset=utf-8"
    # The catalogue reads as a spreadsheet: references by name, the default type empty.
    assert [
        (row["id"], row["name"], row["sku"], row["tracking_unit"], row["category"], row["type"])
        for row in csv.DictReader(io.StringIO(materials.text))
    ] == [(str(flour.id), "'=Flour", "F-1", "g", "", "")]
    assert lines.headers["content-disposition"] == 'attachment; filename="purchase_lines.json"'
    [line] = lines.json()
    assert (line["purchase_id"], line["item_id"], line["tracking_quantity"]) == (
        purchase.id,
        flour.id,
        "4.000000",
    )
    assert "unit_cost" not in line
    for kind in CATALOGUE:
        refused = await client.post(f"/api/v1/export/{kind}", params={"format": "json"})
        assert (refused.status_code, refused.json()["detail"]) == (422, "CSV_ONLY"), kind


async def test_an_export_downloads_only_for_the_member_who_asked(
    client, sign_in, export, suppliers
):
    await sign_in(client, (plugin_manifest.SEE_COSTS,))
    exported = await export("products")
    assert exported.status_code == 200
    job_id = exported.request.url.path.split("/")[-2]

    await sign_in(client, member_ref="another-member")
    refused = await client.get(f"/api/v1/jobs/{job_id}/result")
    assert (refused.status_code, refused.json()["detail"]) == (403, "JOB_STARTER_ONLY")
