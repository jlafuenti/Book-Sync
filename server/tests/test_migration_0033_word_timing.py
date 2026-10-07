"""
Migration 0033 adds the two word-timing columns (issue #835).

`audio_transcripts.words_json` holds the worker's per-word timing and
`sync_points.word_starts` the per-ebook-token start times derived from it. Both
are nullable: every existing row predates word timing and stays NULL until its
pair is re-transcribed.
"""

import importlib.util
import os

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from models.sync_map import SyncPoint
from models.transcript import AudioTranscript

_MIGRATION = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "alembic", "versions", "0033_word_timing.py",
)


def _load():
    spec = importlib.util.spec_from_file_location("migration_0033", _MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_revision_chain_and_length():
    m = _load()
    assert m.revision == "0033_word_timing"
    assert m.down_revision == "0032_sync_point_full_text"
    assert len(m.revision) <= 32


def test_models_carry_the_columns_as_nullable_text():
    for col in (AudioTranscript.__table__.c.words_json, SyncPoint.__table__.c.word_starts):
        assert col.nullable is True
        assert isinstance(col.type, sa.Text)


def test_upgrade_adds_and_downgrade_drops_the_columns():
    m = _load()
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(sa.text("CREATE TABLE audio_transcripts (id INTEGER PRIMARY KEY)"))
        conn.execute(sa.text("CREATE TABLE sync_points (id INTEGER PRIMARY KEY)"))
        with Operations.context(MigrationContext.configure(conn)):
            m.upgrade()

        def names(table):
            return {c["name"] for c in sa.inspect(conn).get_columns(table)}

        assert "words_json" in names("audio_transcripts")
        assert "word_starts" in names("sync_points")
        with Operations.context(MigrationContext.configure(conn)):
            m.downgrade()
        assert "words_json" not in names("audio_transcripts")
        assert "word_starts" not in names("sync_points")
