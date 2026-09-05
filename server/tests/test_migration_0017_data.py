"""
Migration 0017's repair step, exercised without Postgres (issue #389).

Production predates Alembic: it was built by `create_all` and then stamped at
the baseline, so three columns the models and `0001_baseline.py` both declare
`nullable=False` are `is_nullable = YES` in the live database —
`transcription_queue.retry_count`, `users.role`, `users.must_reset_password`.
`alembic check` therefore reports three spurious `modify_nullable` operations
against production and is unusable as a health check there.

0017 is the repair. The two properties that matter are the two this file pins:

1. **It repairs a stamped database.** Given the three columns relaxed and rows
   carrying NULLs in them, `upgrade()` backfills the declared defaults (`0`,
   `'user'`, `false`) and then makes all three `NOT NULL`. The backfill has to
   come first — the `ALTER` fails on any row still holding a NULL, and such a
   row is precisely what this migration exists for.
2. **It is a no-op on a database built from the migrations.** CI's database
   already has the constraints, so running `upgrade()` against it must be
   silent rather than an error, and must not touch any row.

`downgrade()` relaxes the three back, which is both the reversibility contract
and — used here — the cheapest way to manufacture the pre-repair shape on the
SQLite harness the conftest builds from today's ORM.

The DDL half against a real Postgres (which is the database that actually
carries the drift) lives in `tests/test_migrations_postgres.py`; this is the
same split as `test_migration_0012_data.py`.
"""

import importlib.util
import os

import pytest
from sqlalchemy import text

from models.transcription_queue import TranscriptionQueueItem
from tests.factories import make_book_pair

_MIGRATION = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "alembic", "versions", "0017_repair_nullability.py",
)

# (table, column, the value the migration backfills)
_REPAIRED = (
    ("transcription_queue", "retry_count", 0),
    ("users", "role", "user"),
    ("users", "must_reset_password", False),
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("migration_0017", _MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def _run_migration(db, direction):
    """Run `upgrade()` / `downgrade()` against the SQLite session's connection.

    Alembic's `Operations.context` binds the module-level `op` proxy to a plain
    connection, so the real migration functions run — the same code path prod
    takes, only the emitted SQL differs (`batch_alter_table` is a transparent
    wrapper on Postgres and a table rebuild on SQLite).
    """
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    migration = _load_migration()

    def _apply(connection):
        context = MigrationContext.configure(connection)
        with Operations.context(context):
            getattr(migration, direction)()

    await db.run_sync(lambda session: _apply(session.connection()))
    await db.commit()


async def _nullable(db, table, column):
    """True if SQLite reports `table.column` as accepting NULL."""
    rows = (await db.execute(text(f"PRAGMA table_info({table})"))).all()
    for row in rows:
        if row.name == column:
            return not row.notnull
    raise AssertionError(f"{table}.{column} does not exist")


async def _assert_constrained(db, constrained=True):
    for table, column, _ in _REPAIRED:
        assert await _nullable(db, table, column) is not constrained, (
            f"{table}.{column}: expected nullable={not constrained}"
        )


async def _relax(db):
    """Put the schema into the shape production is in: the three columns
    nullable. `downgrade()` is exactly that operation, so use it."""
    await _run_migration(db, "downgrade")
    await _assert_constrained(db, constrained=False)


async def _seed(db, make_user, *, nulls):
    """A user row and a queue row. With `nulls=True` the three columns are
    NULLed afterwards — the state a stamped database is in.

    The NULLing is raw SQL because the models declare these columns
    non-optional with Python-side defaults: there is no way to write a NULL
    through the ORM, which is the whole reason the drift is invisible in CI.
    """
    user = await make_user(username="stamped", role="admin")
    pair = await make_book_pair(db)
    item = TranscriptionQueueItem(book_pair_id=pair.id, status="pending",
                                  retry_count=4)
    db.add(item)
    await db.commit()
    await db.refresh(item)

    if nulls:
        await db.execute(text(
            "UPDATE users SET role = NULL, must_reset_password = NULL "
            "WHERE id = :i"
        ), {"i": user.id})
        await db.execute(text(
            "UPDATE transcription_queue SET retry_count = NULL WHERE id = :i"
        ), {"i": item.id})
        await db.commit()

    return user.id, item.id


async def _snapshot(db, user_id, item_id):
    """The values the migration must not disturb on already-correct rows."""
    user = (await db.execute(text(
        "SELECT username, role, must_reset_password FROM users WHERE id = :i"
    ), {"i": user_id})).one()
    queue = (await db.execute(text(
        "SELECT status, retry_count FROM transcription_queue WHERE id = :i"
    ), {"i": item_id})).one()
    return (
        (user.username, user.role, bool(user.must_reset_password)),
        (queue.status, queue.retry_count),
    )


async def test_upgrade_backfills_the_nulls_and_constrains_the_columns(db, make_user):
    """The production case: relaxed columns, NULL rows, one `upgrade()`."""
    await _relax(db)
    user_id, item_id = await _seed(db, make_user, nulls=True)

    await _run_migration(db, "upgrade")

    await _assert_constrained(db)
    ids = {"users": user_id, "transcription_queue": item_id}
    for table, column, expected in _REPAIRED:
        value = (await db.execute(
            text(f"SELECT {column} FROM {table} WHERE id = :i"),
            {"i": ids[table]},
        )).scalar_one()
        # SQLite stores booleans as 0/1, so compare the bool on truthiness.
        actual = bool(value) if isinstance(expected, bool) else value
        assert actual == expected, f"{table}.{column} = {value!r}"


async def test_upgrade_rejects_a_new_null_afterwards(db, make_user):
    """The constraint has to actually bite. A rebuilt table whose `NOT NULL`
    did not survive the copy would pass every assertion above and still let
    production drift straight back."""
    await _relax(db)
    user_id, _ = await _seed(db, make_user, nulls=True)

    await _run_migration(db, "upgrade")

    with pytest.raises(Exception):
        await db.execute(
            text("UPDATE users SET role = NULL WHERE id = :i"), {"i": user_id}
        )
        await db.commit()
    await db.rollback()


async def test_upgrade_is_a_no_op_on_a_database_built_from_the_migrations(db, make_user):
    """CI's database, and every install created after this lands. The three
    columns are already `NOT NULL`, so `upgrade()` must be silent rather than
    an error, and must not rewrite a row."""
    user_id, item_id = await _seed(db, make_user, nulls=False)
    before = await _snapshot(db, user_id, item_id)
    await _assert_constrained(db)

    await _run_migration(db, "upgrade")

    await _assert_constrained(db)
    assert await _snapshot(db, user_id, item_id) == before


async def test_downgrade_relaxes_and_upgrade_re_applies(db, make_user):
    """Reversibility. Nothing here deletes data, so a downgrade that dropped
    and re-added the columns — or a rebuild that lost the rows — would be
    wrong, and the migration must still re-apply on top of its own downgrade.
    """
    user_id, item_id = await _seed(db, make_user, nulls=False)
    before = await _snapshot(db, user_id, item_id)

    await _run_migration(db, "downgrade")
    await _assert_constrained(db, constrained=False)
    assert await _snapshot(db, user_id, item_id) == before

    await _run_migration(db, "upgrade")
    await _assert_constrained(db)
    assert await _snapshot(db, user_id, item_id) == before
