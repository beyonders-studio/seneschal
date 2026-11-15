"""Jobs: long work run in the background for a person, such as an export
(``seneschal_core.jobs``).

Revision ID: 0010
Revises: 0009
"""

from collections.abc import Sequence

from seneschal_core import jobs

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    jobs.create_table()


def downgrade() -> None:
    jobs.drop_table()
