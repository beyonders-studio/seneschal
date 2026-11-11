"""Stocktakes through the API: started, confirmed row by row, finished with
their accuracy."""

from __future__ import annotations

from decimal import Decimal

from hoard.records import Category, CategoryKind
from hoard.stock import on_hand
from seneschal_core.db import WriteSession


async def test_a_stocktake_is_confirmed_row_by_row_to_its_accuracy(
    client, sign_in, db: WriteSession, material, buy, main
):
    flour = await material("Flour")
    await material("Sugar")
    await buy((flour, "10", "2"))
    await db.commit()
    await sign_in(client)

    started = await client.post(
        "/api/v1/stocktakes", json={"kind": "material", "dated_on": "2026-11-05"}
    )
    stocktake = started.json()
    assert started.status_code == 201, started.text
    assert (stocktake["row_count"], stocktake["accuracy"], stocktake["finished"]) == (
        2,
        None,
        False,
    )
    rows = {row["item"]["name"]: row for row in stocktake["rows"]}
    assert (rows["Flour"]["on_hand"], rows["Flour"]["expected"]) == ("10.000000", None)

    def confirm(row: dict, counted: str):
        return client.post(
            f"/api/v1/stocktakes/{stocktake['id']}/rows/{row['id']}/confirm",
            json={"counted": counted},
        )

    short = (await confirm(rows["Flour"], "8")).json()
    assert (short["confirmed_count"], short["accuracy"], short["finished"]) == (1, "0.0000", False)
    assert await on_hand(db, flour.id, main.id) == Decimal(8)

    finished = (await confirm(rows["Sugar"], "0")).json()
    assert (finished["accuracy"], finished["finished"]) == ("0.5000", True)

    recounted = (await confirm(rows["Flour"], "10")).json()
    assert recounted["accuracy"] == "1.0000"
    assert await on_hand(db, flour.id, main.id) == Decimal(10)

    listed = (await client.get("/api/v1/stocktakes")).json()
    assert [(item["id"], item["accuracy"], item["finished"]) for item in listed["items"]] == [
        (stocktake["id"], "1.0000", True)
    ]


async def test_a_stocktake_counts_by_a_category_of_its_own_kind(
    client, sign_in, db: WriteSession, material
):
    dry = Category(organization_id=db.organization_id, kind=CategoryKind.MATERIAL, name="Dry")
    sold = Category(organization_id=db.organization_id, kind=CategoryKind.PRODUCT, name="Cakes")
    db.add_all([dry, sold])
    await db.flush()
    await material("Flour", category_id=dry.id)
    await material("Milk")
    await db.commit()
    await sign_in(client)

    counted = await client.post(
        "/api/v1/stocktakes", json={"kind": "material", "category_id": dry.id}
    )
    assert [row["item"]["name"] for row in counted.json()["rows"]] == ["Flour"]

    refused = await client.post(
        "/api/v1/stocktakes", json={"kind": "material", "category_id": sold.id}
    )
    assert (refused.status_code, refused.json()) == (
        422,
        {"detail": "STOCKTAKE_CATEGORY_KIND"},
    )
