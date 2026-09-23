"""enforce one-to-one book pairs (issue #691)

The only uniqueness `book_pairs` ever had was on the *combination* of
`(ebook_id, audiobook_id)` (`uq_book_pairs_pair`, added in 0012). That let the
same ebook be paired to two different audiobooks at once -- `(E, A1)` and
`(E, A2)` are distinct rows under a composite constraint -- and nothing
anywhere asked whether `E` was already paired. In the field this happened once
by `auto_match` during a scan and once by hand through `POST
/api/library/pairs` three days later: two ~34-hour transcription jobs for one
book, and a reader's position able to land on either pair since
`user_progress`/`bookmarks` key on `book_pair_id`.

The operator's decision (not re-litigated here): a pair is strictly
one-to-one. An ebook may be in at most one `BookPair`; an audiobook may be in
at most one. This migration replaces the composite constraint with two
per-column unique indexes, `ux_book_pairs_ebook_id` and
`ux_book_pairs_audiobook_id` -- unique `ebook_id` alone already implies the
combination can never repeat, so `uq_book_pairs_pair` is redundant once these
exist and is dropped.

Both are plain `Index(unique=True)` rather than table-level
`UniqueConstraint`s, unlike `uq_book_pairs_pair` was. SQLite bakes a
`UniqueConstraint` into `CREATE TABLE`, so removing one means a full table
rebuild (`tests.factories.suspend_book_pair_uniqueness`); a separately
created unique index is just `DROP INDEX`, which is what `ux_ebooks_file_path`
/ `ux_audiobooks_file_path` already do (issue #256) and what production
operators reach for when they need to lift this temporarily.

**This migration does not delete or merge any pair.** A previous multi-pair
row is exactly the data loss issue #691 is about avoiding, so the choice of
which pair to keep is the operator's, not this migration's. `upgrade()`
checks first: if any `ebook_id` or `audiobook_id` already repeats across
`book_pairs`, it raises with the offending ids and pair ids named, and creates
no index. Resolve each by hand -- decide which pair to keep and unpair the
other (`DELETE /api/library/pairs/{id}`, or the Pairs page) -- then re-run
`alembic upgrade head`.

### Upgrade notes (also recorded in CHANGELOG.md)

Find any existing multi-paired rows before upgrading:

```sql
SELECT ebook_id, array_agg(id) FROM book_pairs
GROUP BY ebook_id HAVING count(*) > 1;

SELECT audiobook_id, array_agg(id) FROM book_pairs
GROUP BY audiobook_id HAVING count(*) > 1;
```

For each group, keep one pair and delete the others (`DELETE
/api/library/pairs/{id}` preserves the deleted pair's reading positions by
demoting them onto the surviving ebook/audiobook first -- see
`docs/position-sync-contract.md`). Then re-run the upgrade.

Revision ID: 0025_book_pairs_one_to_one
Revises: 0024_captured_at_backfill
Create Date: 2026-09-22

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
# NOTE: keep this at or under 32 chars — see the note in
# 0002_conflict_resolution.py (alembic_version is a VARCHAR(32)).
revision: str = "0025_book_pairs_one_to_one"
down_revision: Union[str, None] = "0024_captured_at_backfill"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _duplicate_report(conn, column: str) -> list[str]:
    """One line per `column` value shared by more than one `book_pairs` row,
    e.g. "ebook_id 42: pairs 7, 19". `array_agg`/`GROUP_CONCAT` differ between
    Postgres and SQLite, so the grouping happens in Python instead of SQL --
    this stays portable and testable without a real Postgres (issue #691).
    """
    rows = conn.execute(sa.text(
        f"SELECT {column} AS val, id FROM book_pairs WHERE {column} IN ("
        f"  SELECT {column} FROM book_pairs "
        f"  GROUP BY {column} HAVING COUNT(*) > 1"
        f") ORDER BY {column}, id"
    )).all()
    by_value: dict = {}
    for row in rows:
        by_value.setdefault(row.val, []).append(row.id)
    return [
        f"{column} {val}: pairs {', '.join(str(i) for i in ids)}"
        for val, ids in by_value.items()
    ]


def check_no_duplicate_pairs(conn) -> None:
    """Refuse to proceed if `book_pairs` already violates the one-to-one rule.

    Deliberately does not fix anything -- see the migration's own docstring
    for why. Raises `RuntimeError` naming every offending id so an operator
    can resolve them by hand, rather than the opaque `IntegrityError` that
    `create_index` would otherwise raise partway through.
    """
    lines = _duplicate_report(conn, "ebook_id") + _duplicate_report(conn, "audiobook_id")
    if lines:
        raise RuntimeError(
            "book_pairs has rows that share an ebook_id or audiobook_id "
            "(issue #691); upgrading would create a unique index these "
            "violate:\n  " + "\n  ".join(lines) +
            "\nResolve each by hand: decide which pair to keep and unpair "
            "the other (DELETE /api/library/pairs/{id}, or the Pairs page), "
            "then re-run `alembic upgrade head`. See this migration's "
            "docstring (0025_book_pairs_one_to_one.py) or CHANGELOG.md's "
            "Upgrade notes for the query that finds these."
        )


def upgrade() -> None:
    conn = op.get_bind()
    check_no_duplicate_pairs(conn)

    op.create_index("ux_book_pairs_ebook_id", "book_pairs", ["ebook_id"], unique=True)
    op.create_index("ux_book_pairs_audiobook_id", "book_pairs", ["audiobook_id"], unique=True)

    # SQLite has no ALTER TABLE ... DROP CONSTRAINT, so this goes inside a
    # batch operation (a no-op wrapper on Postgres, a table rebuild on SQLite
    # that carries the two indexes just created above along with it — same
    # pattern 0012 used to *add* this same constraint).
    with op.batch_alter_table("book_pairs") as batch:
        batch.drop_constraint("uq_book_pairs_pair", type_="unique")

    # Now redundant: a unique index on `ebook_id` alone already covers every
    # lookup the plain index served.
    op.drop_index("ix_book_pairs_ebook_id", table_name="book_pairs")
    op.drop_index("ix_book_pairs_audiobook_id", table_name="book_pairs")


def downgrade() -> None:
    op.create_index("ix_book_pairs_ebook_id", "book_pairs", ["ebook_id"])
    op.create_index("ix_book_pairs_audiobook_id", "book_pairs", ["audiobook_id"])

    with op.batch_alter_table("book_pairs") as batch:
        batch.create_unique_constraint("uq_book_pairs_pair", ["ebook_id", "audiobook_id"])

    op.drop_index("ux_book_pairs_audiobook_id", table_name="book_pairs")
    op.drop_index("ux_book_pairs_ebook_id", table_name="book_pairs")
