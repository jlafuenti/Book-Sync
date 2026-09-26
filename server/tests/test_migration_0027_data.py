"""
Migration 0027's repair of false "last read" dates, exercised without Postgres
(issue #726).

Before the #679 fix, a server-side rewrite (a realign's remap, an unpair, a batch
job) bumped `updated_at` on bookmarks with no `captured_at`, and migration 0024
then copied `updated_at` into `captured_at`. The rewrite's time became the
position's "last read", so Next up listed series nobody had opened and Continue
Reading's order was scrambled.

0027 finds those rows by the backfill's signature — no `device_id`,
`captured_at` exactly equal to `updated_at`, dated on or after the day clients
began sending both (2026-07-20) — and moves `captured_at` back to the latest
real evidence: a `bookmark_logs` entry or `synced_at` before the stamp, else the
account's creation time. `updated_at` stays, which is what the server's echo
guard (`position_service.echoes_server_stamp`) keys on. The projection rows
carrying the same false stamp move with their bookmark.

The same migration against real Postgres is in `tests/test_migrations_postgres.py`.
"""

import importlib.util
import os
from datetime import datetime

from sqlalchemy import select

from models.bookmark import Bookmark, BookmarkLog, BookmarkSource
from models.progress import ProgressType, UserProgress
from models.user import User
from tests.factories import ensure_users, make_book_pair, make_ebook

_MIGRATION = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "alembic", "versions", "0027_false_capture_dates.py",
)

SIGNED_UP = datetime(2026, 4, 23, 2, 15, 3)
STAMP = datetime(2026, 9, 21, 13, 7, 23, 615241)
PROJ_STAMP = datetime(2026, 9, 21, 13, 7, 23, 620216)


def _load_migration():
    spec = importlib.util.spec_from_file_location("migration_0027", _MIGRATION)
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


async def _reader(db, user_id=1):
    await ensure_users(db, user_id)
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one()
    user.created_at = SIGNED_UP
    await db.commit()
    return user_id


async def _stamped_pair_record(db, user_id, stamp=STAMP, **fields):
    """A pair record and its two projection rows as a realign + 0024 left them."""
    pair = await make_book_pair(db)
    bookmark = Bookmark(user_id=user_id, book_pair_id=pair.id, source=BookmarkSource.EBOOK,
                        epub_chapter=2, audio_position_ms=240,
                        updated_at=stamp, captured_at=stamp, **fields)
    db.add_all([
        bookmark,
        UserProgress(user_id=user_id, media_type=ProgressType.EBOOK, ebook_id=pair.ebook_id,
                     book_pair_id=pair.id, epub_chapter=2,
                     updated_at=PROJ_STAMP, captured_at=PROJ_STAMP),
        UserProgress(user_id=user_id, media_type=ProgressType.AUDIOBOOK,
                     audiobook_id=pair.audiobook_id, book_pair_id=pair.id,
                     audio_position_ms=240, updated_at=PROJ_STAMP, captured_at=PROJ_STAMP),
    ])
    await db.commit()
    return bookmark.id, pair.id


async def _bookmark(db, bookmark_id):
    return (await db.execute(select(Bookmark).where(Bookmark.id == bookmark_id))).scalar_one()


async def _projection(db, user_id):
    return (await db.execute(
        select(UserProgress).where(UserProgress.user_id == user_id)
    )).scalars().all()


async def test_a_false_stamp_goes_back_to_the_account_creation(db):
    user_id = await _reader(db)
    bookmark_id, _ = await _stamped_pair_record(db, user_id)

    await _upgrade(db)

    row = await _bookmark(db, bookmark_id)
    assert row.captured_at == SIGNED_UP
    assert row.updated_at == STAMP, "updated_at stays: the echo guard keys on it"
    assert row.epub_chapter == 2
    assert [p.captured_at for p in await _projection(db, user_id)] == [SIGNED_UP, SIGNED_UP]


