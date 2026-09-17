"""transcript audio fingerprint (issue #588)

`services.queue_manager`'s transcript cache used to key a hit on
`audiobook_path` alone, so a file replaced in place -- a downloader
"upgrade", a re-rip, an Audiobookshelf merge/re-encode -- kept reusing the
old recording's transcript and aligning its timestamps against different
audio. This adds the fingerprint of the audio that was actually
transcribed: a snapshot of `AudioBook.file_hash` (a whole-file hash) and
`AudioBook.duration_seconds` taken when the transcript was written.

Both columns are nullable and **not backfilled**: computing a hash for
every existing transcript's audiobook here would mean hashing the whole
library during a migration, on a background job (the Jetson worker, or a
long-idle install) that may not even have the files mounted. A transcript
with a NULL fingerprint is "unknown provenance", not "known unchanged" --
the cache check in `services.queue_manager` treats it as "trust the path
match this one time, and stamp today's fingerprint for next time" rather
than forcing every pre-existing transcript to re-transcribe the moment
this ships.

Revision ID: 0023_audio_fingerprint
Revises: 0022_sync_map_degraded
Create Date: 2026-09-17

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
# NOTE: keep this at or under 32 chars — see the note in
# 0002_conflict_resolution.py (alembic_version is a VARCHAR(32)).
revision: str = "0023_audio_fingerprint"
down_revision: Union[str, None] = "0022_sync_map_degraded"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "audio_transcripts",
        sa.Column("audio_file_hash", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "audio_transcripts",
        sa.Column("audio_duration_seconds", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("audio_transcripts", "audio_duration_seconds")
    op.drop_column("audio_transcripts", "audio_file_hash")
