"""Series entries: ebook_series / audiobook_series, ebooks.series_cleared (issue #905)

A book can belong to several series, so the scalar `series` / `series_index`
columns on `ebooks` and `audiobooks` are replaced by child tables, one row per
series with a `position` (0 is the primary series). The `series = ''` sentinel
("the user cleared it, do not refill it from a rescan") becomes an explicit
boolean `series_cleared` column.

Upgrade, per table:

1. create `<media>_series` with its unique `(owner, position)` and name indexes;
2. copy every non-empty `series` (and its index) in as position 0;
3. add `series_cleared` (false, keeping its server default like every other NOT NULL
   boolean on these tables, so raw inserts that do not name it still work), set it where
   the old value was `''`;
4. drop `series` and `series_index` (batch mode, so SQLite can do it too).

Downgrade reverses that. A book with several series keeps only its primary one
in the old schema; the others are lost on a downgrade. Files are not touched
by either direction: tags are rewritten only on the next edit or enrich.

Revision ID: 0034_series_entries
Revises: 0033_word_timing
Create Date: 2026-11-02

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# NOTE: keep this at or under 32 chars — see 0002_conflict_resolution.py.
revision: str = "0034_series_entries"
down_revision: Union[str, None] = "0033_word_timing"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# (media table, entry table, entry-table foreign key column)
_TABLES = (
    ("ebooks", "ebook_series", "ebook_id"),
    ("audiobooks", "audiobook_series", "audiobook_id"),
)


def upgrade() -> None:
    for media, entries, fk in _TABLES:
        op.create_table(
            entries,
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(fk, sa.Integer(), sa.ForeignKey(f"{media}.id", ondelete="CASCADE"), nullable=False),
            sa.Column("name", sa.String(length=500), nullable=False),
            sa.Column("series_index", sa.Float(), nullable=True),
            sa.Column("position", sa.Integer(), nullable=False),
        )
        op.create_index(f"ux_{entries}_position", entries, [fk, "position"], unique=True)
        op.create_index(f"ix_{entries}_name", entries, ["name"])

        op.execute(sa.text(
            f"INSERT INTO {entries} ({fk}, name, series_index, position) "
            f"SELECT id, series, series_index, 0 FROM {media} "
            "WHERE series IS NOT NULL AND series <> ''"
        ))

        op.add_column(media, sa.Column(
            "series_cleared", sa.Boolean(), nullable=False, server_default=sa.false(),
        ))
        op.execute(
            sa.text(f"UPDATE {media} SET series_cleared = :flag WHERE series = ''")
            .bindparams(flag=True)
        )

        with op.batch_alter_table(media) as batch:
            batch.drop_column("series")
            batch.drop_column("series_index")


def downgrade() -> None:
    for media, entries, fk in _TABLES:
        with op.batch_alter_table(media) as batch:
            batch.add_column(sa.Column("series", sa.String(length=500), nullable=True))
            batch.add_column(sa.Column("series_index", sa.Float(), nullable=True))

        op.execute(sa.text(
            f"UPDATE {media} SET "
            f"series = (SELECT e.name FROM {entries} e WHERE e.{fk} = {media}.id AND e.position = 0), "
            f"series_index = (SELECT e.series_index FROM {entries} e WHERE e.{fk} = {media}.id AND e.position = 0)"
        ))
        op.execute(
            sa.text(f"UPDATE {media} SET series = '' WHERE series_cleared = :flag AND series IS NULL")
            .bindparams(flag=True)
        )

        with op.batch_alter_table(media) as batch:
            batch.drop_column("series_cleared")

        op.drop_index(f"ix_{entries}_name", table_name=entries)
        op.drop_index(f"ux_{entries}_position", table_name=entries)
        op.drop_table(entries)
