"""record which sync-map version a bookmark's coordinates belong to (issue #55)

``bookmarks.epub_sentence_index`` is a *sync-map coordinate*: it only means
anything relative to the ``sync_points`` rows a particular ``sync_maps.version``
produced. Re-transcribing a pair deletes that map and inserts a fresh one, so
the stored index can silently start naming different text.

``save_sync_map`` now re-maps the affected bookmarks onto the new map and stamps
this column with the version it translated them to. A row whose
``sync_map_version`` trails the pair's current ``sync_maps.version`` is one the
re-map could not translate (no text anchor, no audio position, or no match), and
its sentence index should not be trusted.

Backfilled to NULL — "never established against a map" — rather than to the
pair's current version: an existing row's coordinates predate this tracking and
claiming otherwise would assert something the data does not support.

Revision ID: 0008_bookmark_syncmap_ver
Revises: 0007_drop_legacy_mirrors
Create Date: 2026-08-14

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
# NOTE: keep this at or under 32 chars — see the note in
# 0002_conflict_resolution.py (alembic_version is a VARCHAR(32)).
revision: str = "0008_bookmark_syncmap_ver"
down_revision: Union[str, None] = "0007_drop_legacy_mirrors"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "bookmarks", sa.Column("sync_map_version", sa.Integer(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("bookmarks", "sync_map_version")
