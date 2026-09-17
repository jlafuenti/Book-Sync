"""flag degraded sync maps (issue #586)

An audio file whose content is reordered relative to the ebook (e.g. two
blocks swapped mid-book) stays invisible to the alignment's own anchor
filter: the longest-increasing-subsequence filter that keeps timestamps
monotonic just drops the displaced block's anchors and DTW/interpolation
quietly fills the gap with plausible-looking but wrong timestamps. The
sync-map audit (`GET /api/troubleshoot/sync-map-audit`) never checked
timings against the transcript, so a book could switch 30-45 minutes off
and report itself healthy.

`services.alignment.align_texts_with_diagnostics` now classifies this at
alignment time — a large, contiguous, consistently-displaced run of
rejected anchors — and `sync_engine.save_sync_map` stamps the verdict here
so it can be surfaced to operators instead of silently interpolated over.

Backfilled to `degraded=False` — "not (yet) known to be degraded" — rather
than attempting to re-derive the verdict for existing maps: that would
require re-running alignment, which this migration does not do. An
operator can re-run `POST /api/transcription/{pair_id}/realign` (or the
`sync-map-audit` timing check, once it flags a pair) to get a current
verdict for a map written before this column existed.

Revision ID: 0022_sync_map_degraded
Revises: 0021_epub_font_obfuscation
Create Date: 2026-09-16

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
# NOTE: keep this at or under 32 chars — see the note in
# 0002_conflict_resolution.py (alembic_version is a VARCHAR(32)).
revision: str = "0022_sync_map_degraded"
down_revision: Union[str, None] = "0021_epub_font_obfuscation"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "sync_maps",
        sa.Column(
            "degraded", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
    )
    op.add_column(
        "sync_maps", sa.Column("degraded_reason", sa.Text(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("sync_maps", "degraded_reason")
    op.drop_column("sync_maps", "degraded")
