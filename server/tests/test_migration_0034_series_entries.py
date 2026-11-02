"""
Migration 0034 moves the scalar `series`/`series_index` columns into child
tables (`ebook_series`, `audiobook_series`) and replaces the `''` "user cleared
it" sentinel with a boolean `series_cleared` column (issue #905).
"""

import importlib.util
import os

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from models.book import AudioBook, AudioBookSeries, EBook, EBookSeries

_MIGRATION = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "alembic", "versions", "0034_series_entries.py",
)


def _load():
    spec = importlib.util.spec_from_file_location("migration_0034", _MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _legacy_schema(conn):
    for table in ("ebooks", "audiobooks"):
        conn.execute(sa.text(
            f"CREATE TABLE {table} (id INTEGER PRIMARY KEY, title VARCHAR(500), "
            "filename VARCHAR(1000), file_path VARCHAR(2000), format VARCHAR(10), "
            "uploaded_at DATETIME, series VARCHAR(500), series_index FLOAT)"
        ))
        conn.execute(sa.text(
            f"INSERT INTO {table} (id, title, series, series_index) VALUES "
            "(1, 'One', 'Axis Saga', 3.0), (2, 'Two', '', NULL), (3, 'Three', NULL, NULL), "
            "(4, 'Four', 'Axis Chronological', NULL)"
        ))


def _names(conn, table):
    return {c["name"] for c in sa.inspect(conn).get_columns(table)}


def _upgrade(m, conn):
    with Operations.context(MigrationContext.configure(conn)):
        m.upgrade()


def _downgrade(m, conn):
    with Operations.context(MigrationContext.configure(conn)):
        m.downgrade()


def test_revision_chain_and_length():
    m = _load()
    assert m.revision == "0034_series_entries"
    assert m.down_revision == "0033_word_timing"
    assert len(m.revision) <= 32


def test_models_match_the_migrated_schema():
    for model, table, fk in (
        (EBookSeries, "ebook_series", "ebook_id"),
        (AudioBookSeries, "audiobook_series", "audiobook_id"),
    ):
        assert model.__tablename__ == table
        cols = {c.name: c for c in model.__table__.columns}
        assert set(cols) == {"id", fk, "name", "series_index", "position"}
        assert cols["name"].nullable is False
        assert cols["series_index"].nullable is True
        assert cols["position"].nullable is False
        assert next(iter(cols[fk].foreign_keys)).ondelete == "CASCADE"
        assert any(ix.unique for ix in model.__table__.indexes)
    for model in (EBook, AudioBook):
        assert "series" not in model.__table__.c
        assert model.__table__.c.series_cleared.nullable is False


def test_upgrade_moves_series_and_flags_cleared_rows():
    m = _load()
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        _legacy_schema(conn)
        _upgrade(m, conn)

        for table, entries, fk in (
            ("ebooks", "ebook_series", "ebook_id"),
            ("audiobooks", "audiobook_series", "audiobook_id"),
        ):
            assert "series" not in _names(conn, table)
            assert "series_index" not in _names(conn, table)
            assert "series_cleared" in _names(conn, table)
            rows = conn.execute(sa.text(
                f"SELECT {fk}, name, series_index, position FROM {entries} ORDER BY {fk}"
            )).all()
            assert [tuple(r) for r in rows] == [
                (1, "Axis Saga", 3.0, 0),
                (4, "Axis Chronological", None, 0),
            ]
            cleared = dict(conn.execute(sa.text(
                f"SELECT id, series_cleared FROM {table}"
            )).all())
            assert {k: bool(v) for k, v in cleared.items()} == {
                1: False, 2: True, 3: False, 4: False,
            }


def test_upgrade_leaves_existing_rows_intact():
    m = _load()
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        _legacy_schema(conn)
        _upgrade(m, conn)
        titles = conn.execute(sa.text("SELECT title FROM ebooks ORDER BY id")).scalars().all()
        assert titles == ["One", "Two", "Three", "Four"]


def test_downgrade_round_trips_the_primary_series():
    m = _load()
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        _legacy_schema(conn)
        _upgrade(m, conn)
        # A second series on book 1 has no home in the old schema and is dropped.
        conn.execute(sa.text(
            "INSERT INTO ebook_series (ebook_id, name, series_index, position) "
            "VALUES (1, 'Axis Extra', 1.0, 1)"
        ))
        _downgrade(m, conn)

        assert {"series", "series_index"} <= _names(conn, "ebooks")
        assert "series_cleared" not in _names(conn, "ebooks")
        assert "ebook_series" not in sa.inspect(conn).get_table_names()
        rows = conn.execute(sa.text(
            "SELECT id, series, series_index FROM ebooks ORDER BY id"
        )).all()
        assert [tuple(r) for r in rows] == [
            (1, "Axis Saga", 3.0),
            (2, "", None),
            (3, None, None),
            (4, "Axis Chronological", None),
        ]


def test_series_cleared_keeps_its_server_default_for_raw_inserts():
    """Every other NOT NULL boolean on these tables keeps its server default
    (`acknowledged`, from the baseline), and callers that insert by raw SQL —
    the Postgres migration tests, an operator's script — name only the columns
    they know. The column must therefore default to false in the database, not
    just in the ORM."""
    m = _load()
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        _legacy_schema(conn)
        _upgrade(m, conn)
        for table in ("ebooks", "audiobooks"):
            conn.execute(sa.text(
                f"INSERT INTO {table} (title, filename, file_path, format, uploaded_at) "
                "VALUES ('Raw', 'r.bin', '/x/raw', 'bin', CURRENT_TIMESTAMP)"
            ))
            flag = conn.execute(sa.text(
                f"SELECT series_cleared FROM {table} WHERE title = 'Raw'"
            )).scalar_one()
            assert bool(flag) is False


def test_models_declare_the_same_server_default():
    for model in (EBook, AudioBook):
        assert model.__table__.c.series_cleared.server_default is not None
