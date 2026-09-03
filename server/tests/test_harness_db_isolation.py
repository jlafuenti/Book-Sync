"""
The test harness's own SQLite file must be private to this pytest run.

The bug this pins: `conftest.py` used a fixed `<tempdir>/booksync_test.db` for
every run on the machine. The autouse `_fresh_schema` fixture drops and recreates
the whole schema before *each test*, so two concurrent runs — trivially easy with
git worktrees, one suite per branch — tore down each other's tables mid-test. The
symptom is a storm of `no such table: <x>` and `table <x> already exists` errors
in files that have nothing to do with the change under test, which reads like a
real regression and isn't one. A crashed run could also leave the shared file
locked on Windows, breaking every later run until the handle was released.
"""

import os
import re
import tempfile


def test_database_url_points_at_sqlite_in_the_temp_dir():
    url = os.environ["DATABASE_URL"]
    assert url.startswith("sqlite+aiosqlite:///")
    assert tempfile.gettempdir().replace("\\", "/") in url


def test_test_db_file_is_private_to_this_process():
    # The whole point: the filename must carry something unique to this run, so a
    # second pytest process gets its own file instead of sharing this one.
    url = os.environ["DATABASE_URL"]
    assert str(os.getpid()) in url, (
        f"test DB path {url!r} is not process-specific — a concurrent pytest run "
        "would share it and drop this run's tables mid-test"
    )


def test_test_db_filename_is_not_the_old_shared_name():
    url = os.environ["DATABASE_URL"]
    assert not re.search(r"/booksync_test\.db$", url), (
        "test DB is back on the fixed shared path that caused cross-run collisions"
    )


async def test_sqlite_harness_enforces_foreign_keys(db):
    """SQLite defaults to `PRAGMA foreign_keys=OFF`; conftest turns it on.

    Without it no cascade or FK behaviour in the schema is exercised by CI at
    all — the harness happily deleted a user and left orphan `user_progress`
    rows behind, while production (Postgres, which always enforces) returned a
    500 (issue #198).
    """
    from sqlalchemy import text

    assert (await db.execute(text("PRAGMA foreign_keys"))).scalar() == 1
