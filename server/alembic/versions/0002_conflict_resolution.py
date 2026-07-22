"""conflict resolution contract (issue #54)

Adds the shared device-attribution / staleness-check contract consumed by the
server sync endpoints (and, in later tasks, the web and Android clients):

- ``bookmarks``: ``captured_at``, ``device_id``, ``device_name``
- ``bookmark_logs``: ``device_id``, ``device_name``, ``captured_at`` (so
  Session History rows carry device attribution + event time)
- ``user_progress``: ``captured_at``, ``device_name`` (``device_id`` already
  existed)

All new columns are nullable — legacy rows and legacy clients (which never
send ``captured_at``) are unaffected; see ``routers.sync._is_stale``.

Revision ID: 0002_conflict_resolution
Revises: 0001_baseline
Create Date: 2026-07-20

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
# NOTE: keep this at or under 32 chars — Alembic's default alembic_version
# table stores it in a VARCHAR(32) column, and a longer id fails at
# migration time with "value too long for type character varying(32)"
# (a legacy client-app value of 200 chars is unrelated; this is Alembic's
# own bookkeeping column, not a domain column, and Alembic does not
# configure a wider one by default).
revision: str = "0002_conflict_resolution"
down_revision: Union[str, None] = "0001_baseline"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("bookmarks", sa.Column("captured_at", sa.DateTime(), nullable=True))
    op.add_column("bookmarks", sa.Column("device_id", sa.String(length=200), nullable=True))
    op.add_column("bookmarks", sa.Column("device_name", sa.String(length=200), nullable=True))

    op.add_column("bookmark_logs", sa.Column("device_id", sa.String(length=200), nullable=True))
    op.add_column("bookmark_logs", sa.Column("device_name", sa.String(length=200), nullable=True))
    op.add_column("bookmark_logs", sa.Column("captured_at", sa.DateTime(), nullable=True))

    op.add_column("user_progress", sa.Column("captured_at", sa.DateTime(), nullable=True))
    op.add_column("user_progress", sa.Column("device_name", sa.String(length=200), nullable=True))


def downgrade() -> None:
    op.drop_column("user_progress", "device_name")
    op.drop_column("user_progress", "captured_at")

    op.drop_column("bookmark_logs", "captured_at")
    op.drop_column("bookmark_logs", "device_name")
    op.drop_column("bookmark_logs", "device_id")

    op.drop_column("bookmarks", "device_name")
    op.drop_column("bookmarks", "device_id")
    op.drop_column("bookmarks", "captured_at")
