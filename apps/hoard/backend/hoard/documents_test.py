"""Initiative files linked by id to lots, products, materials and bases."""

from __future__ import annotations

import pytest

from hoard import documents
from seneschal_core.db import WriteSession


async def test_files_linked_to_a_record_list_together_in_order(
    db: WriteSession, material, base, lot
):
    flour = await material("Flour")
    batter = await base("Batter")
    sack = await lot(flour.id, "F-1", "3")

    first = await documents.link(db, 31, lot_id=sack.id)
    await documents.link(db, 12, lot_id=sack.id)
    again = await documents.link(db, 31, lot_id=sack.id)
    certificate = await documents.link(db, 31, material_id=flour.id)
    plan = await documents.link(db, 40, base_id=batter.id)

    assert again.id == first.id
    assert [each.file_id for each in await documents.links(db, lot_id=sack.id)] == [31, 12]
    assert [each.id for each in await documents.links(db, material_id=flour.id)] == [certificate.id]
    assert (plan.base_id, plan.created_by) == (batter.id, "member-ref")
    assert await documents.links(db, product_id=999999) == []


async def test_a_link_names_one_record(db: WriteSession):
    with pytest.raises(ValueError):
        await documents.link(db, 31)
    with pytest.raises(ValueError):
        await documents.link(db, 31, lot_id=1, base_id=2)
    with pytest.raises(ValueError):
        await documents.links(db, order_id=1)
