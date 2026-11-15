"""Search: the index of every record people look for, and what it reads
with (``seneschal_core.search``). Its triggers are rendered from
``hoard.search_sources`` and applied once Hoard has migrated (``alembic/env.py``).

Revision ID: 0015
Revises: 0014
"""

from collections.abc import Sequence

from seneschal_core import search

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    search.create_table()


def downgrade() -> None:
    search.drop_table()
