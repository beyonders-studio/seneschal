"""Sign-ins made for an MCP client, beside a browser's and a phone app's
(``seneschal_core.oauth``).

Revision ID: 0031
Revises: 0030
"""

from collections.abc import Sequence

from alembic import op

from seneschal_core import sign_in

revision: str = "0031"
down_revision: str | None = "0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    sign_in.allow_clients()


def downgrade() -> None:
    op.execute("DELETE FROM sign_ins WHERE client = 'mcp'")
    op.drop_constraint("ck_sign_ins_client", "sign_ins", type_="check")
    op.create_check_constraint("ck_sign_ins_client", "sign_ins", "client IN ('browser', 'device')")
