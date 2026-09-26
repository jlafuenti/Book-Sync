"""
Migration 0026's re-homing of orphaned positions, exercised without Postgres
(issue #720).

Before #720 a write addressed to a paired book's own scope made a standalone
record next to the pair's. When that record was the reader's only position for
the book, opening the pair found nothing and started at the beginning. 0026
moves each such orphan onto its pair: the same row, with `book_pair_id` set and
the media ids cleared, and its `user_progress` projection stamped with the pair.

It leaves alone a standalone row that sits beside a pair record (the pair
record is what readers already open), a row on an unpaired book, and a row that
also names a medium outside the pair (an unpair's demotion output, which still
speaks for that other medium).

The same migration against real Postgres is in `tests/test_migrations_postgres.py`.
"""

import importlib.util
import os
from datetime import datetime

from sqlalchemy import select

from models.bookmark import Bookmark, BookmarkSource
from models.progress import ProgressType, UserProgress
from tests.factories import ensure_users, make_audiobook, make_book_pair, make_ebook

_MIGRATION = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "alembic", "versions", "0026_fold_orphan_positions.py",
)

EARLY = datetime(2026, 9, 1, 12, 0, 0)
LATE = datetime(2026, 9, 2, 12, 0, 0)


def _load_migration():
    spec = importlib.util.spec_from_file_location("migration_0026", _MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def _upgrade(db):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    migration = _load_migration()

    def _apply(connection):
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()

    await db.run_sync(lambda session: _apply(session.connection()))
    await db.commit()
    db.expire_all()


async def _add(db, *rows):
    db.add_all(rows)
    await db.commit()
    return rows[0] if len(rows) == 1 else rows


async def _get(db, bookmark_id):
    return (await db.execute(select(Bookmark).where(Bookmark.id == bookmark_id))).scalar_one()


async def _progress(db, user_id):
    return (await db.execute(
        select(UserProgress).where(UserProgress.user_id == user_id)
    )).scalars().all()


async def test_an_orphan_on_a_paired_audiobook_moves_onto_the_pair(db):
    await ensure_users(db, 1)
    pair = await make_book_pair(db)
    orphan, progress = await _add(
        db,
        Bookmark(user_id=1, audiobook_id=pair.audiobook_id, source=BookmarkSource.AUDIOBOOK,
                 audio_position_ms=600_000, updated_at=EARLY, captured_at=EARLY,
                 device_id="phone"),
        UserProgress(user_id=1, media_type=ProgressType.AUDIOBOOK,
                     audiobook_id=pair.audiobook_id, audio_position_ms=600_000,
                     updated_at=EARLY, captured_at=EARLY),
    )
    orphan_id, progress_id, pair_id = orphan.id, progress.id, pair.id

    await _upgrade(db)

    row = await _get(db, orphan_id)
    assert (row.book_pair_id, row.ebook_id, row.audiobook_id) == (pair_id, None, None)
    assert row.audio_position_ms == 600_000
    assert row.device_id == "phone"
    assert row.updated_at == EARLY, "a re-home is not a read; the timestamps stay"
    assert row.captured_at == EARLY
    [prog] = await _progress(db, 1)
    assert prog.id == progress_id
    assert prog.book_pair_id == pair_id


async def test_an_orphan_on_a_paired_ebook_moves_onto_the_pair(db):
    await ensure_users(db, 1)
    pair = await make_book_pair(db)
    orphan = await _add(db, Bookmark(
        user_id=1, ebook_id=pair.ebook_id, source=BookmarkSource.EBOOK,
        epub_chapter=4, epub_sentence_index=7, updated_at=EARLY, captured_at=EARLY))
    orphan_id, pair_id = orphan.id, pair.id

    await _upgrade(db)

    row = await _get(db, orphan_id)
    assert (row.book_pair_id, row.ebook_id, row.audiobook_id) == (pair_id, None, None)
    assert (row.epub_chapter, row.epub_sentence_index) == (4, 7)


async def test_a_row_beside_the_pair_record_is_left_alone(db):
    await ensure_users(db, 1)
    pair = await make_book_pair(db)
    pair_row, standalone = await _add(
        db,
        Bookmark(user_id=1, book_pair_id=pair.id, source=BookmarkSource.EBOOK,
                 epub_chapter=2, updated_at=EARLY, captured_at=EARLY),
        Bookmark(user_id=1, ebook_id=pair.ebook_id, source=BookmarkSource.EBOOK,
                 epub_chapter=9, updated_at=LATE, captured_at=LATE),
    )
    pair_row_id, standalone_id, ebook_id = pair_row.id, standalone.id, pair.ebook_id

    await _upgrade(db)

    assert (await _get(db, pair_row_id)).epub_chapter == 2
    row = await _get(db, standalone_id)
    assert (row.book_pair_id, row.ebook_id) == (None, ebook_id)


async def test_another_users_pair_record_does_not_block_the_move(db):
    await ensure_users(db, 1, 2)
    pair = await make_book_pair(db)
    _, orphan = await _add(
        db,
        Bookmark(user_id=2, book_pair_id=pair.id, source=BookmarkSource.EBOOK,
                 updated_at=EARLY, captured_at=EARLY),
        Bookmark(user_id=1, ebook_id=pair.ebook_id, source=BookmarkSource.EBOOK,
                 updated_at=EARLY, captured_at=EARLY),
    )
    orphan_id, pair_id = orphan.id, pair.id

    await _upgrade(db)

    assert (await _get(db, orphan_id)).book_pair_id == pair_id


async def test_a_position_on_an_unpaired_book_is_left_alone(db):
    await ensure_users(db, 1)
    ab = await make_audiobook(db, title="Loose")
    row = await _add(db, Bookmark(
        user_id=1, audiobook_id=ab.id, source=BookmarkSource.AUDIOBOOK,
        updated_at=EARLY, captured_at=EARLY))
    row_id, ab_id = row.id, ab.id

    await _upgrade(db)

    row = await _get(db, row_id)
    assert (row.book_pair_id, row.audiobook_id) == (None, ab_id)


async def test_a_row_naming_a_medium_outside_the_pair_is_left_alone(db):
    """An unpair demotes the pair record onto both media. If the ebook is later
    paired with a different audiobook, that row still speaks for the old
    audiobook; folding it would drop that position and pin its audio offset on
    the wrong recording."""
    await ensure_users(db, 1)
    pair = await make_book_pair(db)
    old_ab = await make_audiobook(db, title="Previous recording")
    row = await _add(db, Bookmark(
        user_id=1, ebook_id=pair.ebook_id, audiobook_id=old_ab.id,
        source=BookmarkSource.EBOOK, audio_position_ms=42_000,
        updated_at=EARLY, captured_at=EARLY))
    row_id, ebook_id, old_ab_id = row.id, pair.ebook_id, old_ab.id

    await _upgrade(db)

    row = await _get(db, row_id)
    assert (row.book_pair_id, row.ebook_id, row.audiobook_id) == (None, ebook_id, old_ab_id)


async def test_two_orphans_for_one_pair_the_newer_takes_the_pair(db):
    """Only one record fits the (user, pair) slot. The newer one is the
    reader's latest place; the older stays where it is."""
    await ensure_users(db, 1)
    pair = await make_book_pair(db)
    older, newer = await _add(
        db,
        Bookmark(user_id=1, ebook_id=pair.ebook_id, source=BookmarkSource.EBOOK,
                 epub_chapter=1, updated_at=EARLY, captured_at=EARLY),
        Bookmark(user_id=1, audiobook_id=pair.audiobook_id, source=BookmarkSource.AUDIOBOOK,
                 audio_position_ms=900_000, updated_at=LATE, captured_at=LATE),
    )
    older_id, newer_id, pair_id, ebook_id = older.id, newer.id, pair.id, pair.ebook_id

    await _upgrade(db)

    assert (await _get(db, newer_id)).book_pair_id == pair_id
    row = await _get(db, older_id)
    assert (row.book_pair_id, row.ebook_id) == (None, ebook_id)


async def test_running_it_twice_changes_nothing(db):
    await ensure_users(db, 1)
    pair = await make_book_pair(db)
    other = await make_ebook(db, title="Unpaired")
    orphan, loose = await _add(
        db,
        Bookmark(user_id=1, ebook_id=pair.ebook_id, source=BookmarkSource.EBOOK,
                 updated_at=EARLY, captured_at=EARLY),
        Bookmark(user_id=1, ebook_id=other.id, source=BookmarkSource.EBOOK,
                 updated_at=EARLY, captured_at=EARLY),
    )
    orphan_id, loose_id, pair_id, other_id = orphan.id, loose.id, pair.id, other.id

    await _upgrade(db)
    await _upgrade(db)

    assert (await _get(db, orphan_id)).book_pair_id == pair_id
    assert (await _get(db, loose_id)).ebook_id == other_id
