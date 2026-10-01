"""sync_maps.splitter_version (issue #774)

Records which version of the EPUB sentence splitter built each sync map, so a
change to the split — which shifts `sentence_index` — can be told apart from a
map that is current. NOT NULL with a server default of 1: every existing map was
built under the first split and is reported as outdated until an admin rebuilds
it from the cached transcript. Nothing is rebuilt by the migration itself.

Revision ID: 0031_sync_map_splitter_version
Revises: 0030_web_tour_offered_at
Create Date: 2026-10-01

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# NOTE: keep this at or under 32 chars — see 0002_conflict_resolution.py.
revision: str = "0031_sync_map_splitter_version"
down_revision: Union[str, None] = "0030_web_tour_offered_at"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "sync_maps",
        sa.Column("splitter_version", sa.Integer(), nullable=False, server_default="1"),
    )


def downgrade() -> None:
    op.drop_column("sync_maps", "splitter_version")
