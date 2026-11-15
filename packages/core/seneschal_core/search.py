"""Search across an app's records: one index, kept current by the database,
read by words and by codes.

* **Sources** (:class:`SearchSource`): one per searchable table, in the app's
  registry of them. A source names the record's title, its codes (a lot
  number, a SKU), its body, an optional subtitle, the page that opens it, and
  what marks it archived, each as SQL over the record's row ``r``. Where its
  text holds another table's rows, such as a product's variations' SKUs, a
  :class:`SearchDependency` names the records a change there feeds.
* **The index** (:class:`SearchEntry`): one entry per record, isolated per
  organization like every other table, made by :func:`create_table` from a
  migration. It keeps the title, subtitle, codes, body and page, the codes
  folded (:func:`fold`'s rule: lowercase, accents removed, letters and digits
  only), and the words weighted: title above codes above body, in the
  ``simple`` configuration with accents removed, so any language is read
  alike.
* **Applying** (:func:`apply_sources`): the triggers that keep the index are
  rendered from the registry rather than written into migrations, and applied
  by the owner login once the app has migrated. Each source's rendering is
  digested and the digest kept with its functions; a source whose digest
  changed has its triggers replaced and its records indexed again, in every
  organization, and a source no longer in the registry is removed with its
  entries.
* **Reading** (:func:`search`, :func:`suggest`): a query's folded codes are
  matched exactly and then by prefix, and its words through
  ``websearch_to_tsquery`` (quotes, ``-`` to exclude, ``or``) with the last
  word typed matched as a prefix. An exact code ranks above a prefix, and a
  prefix above words, which rank by ``ts_rank_cd`` with a small lift for
  what changed lately. Archived records are left out unless asked for. When
  nothing matches, titles close to the query are offered apart, as close
  matches.
* **Routes** (:data:`router`, mounted under ``/api/v1``): ``GET /search`` and
  ``GET /search/suggest`` for the organization's members.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Any

import sqlalchemy as sa
from alembic import op
from fastapi import APIRouter, Query
from sqlalchemy import Column, DateTime, Text, case, cast, func, literal, or_, text
from sqlalchemy.dialects.postgresql import ARRAY, TSQUERY, TSVECTOR
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Field, SQLModel, col
from sqlmodel.ext.asyncio.session import AsyncSession

from seneschal_core.api import OrganizationSessionDep
from seneschal_core.db import ORGANIZATION_SETTING, REGISTRY_TABLE
from seneschal_core.migrations import isolate
from seneschal_core.models import OrganizationRow

#: The configuration words are read in: ``simple``, with accents removed.
WORDS = "simple_unaccent"
#: How a source names its record's row, and a dependency the row that changed.
ROW, CHANGED = "r", "d"
#: Where the index's functions look names up.
_SEARCH_PATH = "pg_catalog, public, pg_temp"
#: What every function :func:`apply_sources` makes says of itself, before its
#: source's kind and digest.
_MARK = "seneschal search"

#: Results on a page, and suggestions for the field.
PAGE_SIZE = 20
SUGGEST_LIMIT = 10
#: The longest query read.
MAX_QUERY = 200
#: The last word typed is matched as a prefix once it holds this many characters.
MIN_PREFIX = 3
#: A folded code is matched as a prefix once it holds this many characters.
MIN_CODE_PREFIX = 2
#: What ``websearch_to_tsquery`` reads as syntax rather than as a word.
_OPERATORS = frozenset({"or", "and"})
#: How much a record changed today is lifted over a word match as good, and
#: over how many days the lift halves.
RECENT_LIFT = 0.2
RECENT_DAYS = 30
#: How close a title must be to the query to be offered as a close match, how
#: many are offered, and how long they are looked for.
CLOSE_THRESHOLD = 0.4
CLOSE_LIMIT = 5
CLOSE_TIMEOUT_MS = 250
#: Where a snippet marks a matched word: characters of Unicode's private use
#: area, which no record's text is expected to hold.
_MARK_START, _MARK_STOP = "\ue000", "\ue001"
_HEADLINE = f"StartSel={_MARK_START},StopSel={_MARK_STOP},MaxFragments=2,MinWords=5,MaxWords=18"


# Sources.


@dataclass(frozen=True)
class SearchDependency:
    """Another table whose rows feed a source's text. ``records`` is a query
    answering the ids of the source's records that the changed row ``d`` feeds,
    such as ``SELECT d.product_id``. ``watch`` names the columns whose update
    matters; empty, every update does. An insert or delete always does."""

    table: str
    records: str
    watch: tuple[str, ...] = ()


@dataclass(frozen=True)
class SearchSource:
    """How one table's rows become search entries. Every expression is SQL
    over the record's row ``r``, which has an ``id``.

    A row whose ``title`` is null has no entry. ``codes`` answers ``text[]``;
    ``body`` is joined with spaces, nulls skipped; ``path`` is the page that
    opens the record. ``watch`` names the table's columns whose update
    matters; empty, every update does. Costs and prices are never a source's
    text: whoever finds a record may not be allowed to see them."""

    kind: str
    table: str
    title: str
    path: str
    codes: str | None = None
    body: tuple[str, ...] = ()
    subtitle: str | None = None
    archived: str | None = None
    watch: tuple[str, ...] = ()
    dependencies: tuple[SearchDependency, ...] = ()

    def __post_init__(self) -> None:
        if not (
            self.kind.replace("_", "").isalnum() and self.kind.isascii() and self.kind.islower()
        ):
            raise ValueError(
                f"a search kind is lowercase letters, digits and underscores: {self.kind!r}"
            )
        tables = [dependency.table for dependency in self.dependencies]
        if len(set(tables)) != len(tables) or self.table in tables:
            raise ValueError(f"{self.kind} names one dependency per table, other than its own")


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _refresh(source: SearchSource) -> str:
    return f"search_refresh_{source.kind}"


def _trigger(source: SearchSource, table: str | None = None) -> str:
    return f"search_index_{source.kind}" + (f"_from_{table}" if table else "")


def _columns(watch: tuple[str, ...]) -> str:
    return " OF " + ", ".join(map(_quote, watch)) if watch else ""


def render(source: SearchSource) -> list[str]:
    """The statements that make ``source``'s functions and triggers.

    One function writes the entries of the records it is given, from their
    rows as they now stand: an entry for each that has a title, none for the
    rest. Each trigger, on the source's table and on each dependency's, hands
    it the records a changed row feeds."""
    refresh = _refresh(source)
    table = _quote(source.table)
    body = f"concat_ws(' ', {', '.join(source.body)})" if source.body else "NULL::text"
    statements = [
        f"""
        CREATE FUNCTION {refresh}(ids bigint[]) RETURNS void LANGUAGE sql
        SET search_path = {_SEARCH_PATH} AS $search$
            DELETE FROM search_entries e
             WHERE e.kind = '{source.kind}' AND e.record_id = ANY(ids)
               AND NOT EXISTS (
                   SELECT 1 FROM {table} {ROW}
                    WHERE {ROW}.id = e.record_id AND ({source.title}) IS NOT NULL
               );
            INSERT INTO search_entries AS e (
                organization_id, kind, record_id, title, subtitle, path, codes,
                folded_codes, body, archived, words
            )
            SELECT {ROW}.organization_id, '{source.kind}', {ROW}.id, t.title, t.subtitle,
                   t.path, c.codes, c.folded, t.body, t.archived,
                   setweight(to_tsvector('{WORDS}', t.title), 'A')
                   || setweight(to_tsvector('{WORDS}', array_to_string(c.codes, ' ')), 'B')
                   || setweight(to_tsvector('{WORDS}', coalesce(t.body, '')), 'C')
              FROM {table} {ROW}
             CROSS JOIN LATERAL (
                   SELECT ({source.title})::text AS title,
                          ({source.subtitle or "NULL"})::text AS subtitle,
                          ({source.path})::text AS path,
                          ({source.codes or "ARRAY[]::text[]"})::text[] AS codes,
                          nullif({body}, '') AS body,
                          coalesce(({source.archived or "false"})::boolean, false) AS archived
             ) t
             CROSS JOIN LATERAL (
                   SELECT ARRAY(SELECT code FROM unnest(t.codes) code WHERE code <> '') AS codes,
                          ARRAY(
                              SELECT DISTINCT folded FROM unnest(t.codes) code,
                                     search_fold(code) folded
                               WHERE folded <> ''
                          ) AS folded
             ) c
             WHERE {ROW}.id = ANY(ids) AND t.title IS NOT NULL
            ON CONFLICT (organization_id, kind, record_id) DO UPDATE
               SET title = EXCLUDED.title, subtitle = EXCLUDED.subtitle, path = EXCLUDED.path,
                   codes = EXCLUDED.codes, folded_codes = EXCLUDED.folded_codes,
                   body = EXCLUDED.body, archived = EXCLUDED.archived, words = EXCLUDED.words,
                   updated_at = now()
             WHERE (e.title, e.subtitle, e.path, e.codes, e.body, e.archived)
                   IS DISTINCT FROM (EXCLUDED.title, EXCLUDED.subtitle, EXCLUDED.path,
                                     EXCLUDED.codes, EXCLUDED.body, EXCLUDED.archived)
        $search$
        """,
        f"""
        CREATE FUNCTION {_trigger(source)}() RETURNS trigger LANGUAGE plpgsql
        SET search_path = {_SEARCH_PATH} AS $search$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                PERFORM {refresh}(ARRAY[OLD.id]);
            ELSE
                PERFORM {refresh}(ARRAY[NEW.id]);
            END IF;
            RETURN NULL;
        END
        $search$
        """,
        f"""
        CREATE TRIGGER {_trigger(source)}
        AFTER INSERT OR DELETE OR UPDATE{_columns(source.watch)} ON {table}
        FOR EACH ROW EXECUTE FUNCTION {_trigger(source)}()
        """,
    ]
    for dependency in source.dependencies:
        function = _trigger(source, dependency.table)
        statements += [
            f"""
            CREATE FUNCTION {function}() RETURNS trigger LANGUAGE plpgsql
            SET search_path = {_SEARCH_PATH} AS $search$
            DECLARE
                {CHANGED} {_quote(dependency.table)};
                ids bigint[] := '{{}}';
            BEGIN
                IF TG_OP <> 'INSERT' THEN
                    {CHANGED} := OLD;
                    ids := ids || ARRAY({dependency.records})::bigint[];
                END IF;
                IF TG_OP <> 'DELETE' THEN
                    {CHANGED} := NEW;
                    ids := ids || ARRAY({dependency.records})::bigint[];
                END IF;
                PERFORM {refresh}(ids);
                RETURN NULL;
            END
            $search$
            """,
            f"""
            CREATE TRIGGER {function}
            AFTER INSERT OR DELETE OR UPDATE{_columns(dependency.watch)}
            ON {_quote(dependency.table)}
            FOR EACH ROW EXECUTE FUNCTION {function}()
            """,
        ]
    return statements


def digest(source: SearchSource) -> str:
    """What ``source`` renders to, digested: a change to it, or to how this
    module renders, gives another."""
    return hashlib.sha256("\n".join(render(source)).encode()).hexdigest()[:16]


def _functions(source: SearchSource) -> list[str]:
    return [
        f"{_refresh(source)}(bigint[])",
        f"{_trigger(source)}()",
        *(f"{_trigger(source, dependency.table)}()" for dependency in source.dependencies),
    ]


def _marked(connection: sa.Connection) -> dict[str, tuple[str, list[str]]]:
    """What the database holds of each kind: its digest, and its functions."""
    rows = connection.execute(
        text(
            """
            SELECT p.oid::regprocedure::text, obj_description(p.oid, 'pg_proc')
              FROM pg_proc p
             WHERE p.pronamespace = 'public'::regnamespace
               AND obj_description(p.oid, 'pg_proc') LIKE :mark
            """
        ),
        {"mark": f"{_MARK} %"},
    )
    held: dict[str, tuple[str, list[str]]] = {}
    for function, comment in rows.all():
        kind, found = comment.removeprefix(f"{_MARK} ").split(" ")
        held.setdefault(kind, (found, []))[1].append(function)
    return held


def _drop(connection: sa.Connection, functions: list[str]) -> None:
    """Drop the functions, and with them the triggers that call them."""
    for function in functions:
        connection.exec_driver_sql(f"DROP FUNCTION IF EXISTS {function} CASCADE")


def apply_sources(connection: sa.Connection, sources: Sequence[SearchSource]) -> list[str]:
    """Bring the database's search triggers to ``sources``, as the owner login,
    once the app has migrated; answers the kinds indexed again.

    A source whose digest the database does not hold has its functions and
    triggers made again, and its records indexed again in every organization;
    a kind no source names any more is removed, with its entries. Runs in the
    connection's transaction, which the caller commits; one start at a time
    applies them."""
    kinds = [source.kind for source in sources]
    if len(set(kinds)) != len(kinds):
        raise ValueError("each search source has a kind of its own")
    connection.execute(text("SELECT pg_advisory_xact_lock(hashtext(:mark))"), {"mark": _MARK})
    held = _marked(connection)
    changed = [source for source in sources if held.get(source.kind, ("",))[0] != digest(source)]
    gone = [kind for kind in held if kind not in kinds]
    for kind in gone:
        _drop(connection, held[kind][1])
    for source in changed:
        _drop(connection, held.get(source.kind, ("", []))[1])
        for statement in render(source):
            connection.exec_driver_sql(statement)
        mark = f"{_MARK} {source.kind} {digest(source)}"
        for function in _functions(source):
            connection.execute(text(f"COMMENT ON FUNCTION {function} IS '{mark}'"))
    if changed or gone:
        organizations = connection.execute(text(f"SELECT id FROM {REGISTRY_TABLE}"))
        for (organization_id,) in organizations.all():
            connection.execute(
                text("SELECT set_config(:setting, :value, true)"),
                {"setting": ORGANIZATION_SETTING, "value": str(organization_id)},
            )
            for kind in gone:
                connection.execute(
                    text("DELETE FROM search_entries WHERE kind = :kind"), {"kind": kind}
                )
            for source in changed:
                # Every record, and every entry, so that one whose record
                # is gone goes too; an entry whose text is the same keeps
                # when it last changed.
                connection.execute(
                    text(
                        f"SELECT {_refresh(source)}(ARRAY("
                        f"SELECT id FROM {_quote(source.table)} "
                        "UNION SELECT record_id FROM search_entries WHERE kind = :kind))"
                    ),
                    {"kind": source.kind},
                )
        connection.execute(
            text("SELECT set_config(:setting, '', true)"), {"setting": ORGANIZATION_SETTING}
        )
    return [source.kind for source in changed]


# The index.


class SearchEntry(OrganizationRow, table=True):
    """One record, as the index holds it. Written only by the functions
    :func:`apply_sources` makes."""

    __tablename__ = "search_entries"

    kind: str = Field(primary_key=True)
    record_id: int = Field(primary_key=True, sa_type=sa.BigInteger)
    title: str
    subtitle: str | None = None
    #: The page that opens the record.
    path: str
    codes: list[str] = Field(default_factory=list, sa_column=Column(ARRAY(Text), nullable=False))
    #: Each code folded, for matching.
    folded_codes: list[str] = Field(
        default_factory=list, sa_column=Column(ARRAY(Text), nullable=False)
    )
    body: str | None = None
    archived: bool = False
    words: Any = Field(default=None, sa_column=Column(TSVECTOR, nullable=False))
    #: When its text last changed.
    updated_at: datetime = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def create_table() -> None:
    """Make the index and what it reads with: the ``unaccent`` and ``pg_trgm``
    extensions, the configuration words are read in, :func:`fold`'s function,
    and ``search_entries``, an organization's table."""
    op.execute("CREATE EXTENSION IF NOT EXISTS unaccent SCHEMA public")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm SCHEMA public")
    op.execute(f"CREATE TEXT SEARCH CONFIGURATION {WORDS} (COPY = pg_catalog.simple)")
    op.execute(
        f"ALTER TEXT SEARCH CONFIGURATION {WORDS} ALTER MAPPING FOR "
        "word, numword, hword, hword_part, numhword, hword_numpart "
        "WITH public.unaccent, pg_catalog.simple"
    )
    op.execute(
        f"""
        CREATE FUNCTION search_fold(code text) RETURNS text
        LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE SET search_path = {_SEARCH_PATH} AS $$
            SELECT lower(regexp_replace(
                public.unaccent('public.unaccent'::regdictionary, code), '[^[:alnum:]]+', '', 'g'
            ))
        $$
        """
    )
    op.create_table(
        "search_entries",
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("record_id", sa.BigInteger(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("subtitle", sa.Text(), nullable=True),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("codes", ARRAY(sa.Text()), nullable=False, server_default="{}"),
        sa.Column("folded_codes", ARRAY(sa.Text()), nullable=False, server_default="{}"),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("archived", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("words", TSVECTOR(), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    isolate("search_entries")
    op.create_primary_key(
        "pk_search_entries", "search_entries", ["organization_id", "kind", "record_id"]
    )
    # No index beyond the key: under forced row security PostgreSQL uses an
    # index only for leakproof operators, which the text search, array and
    # trigram operators are not, so each search reads the organization's
    # entries by the key and filters them.


def drop_table() -> None:
    """Undo :func:`create_table`, and remove every trigger :func:`apply_sources` made."""
    for _, functions in _marked(op.get_bind()).values():
        for function in functions:
            op.execute(f"DROP FUNCTION IF EXISTS {function} CASCADE")
    op.drop_table("search_entries")
    op.execute("DROP FUNCTION search_fold(text)")
    op.execute(f"DROP TEXT SEARCH CONFIGURATION {WORDS}")
    op.execute("DROP EXTENSION IF EXISTS pg_trgm")
    op.execute("DROP EXTENSION IF EXISTS unaccent")


# Reading.


class SnippetPart(SQLModel):
    """A stretch of a snippet, and whether it is a word the query matched."""

    text: str
    marked: bool


class SearchHit(SQLModel):
    """A record found: what it is, the page that opens it, and how it matched.
    ``exact`` says one of its codes is exactly what was asked."""

    kind: str
    record_id: int
    title: str
    subtitle: str | None
    path: str
    codes: list[str]
    archived: bool
    exact: bool
    snippet: list[SnippetPart]


class SearchResults(SQLModel):
    """A page of results, how many there are of the kind asked (every kind,
    when none was), how many of each kind, and, when nothing matched, the
    titles close to the query."""

    items: list[SearchHit]
    total: int
    page: int
    page_size: int
    counts: dict[str, int]
    close_matches: list[SearchHit]


class Suggestions(SQLModel):
    """Records to jump to as the query is typed, exact codes first.
    ``exact_match`` says the query is exactly one record's code, the first."""

    items: list[SearchHit]
    exact_match: bool


@dataclass(frozen=True)
class _Query:
    """A query as the index reads it: folded, and as words."""

    folded: str
    words: str


def trailing_prefix(query: str) -> str | None:
    """The last word of ``query``, to match as a prefix, since what is typed
    is usually half-finished: a plain word of at least :data:`MIN_PREFIX`
    characters, never a quoted phrase, an exclusion or an operator."""
    if query.count('"') % 2:
        return None
    last = query.rstrip().rpartition(" ")[2]
    if not last.isalnum() or len(last) < MIN_PREFIX or last.lower() in _OPERATORS:
        return None
    return last


async def _read(db: AsyncSession, query: str) -> _Query:
    words: Any = func.websearch_to_tsquery(WORDS, query)
    prefix = trailing_prefix(query)
    if prefix is not None:
        # The last word becomes its prefix where the query holds it, so that
        # phrases, exclusions and ``or`` keep their meaning. It holds only
        # letters and digits, so it reaches to_tsquery as a word, never as
        # syntax.
        words = func.ts_rewrite(
            words, func.to_tsquery(WORDS, prefix), func.to_tsquery(WORDS, f"{prefix}:*")
        )
    folded, parsed = (await db.execute(sa.select(func.search_fold(query), cast(words, Text)))).one()
    return _Query(folded=folded or "", words=parsed or "")


#: An entry's columns, as a hit reads them.
_HIT_COLUMNS = (
    col(SearchEntry.kind),
    col(SearchEntry.record_id),
    col(SearchEntry.title),
    col(SearchEntry.subtitle),
    col(SearchEntry.path),
    col(SearchEntry.codes),
    col(SearchEntry.archived),
)


def _clauses(read: _Query) -> tuple[Any, Any, Any, Any]:
    """Whether an entry matches; whether by a code exactly, or by a code's
    prefix; and how well by its words."""
    exact = (
        literal(read.folded) == sa.func.any(col(SearchEntry.folded_codes))
        if read.folded
        else sa.false()
    )
    prefix = (
        func.array_to_tsvector(col(SearchEntry.folded_codes)).op("@@")(
            # Folded, the code holds only letters and digits.
            cast(literal(f"'{read.folded}':*"), TSQUERY)
        )
        if len(read.folded) >= MIN_CODE_PREFIX
        else exact
    )
    if read.words:
        words = cast(literal(read.words), TSQUERY)
        matched = col(SearchEntry.words).op("@@")(words)
        rank = func.ts_rank_cd(col(SearchEntry.words), words)
    else:
        matched, rank = sa.false(), literal(0.0)
    return or_(exact, prefix, matched), exact, prefix, rank


def _narrowed(statement: Any, *, kind: str | None, archived: bool) -> Any:
    if kind is not None:
        statement = statement.where(col(SearchEntry.kind) == kind)
    if not archived:
        statement = statement.where(col(SearchEntry.archived).is_(False))
    return statement


def _snippet(headline: str | None) -> list[SnippetPart]:
    """A headline as stretches, the marked ones being matched words."""
    parts: list[SnippetPart] = []
    for i, stretch in enumerate((headline or "").split(_MARK_START)):
        marked, _, rest = stretch.partition(_MARK_STOP) if i else ("", "", stretch)
        if marked:
            parts.append(SnippetPart(text=marked, marked=True))
        if rest:
            parts.append(SnippetPart(text=rest, marked=False))
    return parts


def _hit(row: Any) -> SearchHit:
    return SearchHit(
        kind=row.kind,
        record_id=row.record_id,
        title=row.title,
        subtitle=row.subtitle,
        path=row.path,
        codes=row.codes,
        archived=row.archived,
        exact=row.exact,
        snippet=_snippet(row.headline),
    )


async def _ranked(
    db: AsyncSession,
    read: _Query,
    *,
    kind: str | None,
    archived: bool,
    limit: int,
    offset: int = 0,
    snippets: bool,
) -> list[SearchHit]:
    """The matching entries in rank order: exact codes, then codes by prefix,
    then words, lifted a little for what changed lately. The snippets are
    drawn for the entries answered alone."""
    matched, exact, prefix, rank = _clauses(read)
    age_days = func.extract("epoch", func.now() - col(SearchEntry.updated_at)) / 86400
    lifted = rank * (1 + RECENT_LIFT / (1 + age_days / RECENT_DAYS))
    headline = (
        func.ts_headline(
            WORDS, col(SearchEntry.body), cast(literal(read.words), TSQUERY), _HEADLINE
        )
        if snippets and read.words
        else literal(None, Text)
    )
    statement = _narrowed(
        sa.select(*_HIT_COLUMNS, exact.label("exact"), headline.label("headline")).where(matched),
        kind=kind,
        archived=archived,
    ).order_by(
        case((exact, 2), (prefix, 1), else_=0).desc(),
        lifted.desc(),
        col(SearchEntry.updated_at).desc(),
        col(SearchEntry.kind),
        col(SearchEntry.record_id),
    )
    rows = await db.execute(statement.limit(limit).offset(offset))
    return [_hit(row) for row in rows.all()]


async def close_matches(
    db: AsyncSession, query: str, *, kind: str | None = None, archived: bool = False
) -> list[SearchHit]:
    """Records whose title is close to ``query``, for when nothing matched it:
    within :data:`CLOSE_THRESHOLD` of the title's closest stretch, accents
    aside. Given up after :data:`CLOSE_TIMEOUT_MS`, answering none."""
    closeness = func.word_similarity(func.unaccent(query), func.unaccent(col(SearchEntry.title)))
    statement = (
        _narrowed(
            sa.select(
                *_HIT_COLUMNS, sa.false().label("exact"), literal(None, Text).label("headline")
            ).where(closeness >= CLOSE_THRESHOLD),
            kind=kind,
            archived=archived,
        )
        .order_by(closeness.desc(), col(SearchEntry.kind), col(SearchEntry.record_id))
        .limit(CLOSE_LIMIT)
    )
    try:
        # A savepoint, so that giving up leaves the transaction usable.
        async with db.begin_nested():
            await db.execute(
                text("SELECT set_config('statement_timeout', :timeout, true)"),
                {"timeout": f"{CLOSE_TIMEOUT_MS}ms"},
            )
            rows = (await db.execute(statement)).all()
    except SQLAlchemyError:
        return []
    finally:
        # A transaction-local setting outlives its savepoint, so it is put back.
        await db.execute(text("SET LOCAL statement_timeout = DEFAULT"))
    return [_hit(row) for row in rows]


async def search(
    db: AsyncSession,
    query: str,
    *,
    kind: str | None = None,
    archived: bool = False,
    page: int = 1,
) -> SearchResults:
    """A page of the records matching ``query``, of ``kind`` if given, with
    snippets of their bodies; how many match of each kind; and the close
    matches, when nothing does."""
    read = await _read(db, query)
    counted = await db.execute(
        _narrowed(
            sa.select(col(SearchEntry.kind), func.count()).where(_clauses(read)[0]),
            kind=None,
            archived=archived,
        ).group_by(col(SearchEntry.kind))
    )
    counts: dict[str, int] = {found: count for found, count in counted.all()}
    items = await _ranked(
        db,
        read,
        kind=kind,
        archived=archived,
        limit=PAGE_SIZE,
        offset=(page - 1) * PAGE_SIZE,
        snippets=True,
    )
    close = (
        await close_matches(db, query, kind=kind, archived=archived)
        if not counts and query.strip()
        else []
    )
    return SearchResults(
        items=items,
        total=counts.get(kind, 0) if kind is not None else sum(counts.values()),
        page=page,
        page_size=PAGE_SIZE,
        counts=counts,
        close_matches=close,
    )


async def suggest(db: AsyncSession, query: str) -> Suggestions:
    """Up to :data:`SUGGEST_LIMIT` records to jump to, exact codes first, and
    whether the query is exactly one of their codes, the first's."""
    read = await _read(db, query)
    items = await _ranked(db, read, kind=None, archived=False, limit=SUGGEST_LIMIT, snippets=False)
    return Suggestions(items=items, exact_match=[item.exact for item in items].count(True) == 1)


# Routes.

router = APIRouter(prefix="/search", tags=["search"])

QueryText = Annotated[str, Query(max_length=MAX_QUERY)]


@router.get("", responses={401: {"description": "NOT_AUTHENTICATED"}})
async def search_records(
    db: OrganizationSessionDep,
    q: QueryText,
    kind: str | None = None,
    archived: bool = False,
    page: Annotated[int, Query(ge=1)] = 1,
) -> SearchResults:
    """The organization's records matching ``q``: a page of them, of ``kind``
    if given, archived ones only when asked."""
    return await search(db, q, kind=kind, archived=archived, page=page)


@router.get("/suggest", responses={401: {"description": "NOT_AUTHENTICATED"}})
async def suggest_records(db: OrganizationSessionDep, q: QueryText) -> Suggestions:
    """Records to jump to as ``q`` is typed."""
    return await suggest(db, q)