async def test_the_latest_history_entry_before_the_stamp_wins(db):
    user_id = await _reader(db)
    bookmark_id, _ = await _stamped_pair_record(db, user_id)
    moved = datetime(2026, 5, 2, 20, 0, 0)
    db.add_all([
        BookmarkLog(bookmark_id=bookmark_id, source=BookmarkSource.EBOOK,
                    changed_at=datetime(2026, 4, 30, 9, 0, 0)),
        BookmarkLog(bookmark_id=bookmark_id, source=BookmarkSource.EBOOK, changed_at=moved),
    ])
    await db.commit()

    await _upgrade(db)

    assert (await _bookmark(db, bookmark_id)).captured_at == moved


async def test_a_sync_time_before_the_stamp_counts_as_evidence(db):
    user_id = await _reader(db)
    synced = datetime(2026, 4, 23, 3, 50, 1)
    bookmark_id, _ = await _stamped_pair_record(db, user_id, synced_at=synced)

    await _upgrade(db)

    assert (await _bookmark(db, bookmark_id)).captured_at == synced


async def test_a_standalone_record_and_its_projection_are_repaired(db):
    user_id = await _reader(db)
    ebook = await make_ebook(db, title="Loose")
    bookmark = Bookmark(user_id=user_id, ebook_id=ebook.id, source=BookmarkSource.EBOOK,
                        epub_chapter=1, updated_at=STAMP, captured_at=STAMP)
    db.add_all([
        bookmark,
        UserProgress(user_id=user_id, media_type=ProgressType.EBOOK, ebook_id=ebook.id,
                     epub_chapter=1, updated_at=STAMP, captured_at=STAMP),
    ])
    await db.commit()
    bookmark_id = bookmark.id

    await _upgrade(db)

    assert (await _bookmark(db, bookmark_id)).captured_at == SIGNED_UP
    [prog] = await _projection(db, user_id)
    assert prog.captured_at == SIGNED_UP


async def test_a_record_a_device_wrote_is_left_alone(db):
    user_id = await _reader(db)
    bookmark_id, _ = await _stamped_pair_record(db, user_id, device_id="phone")

    await _upgrade(db)

    assert (await _bookmark(db, bookmark_id)).captured_at == STAMP


async def test_a_capture_that_differs_from_its_update_is_left_alone(db):
    user_id = await _reader(db)
    pair = await make_book_pair(db)
    captured = datetime(2026, 9, 21, 13, 7, 20)
    bookmark = Bookmark(user_id=user_id, book_pair_id=pair.id, source=BookmarkSource.EBOOK,
                        updated_at=STAMP, captured_at=captured)
    db.add(bookmark)
    await db.commit()
    bookmark_id = bookmark.id

    await _upgrade(db)

    assert (await _bookmark(db, bookmark_id)).captured_at == captured


async def test_a_stamp_from_before_clients_sent_capture_times_is_left_alone(db):
    """Before 2026-07-20 a client write carried neither a device nor a capture
    time, so a backfilled date from then may be a real read."""
    user_id = await _reader(db)
    early = datetime(2026, 7, 6, 1, 1, 26)
    bookmark_id, _ = await _stamped_pair_record(db, user_id, stamp=early)

    await _upgrade(db)

    assert (await _bookmark(db, bookmark_id)).captured_at == early


async def test_a_history_entry_at_the_stamp_marks_a_real_move(db):
    user_id = await _reader(db)
    bookmark_id, _ = await _stamped_pair_record(db, user_id)
    db.add(BookmarkLog(bookmark_id=bookmark_id, source=BookmarkSource.EBOOK, changed_at=STAMP))
    await db.commit()

    await _upgrade(db)

    assert (await _bookmark(db, bookmark_id)).captured_at == STAMP


async def test_running_it_twice_changes_nothing(db):
    user_id = await _reader(db)
    bookmark_id, _ = await _stamped_pair_record(db, user_id)

    await _upgrade(db)
    await _upgrade(db)

    row = await _bookmark(db, bookmark_id)
    assert (row.captured_at, row.updated_at) == (SIGNED_UP, STAMP)
