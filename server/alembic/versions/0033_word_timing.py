"""Word-level timing: audio_transcripts.words_json, sync_points.word_starts (issue #835)

The transcription worker now returns each word's start and end with every
sentence. `audio_transcripts.words_json` keeps that timing next to the sentences
it belongs to, and `sync_points.word_starts` holds, per aligned ebook sentence,
the audio start (ms) of each whitespace-separated token, derived at alignment
time. Both columns are nullable TEXT: every existing row predates word timing
and stays NULL until its pair is re-transcribed. Adding a nullable column is a
catalog-only change on Postgres, so the upgrade is instant whatever the table
size. Nothing is backfilled by the migration.

Revision ID: 0033_word_timing
Revises: 0032_sync_point_full_text
Create Date: 2026-10-07

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# NOTE: keep this at or under 32 chars — see 0002_conflict_resolution.py.
revision: str = "0033_word_timing"
down_revision: Union[str, None] = "0032_sync_point_full_text"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("audio_transcripts", sa.Column("words_json", sa.Text(), nullable=True))
    op.add_column("sync_points", sa.Column("word_starts", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("sync_points", "word_starts")
    op.drop_column("audio_transcripts", "words_json")
