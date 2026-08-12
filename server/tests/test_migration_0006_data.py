"""
Migration 0006's dedupe step, exercised without Postgres (issue #64).

The DDL half — creating the partial unique indexes — needs a real Postgres and
is covered by the migrations CI job. The *data* half is what runs against a
production database that already carries duplicates, and it is plain portable
SQL, so it is tested here.

Three things have to hold:

1. Exactly one row survives per (user, media), and it is the newest — the row
   `latest_progress_row` was already serving, so nothing visibly moves.
2. The survivor keeps a CFI. The newest row is not necessarily the one carrying
   the web reader's precise restore hint; dropping it silently demotes the
   reader to a coarser rung of the restore ladder.
3. Rows that were never duplicated are left completely alone.

`user_progress.epub_cfi` no longer exists at head — migration 0007 dropped it
(issue #102) — but 0006 still runs against databases that are at 0006, where it
does. So these tests rebuild the *contemporary* shape: `_add_epub_cfi_column`
puts the column back on the SQLite table the conftest built from today's ORM,
and rows are inserted with raw SQL rather than the ORM, which no longer has the
attribute. Testing 0006 against a post-0007 table would be testing a schema the
migration can never encounter.
"""

import importlib.util
import os
from datetime import datetime

from sqlalchemy import text

from models.progress import ProgressType, UserProgress
from tests.factories import make_book_pair, suspend_user_progress_uniqueness

