"""A production run's own notes, and Initiative files linked to a run.

Revision ID: 0024
Revises: 0023
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from seneschal_core.migrations import reference

revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("production_runs", sa.Column("notes", sa.Text(), nullable=True))

    op.add_column("document_links", sa.Column("run_id", sa.BigInteger(), nullable=True))
    reference("document_links", "run_id", "production_runs", ondelete="CASCADE")
    op.create_unique_constraint(
        "uq_document_links_run", "document_links", ["organization_id", "run_id", "file_id"]
    )
    op.drop_constraint("ck_document_links_record", "document_links")
    op.create_check_constraint(
        "ck_document_links_record",
        "document_links",
        "num_nonnulls(lot_id, product_id, material_id, base_id, run_id) = 1",
    )


def downgrade() -> None:
    # A run's links go with the column that names it.
    op.execute("ALTER TABLE document_links NO FORCE ROW LEVEL SECURITY")
    try:
        op.execute("DELETE FROM document_links WHERE run_id IS NOT NULL")
    finally:
        op.execute("ALTER TABLE document_links FORCE ROW LEVEL SECURITY")
    op.drop_constraint("ck_document_links_record", "document_links")
    op.create_check_constraint(
        "ck_document_links_record",
        "document_links",
        "num_nonnulls(lot_id, product_id, material_id, base_id) = 1",
    )
    op.drop_constraint("uq_document_links_run", "document_links")
    op.drop_constraint("fk_document_links_run_id", "document_links")
    op.drop_column("document_links", "run_id")

    op.drop_column("production_runs", "notes")
