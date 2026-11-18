"""Sign-ins at Hoard's own address: each renewal of a sign-in, and the chain it
makes (``seneschal_core.sign_in``).

Revision ID: 0028
Revises: 0027
"""

from collections.abc import Sequence

from seneschal_core import sign_in

revision: str = "0028"
down_revision: str | None = "0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    sign_in.create_table()


def downgrade() -> None:
    sign_in.drop_table()
