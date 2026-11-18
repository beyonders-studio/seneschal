"""Scanning lots onto shipments: how each category's shipment lines choose
their lots (``categories.lot_scan``), and how each line's was chosen
(``shipment_lines.lot_source``).

Revision ID: 0030
Revises: 0029
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0030"
down_revision: str | None = "0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    lot_scan = postgresql.ENUM("by_hand", "warn", "required", name="lot_scan")
    lot_source = postgresql.ENUM("scanned", "chosen", "admin", name="lot_source")
    lot_scan.create(op.get_bind())
    lot_source.create(op.get_bind())
    op.add_column(
        "categories",
        sa.Column(
            "lot_scan",
            postgresql.ENUM(name="lot_scan", create_type=False),
            nullable=False,
            server_default="by_hand",
        ),
    )
    op.add_column(
        "shipment_lines",
        sa.Column(
            "lot_source", postgresql.ENUM(name="lot_source", create_type=False), nullable=True
        ),
    )


def downgrade() -> None:
    op.drop_column("shipment_lines", "lot_source")
    op.drop_column("categories", "lot_scan")
    postgresql.ENUM(name="lot_source").drop(op.get_bind())
    postgresql.ENUM(name="lot_scan").drop(op.get_bind())
