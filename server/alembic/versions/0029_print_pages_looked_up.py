"""ebooks.print_pages_looked_up_at (issue #739)

When the Google Books print-page fill job last looked an ebook up, found or not,
so a later run skips it rather than spending the daily quota on the same miss.
Nullable; NULL means never tried. Nothing is backfilled.

Revision ID: 0029_print_pages_looked_up
Revises: 0028_ebook_print_page_count
Create Date: 2026-09-27

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# NOTE: keep this at or under 32 chars — see 0002_conflict_resolution.py.
revision: str = "0029_print_pages_looked_up"
down_revision: Union[str, None] = "0028_ebook_print_page_count"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("ebooks", sa.Column("print_pages_looked_up_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("ebooks", "print_pages_looked_up_at")
