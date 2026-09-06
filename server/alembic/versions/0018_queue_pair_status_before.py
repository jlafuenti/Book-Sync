"""remember a pair's status before a transcription job claims it (issue #381)

Cancelling a running transcription flipped the pair to ERROR, because nothing
recorded what it had been before the pipeline set it to TRANSCRIBING. A job
queued by mistake and cancelled therefore left a red pair with nothing wrong
with the books.

This column is that record. The pipeline writes the pair's status on the first
claim (never on a resume, retry or restart — the pair is already TRANSCRIBING
then), and ``queue_manager.settle_pair_after_cancel`` hands it back when the job
is cancelled. A failed job still puts the pair in ERROR; the column is read for
cancels only.

Nullable and not backfilled: a row that predates the column — a job running
across the deploy — has no record, and a cancel then derives the status (SYNCED
if the pair still has a sync map, otherwise AUTO_MATCHED). Additive DDL only.

Revision ID: 0018_queue_pair_status_before
Revises: 0017_repair_nullability
Create Date: 2026-09-06

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
# NOTE: keep this at or under 32 chars — see the note in
# 0002_conflict_resolution.py (alembic_version is a VARCHAR(32)).
revision: str = "0018_queue_pair_status_before"
down_revision: Union[str, None] = "0017_repair_nullability"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "transcription_queue",
        sa.Column("pair_status_before", sa.String(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("transcription_queue", "pair_status_before")
