"""Migration 0035 adds the per-account `next_up_dismissals` table (issue #921)."""

import importlib.util
import os

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from models.next_up_dismissal import NextUpDismissal

_MIGRATION = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "alembic", "versions", "0035_next_up_dismissals.py",
)


def _load():
    spec = importlib.util.spec_from_file_location("migration_0035", _MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(fn, conn):
    with Operations.context(MigrationContext.configure(conn)):
        fn()


def _users(conn):
    conn.execute(sa.text("CREATE TABLE users (id INTEGER PRIMARY KEY)"))


def test_revision_chain_and_length():
    m = _load()
    assert m.revision == "0035_next_up_dismissals"
    assert m.down_revision == "0034_series_entries"
    assert len(m.revision) <= 32


def test_upgrade_creates_table_and_unique_index_then_downgrade_drops_them():
    m = _load()
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        _users(conn)
        _run(m.upgrade, conn)
        insp = sa.inspect(conn)
        assert "next_up_dismissals" in insp.get_table_names()
        cols = {c["name"] for c in insp.get_columns("next_up_dismissals")}
        assert cols == {"id", "user_id", "series_key", "series", "dismissed_at"}
        idx = {i["name"]: i for i in insp.get_indexes("next_up_dismissals")}
        assert idx["ux_next_up_dismissals_user_series"]["unique"]
        assert idx["ux_next_up_dismissals_user_series"]["column_names"] == ["user_id", "series_key"]

        _run(m.downgrade, conn)
        assert "next_up_dismissals" not in sa.inspect(conn).get_table_names()


def test_model_matches_migration():
    cols = {c.name: c for c in NextUpDismissal.__table__.columns}
    assert set(cols) == {"id", "user_id", "series_key", "series", "dismissed_at"}
    assert all(not cols[n].nullable for n in ("user_id", "series_key", "series", "dismissed_at"))
    assert next(iter(cols["user_id"].foreign_keys)).ondelete == "CASCADE"
