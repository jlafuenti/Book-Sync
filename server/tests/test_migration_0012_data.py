"""
Migration 0012's dedupe step, exercised without Postgres (issue #256).

The DDL half — creating the unique/plain indexes — needs a real Postgres and is
covered by the migrations CI job. The *data* half is what runs against a
production database that already carries duplicates, and it is plain portable
SQL, so it is tested here (same split as tests/test_migration_0006_data.py).

What has to hold:

1. One row survives per `file_path`, and it is the lowest id — the row every
   existing pair, bookmark and progress row was created against.
2. Everything that referenced a loser now references the survivor: pairs,
   bookmarks, progress rows, and the (FK-less) `library_check_results` entries.
3. Where re-pointing would collide with a row the survivor already has, the
   loser's row is *dropped*, not left behind — a leftover is a duplicate, and
   the unique index the migration exists to create could not then be built.
4. A dropped pair takes its children with it. `BookPair`'s cascades to sync maps
   and bookmarks are ORM-level, so a bare DELETE would orphan them.
5. Rows that were never duplicated are untouched.

The conftest builds the SQLite schema from today's ORM, which already carries
the unique indexes, so each test calls `suspend_file_path_uniqueness` first to
get back to the shape the migration actually encounters.
"""

import importlib.util
import os

from sqlalchemy import text

from models.book import AudioBook, BookPair, EBook, PairStatus
from models.bookmark import Bookmark, BookmarkSource
from models.progress import ProgressType, UserProgress
from models.transcription_queue import TranscriptionQueueItem
from tests.factories import (
    make_sync_map,
    suspend_book_pair_uniqueness,
    suspend_file_path_uniqueness,
)

_MIGRATION = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "alembic", "versions", "0012_book_file_path_indexes.py",
)

DUP_PATH = "/books/dup.epub"
DUP_AUDIO = "/audio/dup.m4b"


