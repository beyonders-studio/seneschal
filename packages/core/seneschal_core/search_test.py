"""The search index follows every write, through a source's own table and
through its dependencies; reads codes before words, words with their syntax
and without accents; keeps archived records and other organizations out; and
is rebuilt when a source changes."""

from __future__ import annotations

import secrets
import time
from collections.abc import AsyncIterator, Iterator
from dataclasses import replace

import psycopg
import pytest
import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.engine import make_url

from seneschal_core import organizations, search
from seneschal_core.db import Database, WriteSession
from seneschal_core.search import SearchDependency, SearchSource
from seneschal_core.testing import AppDatabase

PARTS = (
    "(SELECT string_agg(p.name, ' ' ORDER BY p.id) FROM gadget_parts p WHERE p.gadget_id = r.id)"
)

GADGETS = SearchSource(
    kind="gadget",
    table="gadgets",
    title="r.name",
    path="'/gadgets/' || r.id",
    codes="ARRAY[r.code] || ARRAY(SELECT p.sku FROM gadget_parts p WHERE p.gadget_id = r.id ORDER BY p.id)",
    body=("r.notes", PARTS),
    subtitle="r.maker",
    archived="r.retired",
    watch=("name", "code", "maker", "notes", "retired"),
    dependencies=(
        SearchDependency("gadget_parts", "SELECT d.gadget_id", watch=("gadget_id", "name", "sku")),
    ),
)
PART = SearchSource(
    kind="part",
    table="gadget_parts",
    title="r.name",
    path="'/gadgets/' || r.gadget_id",
    codes="ARRAY[r.sku]",
)
SOURCES = (GADGETS, PART)


def _apply(database: AppDatabase, sources: tuple[SearchSource, ...]) -> list[str]:
    engine = sa.create_engine(database.owner_url)
    try:
        with engine.begin() as connection:
            return search.apply_sources(connection, sources)
    finally:
        engine.dispose()


@pytest.fixture(scope="session")
def indexed(database: AppDatabase) -> Iterator[None]:
    _apply(database, SOURCES)
    yield


async def _organization(database: Database) -> int:
    async with database.session() as db:
        organization_id = await organizations.organization_for(
            db, community_ref=f"community-{secrets.token_hex(6)}", plugin_install_id=1
        )
        await db.commit()
    return organization_id


@pytest.fixture
async def db(serving_database: Database, indexed: None) -> AsyncIterator[WriteSession]:
    """A write session for an organization of the test's own."""
    organization_id = await _organization(serving_database)
    async with serving_database.write_session(organization_id, actor=None) as session:
        yield session


async def _gadget(db: WriteSession, name: str, **fields: object) -> int:
    columns = {"name": name, **fields}
    return (
        await db.execute(
            text(
                f"INSERT INTO gadgets (organization_id, {', '.join(columns)}) "
                f"VALUES (:org, {', '.join(f':{c}' for c in columns)}) RETURNING id"
            ),
            {"org": db.organization_id, **columns},
        )
    ).scalar_one()


async def _part(db: WriteSession, gadget_id: int, name: str, sku: str | None = None) -> int:
    return (
        await db.execute(
            text(
                "INSERT INTO gadget_parts (organization_id, gadget_id, name, sku) "
                "VALUES (:org, :gadget, :name, :sku) RETURNING id"
            ),
            {"org": db.organization_id, "gadget": gadget_id, "name": name, "sku": sku},
        )
    ).scalar_one()


async def _entry(db: WriteSession, kind: str, record_id: int) -> dict | None:
    row = (
        await db.execute(
            text(
                "SELECT title, subtitle, path, codes, folded_codes, body, archived "
                "FROM search_entries WHERE kind = :kind AND record_id = :id"
            ),
            {"kind": kind, "id": record_id},
        )
    ).first()
    return None if row is None else dict(row._mapping)


async def _titles(db: WriteSession, query: str, **narrowed: object) -> list[str]:
    results = await search.search(db, query, **narrowed)  # type: ignore[arg-type]
    return [hit.title for hit in results.items]


