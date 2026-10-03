"""
Migration 0032 widens `sync_points.epub_text_preview` to the whole sentence
(issue #763), exercised without Postgres.

The column was `VARCHAR(200)` and the aligner cut every sentence to fit, so
read-along marked a long sentence only up to the cut. The upgrade is a type
change only; the text stays cut until the map is realigned. The downgrade has to
cut it back to 200 characters first, or Postgres refuses the narrower type.

The same migration against real Postgres is in `tests/test_migrations_postgres.py`.
"""

import importlib.util
import os

from sqlalchemy import Text, select

from models.sync_map import SyncPoint
from tests.factories import make_book_pair, make_sync_map

_MIGRATION = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "alembic", "versions", "0032_sync_point_full_text.py",
)

LONG = " ".join(f"word{k}" for k in range(100))


def _load_migration():
    spec = importlib.util.spec_from_file_location("migration_0032", _MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def _run(db, step):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    migration = _load_migration()

    def _apply(connection):
        with Operations.context(MigrationContext.configure(connection)):
            getattr(migration, step)()

    await db.run_sync(lambda session: _apply(session.connection()))
    await db.commit()
    db.expire_all()


async def _previews(db):
    return (await db.execute(
        select(SyncPoint.epub_text_preview).order_by(SyncPoint.epub_sentence_index)
    )).scalars().all()


def test_the_model_stores_the_whole_sentence():
    column = SyncPoint.__table__.c.epub_text_preview
    assert isinstance(column.type, Text)
    assert getattr(column.type, "length", None) is None


async def test_downgrade_cuts_long_sentences_back_to_200(db):
    pair = await make_book_pair(db)
    await make_sync_map(db, pair.id, points=[
        (0, 0, 0, LONG),
        (0, 1, 5000, "a short one"),
        (0, 2, 10000, None),
    ])
    assert len(LONG) > 400

    await _run(db, "downgrade")

    assert await _previews(db) == [LONG[:200], "a short one", None]


async def test_upgrade_keeps_existing_text(db):
    pair = await make_book_pair(db)
    await make_sync_map(db, pair.id, points=[(0, 0, 0, "x" * 200), (0, 1, 5000, "short")])

    await _run(db, "upgrade")

    assert await _previews(db) == ["x" * 200, "short"]