def _load_migration():
    spec = importlib.util.spec_from_file_location("migration_0012", _MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def _dedupe(db):
    migration = _load_migration()
    await db.run_sync(lambda conn: migration.dedupe_file_paths(conn))
    await db.run_sync(lambda conn: migration.dedupe_book_pairs(conn))
    await db.commit()


async def _twin_ebooks(db, path=DUP_PATH):
    """Two `ebooks` rows for the same file — the state 0012 has to clean up."""
    await suspend_file_path_uniqueness(db)
    keeper = EBook(title="Keeper", filename="dup.epub", file_path=path)
    loser = EBook(title="Loser", filename="dup.epub", file_path=path)
    db.add_all([keeper, loser])
    await db.commit()
    await db.refresh(keeper)
    await db.refresh(loser)
    assert keeper.id < loser.id
    return keeper, loser


async def _audiobook(db, *, title="A", path="/audio/a.m4b"):
    ab = AudioBook(title=title, filename=os.path.basename(path), file_path=path)
    db.add(ab)
    await db.commit()
    await db.refresh(ab)
    return ab


async def _ids(db, table):
    return [r.id for r in (await db.execute(
        text(f"SELECT id FROM {table} ORDER BY id"))).all()]


async def test_only_the_lowest_id_survives_per_file_path(db):
    keeper, loser = await _twin_ebooks(db)

    await _dedupe(db)

    assert await _ids(db, "ebooks") == [keeper.id]


async def test_the_same_holds_for_audiobooks(db):
    """Both tables carry the identity key; only one of them being cleaned would
    leave the audiobook unique index uncreatable."""
    await suspend_file_path_uniqueness(db)
    keeper = AudioBook(title="Keeper", filename="dup.m4b", file_path=DUP_AUDIO)
    loser = AudioBook(title="Loser", filename="dup.m4b", file_path=DUP_AUDIO)
    db.add_all([keeper, loser])
    await db.commit()
    await db.refresh(keeper)

    await _dedupe(db)

    assert await _ids(db, "audiobooks") == [keeper.id]


async def test_a_pair_on_the_loser_is_repointed(db):
    """The pair is the whole reason a bare DELETE is wrong — it cascades."""
    keeper, loser = await _twin_ebooks(db)
    audio = await _audiobook(db)
    pair = BookPair(ebook_id=loser.id, audiobook_id=audio.id,
                    status=PairStatus.SYNCED)
    db.add(pair)
    await db.commit()
    await db.refresh(pair)

    await _dedupe(db)

    rows = (await db.execute(text(
        "SELECT id, ebook_id FROM book_pairs"))).all()
    assert len(rows) == 1
    assert rows[0].id == pair.id, "the pair was deleted, not re-pointed"
    assert rows[0].ebook_id == keeper.id


async def test_a_bookmark_and_progress_on_the_loser_move_across(db, make_user):
    """A reader's position must survive the collapse — it is the product."""
    user = await make_user(username="reader")
    keeper, loser = await _twin_ebooks(db)
    db.add_all([
        Bookmark(user_id=user.id, ebook_id=loser.id, source=BookmarkSource.EBOOK,
                 epub_chapter=4, epub_sentence_index=11),
        UserProgress(user_id=user.id, media_type=ProgressType.EBOOK,
                     ebook_id=loser.id, epub_chapter=4),
    ])
    await db.commit()

    await _dedupe(db)

    bookmarks = (await db.execute(text(
        "SELECT ebook_id, epub_chapter FROM bookmarks"))).all()
    assert [(b.ebook_id, b.epub_chapter) for b in bookmarks] == [(keeper.id, 4)]
    progress = (await db.execute(text(
        "SELECT ebook_id FROM user_progress"))).all()
    assert [p.ebook_id for p in progress] == [keeper.id]


async def test_a_pair_that_would_collide_is_dropped_with_its_children(db, make_user):
    """Both rows are the same file, so both pairs are the same pairing.

    Re-pointing the second onto the survivor would violate `uq_book_pairs_pair`,
    so it goes — and its sync map, sync points and bookmarks have to go with it,
    or the delete fails / orphans rows.
    """
    user = await make_user(username="reader")
    keeper, loser = await _twin_ebooks(db)
    audio = await _audiobook(db)

    kept_pair = BookPair(ebook_id=keeper.id, audiobook_id=audio.id,
                         status=PairStatus.SYNCED)
    doomed_pair = BookPair(ebook_id=loser.id, audiobook_id=audio.id,
                           status=PairStatus.SYNCED)
    db.add_all([kept_pair, doomed_pair])
    await db.flush()
    db.add(Bookmark(user_id=user.id, book_pair_id=doomed_pair.id,
                    source=BookmarkSource.EBOOK, epub_chapter=2))
    await db.commit()
    kept_id, doomed_id = kept_pair.id, doomed_pair.id

    # A sync map (with points) and a cached transcript, so every child table the
    # delete has to walk is actually populated — the column names differ between
    # them (`audio_transcripts.pair_id`, not `book_pair_id`), which a test with
    # empty tables would never notice.
    await make_sync_map(db, book_pair_id=doomed_id)
    await db.execute(text(
        "INSERT INTO audio_transcripts "
        "(pair_id, audiobook_path, sentence_count, sentences_json, created_at) "
        "VALUES (:p, '/audio/a.m4b', 1, '[]', '2026-08-01 00:00:00')"
    ), {"p": doomed_id})
    db.add(TranscriptionQueueItem(book_pair_id=doomed_id, status="pending"))
    await db.commit()

    await _dedupe(db)

    assert await _ids(db, "book_pairs") == [kept_id]
    assert await _ids(db, "bookmarks") == [], "the dropped pair orphaned a bookmark"
    for table in ("sync_maps", "sync_points", "audio_transcripts", "transcription_queue"):
        assert await _ids(db, table) == [], f"the dropped pair orphaned {table}"


async def test_a_colliding_standalone_bookmark_is_dropped(db, make_user):
    """`ux_bookmarks_user_ebook` is unique per (user, ebook) for standalone rows,
    so the user cannot end up holding two on the survivor."""
    user = await make_user(username="reader")
    keeper, loser = await _twin_ebooks(db)
    db.add_all([
        Bookmark(user_id=user.id, ebook_id=keeper.id,
                 source=BookmarkSource.EBOOK, epub_chapter=9),
        Bookmark(user_id=user.id, ebook_id=loser.id,
                 source=BookmarkSource.EBOOK, epub_chapter=2),
    ])
    await db.commit()

    await _dedupe(db)

    rows = (await db.execute(text(
        "SELECT ebook_id, epub_chapter FROM bookmarks"))).all()
    assert [(r.ebook_id, r.epub_chapter) for r in rows] == [(keeper.id, 9)], (
        "the survivor's own bookmark is the one the app has been serving"
    )


async def test_a_colliding_progress_row_is_dropped(db, make_user):
    user = await make_user(username="reader")
    keeper, loser = await _twin_ebooks(db)
    db.add_all([
        UserProgress(user_id=user.id, media_type=ProgressType.EBOOK,
                     ebook_id=keeper.id, epub_chapter=9),
        UserProgress(user_id=user.id, media_type=ProgressType.EBOOK,
                     ebook_id=loser.id, epub_chapter=2),
    ])
    await db.commit()

    await _dedupe(db)

    rows = (await db.execute(text(
        "SELECT ebook_id, epub_chapter FROM user_progress"))).all()
    assert [(r.ebook_id, r.epub_chapter) for r in rows] == [(keeper.id, 9)]


async def test_library_check_results_follow_the_survivor(db):
    """No FK holds these, so nothing else would move them — and a stale row
    would attach an old integrity verdict to whatever id is reused next."""
    keeper, loser = await _twin_ebooks(db)
    await db.execute(text(
        "INSERT INTO library_check_results "
        "(item_type, item_id, check_type, ok, checked_at) "
        "VALUES ('ebook', :i, 'ebook_integrity', 0, '2026-08-01 00:00:00')"
    ), {"i": loser.id})
    await db.commit()

    await _dedupe(db)

    rows = (await db.execute(text(
        "SELECT item_id FROM library_check_results"))).all()
    assert [r.item_id for r in rows] == [keeper.id]


async def test_a_colliding_check_result_is_dropped(db):
    keeper, loser = await _twin_ebooks(db)
    for item_id, ok in ((keeper.id, 1), (loser.id, 0)):
        await db.execute(text(
            "INSERT INTO library_check_results "
            "(item_type, item_id, check_type, ok, checked_at) "
            "VALUES ('ebook', :i, 'ebook_integrity', :ok, '2026-08-01 00:00:00')"
        ), {"i": item_id, "ok": ok})
    await db.commit()

    await _dedupe(db)

    rows = (await db.execute(text(
        "SELECT item_id, ok FROM library_check_results"))).all()
    assert len(rows) == 1
    assert rows[0].item_id == keeper.id


async def test_duplicate_pairs_collapse_before_the_constraint(db):
    """`create_pair` 409s on these, so they should not exist — but
    `uq_book_pairs_pair` cannot be created if one ever slipped through."""
    await suspend_book_pair_uniqueness(db)
    ebook = EBook(title="E", filename="e.epub", file_path="/books/e.epub")
    audio = await _audiobook(db)
    db.add(ebook)
    await db.flush()
    first = BookPair(ebook_id=ebook.id, audiobook_id=audio.id,
                     status=PairStatus.SYNCED)
    second = BookPair(ebook_id=ebook.id, audiobook_id=audio.id,
                      status=PairStatus.SYNCED)
    db.add_all([first, second])
    await db.commit()
    await db.refresh(first)

    await _dedupe(db)

    assert await _ids(db, "book_pairs") == [first.id]


async def test_unduplicated_rows_are_untouched(db, make_user):
    """Two different books, and two users on the same book, are not duplicates."""
    user = await make_user(username="reader")
    await suspend_file_path_uniqueness(db)
    one = EBook(title="One", filename="one.epub", file_path="/books/one.epub")
    two = EBook(title="Two", filename="two.epub", file_path="/books/two.epub")
    db.add_all([one, two])
    await db.flush()
    audio = AudioBook(title="A", filename="a.m4b", file_path="/audio/a.m4b")
    db.add(audio)
    await db.flush()
    pair = BookPair(ebook_id=two.id, audiobook_id=audio.id, status=PairStatus.SYNCED)
    db.add(pair)
    await db.flush()
    db.add(Bookmark(user_id=user.id, ebook_id=one.id,
                    source=BookmarkSource.EBOOK, epub_chapter=7))
    await db.commit()
    before = (await _ids(db, "ebooks"), await _ids(db, "book_pairs"))

    await _dedupe(db)

    assert (await _ids(db, "ebooks"), await _ids(db, "book_pairs")) == before
    rows = (await db.execute(text(
        "SELECT ebook_id, epub_chapter FROM bookmarks"))).all()
    assert [(r.ebook_id, r.epub_chapter) for r in rows] == [(one.id, 7)]


async def test_dedupe_is_a_no_op_on_a_clean_database(db):
    """The expected production case: it must touch nothing and not raise."""
    keeper = EBook(title="E", filename="e.epub", file_path="/books/e.epub")
    db.add(keeper)
    await db.commit()

    await _dedupe(db)

    assert await _ids(db, "ebooks") == [keeper.id]


# ---------------------------------------------------------------------------
# The DDL half, round-tripped on SQLite
# ---------------------------------------------------------------------------
#
# `upgrade()` and `downgrade()` are the two functions production actually calls,
# and the dedupe tests above reach neither — they call `dedupe_file_paths`
# directly, which is what lets them run without an Alembic context. So the
# migration's most consequential lines were the only ones untested.
#
# Alembic's `Operations.context` binds the module-level `op` proxy to a plain
# connection, which is enough to run both directions against the SQLite harness.
# The DDL is dialect-neutral (`create_index`, and a `batch_alter_table` that is
# a no-op wrapper on Postgres and a table rebuild on SQLite), so what runs here
# is the same code path prod takes; only the emitted SQL differs. The Postgres
# job still owns the "does this work on the real database" question
# (test_migrations_postgres.py::test_0012_creates_the_library_indexes...).

_CREATED_INDEXES = (
    "ux_ebooks_file_path", "ix_ebooks_file_hash",
    "ux_audiobooks_file_path", "ix_audiobooks_file_hash",
    "ix_book_pairs_ebook_id", "ix_book_pairs_audiobook_id",
)


async def _rewind_to_pre_0012(db):
    """Put the schema back to the shape 0012 expects to find.

    The conftest builds SQLite from today's ORM, which already carries
    everything 0012 adds — so `upgrade()` would fail with "index already
    exists" against it. Dropping them first is what makes the run real rather
    than a no-op.

    Call this BEFORE seeding: dropping the `book_pairs` constraint means
    rebuilding the table, which discards its rows.
    """
    await suspend_book_pair_uniqueness(db)  # also re-creates the two FK indexes
    for index in _CREATED_INDEXES:
        await db.execute(text(f"DROP INDEX IF EXISTS {index}"))
    await db.commit()


async def _run_migration(db, direction):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    migration = _load_migration()

    def _apply(connection):
        context = MigrationContext.configure(connection)
        with Operations.context(context):
            getattr(migration, direction)()

    await db.run_sync(lambda session: _apply(session.connection()))
    await db.commit()


async def _index_names(db):
    rows = (await db.execute(text(
        "SELECT name FROM sqlite_master WHERE type = 'index' AND name IS NOT NULL"
    ))).all()
    return {r.name for r in rows}


async def _book_pairs_sql(db):
    return (await db.execute(text(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'book_pairs'"
    ))).scalar_one()


async def test_upgrade_creates_every_index_and_the_pair_constraint(db):
    await _rewind_to_pre_0012(db)
    assert not (set(_CREATED_INDEXES) & await _index_names(db)), "rewind did not"

    await _run_migration(db, "upgrade")

    missing = set(_CREATED_INDEXES) - await _index_names(db)
    assert not missing, f"upgrade() did not create: {sorted(missing)}"
    assert "uq_book_pairs_pair" in await _book_pairs_sql(db)


async def test_upgrade_dedupes_before_it_constrains(db):
    """The ordering inside `upgrade()` is the whole reason it is not two lines.

    Creating the unique index first fails on any database carrying a duplicate,
    which is precisely the database this migration exists for.
    """
    await _rewind_to_pre_0012(db)
    db.add_all([
        EBook(title="Keeper", filename="dup.epub", file_path=DUP_PATH),
        EBook(title="Loser", filename="dup.epub", file_path=DUP_PATH),
    ])
    await db.commit()

    await _run_migration(db, "upgrade")

    assert len(await _ids(db, "ebooks")) == 1
    assert "ux_ebooks_file_path" in await _index_names(db)


async def test_upgrade_then_downgrade_leaves_the_schema_and_the_data_as_found(db):
    """Reversibility. The collapsed rows never come back — that is stated in the
    docstring — but everything the migration did *not* delete must survive, and
    the schema must come off cleanly enough to re-apply."""
    await _rewind_to_pre_0012(db)
    ebook = EBook(title="E", filename="e.epub", file_path="/books/e.epub")
    audio = AudioBook(title="A", filename="a.m4b", file_path="/audio/a.m4b")
    db.add_all([ebook, audio])
    await db.flush()
    db.add(BookPair(ebook_id=ebook.id, audiobook_id=audio.id,
                    status=PairStatus.SYNCED))
    await db.commit()
    before = (await _ids(db, "ebooks"), await _ids(db, "audiobooks"),
              await _ids(db, "book_pairs"))

    await _run_migration(db, "upgrade")
    await _run_migration(db, "downgrade")

    left_over = set(_CREATED_INDEXES) & await _index_names(db)
    assert not left_over, f"downgrade() left: {sorted(left_over)}"
    assert "uq_book_pairs_pair" not in await _book_pairs_sql(db)
    assert (await _ids(db, "ebooks"), await _ids(db, "audiobooks"),
            await _ids(db, "book_pairs")) == before

    # And it re-applies, which is what a botched downgrade breaks.
    await _run_migration(db, "upgrade")
    assert not (set(_CREATED_INDEXES) - await _index_names(db))
