"""drop the legacy position mirror columns (issue #102)

``bookmarks.epub_locator``, ``bookmarks.locator_audio_ms`` and
``user_progress.epub_cfi`` were mirrors of the current-anchor position hint,
written on every save purely so app builds predating ``position_hints``
(migration 0004) kept resuming. Every client now reads and writes hints, and
the legacy ``PUT /sync/bookmark/{pair}`` and ``PUT /sync/progress/{type}/{id}``
adapters that fed them are gone.

``position_hints`` is the source of truth — 0004 backfilled *from* these
columns, never the reverse — so dropping them loses nothing that isn't already
stored per-device with the anchor revision it belongs to.

The downgrade re-creates the columns nullable but **cannot restore their
values**: a bookmark rolled back to a pre-0007 schema would carry NULL mirrors
until the next write. That is survivable (they were nullable from the start and
a NULL simply means "no locator recorded yet"), and the hints they mirrored are
untouched.

Revision ID: 0007_drop_legacy_mirrors
Revises: 0006_user_progress_unique
Create Date: 2026-08-12

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
# NOTE: keep this at or under 32 chars — see the note in
# 0002_conflict_resolution.py (alembic_version is a VARCHAR(32)).
revision: str = "0007_drop_legacy_mirrors"
down_revision: Union[str, None] = "0006_user_progress_unique"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("bookmarks", "epub_locator")
    op.drop_column("bookmarks", "locator_audio_ms")
    op.drop_column("user_progress", "epub_cfi")


def downgrade() -> None:
    op.add_column("user_progress", sa.Column("epub_cfi", sa.String(length=500),
                                             nullable=True))
    op.add_column("bookmarks", sa.Column("locator_audio_ms", sa.Integer(),
                                         nullable=True))
    op.add_column("bookmarks", sa.Column("epub_locator", sa.Text(), nullable=True))
