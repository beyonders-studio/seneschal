"""A ledger's mechanics: entries that keep running balances.

A ledger is a table of entries, each moving an amount, and the balances kept
from them: one balance row per key the entries name, such as an item at a
location. The database keeps each balance the sum of its entries' amounts,
through a trigger on the entries table, so that every insert, edit and delete
of an entry moves its balance in the same statement, a cascade's included, and
concurrent entries add up rather than overwrite each other.

The trigger runs as whoever writes the entry, so the organization policy binds
its writes as it binds theirs. What an entry means, and what else follows from
it, is the app's: these helpers keep only the sums.

Migrations call :func:`keep_balance` on tables :func:`~seneschal_core.migrations.isolate`
made, and :func:`drop_balance` to undo it.
"""

from __future__ import annotations

from collections.abc import Mapping

from alembic import op

from seneschal_core.migrations import quote

#: Where a ledger's trigger looks names up: the catalog, then the app's tables.
_SEARCH_PATH = "pg_catalog, public, pg_temp"


def _name(entries: str, balances: str) -> str:
    return quote(f"{entries}_keep_{balances}")


def keep_balance(
    entries: str,
    *,
    amount: str,
    balances: str,
    total: str,
    keys: Mapping[str, str],
    create: bool,
) -> None:
    """Keep ``balances.total`` the sum of ``entries.amount`` over the entries
    that name its row.

    ``keys`` maps each key column of ``balances`` to the column of ``entries``
    naming it, within the entry's organization. An entry with a null key moves
    no balance. With ``create``, an entry naming a key no balance has yet makes
    its row, which needs a unique key on ``organization_id`` and the key
    columns; without, the row must already exist, as a foreign key from the
    entry to it makes sure.
    """
    function = _name(entries, balances)
    table, sum_column, moved = quote(balances), quote(total), quote(amount)

    def present(row: str) -> str:
        return " AND ".join(f"{row}.{quote(column)} IS NOT NULL" for column in keys.values())

    def match(row: str) -> str:
        return " AND ".join(
            [
                f"organization_id = {row}.organization_id",
                *(f"{quote(key)} = {row}.{quote(column)}" for key, column in keys.items()),
            ]
        )

    if create:
        key_columns = ", ".join(["organization_id", *map(quote, keys)])
        values = ", ".join(
            [
                "NEW.organization_id",
                *(f"NEW.{quote(column)}" for column in keys.values()),
                f"NEW.{moved}",
            ]
        )
        add = (
            f"INSERT INTO {table} ({key_columns}, {sum_column}) VALUES ({values}) "
            f"ON CONFLICT ({key_columns}) "
            f"DO UPDATE SET {sum_column} = {table}.{sum_column} + EXCLUDED.{sum_column};"
        )
    else:
        add = f"UPDATE {table} SET {sum_column} = {sum_column} + NEW.{moved} WHERE {match('NEW')};"

    op.execute(
        f"""
        CREATE FUNCTION {function}() RETURNS trigger LANGUAGE plpgsql
        SET search_path = {_SEARCH_PATH} AS $$
        BEGIN
            IF TG_OP <> 'INSERT' AND {present("OLD")} THEN
                UPDATE {table} SET {sum_column} = {sum_column} - OLD.{moved} WHERE {match("OLD")};
            END IF;
            IF TG_OP <> 'DELETE' AND {present("NEW")} THEN
                {add}
            END IF;
            RETURN NULL;
        END
        $$
        """
    )
    watched = ", ".join([moved, *(quote(column) for column in keys.values())])
    op.execute(
        f"CREATE TRIGGER {function} AFTER INSERT OR DELETE OR UPDATE OF {watched} "
        f"ON {quote(entries)} FOR EACH ROW EXECUTE FUNCTION {function}()"
    )


def drop_balance(entries: str, balances: str) -> None:
    """Undo :func:`keep_balance`."""
    function = _name(entries, balances)
    op.execute(f"DROP TRIGGER IF EXISTS {function} ON {quote(entries)}")
    op.execute(f"DROP FUNCTION {function}()")
