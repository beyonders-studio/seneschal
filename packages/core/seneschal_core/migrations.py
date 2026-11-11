"""What an app's migrations call to make the registry, to isolate a table and
to reference one organization row from another.

Migrations run as the owner login. The serving login's name reaches them from
the migration environment as the context option ``serving_login`` (each app's
``alembic/env.py`` reads it from the serving URL), so grants name the login the
app actually serves with.

A revision that has run is never changed, and neither is what these helpers
make once a release carries them: a later change is a new helper, called from
a new revision.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from seneschal_core.db import POLICY_CONDITION, POLICY_NAME, REGISTRY_TABLE

#: The privileges the serving login holds on an organization's table.
SERVING_PRIVILEGES = "SELECT, INSERT, UPDATE, DELETE"

#: Where every security-definer function looks names up: the catalog, then the
#: app's tables, then nothing temporary ahead of either.
_DEFINER_SEARCH_PATH = "pg_catalog, public, pg_temp"


def serving_login() -> str:
    """The serving login, as the migration environment names it."""
    return op.get_context().opts["serving_login"]


def quote(name: str) -> str:
    """``name`` as an identifier in SQL."""
    return op.get_bind().dialect.identifier_preparer.quote(name)


def isolate(table: str) -> None:
    """Make ``table`` an organization's table: its ``organization_id``, referencing
    the registry and deleted with its organization; row security enabled and
    forced; the one organization policy; and the serving login's privileges.

    Call it on a table just made, before any row is in it. The model declares
    ``organization_id`` too, so that queries can name it.
    """
    op.add_column(
        table,
        sa.Column(
            "organization_id",
            sa.BigInteger(),
            sa.ForeignKey(
                f"{REGISTRY_TABLE}.id", ondelete="CASCADE", name=f"fk_{table}_organization_id"
            ),
            nullable=False,
        ),
    )
    op.create_index(f"ix_{table}_organization_id", table, ["organization_id"])
    quoted = quote(table)
    op.execute(f"ALTER TABLE {quoted} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {quoted} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {POLICY_NAME} ON {quoted} "
        f"USING ({POLICY_CONDITION}) WITH CHECK ({POLICY_CONDITION})"
    )
    op.execute(f"GRANT {SERVING_PRIVILEGES} ON {quoted} TO {quote(serving_login())}")


def referable(table: str) -> None:
    """Let other organization tables reference ``table``'s rows through
    :func:`reference`: a unique key on its organization and ``id``. Call it
    after :func:`isolate`."""
    op.create_unique_constraint(f"uq_{table}_organization_id_id", table, ["organization_id", "id"])


def reference(table: str, column: str, parent: str, *, ondelete: str | None = None) -> None:
    """``table.column`` references a row of ``parent`` (made :func:`referable`)
    in the same organization: the foreign key holds ``organization_id`` beside
    ``column``. A null ``column`` references nothing."""
    op.create_foreign_key(
        f"fk_{table}_{column}",
        table,
        parent,
        ["organization_id", column],
        ["organization_id", "id"],
        ondelete=ondelete,
    )


def create_registry() -> None:
    """The organization registry, the four functions the serving login writes
    it through, and the record of spent page handoffs.

    The registry has one row per community install: the community's reference,
    the install, where the community stands, and what billing's newest
    statement says. The serving login only reads it; it adds and changes rows
    through these functions, each owned by the owner login and doing one thing:

    * ``organization_for(community_ref, plugin_install_id)``: the organization
      for a community, made on first sight, recording its newest install;
    * ``set_organization_status(organization_id, community_status)``;
    * ``apply_organization_statement(organization_id, version, plan, active,
      paid_through)``: keeps the highest version, and answers whether it was;
    * ``erase_organization(community_ref)``: deletes the organization, and with
      it every row that names it.
    """
    op.create_table(
        REGISTRY_TABLE,
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("community_ref", sa.Text(), nullable=False, unique=True),
        sa.Column("plugin_install_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "community_status", sa.Text(), nullable=False, server_default=sa.text("'active'")
        ),
        sa.Column("statement_version", sa.Integer(), nullable=True),
        sa.Column("plan", sa.Text(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=True),
        sa.Column("paid_through", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "community_status IN ('active', 'read_only', 'suspended', 'on_hold', 'deleted')",
            name="ck_organizations_community_status",
        ),
        sa.CheckConstraint("statement_version >= 1", name="ck_organizations_statement_version"),
    )
    serving = quote(serving_login())
    op.execute(f"GRANT SELECT ON {REGISTRY_TABLE} TO {serving}")

    definer = f"SECURITY DEFINER SET search_path = {_DEFINER_SEARCH_PATH}"
    functions = {
        "organization_for(text, bigint)": f"""
            CREATE FUNCTION organization_for(p_community_ref text, p_plugin_install_id bigint)
            RETURNS bigint LANGUAGE plpgsql {definer} AS $$
            DECLARE
                found bigint;
            BEGIN
                INSERT INTO public.{REGISTRY_TABLE} (community_ref, plugin_install_id)
                VALUES (p_community_ref, p_plugin_install_id)
                ON CONFLICT (community_ref) DO NOTHING
                RETURNING id INTO found;
                IF found IS NULL THEN
                    UPDATE public.{REGISTRY_TABLE}
                       SET plugin_install_id = p_plugin_install_id
                     WHERE community_ref = p_community_ref
                       AND plugin_install_id <> p_plugin_install_id;
                    SELECT id INTO found FROM public.{REGISTRY_TABLE}
                     WHERE community_ref = p_community_ref;
                END IF;
                RETURN found;
            END
            $$
        """,
        "set_organization_status(bigint, text)": f"""
            CREATE FUNCTION set_organization_status(p_organization_id bigint, p_community_status text)
            RETURNS void LANGUAGE sql {definer} AS $$
                UPDATE public.{REGISTRY_TABLE}
                   SET community_status = p_community_status
                 WHERE id = p_organization_id AND community_status <> p_community_status
            $$
        """,
        "apply_organization_statement(bigint, integer, text, boolean, timestamptz)": f"""
            CREATE FUNCTION apply_organization_statement(
                p_organization_id bigint, p_version integer, p_plan text, p_active boolean,
                p_paid_through timestamptz
            )
            RETURNS boolean LANGUAGE sql {definer} AS $$
                WITH applied AS (
                    UPDATE public.{REGISTRY_TABLE}
                       SET statement_version = p_version, plan = p_plan, active = p_active,
                           paid_through = p_paid_through
                     WHERE id = p_organization_id
                       AND (statement_version IS NULL OR statement_version < p_version)
                    RETURNING 1
                )
                SELECT EXISTS (SELECT 1 FROM applied)
            $$
        """,
        "erase_organization(text)": f"""
            CREATE FUNCTION erase_organization(p_community_ref text)
            RETURNS boolean LANGUAGE sql {definer} AS $$
                WITH erased AS (
                    DELETE FROM public.{REGISTRY_TABLE} WHERE community_ref = p_community_ref
                    RETURNING 1
                )
                SELECT EXISTS (SELECT 1 FROM erased)
            $$
        """,
    }
    for signature, definition in functions.items():
        op.execute(definition)
        op.execute(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO {serving}")

    op.create_table(
        "handoff_redemptions",
        sa.Column("jti", sa.Text(), primary_key=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    isolate("handoff_redemptions")


def drop_registry() -> None:
    """Undo :func:`create_registry`."""
    op.drop_table("handoff_redemptions")
    for signature in (
        "erase_organization(text)",
        "apply_organization_statement(bigint, integer, text, boolean, timestamptz)",
        "set_organization_status(bigint, text)",
        "organization_for(text, bigint)",
    ):
        op.execute(f"DROP FUNCTION {signature}")
    op.drop_table(REGISTRY_TABLE)
