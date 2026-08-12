"""unique user_progress rows per (user, media)

`user_progress` had no unique constraint on its logical key, and
`GET /api/sync/progress/{type}/{id}` was get-or-**create** — it INSERTed on a
miss. Two concurrent first reads for the same book each left a row behind, and
from then on every read *and* write for that media hit `scalar_one_or_none()` on
two rows and raised `MultipleResultsFound`: that one book 500ed forever until a
row was deleted by hand (issue #64).

The GET is read-only now. This migration closes the schema half, and must dedupe
before it can constrain — production is known to carry at least one such pair
(see step 5 of 0004, which already had to work around it).

Order inside upgrade():

1. Salvage `epub_cfi` from the rows about to be deleted. The CFI is the web
   reader's precise restore hint; the newest row is not necessarily the one
   carrying it, and losing it silently drops the reader back to a coarser rung
   of the restore ladder.
2. Delete all but the newest row per key, by `(updated_at, id)` — the same
   tiebreak `latest_progress_row` uses, so the survivor is the row the app was
   already serving. Nothing else references `user_progress.id`.
3. Create the partial unique indexes.

Revision ID: 0006_user_progress_unique
Revises: 0005_offhours_queue
Create Date: 2026-08-11

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
# NOTE: keep this at or under 32 chars — see 0002_conflict_resolution.py.
revision: str = "0006_user_progress_unique"
down_revision: Union[str, None] = "0005_offhours_queue"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# (media_type value, id column) — the enum is stored by member name.
_SCOPES = (("EBOOK", "ebook_id"), ("AUDIOBOOK", "audiobook_id"))


def _newer_exists(alias: str, id_col: str, media: str) -> str:
    """SQL for "some other row with the same key is newer than [alias]".

    A correlated NOT EXISTS rather than a window function, so this runs on
    SQLite as well as Postgres (same shape as step 5 of 0004).
    """
    return (
        f"EXISTS (SELECT 1 FROM user_progress other "
        f"        WHERE other.user_id = {alias}.user_id "
        f"          AND other.{id_col} = {alias}.{id_col} "
        f"          AND other.media_type = '{media}' "
        f"          AND (other.updated_at > {alias}.updated_at "
        f"               OR (other.updated_at = {alias}.updated_at "
        f"                   AND other.id > {alias}.id)))"
    )


def dedupe_user_progress(conn) -> None:
    """Salvage, then collapse each duplicate group to its newest row.

    MUST run before the indexes are created — creating them first fails on any
    database that already carries a duplicate, which is most of the point.
    Separated from :func:`upgrade` so it can be exercised without Postgres
    (see tests/test_migration_0006_data.py); it is plain portable SQL.
    """
    for media, id_col in _SCOPES:
        # ---- 1. salvage the CFI onto the survivor ------------------------
        # Only for the ebook scope: `epub_cfi` is meaningless on an audiobook row.
        if media == "EBOOK":
            conn.execute(sa.text(
                f"UPDATE user_progress SET epub_cfi = ("
                f"  SELECT donor.epub_cfi FROM user_progress donor "
                f"  WHERE donor.user_id = user_progress.user_id "
                f"    AND donor.{id_col} = user_progress.{id_col} "
                f"    AND donor.media_type = '{media}' "
                f"    AND donor.id <> user_progress.id "
                f"    AND donor.epub_cfi IS NOT NULL "
                f"  ORDER BY donor.updated_at DESC, donor.id DESC LIMIT 1) "
                f"WHERE user_progress.media_type = '{media}' "
                f"  AND user_progress.{id_col} IS NOT NULL "
                f"  AND user_progress.epub_cfi IS NULL "
                f"  AND NOT {_newer_exists('user_progress', id_col, media)}"
            ))

        # ---- 2. keep only the newest row per key --------------------------
        conn.execute(sa.text(
            f"DELETE FROM user_progress WHERE id IN ("
            f"  SELECT dup.id FROM user_progress dup "
            f"  WHERE dup.media_type = '{media}' "
            f"    AND dup.{id_col} IS NOT NULL "
            f"    AND {_newer_exists('dup', id_col, media)})"
        ))


def upgrade() -> None:
    dedupe_user_progress(op.get_bind())

    # ---- 3. constrain --------------------------------------------------------
    op.create_index(
        "ux_user_progress_user_ebook", "user_progress", ["user_id", "ebook_id"],
        unique=True,
        postgresql_where=sa.text("media_type = 'EBOOK' AND ebook_id IS NOT NULL"),
        sqlite_where=sa.text("media_type = 'EBOOK' AND ebook_id IS NOT NULL"),
    )
    op.create_index(
        "ux_user_progress_user_audiobook", "user_progress",
        ["user_id", "audiobook_id"], unique=True,
        postgresql_where=sa.text("media_type = 'AUDIOBOOK' AND audiobook_id IS NOT NULL"),
        sqlite_where=sa.text("media_type = 'AUDIOBOOK' AND audiobook_id IS NOT NULL"),
    )


def downgrade() -> None:
    # The deduped rows are not restorable; only the constraint comes off.
    op.drop_index("ux_user_progress_user_audiobook", table_name="user_progress")
    op.drop_index("ux_user_progress_user_ebook", table_name="user_progress")
