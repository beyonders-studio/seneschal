"""A lot's trace and recalls through the API, with their files."""

from __future__ import annotations

import csv
import io

import httpx2

from hoard.recall import FDA_COLUMNS
from hoard.trace_test import chai, ship  # noqa: F401  (fixtures)
from seneschal_core.db import WriteSession
from seneschal_core.testing import InitiativeStandIn


def _customer(stand_in: InitiativeStandIn) -> None:
    stand_in.routes[("GET", "/contacts/9")] = lambda _r: httpx2.Response(
        200, json={"id": 9, "name": "Corner Café", "phones": [{"label": "", "value": "555"}]}
    )


async def test_a_lot_reads_its_trace_back_and_forward_with_its_parties(
    client,
    sign_in,
    db: WriteSession,
    initiative_stand_in: InitiativeStandIn,
    chai,  # noqa: F811
    ship,  # noqa: F811
):
    made = await chai(assume_spice=True)
    shipment = await ship(made.chai_item, "3", 5, lot=made.chai)
    await db.commit()
    _customer(initiative_stand_in)
    await sign_in(client)

    read = (await client.get(f"/api/v1/trace/lots/{made.spice.id}")).json()

    assert [
        (step["kind"], step["code"], step["lot"]["number"], step["assumed"])
        for step in read["back"]
    ] == [
        ("run", made.spice_run.code, made.cinnamon.number, False),
        ("purchase", made.purchase.code, made.cinnamon.number, False),
    ]
    assert read["back"][1]["party"] == {"contact_id": 7, "name": None}
    [run, shipped] = read["forward"]
    assert (run["made_lot"]["number"], run["item"]["name"], run["assumed"]) == (
        made.chai.number,
        "Chai spice",
        True,
    )
    assert (shipped["record_id"], shipped["party"], shipped["level"]) == (
        shipment.id,
        {"contact_id": 9, "name": "Corner Café"},
        2,
    )
    assert shipped["order_id"] == shipment.order_id
    assert (await client.get("/api/v1/trace/lots/999999")).status_code == 404


async def test_a_recall_reads_from_a_lot_or_an_items_lots_in_a_range(
    client,
    sign_in,
    db: WriteSession,
    initiative_stand_in: InitiativeStandIn,
    chai,  # noqa: F811
    ship,  # noqa: F811
):
    made = await chai()
    await ship(made.chai_item, "3", 5, lot=made.chai)
    await db.commit()
    initiative_stand_in.routes[("GET", "/contacts/9")] = lambda _r: httpx2.Response(503)
    await sign_in(client)

    by_lot = (await client.get(f"/api/v1/trace/recall?lot_id={made.cinnamon.id}")).json()
    by_item = await client.get(
        f"/api/v1/trace/recall?item_id={made.chai_item}&since=2026-11-04&until=2026-11-04"
    )
    none = await client.get(
        f"/api/v1/trace/recall?item_id={made.chai_item}&since=2026-11-05&until=2026-11-30"
    )

    [traced] = by_lot["traces"]
    assert (traced["lot"]["number"], traced["item"]["name"]) == (made.cinnamon.number, "Cinnamon")
    assert [step["kind"] for step in traced["steps"]] == ["run", "run", "shipment"]
    # Initiative did not answer: the customer is named by contact alone.
    assert traced["steps"][-1]["party"] == {"contact_id": 9, "name": None}
    assert [t["lot"]["id"] for t in by_item.json()["traces"]] == [made.chai.id]
    assert none.json() == {"traces": []}
    for refused in ("", f"?item_id={made.chai_item}&since=2026-11-05&until=2026-11-01"):
        answer = await client.get(f"/api/v1/trace/recall{refused}")
        assert (answer.status_code, answer.json()) == (422, {"detail": "REQUEST_INVALID"})


async def test_a_recall_downloads_as_each_file(
    client,
    sign_in,
    db: WriteSession,
    initiative_stand_in: InitiativeStandIn,
    chai,  # noqa: F811
    ship,  # noqa: F811
):
    made = await chai()
    await ship(made.chai_item, "3", 5, lot=made.chai)
    await db.commit()
    _customer(initiative_stand_in)
    await sign_in(client)
    query = f"lot_id={made.cinnamon.id}"

    fda = await client.get(f"/api/v1/trace/recall/export?format=fda&{query}")
    canada = await client.get(f"/api/v1/trace/recall/export?format=canada&{query}")
    pdf = await client.get(f"/api/v1/trace/recall/export?format=pdf&{query}")

    assert fda.headers["content-type"] == "text/csv; charset=utf-8"
    assert fda.headers["content-disposition"] == 'attachment; filename="recall-fda.csv"'
    [header, row] = list(csv.reader(io.StringIO(fda.text)))
    assert header == list(FDA_COLUMNS)
    assert (row[0], row[9], row[10]) == (made.chai.number, "Corner Café", "555")
    assert canada.headers["content-type"] == "text/plain; charset=utf-8"
    assert "Corner Café" in canada.text
    assert (pdf.headers["content-type"], pdf.content[:4]) == ("application/pdf", b"%PDF")


async def test_a_recall_file_waits_for_initiative_to_name_its_parties(
    client,
    sign_in,
    db: WriteSession,
    initiative_stand_in: InitiativeStandIn,
    chai,  # noqa: F811
    ship,  # noqa: F811
):
    made = await chai()
    await ship(made.chai_item, "3", 5, lot=made.chai)
    await db.commit()
    initiative_stand_in.routes[("GET", "/contacts/9")] = lambda _r: httpx2.Response(503)
    await sign_in(client)

    answer = await client.get(f"/api/v1/trace/recall/export?format=fda&lot_id={made.chai.id}")

    assert (answer.status_code, answer.json()) == (503, {"detail": "INITIATIVE_UNAVAILABLE"})
