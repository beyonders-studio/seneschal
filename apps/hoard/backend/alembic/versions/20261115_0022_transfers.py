"""Transfers, each a pair of adjustments moving stock from one location to
another, and consignees' locations.

Every location starts as the organization's own.

Revision ID: 0022
Revises: 0020
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from seneschal_core.migrations import isolate, referable, reference, stamp

revision: str = "0022"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE adjustment_kind ADD VALUE IF NOT EXISTS 'transfer'")

    op.create_table(
        "transfers", sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True)
    )
    isolate("transfers")
    stamp("transfers")
    referable("transfers")

    op.add_column("adjustments", sa.Column("transfer_id", sa.BigInteger(), nullable=True))
    reference("adjustments", "transfer_id", "transfers", ondelete="CASCADE")
    op.create_unique_constraint(
        "uq_adjustments_transfer_location", "adjustments", ["transfer_id", "location_id"]
    )
    op.create_check_constraint(
        "ck_adjustments_transfer", "adjustments", "(kind = 'transfer') = (transfer_id IS NOT NULL)"
    )

    op.add_column(
        "locations",
        sa.Column("consignee", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_check_constraint(
        "ck_locations_consignee",
        "locations",
        "NOT consignee OR (contact_id IS NOT NULL AND NOT main)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_locations_consignee", "locations")
    op.drop_column("locations", "consignee")

    # Postgres keeps a type's values once added: the transfers' adjustments go
    # with the transfers, so that no row holds the new one.
    op.execute("ALTER TABLE adjustments NO FORCE ROW LEVEL SECURITY")
    try:
        op.execute("DELETE FROM adjustments WHERE transfer_id IS NOT NULL")
    finally:
        op.execute("ALTER TABLE adjustments FORCE ROW LEVEL SECURITY")
    op.drop_constraint("ck_adjustments_transfer", "adjustments")
    op.drop_constraint("uq_adjustments_transfer_location", "adjustments")
    op.drop_constraint("fk_adjustments_transfer_id", "adjustments")
    op.drop_column("adjustments", "transfer_id")
    op.drop_table("transfers")
