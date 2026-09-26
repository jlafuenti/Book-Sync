"""move orphaned positions on paired books onto the pair (issue #720)

`PUT /api/sync/position/{ebook|audiobook}/{id}` used to store a standalone
record even when that book was half of a pair. Opening the pair reads the pair
record, so when the standalone row was the reader's only position for the book,
the book opened at the beginning. The server now folds such writes onto the
pair (`position_service.resolve_write_scope`); this migration re-homes the rows
written before that.

A row moves when all of these hold:

* it is standalone (`book_pair_id IS NULL`) and one of its media is paired;
* every medium it names belongs to that pair. A row that also names another
  medium is an unpair's demotion output and still speaks for that medium, so it
  stays; folding it would drop that position and put its audio offset on a
  different recording;
* the user has no record for the pair yet. A standalone row beside a pair
  record stays where it is: the pair record is what readers already open.

If a user has two such rows for one pair (ebook-side and audiobook-side), only
one fits the pair's unique slot. The newer one moves (`captured_at`, falling back
to `updated_at`); the older one stays standalone.

A moved row keeps its id, coordinates, hints, log and timestamps; only
`book_pair_id` is set and the media ids are cleared, which is the shape of every
pair record. Its `user_progress` projection row for the same medium gets the
pair's id, as a live pair write would have given it. `sync_map_version` stays as
it was: none of these rows carries one, and NULL is what a live write without
an attested version stores.

Downgrade is a no-op: a moved row is indistinguishable from one written to the
pair, and the pair is where readers look for it.

Revision ID: 0026_fold_orphan_positions
Revises: 0025_book_pairs_one_to_one
Create Date: 2026-09-26

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
# NOTE: keep this at or under 32 chars — see the note in
# 0002_conflict_resolution.py (alembic_version is a VARCHAR(32)).
revision: str = "0026_fold_orphan_positions"
down_revision: Union[str, None] = "0025_book_pairs_one_to_one"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_CANDIDATES = sa.text("""
    SELECT b.id, b.user_id, b.ebook_id, b.audiobook_id, p.id AS pair_id,
           COALESCE(b.captured_at, b.updated_at) AS stamp
    FROM bookmarks b
    JOIN book_pairs p
      ON (b.ebook_id = p.ebook_id OR b.audiobook_id = p.audiobook_id)
    WHERE b.book_pair_id IS NULL
      AND (b.ebook_id IS NULL OR b.ebook_id = p.ebook_id)
      AND (b.audiobook_id IS NULL OR b.audiobook_id = p.audiobook_id)
      AND NOT EXISTS (
          SELECT 1 FROM bookmarks pb
          WHERE pb.user_id = b.user_id AND pb.book_pair_id = p.id
      )
""")


def _newest(rows):
    # Tuple order: a stamped row beats an unstamped one, then the later stamp,
    # then the higher id. The stamp is only compared when both rows have one.
    return max(rows, key=lambda r: (r.stamp is not None, r.stamp or "", r.id))


def upgrade() -> None:
    conn = op.get_bind()

    by_slot = {}
    for row in conn.execute(_CANDIDATES):
        by_slot.setdefault((row.user_id, row.pair_id), []).append(row)

    for (user_id, pair_id), rows in by_slot.items():
        row = _newest(rows)

        media = []
        params = {"pair_id": pair_id, "user_id": user_id}
        if row.ebook_id is not None:
            media.append("ebook_id = :ebook_id")
            params["ebook_id"] = row.ebook_id
        if row.audiobook_id is not None:
            media.append("audiobook_id = :audiobook_id")
            params["audiobook_id"] = row.audiobook_id
        conn.execute(sa.text(
            "UPDATE user_progress SET book_pair_id = :pair_id "
            "WHERE user_id = :user_id AND book_pair_id IS NULL "
            f"AND ({' OR '.join(media)})"
        ), params)

        conn.execute(sa.text(
            "UPDATE bookmarks SET book_pair_id = :pair_id, "
            "ebook_id = NULL, audiobook_id = NULL WHERE id = :id"
        ), {"pair_id": pair_id, "id": row.id})


def downgrade() -> None:
    pass
