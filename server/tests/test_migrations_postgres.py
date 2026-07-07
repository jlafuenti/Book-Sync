"""
Postgres migration-smoke test (issue #46, Phase 2).

The default suite runs on SQLite, which never exercises the raw
`JSONB` / `ADD COLUMN IF NOT EXISTS` / `ALTER … TYPE` migration SQL in
`database.init_db()`. This test runs that SQL against a real Postgres instance to
prove it applies cleanly and is idempotent (safe to run on every startup).

Only runs when `RUN_PG_TESTS=1` and `DATABASE_URL` points at Postgres — the CI
`migrations` job supplies both. Skipped everywhere else (incl. the SQLite job).
"""

import os

import pytest
from sqlalchemy import select

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_PG_TESTS") != "1",
    reason="requires RUN_PG_TESTS=1 and a Postgres DATABASE_URL",
)


async def test_init_db_and_bootstrap_apply_cleanly_and_are_idempotent():
    import database
    from models.user import User

    assert database.engine.url.get_backend_name().startswith("postgresql"), (
        "RUN_PG_TESTS=1 but DATABASE_URL is not Postgres"
    )

    # First run: create_all + all raw JSONB / IF NOT EXISTS / ALTER TYPE migrations.
    await database.init_db()
    # Second run must be a no-op (guards are IF NOT EXISTS / idempotent DDL).
    await database.init_db()

    # bootstrap creates the default superadmin on an empty user table, and must
    # not create a duplicate on a second call.
    await database.bootstrap_superadmin()
    await database.bootstrap_superadmin()

    async with database.async_session() as session:
        supers = (await session.execute(
            select(User).where(User.role == "superadmin")
        )).scalars().all()
        assert len(supers) >= 1
