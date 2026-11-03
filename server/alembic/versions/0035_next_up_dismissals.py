"""Next up dismissals: per-account removals (issue #921)

Adds `next_up_dismissals`, one row per (user, series) the user removed from
Next up, with a unique `(user_id, series_key)` index. Purely additive.

Revision ID: 0035_next_up_dismissals
Revises: 0034_series_entries
Create Date: 2026-11-03

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# NOTE: keep this at or under 32 chars — see 0002_conflict_resolution.py.
revision: str = "0035_next_up_dismissals"
down_revision: Union[str, None] = "0034_series_entries"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "next_up_dismissals",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("series_key", sa.String(length=500), nullable=False),
        sa.Column("series", sa.String(length=500), nullable=False),
        sa.Column("dismissed_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ux_next_up_dismissals_user_series", "next_up_dismissals",
        ["user_id", "series_key"], unique=True,
    )


def downgrade() -> None:
    op.drop_index("ux_next_up_dismissals_user_series", table_name="next_up_dismissals")
    op.drop_table("next_up_dismissals")
