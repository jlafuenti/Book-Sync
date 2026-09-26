"""move false "last read" dates back (issue #726)

Before the #679 fix, a server-side rewrite of a position (a realign's bookmark
remap, an unpair demotion, a batch job) bumped `updated_at` on bookmarks that
had no `captured_at`. Migration 0024 then copied `updated_at` into
`captured_at` for every NULL row, so on rows a rewrite had already bumped, the
rewrite's time became a permanent "last read". Next up (#716) counted those as
reading within its 90-day window and listed series nobody had opened;
Continue Reading sorted months-old, barely opened books among current ones.

A row is repaired when it carries the backfill's signature:

* no `device_id` -- both clients send one with every write since #54;
* `captured_at` exactly equal to `updated_at` -- a capture comes from the
  device's clock and never matches the server's receive time to the
  microsecond; only the backfill produced that;
* dated on or after 2026-07-20, the day both clients began sending
  `device_id` and `captured_at` together. An earlier backfilled date may be a
  real read by a client that sent neither, so it stays;
* no `bookmark_logs` entry within a second of that date, which would mark a
  move the reader made.

Its `captured_at` moves back to the latest real evidence before the false
date: a `bookmark_logs` entry or `synced_at`, else the account's creation
time. The true read time is gone, and this is the latest one the database can
vouch for. `updated_at` is left alone on purpose: the server's echo guard
(`position_service.echoes_server_stamp`) recognises a client pushing the false
date back by its match with `updated_at`, and answers 409 so the client adopts
the repaired value instead of restoring the false one.

The record's `user_progress` projection rows (same user and pair, or the same
medium for a standalone record) with the same signature take the same value.

Downgrade is a no-op: the false dates carry no information worth restoring.

Revision ID: 0027_false_capture_dates
Revises: 0026_fold_orphan_positions
Create Date: 2026-09-26

"""
from datetime import datetime, timedelta
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
# NOTE: keep this at or under 32 chars — see the note in
# 0002_conflict_resolution.py (alembic_version is a VARCHAR(32)).
revision: str = "0027_false_capture_dates"
down_revision: Union[str, None] = "0026_fold_orphan_positions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Both clients send `device_id` and `captured_at` with every write from here (#54).
CUTOFF = datetime(2026, 7, 20)
# A history entry this close to the stamp is the reader's own move.
LOG_WINDOW = timedelta(seconds=1)

_CANDIDATES = sa.text("""
    SELECT b.id, b.user_id, b.book_pair_id, b.ebook_id, b.audiobook_id,
           b.captured_at, b.synced_at, u.created_at AS signed_up,
           p.ebook_id AS pair_ebook_id, p.audiobook_id AS pair_audiobook_id
    FROM bookmarks b
    JOIN users u ON u.id = b.user_id
    LEFT JOIN book_pairs p ON p.id = b.book_pair_id
    WHERE b.device_id IS NULL
      AND b.captured_at IS NOT NULL
      AND b.captured_at = b.updated_at
      AND b.captured_at >= :cutoff
""").bindparams(sa.bindparam("cutoff", type_=sa.DateTime)).columns(
    captured_at=sa.DateTime, synced_at=sa.DateTime, signed_up=sa.DateTime)

_LOGS = sa.text(
    "SELECT changed_at FROM bookmark_logs WHERE bookmark_id = :id"
).columns(changed_at=sa.DateTime)


def _restored(row, logs):
    """The latest real evidence before the false stamp, or None to skip."""
    stamp = row.captured_at
    if any(abs(at - stamp) <= LOG_WINDOW for at in logs):
        return None
    evidence = [at for at in logs if at < stamp]
    if row.synced_at is not None and row.synced_at < stamp:
        evidence.append(row.synced_at)
    if evidence:
        return max(evidence)
    if row.signed_up is not None and row.signed_up < stamp:
        return row.signed_up
    return None


def upgrade() -> None:
    conn = op.get_bind()
    dt = sa.DateTime

    for row in conn.execute(_CANDIDATES, {"cutoff": CUTOFF}).all():
        logs = [r.changed_at for r in conn.execute(_LOGS, {"id": row.id})]
        restored = _restored(row, logs)
        if restored is None:
            continue

        conn.execute(sa.text(
            "UPDATE bookmarks SET captured_at = :restored WHERE id = :id"
        ).bindparams(sa.bindparam("restored", type_=dt)),
            {"restored": restored, "id": row.id})

        params = {"restored": restored, "user_id": row.user_id, "cutoff": CUTOFF}
        media = []
        if row.book_pair_id is not None:
            scope = ["book_pair_id = :pair_id"]
            params["pair_id"] = row.book_pair_id
            ebook_id, audiobook_id = row.pair_ebook_id, row.pair_audiobook_id
        else:
            scope = []
            ebook_id, audiobook_id = row.ebook_id, row.audiobook_id
        if ebook_id is not None:
            media.append("ebook_id = :ebook_id")
            params["ebook_id"] = ebook_id
        if audiobook_id is not None:
            media.append("audiobook_id = :audiobook_id")
            params["audiobook_id"] = audiobook_id
        if media:
            scope.append(f"(book_pair_id IS NULL AND ({' OR '.join(media)}))")
        if not scope:
            continue

        conn.execute(sa.text(
            "UPDATE user_progress SET captured_at = :restored "
            "WHERE user_id = :user_id AND device_id IS NULL "
            "AND captured_at = updated_at AND captured_at >= :cutoff "
            f"AND captured_at > :restored AND ({' OR '.join(scope)})"
        ).bindparams(sa.bindparam("restored", type_=dt), sa.bindparam("cutoff", type_=dt)),
            params)


def downgrade() -> None:
    pass
