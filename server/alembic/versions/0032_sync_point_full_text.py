"""sync_points.epub_text_preview holds the whole sentence (issue #763)

The column was VARCHAR(200) and the aligner cut every sentence to fit. Read-along
marks a sentence by searching the page for exactly this text, so a long sentence
was marked only up to the cut. The upgrade only widens the type — on Postgres a
VARCHAR to TEXT change is a catalog update, no table rewrite — and leaves the
stored text as it is: an existing map keeps its cut sentences until it is
realigned from the cached transcript. The downgrade cuts long sentences back to
200 characters first, or the narrower type would refuse them.

Revision ID: 0032_sync_point_full_text
Revises: 0031_sync_map_splitter_version
Create Date: 2026-10-03

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# NOTE: keep this at or under 32 chars — see 0002_conflict_resolution.py.
revision: str = "0032_sync_point_full_text"
down_revision: Union[str, None] = "0031_sync_map_splitter_version"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("sync_points") as batch_op:
        batch_op.alter_column(
            "epub_text_preview",
            existing_type=sa.String(length=200),
            type_=sa.Text(),
            existing_nullable=True,
        )


def downgrade() -> None:
    op.execute(
        "UPDATE sync_points SET epub_text_preview = substr(epub_text_preview, 1, 200) "
        "WHERE length(epub_text_preview) > 200"
    )
    with op.batch_alter_table("sync_points") as batch_op:
        batch_op.alter_column(
            "epub_text_preview",
            existing_type=sa.Text(),
            type_=sa.String(length=200),
            existing_nullable=True,
        )
