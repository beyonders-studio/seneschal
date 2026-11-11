"""A ledger's balances stay the sums of its entries, as the serving login
writes them."""

from __future__ import annotations

import secrets
from decimal import Decimal

from sqlalchemy import text

from seneschal_core import organizations
from seneschal_core.db import Database


async def _organization(database: Database) -> int:
    async with database.session() as db:
        organization_id = await organizations.organization_for(
            db, community_ref=f"community-{secrets.token_hex(6)}", plugin_install_id=1
        )
        await db.commit()
    return organization_id


async def _move(db, widget_id: int, amount: str, bin: str | None) -> int:
    return (
        await db.execute(
            text(
                "INSERT INTO widget_moves (organization_id, widget_id, bin, amount) "
                "VALUES (:org, :widget, :bin, :amount) RETURNING id"
            ),
            {"org": db.organization_id, "widget": widget_id, "bin": bin, "amount": amount},
        )
    ).scalar_one()


async def _balances(db, widget_id: int) -> tuple[Decimal, dict[str, Decimal]]:
    stock = (
        await db.execute(text("SELECT stock FROM widgets WHERE id = :id"), {"id": widget_id})
    ).scalar_one()
    bins = await db.execute(
        text("SELECT bin, quantity FROM widget_bins WHERE widget_id = :id"), {"id": widget_id}
    )
    return stock, {bin: quantity for bin, quantity in bins.all()}


async def test_entries_keep_their_balances_through_every_change(serving_database: Database):
    organization_id = await _organization(serving_database)
    async with serving_database.write_session(organization_id, actor=None) as db:
        widget_id = (
            await db.execute(
                text(
                    "INSERT INTO widgets (organization_id, name) VALUES (:org, 'bolt') RETURNING id"
                ),
                {"org": organization_id},
            )
        ).scalar_one()
        first = await _move(db, widget_id, "5", "a")
        second = await _move(db, widget_id, "3", "a")
        await _move(db, widget_id, "2.5", None)
        assert await _balances(db, widget_id) == (Decimal("10.5"), {"a": Decimal("8")})

        await db.execute(
            text("UPDATE widget_moves SET amount = 4, bin = 'b' WHERE id = :id"), {"id": first}
        )
        assert await _balances(db, widget_id) == (
            Decimal("9.5"),
            {"a": Decimal("3"), "b": Decimal("4")},
        )

        await db.execute(text("DELETE FROM widget_moves WHERE id = :id"), {"id": second})
        assert await _balances(db, widget_id) == (
            Decimal("6.5"),
            {"a": Decimal("0"), "b": Decimal("4")},
        )

        await db.execute(text("DELETE FROM widgets WHERE id = :id"), {"id": widget_id})
        remaining = await db.execute(text("SELECT count(*) FROM widget_moves"))
        assert remaining.scalar_one() == 0
        await db.commit()