_MIGRATION = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "alembic", "versions", "0006_user_progress_unique.py",
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("migration_0006", _MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def _add_epub_cfi_column(db):
    """Restore the pre-0007 `user_progress` shape this migration runs against."""
    await db.execute(text("ALTER TABLE user_progress ADD COLUMN epub_cfi VARCHAR(500)"))


async def _insert(db, *, user_id, media_type, updated_at, ebook_id=None,
                  audiobook_id=None, epub_chapter=None, epub_cfi=None,
                  audio_position_ms=None):
    """Insert a `user_progress` row including `epub_cfi` (raw SQL — the ORM
    model no longer carries that column)."""
    await db.execute(
        text(
            "INSERT INTO user_progress "
            "(user_id, media_type, ebook_id, audiobook_id, epub_cfi, epub_chapter, "
            " audio_position_ms, is_completed, updated_at) "
            "VALUES (:user_id, :media_type, :ebook_id, :audiobook_id, :epub_cfi, "
            "        :epub_chapter, :audio_position_ms, 0, :updated_at)"
        ),
        {
            "user_id": user_id,
            "media_type": media_type.name,
            "ebook_id": ebook_id,
            "audiobook_id": audiobook_id,
            "epub_cfi": epub_cfi,
            "epub_chapter": epub_chapter,
            "audio_position_ms": audio_position_ms,
            "updated_at": updated_at,
        },
    )


async def _dedupe(db):
    migration = _load_migration()
    await db.run_sync(lambda conn: migration.dedupe_user_progress(conn))
    await db.commit()


async def _rows(db, **where):
    clause = " AND ".join(f"{k} = :{k}" for k in where) or "1=1"
    return (await db.execute(
        text(f"SELECT id, epub_chapter, epub_cfi FROM user_progress "
             f"WHERE {clause} ORDER BY id"), where
    )).all()


async def test_only_the_newest_duplicate_survives(db, make_user):
    """Production carried two rows for the same standalone ebook, 3ms apart."""
    user = await make_user(username="reader")
    pair = await make_book_pair(db)

    await suspend_user_progress_uniqueness(db)
    await _add_epub_cfi_column(db)
    await _insert(db, user_id=user.id, media_type=ProgressType.EBOOK,
                  ebook_id=pair.ebook_id, epub_chapter=3,
                  updated_at=datetime(2026, 7, 5, 22, 19, 38))
    await _insert(db, user_id=user.id, media_type=ProgressType.EBOOK,
                  ebook_id=pair.ebook_id, epub_chapter=9,
                  updated_at=datetime(2026, 7, 5, 22, 19, 39))
    await db.commit()

    await _dedupe(db)

    rows = await _rows(db, ebook_id=pair.ebook_id)
    assert len(rows) == 1
    assert rows[0].epub_chapter == 9


async def test_identical_updated_at_is_broken_by_id(db, make_user):
    """Same timestamp on both rows must still collapse to one, not zero or two."""
    user = await make_user(username="reader")
    pair = await make_book_pair(db)
    stamp = datetime(2026, 7, 5, 22, 19, 38)

    await suspend_user_progress_uniqueness(db)
    await _add_epub_cfi_column(db)
    db.add_all([
        UserProgress(user_id=user.id, media_type=ProgressType.AUDIOBOOK,
                     audiobook_id=pair.audiobook_id, audio_position_ms=1000,
                     is_completed=False, updated_at=stamp),
        UserProgress(user_id=user.id, media_type=ProgressType.AUDIOBOOK,
                     audiobook_id=pair.audiobook_id, audio_position_ms=2000,
                     is_completed=False, updated_at=stamp),
    ])
    await db.commit()

    await _dedupe(db)

    rows = (await db.execute(text(
        "SELECT audio_position_ms FROM user_progress WHERE audiobook_id = :i"
    ), {"i": pair.audiobook_id})).all()
    assert len(rows) == 1
    assert rows[0].audio_position_ms == 2000  # higher id wins the tie


async def test_a_cfi_on_the_losing_row_is_salvaged(db, make_user):
    """The newest row is not necessarily the one holding the web reader's CFI."""
    user = await make_user(username="reader")
    pair = await make_book_pair(db)
    cfi = "epubcfi(/6/4!/4/2/2/1:12)"

    await suspend_user_progress_uniqueness(db)
    await _add_epub_cfi_column(db)
    await _insert(db, user_id=user.id, media_type=ProgressType.EBOOK,
                  ebook_id=pair.ebook_id, epub_chapter=3, epub_cfi=cfi,
                  updated_at=datetime(2026, 7, 5, 22, 19, 38))
    await _insert(db, user_id=user.id, media_type=ProgressType.EBOOK,
                  ebook_id=pair.ebook_id, epub_chapter=9, epub_cfi=None,
                  updated_at=datetime(2026, 7, 5, 22, 19, 39))
    await db.commit()

    await _dedupe(db)

    rows = await _rows(db, ebook_id=pair.ebook_id)
    assert len(rows) == 1
    assert rows[0].epub_chapter == 9
    assert rows[0].epub_cfi == cfi


async def test_the_survivors_own_cfi_is_not_overwritten(db, make_user):
    """Salvage only fills a hole; a newer CFI must win over an older one."""
    user = await make_user(username="reader")
    pair = await make_book_pair(db)

    await suspend_user_progress_uniqueness(db)
    await _add_epub_cfi_column(db)
    await _insert(db, user_id=user.id, media_type=ProgressType.EBOOK,
                  ebook_id=pair.ebook_id, epub_cfi="epubcfi(/old)",
                  updated_at=datetime(2026, 7, 5, 22, 19, 38))
    await _insert(db, user_id=user.id, media_type=ProgressType.EBOOK,
                  ebook_id=pair.ebook_id, epub_cfi="epubcfi(/new)",
                  updated_at=datetime(2026, 7, 5, 22, 19, 39))
    await db.commit()

    await _dedupe(db)

    rows = await _rows(db, ebook_id=pair.ebook_id)
    assert len(rows) == 1
    assert rows[0].epub_cfi == "epubcfi(/new)"


async def test_unduplicated_rows_are_untouched(db, make_user):
    """Two users reading the same book are not duplicates of each other."""
    alice = await make_user(username="alice")
    bob = await make_user(username="bob")
    pair = await make_book_pair(db)

    await _add_epub_cfi_column(db)
    await _insert(db, user_id=alice.id, media_type=ProgressType.EBOOK,
                  ebook_id=pair.ebook_id, epub_chapter=1, epub_cfi="epubcfi(/a)",
                  updated_at=datetime(2026, 7, 5, 22, 19, 38))
    await _insert(db, user_id=bob.id, media_type=ProgressType.EBOOK,
                  ebook_id=pair.ebook_id, epub_chapter=2, epub_cfi=None,
                  updated_at=datetime(2026, 7, 5, 22, 19, 39))
    await _insert(db, user_id=alice.id, media_type=ProgressType.AUDIOBOOK,
                  audiobook_id=pair.audiobook_id, audio_position_ms=500,
                  updated_at=datetime(2026, 7, 5, 22, 19, 40))
    await db.commit()

    await _dedupe(db)

    assert len(await _rows(db)) == 3
    # Bob's NULL CFI must not have been filled from Alice's row.
    bobs = await _rows(db, user_id=bob.id)
    assert bobs[0].epub_cfi is None
