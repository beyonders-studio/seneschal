"""What an app's database must look like for organizations to stay apart,
read from ``pg_catalog`` rather than from a list of tables.

Every table but the organization registry and Alembic's version table must
carry ``organization_id`` (not null, referencing the registry and deleted with
its organization), have row security enabled and forced, and carry exactly one
policy: the organization policy, for every command and every role. The serving
login may only read the registry and holds nothing on the version table, and
every security-definer function pins its search path.

:func:`isolation_problems` answers what breaks those rules; each app's tests
assert it answers nothing for the app's migrated database.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from seneschal_core.db import ORGANIZATION_SETTING, POLICY_NAME, REGISTRY_TABLE

#: Alembic's record of the revision a database is at.
VERSION_TABLE = "alembic_version"

#: The organization policy's condition as Postgres prints it back.
_PRINTED_CONDITION = (
    "(organization_id = (NULLIF(current_setting("
    f"'{ORGANIZATION_SETTING}'::text, true), ''::text))::bigint)"
)

_USER_SCHEMAS = """
    n.nspname NOT IN ('pg_catalog', 'information_schema')
    AND n.nspname NOT LIKE 'pg\\_toast%'
    AND n.nspname NOT LIKE 'pg\\_temp\\_%'
"""


async def isolation_problems(connection: AsyncConnection, *, serving_login: str) -> list[str]:
    """Every way the database at ``connection`` breaks the isolation rules,
    one sentence each; empty when it keeps them all."""
    problems: list[str] = []
    tables = await connection.execute(
        text(
            f"""
            SELECT c.oid, n.nspname, c.relname, c.relrowsecurity, c.relforcerowsecurity
              FROM pg_class c
              JOIN pg_namespace n ON n.oid = c.relnamespace
             WHERE c.relkind IN ('r', 'p') AND {_USER_SCHEMAS}
             ORDER BY n.nspname, c.relname
            """
        )
    )
    for oid, schema, table, row_security, forced in tables.all():
        name = f"{schema}.{table}"
        if schema == "public" and table == REGISTRY_TABLE:
            problems += await _registry_problems(connection, serving_login)
            continue
        if schema == "public" and table == VERSION_TABLE:
            problems += await _version_table_problems(connection, serving_login)
            continue
        if not await _has_organization_column(connection, oid):
            problems.append(f"{name} has no not-null bigint organization_id")
        if not await _references_registry(connection, oid):
            problems.append(
                f"{name}.organization_id does not reference {REGISTRY_TABLE}(id) on delete cascade"
            )
        if not row_security:
            problems.append(f"{name} does not enable row security")
        if not forced:
            problems.append(f"{name} does not force row security")
        problems += await _policy_problems(connection, oid, name)
    problems += await _definer_problems(connection)
    return problems


async def _has_organization_column(connection: AsyncConnection, oid: int) -> bool:
    found = await connection.execute(
        text(
            """
            SELECT 1 FROM pg_attribute
             WHERE attrelid = :oid AND attname = 'organization_id' AND NOT attisdropped
               AND attnotnull AND atttypid = 'bigint'::regtype
            """
        ),
        {"oid": oid},
    )
    return found.first() is not None


async def _references_registry(connection: AsyncConnection, oid: int) -> bool:
    found = await connection.execute(
        text(
            """
            SELECT 1
              FROM pg_constraint k
              JOIN pg_attribute a ON a.attrelid = k.conrelid AND a.attnum = k.conkey[1]
              JOIN pg_attribute r ON r.attrelid = k.confrelid AND r.attnum = k.confkey[1]
             WHERE k.conrelid = :oid AND k.contype = 'f' AND k.confdeltype = 'c'
               AND cardinality(k.conkey) = 1 AND a.attname = 'organization_id'
               AND k.confrelid = to_regclass(:registry) AND r.attname = 'id'
            """
        ),
        {"oid": oid, "registry": f"public.{REGISTRY_TABLE}"},
    )
    return found.first() is not None


async def _policy_problems(connection: AsyncConnection, oid: int, name: str) -> list[str]:
    policies = (
        await connection.execute(
            text(
                """
                SELECT polname, polcmd::text AS polcmd, polpermissive, polroles = '{0}'::oid[] AS everyone,
                       pg_get_expr(polqual, polrelid) AS using_condition,
                       pg_get_expr(polwithcheck, polrelid) AS check_condition
                  FROM pg_policy WHERE polrelid = :oid
                """
            ),
            {"oid": oid},
        )
    ).all()
    if [policy.polname for policy in policies] != [POLICY_NAME]:
        names = ", ".join(sorted(policy.polname for policy in policies)) or "none"
        return [f"{name} must carry the one policy {POLICY_NAME}, and carries: {names}"]
    policy = policies[0]
    if policy.polcmd != "*" or not policy.polpermissive or not policy.everyone:
        return [f"{name}.{POLICY_NAME} must be permissive, for every command and every role"]
    if policy.using_condition != _PRINTED_CONDITION or policy.check_condition != _PRINTED_CONDITION:
        return [f"{name}.{POLICY_NAME} must hold rows to the transaction's organization"]
    return []


async def _privileges(connection: AsyncConnection, table: str, login: str) -> set[str]:
    privileges = ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER")
    held = set()
    for privilege in privileges:
        allowed = await connection.execute(
            text("SELECT has_table_privilege(:login, :table, :privilege)"),
            {"login": login, "table": table, "privilege": privilege},
        )
        if allowed.scalar_one():
            held.add(privilege)
    return held


async def _registry_problems(connection: AsyncConnection, serving_login: str) -> list[str]:
    held = await _privileges(connection, f"public.{REGISTRY_TABLE}", serving_login)
    if held != {"SELECT"}:
        return [f"the serving login must only read {REGISTRY_TABLE}, and holds {sorted(held)}"]
    return []


async def _version_table_problems(connection: AsyncConnection, serving_login: str) -> list[str]:
    held = await _privileges(connection, f"public.{VERSION_TABLE}", serving_login)
    if held:
        return [f"the serving login must hold nothing on {VERSION_TABLE}, and holds {sorted(held)}"]
    return []


async def _definer_problems(connection: AsyncConnection) -> list[str]:
    unpinned = await connection.execute(
        text(
            f"""
            SELECT n.nspname || '.' || p.proname
              FROM pg_proc p
              JOIN pg_namespace n ON n.oid = p.pronamespace
             WHERE p.prosecdef AND {_USER_SCHEMAS}
               AND NOT EXISTS (
                   SELECT 1 FROM unnest(coalesce(p.proconfig, '{{}}')) setting
                    WHERE setting LIKE 'search\\_path=%'
               )
            """
        )
    )
    return [
        f"security-definer function {name} must pin its search_path" for name in unpinned.scalars()
    ]
