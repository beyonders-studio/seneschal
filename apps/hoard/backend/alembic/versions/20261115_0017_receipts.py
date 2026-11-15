"""Purchase lines received on their own day, and received in part; when a
purchase is expected, and its notes.

A line is received once it records the day it arrived: every line received
before this revision arrived on its purchase's date, so its stock stays dated
where it was. A line received in part names the line it was split from.

Revision ID: 0017
Revises: 0016
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from seneschal_core import search

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: The tables this revision reads or writes rows of, with their row security
#: lifted while it does.
WRITTEN = ("purchases", "purchase_lines")


def _lift(statement: str) -> None:
    """Run ``statement`` with the row security of :data:`WRITTEN` lifted."""
    for table in WRITTEN:
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
    try:
        op.execute(statement)
    finally:
        for table in WRITTEN:
            op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")


def upgrade() -> None:
    op.add_column("purchases", sa.Column("expected_on", sa.Date(), nullable=True))
    op.add_column("purchases", sa.Column("notes", sa.Text(), nullable=True))
    op.add_column("purchase_lines", sa.Column("received_on", sa.Date(), nullable=True))
    op.add_column("purchase_lines", sa.Column("split_from_id", sa.BigInteger(), nullable=True))
    _lift(
        "UPDATE purchase_lines pl SET received_on = p.dated_on "
        "FROM purchases p WHERE p.id = pl.purchase_id AND pl.received"
    )
    op.drop_column("purchase_lines", "received")
    op.execute(
        "ALTER TABLE purchase_lines ADD CONSTRAINT fk_purchase_lines_split_from_id "
        "FOREIGN KEY (organization_id, split_from_id) "
        "REFERENCES purchase_lines (organization_id, id) ON DELETE SET NULL (split_from_id)"
    )


def downgrade() -> None:
    op.drop_constraint("fk_purchase_lines_split_from_id", "purchase_lines")
    op.add_column(
        "purchase_lines",
        sa.Column("received", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    _lift("UPDATE purchase_lines SET received = received_on IS NOT NULL")
    op.drop_column("purchase_lines", "split_from_id")
    op.drop_column("purchase_lines", "received_on")
    search.drop_source("purchase")
    op.drop_column("purchases", "notes")
    op.drop_column("purchases", "expected_on")
