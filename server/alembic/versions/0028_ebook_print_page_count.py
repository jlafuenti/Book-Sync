"""ebooks.print_page_count (issue #730)

Pages in the printed edition, used by the reader's "actual pages" when an EPUB
embeds no page list. Nullable; nothing is backfilled.

Revision ID: 0028_ebook_print_page_count
Revises: 0027_false_capture_dates
Create Date: 2026-09-27

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# NOTE: keep this at or under 32 chars — see 0002_conflict_resolution.py.
revision: str = "0028_ebook_print_page_count"
down_revision: Union[str, None] = "0027_false_capture_dates"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("ebooks", sa.Column("print_page_count", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("ebooks", "print_page_count")
