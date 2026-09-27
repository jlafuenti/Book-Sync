"""users.web_tour_offered_at (issue #598)

The web app offers its guided walkthrough once per user rather than once per
browser. This stamp records that the offer was shown, whatever the answer —
set once through `PUT /api/auth/me` and left alone after that, since there is
no un-offering. Nullable; NULL means never offered. Nothing is backfilled.

Revision ID: 0030_web_tour_offered_at
Revises: 0029_print_pages_looked_up
Create Date: 2026-09-27

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# NOTE: keep this at or under 32 chars — see 0002_conflict_resolution.py.
revision: str = "0030_web_tour_offered_at"
down_revision: Union[str, None] = "0029_print_pages_looked_up"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("web_tour_offered_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "web_tour_offered_at")
