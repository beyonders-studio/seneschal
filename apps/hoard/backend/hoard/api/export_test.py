"""Each kind of record exports as CSV or JSON, with what things cost only for
those who see costs."""

from __future__ import annotations

import csv
import io

from hoard import plugin_manifest
from hoard.api.export import KINDS, ExportKind
from seneschal_core.db import WriteSession


def _header(text: str) -> list[str]:
    return next(csv.reader(io.StringIO(text)))


async def test_cost_columns_export_only_to_a_member_who_sees_costs(client, sign_in):
    await sign_in(client)
    hidden = {kind: _header((await client.get(f"/api/v1/export/{kind}")).text) for kind in KINDS}
    await sign_in(client, (plugin_manifest.SEE_COSTS,))
    seen = {kind: _header((await client.get(f"/api/v1/export/{kind}")).text) for kind in KINDS}

    for kind, (_, costs) in KINDS.items():
        assert set(seen[kind]) - set(hidden[kind]) == costs, kind
        assert "organization_id" not in seen[kind], kind
    assert {"code", "dated_on", "paid"} <= set(hidden[ExportKind.PURCHASES])


async def test_records_export_as_csv_and_json(client, sign_in, db: WriteSession, material, buy):
    flour = await material("=Flour", sku="F-1")
    purchase = await buy((flour, "4", "2.5"))
    await db.commit()
    await sign_in(client)

    materials = await client.get("/api/v1/export/materials")
    lines = await client.get("/api/v1/export/purchase_lines", params={"format": "json"})

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