async def test_an_entry_follows_every_write_to_its_record_and_its_dependencies(
    db: WriteSession,
):
    gadget = await _gadget(db, "Whisk", code="W-1", maker="Acme", notes="for eggs")
    assert await _entry(db, "gadget", gadget) == {
        "title": "Whisk",
        "subtitle": "Acme",
        "path": f"/gadgets/{gadget}",
        "codes": ["W-1"],
        "folded_codes": ["w1"],
        "body": "for eggs",
        "archived": False,
    }

    await db.execute(
        text("UPDATE gadgets SET name = 'Balloon whisk' WHERE id = :id"), {"id": gadget}
    )
    part = await _part(db, gadget, "Handle", sku="H-9")
    entry = await _entry(db, "gadget", gadget)
    assert entry is not None
    assert (entry["title"], entry["codes"], entry["body"]) == (
        "Balloon whisk",
        ["W-1", "H-9"],
        "for eggs Handle",
    )
    assert (await _entry(db, "part", part) or {})["title"] == "Handle"

    await db.execute(text("UPDATE gadget_parts SET sku = 'H-10' WHERE id = :id"), {"id": part})
    assert (await _entry(db, "gadget", gadget) or {})["codes"] == ["W-1", "H-10"]

    other = await _gadget(db, "Sieve")
    await db.execute(
        text("UPDATE gadget_parts SET gadget_id = :other WHERE id = :id"),
        {"other": other, "id": part},
    )
    assert (await _entry(db, "gadget", gadget) or {})["codes"] == ["W-1"]
    assert (await _entry(db, "gadget", other) or {})["codes"] == ["H-10"]

    await db.execute(text("DELETE FROM gadget_parts WHERE id = :id"), {"id": part})
    assert (await _entry(db, "gadget", other) or {})["codes"] == []
    assert await _entry(db, "part", part) is None

    await db.execute(text("DELETE FROM gadgets WHERE id = :id"), {"id": gadget})
    assert await _entry(db, "gadget", gadget) is None


async def test_an_update_that_changes_no_text_keeps_when_the_entry_changed(db: WriteSession):
    gadget = await _gadget(db, "Ladle", notes="soup")
    before = (
        await db.execute(
            text("SELECT updated_at FROM search_entries WHERE record_id = :id"), {"id": gadget}
        )
    ).scalar_one()
    await db.commit()
    await db.execute(text("UPDATE gadgets SET notes = 'soup' WHERE id = :id"), {"id": gadget})
    after = (
        await db.execute(
            text("SELECT updated_at FROM search_entries WHERE record_id = :id"), {"id": gadget}
        )
    ).scalar_one()
    assert after == before


async def test_codes_match_exactly_then_by_prefix_folded_and_ahead_of_words(db: WriteSession):
    exact = await _gadget(db, "Grater", code="R-0012")
    longer = await _gadget(db, "Peeler", code="r00123")
    await _gadget(db, "Mandoline", notes="r0012 is the grater's code")
    await _gadget(db, "Zester", code="LOT/24/07")

    results = await search.search(db, "R 0012")
    assert [hit.title for hit in results.items][:2] == ["Grater", "Peeler"]
    assert [(hit.record_id, hit.exact) for hit in results.items[:2]] == [
        (exact, True),
        (longer, False),
    ]
    # Codes by prefix come before a word that starts the same.
    prefixed = await _titles(db, "r001")
    assert (sorted(prefixed[:2]), prefixed[2:]) == (["Grater", "Peeler"], ["Mandoline"])
    assert await _titles(db, "lot2407") == ["Zester"]
    assert await _titles(db, "LOT-24-07") == ["Zester"]


async def test_accents_fold_in_words_and_codes(db: WriteSession):
    await _gadget(db, "Crème brûlée torch", code="ÉTÉ-1", notes="Café use")

    assert await _titles(db, "creme brulee") == ["Crème brûlée torch"]
    assert await _titles(db, "CAFÉ") == ["Crème brûlée torch"]
    assert await _titles(db, "ete1") == ["Crème brûlée torch"]
    snippet = (await search.search(db, "cafe")).items[0].snippet
    assert [part.text for part in snippet if part.marked] == ["Café"]


async def test_the_last_word_typed_matches_as_a_prefix(db: WriteSession):
    await _gadget(db, "Caramel thermometer")

    assert await _titles(db, "thermo") == ["Caramel thermometer"]
    assert await _titles(db, "caramel the") == ["Caramel thermometer"]
    assert await _titles(db, "th") == []
    assert await _titles(db, '"thermo') == []


async def test_phrases_exclusions_and_or(db: WriteSession):
    await _gadget(db, "Salted caramel pot")
    await _gadget(db, "Caramel salted spoon")
    await _gadget(db, "Fudge tin")

    assert await _titles(db, '"salted caramel"') == ["Salted caramel pot"]
    assert await _titles(db, "caramel -spoon") == ["Salted caramel pot"]
    assert sorted(await _titles(db, "fudge or spoon")) == ["Caramel salted spoon", "Fudge tin"]
    assert sorted(await _titles(db, "tin or spo")) == ["Caramel salted spoon", "Fudge tin"]


async def test_archived_records_are_left_out_unless_asked_for(db: WriteSession):
    await _gadget(db, "Old mixer", retired=True)
    await _gadget(db, "New mixer")

    assert await _titles(db, "mixer") == ["New mixer"]
    assert sorted(await _titles(db, "mixer", archived=True)) == ["New mixer", "Old mixer"]
    assert (await search.suggest(db, "mixer")).items[0].title == "New mixer"


async def test_each_kind_is_counted_and_can_be_asked_for_alone(db: WriteSession):
    gadget = await _gadget(db, "Kettle")
    await _part(db, gadget, "Kettle lid")

    results = await search.search(db, "kettle")
    assert (results.counts, results.total) == ({"gadget": 1, "part": 1}, 2)
    parts = await search.search(db, "kettle", kind="part")
    assert ([hit.title for hit in parts.items], parts.total) == (["Kettle lid"], 1)
    assert parts.items[0].path == f"/gadgets/{gadget}"


