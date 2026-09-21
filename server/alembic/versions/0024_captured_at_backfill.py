"""backfill captured_at from updated_at (issue #679)

Clients rank "last read" by `captured_at` and fall back to `updated_at` when
it is NULL. `updated_at` is also bumped by server-side rewrites that are not
reading -- a realign's bookmark remap and the progress projection it drives --
so every row with a NULL `captured_at` looked read "just now" the moment its
pair was realigned: old books jumped to the top of Continue Reading, and the
phone prefetched their sync maps.

Every write through `apply_position` stamps `captured_at`, so the NULL rows are
old ones. This copies each such row's `updated_at` into `captured_at` once,
which is the value clients were already using for it. Rows that already carry
a `captured_at` are untouched, and running it again changes nothing.

Downgrade is a no-op: the copied values are indistinguishable from real
stamps afterwards, and leaving them is harmless -- it is what clients read
anyway.

Revision ID: 0024_captured_at_backfill
Revises: 0023_audio_fingerprint
Create Date: 2026-09-21

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
# NOTE: keep this at or under 32 chars — see the note in
# 0002_conflict_resolution.py (alembic_version is a VARCHAR(32)).
revision: str = "0024_captured_at_backfill"
down_revision: Union[str, None] = "0023_audio_fingerprint"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLES = ("bookmarks", "user_progress")


def upgrade() -> None:
    for table in _TABLES:
        op.execute(
            f"UPDATE {table} SET captured_at = updated_at WHERE captured_at IS NULL"
        )


def downgrade() -> None:
    pass
