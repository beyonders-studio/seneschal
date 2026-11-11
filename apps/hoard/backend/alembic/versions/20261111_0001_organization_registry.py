"""The organization registry and the record of spent page handoffs.

Revision ID: 0001
Revises:
"""

from collections.abc import Sequence

from seneschal_core.migrations import create_registry, drop_registry

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    create_registry()


def downgrade() -> None:
    drop_registry()