async def test_close_matches_only_when_nothing_matched(db: WriteSession):
    await _gadget(db, "Colander")

    missed = await search.search(db, "colandr")
    assert (missed.items, [hit.title for hit in missed.close_matches]) == ([], ["Colander"])
    found = await search.search(db, "colander")
    assert ([hit.title for hit in found.items], found.close_matches) == (["Colander"], [])


async def test_close_matches_give_up_within_their_bound(db: WriteSession, database: AppDatabase):
    await _gadget(db, "Tongs")
    await db.commit()
    url = (
        make_url(database.superuser_url)
        .set(drivername="postgresql")
        .render_as_string(hide_password=False)
    )
    with psycopg.connect(url) as locker:
        locker.execute("LOCK TABLE search_entries IN ACCESS EXCLUSIVE MODE")
        started = time.monotonic()
        assert await search.close_matches(db, "tongz") == []
        assert time.monotonic() - started < 2
    # The session is still usable once the lock is gone.
    assert [hit.title for hit in await search.close_matches(db, "tongz")] == ["Tongs"]


async def test_another_organizations_entries_never_show(
    db: WriteSession, serving_database: Database
):
    await _gadget(db, "Rolling pin", code="RP-1")
    await db.commit()
    other = await _organization(serving_database)
    async with serving_database.write_session(other, actor=None) as theirs:
        assert await _titles(theirs, "rolling") == []
        assert (await search.suggest(theirs, "RP-1")).items == []
        assert (await search.search(theirs, "rollin")).close_matches == []


async def test_a_single_exact_code_is_flagged_for_opening(db: WriteSession):
    await _gadget(db, "Scale", code="SC-77")
    await _gadget(db, "Scoop", code="SC-778")

    single = await search.suggest(db, "sc 77")
    assert single.exact_match
    assert [hit.title for hit in single.items] == ["Scale", "Scoop"]

    await _gadget(db, "Spare scale", code="sc77")
    assert not (await search.suggest(db, "SC-77")).exact_match
    assert not (await search.suggest(db, "SC-7")).exact_match


async def test_a_changed_source_is_indexed_again_on_start(
    db: WriteSession, serving_database: Database, database: AppDatabase
):
    gadget = await _gadget(db, "Funnel")
    await db.commit()
    other = await _organization(serving_database)
    async with serving_database.write_session(other, actor=None) as theirs:
        theirs_id = await _gadget(theirs, "Spatula")
        await theirs.commit()

    assert _apply(database, SOURCES) == []
    shouting = replace(GADGETS, title="upper(r.name)")
    try:
        assert _apply(database, (shouting, PART)) == ["gadget"]
        assert (await _entry(db, "gadget", gadget) or {})["title"] == "FUNNEL"
        async with serving_database.organization_session(other) as theirs:
            assert (await _entry(theirs, "gadget", theirs_id) or {})["title"] == "SPATULA"  # type: ignore[arg-type]
        # A write after the change goes through the new triggers.
        await db.execute(text("UPDATE gadgets SET name = 'Jug' WHERE id = :id"), {"id": gadget})
        assert (await _entry(db, "gadget", gadget) or {})["title"] == "JUG"
        await db.commit()

        assert _apply(database, (PART,)) == []
        assert await _entry(db, "gadget", gadget) is None
        await db.execute(text("UPDATE gadgets SET name = 'Funnel' WHERE id = :id"), {"id": gadget})
        assert await _entry(db, "gadget", gadget) is None
        await db.commit()
    finally:
        assert _apply(database, SOURCES) == ["gadget"]
    assert (await _entry(db, "gadget", gadget) or {})["title"] == "Funnel"


def test_a_source_names_one_dependency_per_table():
    with pytest.raises(ValueError):
        SearchSource(
            kind="gadget",
            table="gadgets",
            title="r.name",
            path="''",
            dependencies=(SearchDependency("gadgets", "SELECT d.id"),),
        )
    with pytest.raises(ValueError):
        SearchSource(kind="gadget; drop", table="gadgets", title="r.name", path="''")


@pytest.mark.parametrize(
    ("query", "prefix"),
    [
        ("caramel ther", "ther"),
        ("th", None),
        ('"salted caram', None),
        ('"salted" caram', "caram"),
        ("fudge or", None),
        ("fudge -spoo", None),
    ],
)
def test_the_trailing_prefix_is_a_plain_last_word(query: str, prefix: str | None):
    assert search.trailing_prefix(query) == prefix


async def test_nothing_typed_finds_nothing(db: WriteSession):
    await _gadget(db, "Whatever")
    for query in ("", "   ", "///"):
        results = await search.search(db, query)
        assert (results.items, results.counts, results.close_matches) == ([], {}, [])
