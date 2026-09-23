"""
Migration 0025's duplicate-detection step, exercised without Postgres (issue
#691), same split as `test_migration_0012_data.py`.

`0025_book_pairs_one_to_one.py` replaces `book_pairs`' composite unique
constraint with two per-column unique indexes enforcing that a pair is
one-to-one. Unlike 0012, it does **not** repair an existing violation by
merging or deleting rows — the whole point of issue #691 is that dropping a
pair silently is exactly the data loss to avoid, so which pair survives is an
operator decision. `check_no_duplicate_pairs` is the guard: it raises with
every offending id named, and creates nothing, if `book_pairs` already
violates the rule it is about to enforce.

The DDL half (index names, that they are *working* uniques, that the
downgrade round-trips) needs a real Postgres for the "is this a real unique,
not a plain index" question and is covered by
`test_migrations_postgres.py::test_0025_creates_the_one_to_one_indexes_and_enforces_them`.
What runs here is the same `upgrade()`/`downgrade()` code, on SQLite, via the
same Alembic `Operations.context` trick 0012's DDL tests use — it proves the
migration's *logic* (ordering, what it drops/creates, that it refuses on a
duplicate) without needing a Postgres service.
"""

import importlib.util
import os

import pytest
from sqlalchemy import text

from models.book import AudioBook, BookPair, EBook, PairStatus
from tests.factories import make_audiobook, make_ebook, suspend_book_pair_uniqueness

