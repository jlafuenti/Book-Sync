"""
Migration 0024's backfill, exercised without Postgres (issue #679).

Clients rank "last read" by `captured_at`, falling back to `updated_at` when it
is NULL. `updated_at` is bumped by server-side rewrites that are not reading —
a realign's bookmark remap, the progress projection it drives — so every row
with a NULL `captured_at` jumped to "just read" the moment its pair was
realigned. 0024 gives those rows the moment they already stood for: their
`updated_at`, copied into `captured_at` once. Rows that already carry a
`captured_at` are left alone.

The same backfill against real Postgres is in `tests/test_migrations_postgres.py`.
"""

import importlib.util
import os
from datetime import datetime

from sqlalchemy import select

from models.bookmark import Bookmark, BookmarkSource
from models.progress import ProgressType, UserProgress
from tests.factories import ensure_users, make_book_pair

_MIGRATION = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "alembic", "versions", "0024_captured_at_backfill.py",
)

OLD = datetime(2025, 3, 1, 12, 0, 0)
STAMPED = datetime(2026, 1, 2, 8, 30, 0)


def _load_migration():
    spec = importlib.util.spec_from_file_location("migration_0024", _MIGRATION)
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


async def _seed(db):
    await ensure_users(db, 1)
    unstamped_pair = await make_book_pair(db)
    stamped_pair = await make_book_pair(db)
    db.add_all([
        Bookmark(user_id=1, book_pair_id=unstamped_pair.id,
                 source=BookmarkSource.EBOOK, updated_at=OLD, captured_at=None),
        Bookmark(user_id=1, book_pair_id=stamped_pair.id,
                 source=BookmarkSource.EBOOK, updated_at=OLD, captured_at=STAMPED),
        UserProgress(user_id=1, media_type=ProgressType.EBOOK,
                     ebook_id=unstamped_pair.ebook_id, book_pair_id=unstamped_pair.id,
                     updated_at=OLD, captured_at=None),
        UserProgress(user_id=1, media_type=ProgressType.EBOOK,
                     ebook_id=stamped_pair.ebook_id, book_pair_id=stamped_pair.id,
                     updated_at=OLD, captured_at=STAMPED),
    ])
    await db.commit()
    return unstamped_pair, stamped_pair


async def _captured(db, model, pair_id):
    return (await db.execute(
        select(model.captured_at).where(model.book_pair_id == pair_id)
    )).scalar_one()


async def test_a_null_captured_at_takes_the_rows_own_updated_at(db):
    unstamped, _ = await _seed(db)

    await _upgrade(db)

    assert await _captured(db, Bookmark, unstamped.id) == OLD
    assert await _captured(db, UserProgress, unstamped.id) == OLD


async def test_an_existing_captured_at_is_left_alone(db):
    _, stamped = await _seed(db)

    await _upgrade(db)

    assert await _captured(db, Bookmark, stamped.id) == STAMPED
    assert await _captured(db, UserProgress, stamped.id) == STAMPED


async def test_running_it_twice_changes_nothing(db):
    unstamped, stamped = await _seed(db)

    await _upgrade(db)
    await _upgrade(db)

    assert await _captured(db, Bookmark, unstamped.id) == OLD
    assert await _captured(db, Bookmark, stamped.id) == STAMPED
