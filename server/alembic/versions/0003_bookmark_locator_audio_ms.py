"""bookmark locator audio anchor (issue #40)

Adds ``bookmarks.locator_audio_ms``: the audio position (ms) the stored
``epub_locator`` was captured at. Android already tracked this locally, so
only the device that wrote a locator could tell whether it still described
where the audio is now. Mirroring it to the server lets a *second* device
make the same judgement instead of falling back to the lossy
preview -> spine -> character-fraction resolution.

Nullable — existing bookmarks keep a NULL anchor and simply don't qualify for
the locator fast path until the next write.

Revision ID: 0003_bookmark_locator_audio
Revises: 0002_conflict_resolution
Create Date: 2026-07-29

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
# NOTE: keep this at or under 32 chars — see the note in
# 0002_conflict_resolution.py (alembic_version is a VARCHAR(32)).
revision: str = "0003_bookmark_locator_audio"
down_revision: Union[str, None] = "0002_conflict_resolution"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("bookmarks", sa.Column("locator_audio_ms", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("bookmarks", "locator_audio_ms")