_MIGRATION = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "alembic", "versions", "0025_book_pairs_one_to_one.py",
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("migration_0025", _MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def _check(db):
    migration = _load_migration()
    await db.run_sync(lambda conn: migration.check_no_duplicate_pairs(conn))


async def _seed_duplicate_ebook(db):
    """Two pairs sharing one `ebook_id` — the exact shape the bug report
    described (one auto-matched, one manual, three days apart)."""
    await suspend_book_pair_uniqueness(db)
    eb = await make_ebook(db)
    ab1 = await make_audiobook(db, filename="a1.m4b")
    ab2 = await make_audiobook(db, filename="a2.m4b")
    p1 = BookPair(ebook_id=eb.id, audiobook_id=ab1.id, status=PairStatus.SYNCED)
    p2 = BookPair(ebook_id=eb.id, audiobook_id=ab2.id, status=PairStatus.SYNCED)
    db.add_all([p1, p2])
    await db.commit()
    await db.refresh(p1)
    await db.refresh(p2)
    return eb, p1, p2


# ---------------------------------------------------------------------------
# check_no_duplicate_pairs — the data half
# ---------------------------------------------------------------------------

async def test_no_op_on_a_clean_database(db):
    eb = await make_ebook(db)
    ab = await make_audiobook(db)
    db.add(BookPair(ebook_id=eb.id, audiobook_id=ab.id, status=PairStatus.SYNCED))
    await db.commit()

    await _check(db)  # must not raise


async def test_raises_naming_the_duplicated_ebook_id_and_its_pairs(db):
    eb, p1, p2 = await _seed_duplicate_ebook(db)

    with pytest.raises(RuntimeError) as exc_info:
        await _check(db)

    message = str(exc_info.value)
    assert f"ebook_id {eb.id}" in message
    assert str(p1.id) in message
    assert str(p2.id) in message


async def test_raises_naming_the_duplicated_audiobook_id(db):
    await suspend_book_pair_uniqueness(db)
    eb1 = await make_ebook(db, filename="e1.epub")
    eb2 = await make_ebook(db, filename="e2.epub")
    ab = await make_audiobook(db)
    p1 = BookPair(ebook_id=eb1.id, audiobook_id=ab.id, status=PairStatus.SYNCED)
    p2 = BookPair(ebook_id=eb2.id, audiobook_id=ab.id, status=PairStatus.SYNCED)
    db.add_all([p1, p2])
    await db.commit()

    with pytest.raises(RuntimeError, match=f"audiobook_id {ab.id}"):
        await _check(db)


async def test_untriplicated_rows_are_untouched_and_unreported(db):
    """Two different, cleanly-paired books must not appear in the report."""
    eb1 = await make_ebook(db, filename="e1.epub")
    ab1 = await make_audiobook(db, filename="a1.m4b")
    eb2 = await make_ebook(db, filename="e2.epub")
    ab2 = await make_audiobook(db, filename="a2.m4b")
    db.add_all([
        BookPair(ebook_id=eb1.id, audiobook_id=ab1.id, status=PairStatus.SYNCED),
        BookPair(ebook_id=eb2.id, audiobook_id=ab2.id, status=PairStatus.SYNCED),
    ])
    await db.commit()

    await _check(db)  # must not raise


async def test_the_check_does_not_delete_or_modify_anything(db):
    """The whole point (issue #691): a failed check leaves every row exactly
    as it found them — no merge, no delete, not even on the loser."""
    eb, p1, p2 = await _seed_duplicate_ebook(db)

    with pytest.raises(RuntimeError):
        await _check(db)

    rows = (await db.execute(text(
        "SELECT id, ebook_id, audiobook_id FROM book_pairs ORDER BY id"
    ))).all()
    assert [(r.id, r.ebook_id, r.audiobook_id) for r in rows] == [
        (p1.id, eb.id, p1.audiobook_id), (p2.id, eb.id, p2.audiobook_id),
    ]


# ---------------------------------------------------------------------------
# The DDL half, round-tripped on SQLite
# ---------------------------------------------------------------------------

_NEW_INDEXES = ("ux_book_pairs_ebook_id", "ux_book_pairs_audiobook_id")
_OLD_INDEXES = ("ix_book_pairs_ebook_id", "ix_book_pairs_audiobook_id")


async def _rewind_to_pre_0025(db):
    """Put the schema back to the shape 0025 expects to find: the plain FK
    indexes and the composite `UniqueConstraint` 0012 created, none of the
    new one-to-one unique indexes.

    Built the same way `tests.factories.suspend_book_pair_uniqueness` builds
    *its* alternate shape — a scratch copy of the table via `to_metadata()` —
    rather than raw `CREATE INDEX` SQL, because 0025's own `upgrade()` drops
    `uq_book_pairs_pair` through `batch_alter_table(...).drop_constraint(...)`,
    which needs a real reflectable `UniqueConstraint`, not a same-effect but
    differently-reflected unique index.

    Call this BEFORE seeding: rebuilding the table (SQLite) discards its rows.
    """
    from sqlalchemy import Index, MetaData, UniqueConstraint

    scratch = MetaData()
    EBook.__table__.to_metadata(scratch)
    AudioBook.__table__.to_metadata(scratch)
    old_shape = BookPair.__table__.to_metadata(scratch)
    for index in list(old_shape.indexes):
        if index.unique:
            old_shape.indexes.discard(index)
    Index("ix_book_pairs_ebook_id", old_shape.c.ebook_id)
    Index("ix_book_pairs_audiobook_id", old_shape.c.audiobook_id)
    old_shape.append_constraint(
        UniqueConstraint("ebook_id", "audiobook_id", name="uq_book_pairs_pair")
    )

    await db.execute(text("DROP TABLE book_pairs"))
    await db.run_sync(lambda session: old_shape.create(session.connection()))
    await db.commit()


async def _run_migration(db, direction):
    """Run `upgrade()`/`downgrade()` on a dedicated connection, not `db`'s own.

    `db`'s session can carry state from whatever the test already did on it
    (an ORM commit, an earlier `_index_names(db)` read) into `session
    .connection()`, and running the batch-mode table rebuild on top of that
    shared connection/transaction was observed to leave a stale index behind
    for the *next* statement on that same connection — `downgrade()`'s
    `create_index` then raising "already exists" for an index `upgrade()` had
    just dropped moments before, reproducible only when this test followed
    another test that also wrote to `book_pairs` through a request (i.e. a
    different connection) earlier in the same run. A fresh connection via
    `engine.begin()`, used only for the DDL and committed before this
    function returns, does not exhibit it — `db`'s own connection then reads
    the committed result on its next statement, same as any other durable
    write between two connections.
    """
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from database import engine

    migration = _load_migration()

    def _apply(connection):
        context = MigrationContext.configure(connection)
        with Operations.context(context):
            getattr(migration, direction)()

    async with engine.begin() as conn:
        await conn.run_sync(_apply)


async def _index_names(db):
    rows = (await db.execute(text(
        "SELECT name FROM sqlite_master WHERE type = 'index' AND name IS NOT NULL"
    ))).all()
    return {r.name for r in rows}


async def test_upgrade_creates_the_new_indexes_and_drops_the_old_ones(db):
    await _rewind_to_pre_0025(db)
    before = await _index_names(db)
    assert set(_OLD_INDEXES) <= before
    assert not (set(_NEW_INDEXES) & before)

    await _run_migration(db, "upgrade")

    after = await _index_names(db)
    missing = set(_NEW_INDEXES) - after
    assert not missing, f"upgrade() did not create: {sorted(missing)}"
    assert not (set(_OLD_INDEXES) & after), "upgrade() left the old FK indexes behind"


async def test_upgrade_refuses_when_duplicates_exist(db):
    """The DDL entry point calls the same guard the data tests exercise
    directly above — proven together here so a future refactor cannot
    disconnect `upgrade()` from `check_no_duplicate_pairs` without a test
    noticing."""
    await _rewind_to_pre_0025(db)
    eb = EBook(title="E", filename="e.epub", file_path="/books/e691.epub")
    ab1 = AudioBook(title="A1", filename="a1.m4b", file_path="/audio/a691-1.m4b")
    ab2 = AudioBook(title="A2", filename="a2.m4b", file_path="/audio/a691-2.m4b")
    db.add_all([eb, ab1, ab2])
    await db.flush()
    db.add_all([
        BookPair(ebook_id=eb.id, audiobook_id=ab1.id, status=PairStatus.SYNCED),
        BookPair(ebook_id=eb.id, audiobook_id=ab2.id, status=PairStatus.SYNCED),
    ])
    await db.commit()

    with pytest.raises(RuntimeError, match="ebook_id"):
        await _run_migration(db, "upgrade")

    # Refused before any DDL ran.
    after = await _index_names(db)
    assert not (set(_NEW_INDEXES) & after)


async def test_upgrade_then_downgrade_leaves_the_schema_and_data_as_found(db):
    await _rewind_to_pre_0025(db)
    eb = EBook(title="E", filename="e.epub", file_path="/books/e691b.epub")
    ab = AudioBook(title="A", filename="a.m4b", file_path="/audio/a691b.m4b")
    db.add_all([eb, ab])
    await db.flush()
    db.add(BookPair(ebook_id=eb.id, audiobook_id=ab.id, status=PairStatus.SYNCED))
    await db.commit()
    before_rows = (await db.execute(text(
        "SELECT id, ebook_id, audiobook_id FROM book_pairs ORDER BY id"
    ))).all()

    await _run_migration(db, "upgrade")
    await _run_migration(db, "downgrade")

    left_over = set(_NEW_INDEXES) & await _index_names(db)
    assert not left_over, f"downgrade() left: {sorted(left_over)}"
    assert set(_OLD_INDEXES) <= await _index_names(db)
    after_rows = (await db.execute(text(
        "SELECT id, ebook_id, audiobook_id FROM book_pairs ORDER BY id"
    ))).all()
    assert after_rows == before_rows

    # And it re-applies.
    await _run_migration(db, "upgrade")
    assert not (set(_NEW_INDEXES) - await _index_names(db))
