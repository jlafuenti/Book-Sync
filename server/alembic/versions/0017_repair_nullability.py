"""repair three columns left nullable by the pre-Alembic schema (issue #389)

Production predates Alembic. It was built by a boot-time ``create_all`` and then
stamped at ``0001_baseline``, so its schema is whatever ``create_all`` produced
at the time rather than what the baseline says. Three columns came out
``is_nullable = YES`` even though both the ORM models and ``0001_baseline.py``
declare them ``nullable=False``:

* ``transcription_queue.retry_count``  (default ``0``)
* ``users.role``                        (default ``'user'``)
* ``users.must_reset_password``         (default ``false``)

Nothing is functionally broken — the server defaults fill all three on every
insert — but ``alembic check`` reports three ``modify_nullable`` operations
against production, which makes the drift gate unusable as a production health
check and would put spurious ``ALTER COLUMN`` lines into any autogenerate run
taken against a production dump.

This migration is the repair, and it is deliberately a **no-op on any database
built by running the migrations** (CI's, and every install created from now on):
those already have the constraints, so the ``UPDATE``s match no rows and the
``ALTER``s ask for the state that is already there.

Order matters. The backfill has to precede the ``ALTER``, because a single row
still holding a NULL aborts the whole ``SET NOT NULL`` — and such a row is
exactly the case this migration exists for.

The defaults are written as **bound parameters** rather than SQL literals so the
statements stay portable: ``false`` is not a literal every SQLite build accepts,
and ``0`` is not a boolean Postgres accepts. The driver renders each side's
spelling. ``batch_alter_table`` is likewise portability, not ceremony — a
transparent wrapper on Postgres, a table rebuild on SQLite, which is what lets
``tests/test_migration_0017_data.py`` run both directions without a Postgres.

The ``downgrade`` relaxes the three back to nullable. It does not restore the
NULLs it backfilled; those values were indistinguishable from the defaults the
application was already substituting for them.

---------------------------------------------------------------------------
Checking for any *other* column with the same drift
---------------------------------------------------------------------------

These three were found by ``alembic check``, which stops being able to say
anything once this migration lands. To ask the same question directly — which
columns are nullable in the live database but ``NOT NULL`` in
``Base.metadata`` — run this on the server host. It prints one line per drifting
column and a count; a healthy database prints ``drift: 0`` and nothing else. The
same snippet, with the surrounding operator notes, is in ``docs/operations.md``
under "Schema drift on a stamped database".

    docker compose exec server python - <<'PY'
    import asyncio, importlib, pkgutil
    from sqlalchemy import text
    import models
    # Import every model module, so Base.metadata is complete. (alembic/env.py
    # keeps a hand-written list for this; the installed `alembic` package
    # shadows it on import, so walk the package instead.)
    for m in pkgutil.iter_modules(models.__path__):
        importlib.import_module(f"models.{m.name}")
    from database import Base, engine

    async def main():
        async with engine.connect() as conn:
            rows = await conn.execute(text(
                "SELECT table_name, column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND is_nullable = 'YES'"
            ))
            live_nullable = {(r.table_name, r.column_name) for r in rows}
        drift = sorted(
            (t.name, c.name)
            for t in Base.metadata.sorted_tables
            for c in t.columns
            if not c.nullable and (t.name, c.name) in live_nullable
        )
        for table, column in drift:
            print(f"{table}.{column}")
        print("drift:", len(drift))

    asyncio.run(main())
    PY

Revision ID: 0017_repair_nullability
Revises: 0016_registration_invites
Create Date: 2026-09-05

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
# NOTE: keep this at or under 32 chars — see the note in
# 0002_conflict_resolution.py (alembic_version is a VARCHAR(32)).
revision: str = "0017_repair_nullability"
down_revision: Union[str, None] = "0016_registration_invites"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# (table, column, type, the default the models declare)
_COLUMNS = (
    ("transcription_queue", "retry_count", sa.Integer(), 0),
    ("users", "role", sa.String(length=20), "user"),
    ("users", "must_reset_password", sa.Boolean(), False),
)


def _backfill() -> None:
    """Replace any NULL with the default the model would have written."""
    bind = op.get_bind()
    for table, column, _type, default in _COLUMNS:
        bind.execute(
            sa.text(f"UPDATE {table} SET {column} = :value WHERE {column} IS NULL"),
            {"value": default},
        )


def _set_nullable(nullable: bool) -> None:
    for table, column, type_, _default in _COLUMNS:
        with op.batch_alter_table(table) as batch_op:
            batch_op.alter_column(column, existing_type=type_, nullable=nullable)


def upgrade() -> None:
    _backfill()
    _set_nullable(False)


def downgrade() -> None:
    _set_nullable(True)
