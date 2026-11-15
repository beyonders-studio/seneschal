"""The install's own state, as Initiative's plugin_status hook states it.

Revision ID: 0014
Revises: 0013
"""

from collections.abc import Sequence

from seneschal_core.migrations import add_plugin_status, drop_plugin_status

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    add_plugin_status()


def downgrade() -> None:
    drop_plugin_status()
