"""remember an unpair by row id, not only by file hash (issue #253)

Manually unpairing an ebook from an audiobook is the normal way to correct a
wrong auto-match, and it records a "never re-pair these two" note on both rows.
The note was a *file hash* on each side, and ``delete_pair`` only wrote it when
**both** rows had one — so a row without a hash kept no memory of the unpair at
all and the very next library scan re-created the pair the user had just broken.

These two columns add a second, always-available key: each side stores the other
side's row id. ``_auto_pair_excluded`` checks ids and hashes, either of which
blocks the pair, so nothing already recorded stops working. Hashes are kept
rather than replaced because they survive a row being deleted and re-ingested at
a new id, and because ``POST /api/library/rehash`` remaps them.

Nothing is backfilled and nothing needs to be: an exclusion already recorded is
a hash pair, which still matches, and an unpair performed after this migration
records both keys. Existing *pairs* are untouched — the matcher only ever reads
rows that are absent from ``book_pairs`` and never deletes one.

Additive DDL only: one ``NOT NULL`` JSONB column per table, with a ``'[]'``
server default so the constraint is satisfiable on a populated table without a
separate UPDATE pass. No batch mode needed (Postgres-only, like every migration
here — see tests/test_migrations_postgres.py).

Revision ID: 0014_auto_pair_excl_ids
Revises: 0013_refresh_tokens
Create Date: 2026-09-03

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
# NOTE: keep this at or under 32 chars — see the note in
# 0002_conflict_resolution.py (alembic_version is a VARCHAR(32)).
revision: str = "0014_auto_pair_excl_ids"
down_revision: Union[str, None] = "0013_refresh_tokens"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Same variant the models and the baseline use: JSONB on Postgres, JSON
# elsewhere, so the column type matches what `alembic check` autogenerates.
_json = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")

_TABLES = ("ebooks", "audiobooks")


def upgrade() -> None:
    for table in _TABLES:
        op.add_column(
            table,
            sa.Column(
                "auto_pair_excluded_ids",
                _json,
                nullable=False,
                server_default=sa.text("'[]'::jsonb"),
            ),
        )


def downgrade() -> None:
    for table in _TABLES:
        op.drop_column(table, "auto_pair_excluded_ids")
