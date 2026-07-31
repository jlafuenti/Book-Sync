"""Alembic migration tests against real Postgres (issue #53).

Schema management moved from a boot-time ``ALTER TABLE`` block in
``database.init_db()`` to Alembic migrations. These tests prove, against a real
Postgres instance, that:

* ``alembic upgrade head`` builds the full schema on an empty database (incl. the
  unique indexes and native enum types the ORM models declare);
* the baseline is reversible — ``downgrade base`` then ``upgrade head`` again
  rebuilds cleanly (this is what catches enum types not being dropped);
* the migrations and the ORM models stay in sync — ``alembic check`` reports no
  outstanding autogenerate diff (the drift gate).

Only runs when ``RUN_PG_TESTS=1`` and ``DATABASE_URL`` points at Postgres — the
CI ``migrations`` job supplies both. Skipped everywhere else (incl. the default
SQLite job), because the migrations use Postgres-only DDL (JSONB, native enums).
"""

import os

import pytest
from sqlalchemy import create_engine, inspect, text

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_PG_TESTS") != "1",
    reason="requires RUN_PG_TESTS=1 and a Postgres DATABASE_URL",
)

_SERVER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Every table the baseline migration must create.
_EXPECTED_TABLES = {
    "ebooks", "audiobooks", "book_pairs", "users", "audit_logs",
    "audio_transcripts", "bookmarks", "bookmark_logs", "sync_maps",
    "sync_points", "transcription_queue", "user_progress", "system_settings",
    "import_sources", "import_source_credentials", "import_jobs",
    "library_check_results",
}


def _alembic_config():
    """Alembic Config with absolute paths, so it works regardless of cwd."""
    from alembic.config import Config

    cfg = Config(os.path.join(_SERVER_DIR, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(_SERVER_DIR, "alembic"))
    return cfg


def _sync_engine():
    """A sync (psycopg2) engine on the same DB the app/env.py use, for asserts.

    Derive the URL from settings.database_url (the raw string) exactly like
    alembic/env.py does — NOT from str(database.engine.url), which masks the
    password as '***' and would make psycopg2 authenticate with a literal '***'.
    """
    from config import settings

    sync_url = settings.database_url.replace("+asyncpg", "+psycopg2")
    return create_engine(sync_url)


@pytest.fixture(autouse=True)
def _reset_to_base():
    """Each test starts and ends on an empty database (revision base)."""
    from alembic import command

    command.downgrade(_alembic_config(), "base")
    yield
    command.downgrade(_alembic_config(), "base")


def test_upgrade_head_builds_full_schema():
    from alembic import command

    command.upgrade(_alembic_config(), "head")

    engine = _sync_engine()
    tables = set(inspect(engine).get_table_names())
    assert _EXPECTED_TABLES <= tables, f"missing: {_EXPECTED_TABLES - tables}"

    # The unique index on users.username must actually enforce uniqueness — this
    # is the correctness bit that CreateTableOp.from_table would silently drop.
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO users (username, email, hashed_password, is_admin, is_active, created_at) "
            "VALUES ('dup', 'a@example.com', 'x', false, true, now())"
        ))
    with pytest.raises(Exception):
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO users (username, email, hashed_password, is_admin, is_active, created_at) "
                "VALUES ('dup', 'b@example.com', 'x', false, true, now())"
            ))


async def test_bootstrap_superadmin_works_on_migrated_schema():
    from alembic import command
    from sqlalchemy import select
    from models.user import User

    command.upgrade(_alembic_config(), "head")

    import database

    # Idempotent: two calls must not create a second superadmin.
    await database.bootstrap_superadmin()
    await database.bootstrap_superadmin()

    async with database.async_session() as session:
        supers = (await session.execute(
            select(User).where(User.role == "superadmin")
        )).scalars().all()
        assert len(supers) == 1


def test_downgrade_then_upgrade_is_clean():
    """Reversibility + idempotency: a full round-trip must rebuild cleanly.

    If the baseline forgot to drop its native enum types on downgrade, the second
    ``upgrade head`` would fail with "type ... already exists".
    """
    from alembic import command

    cfg = _alembic_config()
    engine = _sync_engine()

    command.upgrade(cfg, "head")
    assert _EXPECTED_TABLES <= set(inspect(engine).get_table_names())

    command.downgrade(cfg, "base")
    remaining = set(inspect(engine).get_table_names())
    assert not (_EXPECTED_TABLES & remaining), f"leftover after downgrade: {_EXPECTED_TABLES & remaining}"

    command.upgrade(cfg, "head")
    assert _EXPECTED_TABLES <= set(inspect(engine).get_table_names())


def test_existing_bookmarks_survive_locator_audio_ms_migration():
    """0003 adds ``bookmarks.locator_audio_ms``. A bookmark written before it
    must still be readable afterwards, with a NULL anchor (issue #40) — it
    simply doesn't qualify for the locator fast path until its next write.
    """
    from alembic import command

    cfg = _alembic_config()
    engine = _sync_engine()

    command.downgrade(cfg, "base")
    command.upgrade(cfg, "0002_conflict_resolution")

    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO users (username, email, hashed_password, is_admin, is_active, created_at) "
            "VALUES ('legacy', 'legacy@example.com', 'x', false, true, now())"
        ))
        conn.execute(text(
            "INSERT INTO ebooks (title, filename, file_path, format, uploaded_at) "
            "VALUES ('E', 'e.epub', '/x/e.epub', 'epub', now())"
        ))
        conn.execute(text(
            "INSERT INTO audiobooks (title, filename, file_path, format, uploaded_at) "
            "VALUES ('A', 'a.m4b', '/x/a.m4b', 'm4b', now())"
        ))
        conn.execute(text(
            "INSERT INTO book_pairs (ebook_id, audiobook_id, status) "
            "SELECT (SELECT id FROM ebooks LIMIT 1), (SELECT id FROM audiobooks LIMIT 1), 'UNMATCHED'"
        ))
        conn.execute(text(
            "INSERT INTO bookmarks (user_id, book_pair_id, source, epub_chapter, "
            "epub_sentence_index, epub_locator, updated_at) "
            "SELECT (SELECT id FROM users WHERE username='legacy'), "
            "(SELECT id FROM book_pairs LIMIT 1), 'EBOOK', 3, 7, '{\"href\":\"/ch3\"}', now()"
        ))

    command.upgrade(cfg, "head")

    with engine.begin() as conn:
        row = conn.execute(text(
            "SELECT epub_chapter, epub_locator, locator_audio_ms FROM bookmarks"
        )).one()
    assert row.epub_chapter == 3
    assert row.epub_locator == '{"href":"/ch3"}'
    assert row.locator_audio_ms is None


def test_no_model_migration_drift():
    """The migrations and the ORM models must stay in sync (the drift gate).

    ``alembic check`` autogenerates against the live (migrated) DB and raises if
    the models imply any schema change not captured by a migration.
    """
    from alembic import command

    cfg = _alembic_config()
    command.upgrade(cfg, "head")
    # Raises alembic.util.exc.AutogenerateDiffsDetected on drift → test fails.
    command.check(cfg)
