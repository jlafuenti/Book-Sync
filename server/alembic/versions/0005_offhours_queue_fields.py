"""off-hours queue fields (issue #106)

Adds the two columns the off-hours transcription window needs on
``transcription_queue``:

- ``force_run`` — the per-item "Run now" override that bypasses a closed
  window. Sticky: a forced item is also exempt from being paused when the
  window would otherwise close under it.
- ``paused_at`` — set when a running job was paused at a chunk boundary
  because the window closed. It marks the item as holding a resumable
  checkpoint on the transcription worker, and the queue dispatches such items
  ahead of fresh ones so a half-transcribed book finishes before a new one
  starts.

Both are additive and backfill-free: existing rows get ``force_run = false``
and ``paused_at = NULL``, which is exactly "behaves the way it did before".

Revision ID: 0005_offhours_queue
Revises: 0004_canonical_position
Create Date: 2026-08-07

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
# NOTE: keep this at or under 32 chars — alembic_version is a VARCHAR(32).
revision: str = "0005_offhours_queue"
down_revision: Union[str, None] = "0004_canonical_position"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "transcription_queue",
        sa.Column(
            "force_run",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    op.add_column(
        "transcription_queue",
        sa.Column("paused_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("transcription_queue", "paused_at")
    op.drop_column("transcription_queue", "force_run")
