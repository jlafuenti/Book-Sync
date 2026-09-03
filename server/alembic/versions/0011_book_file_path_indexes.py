"""unique file_path per ebook/audiobook, and index the book_pairs FKs

`ebooks.file_path` and `audiobooks.file_path` are the identity keys: the library
scan, the ACSM/convert path and `_register_epub_in_db` all look a row up by path
with `.scalar_one_or_none()`. Nothing enforced that. One duplicate row — a manual
insert, a restore from an older dump, a second ingest worker added later — and
every one of those lookups raises `MultipleResultsFound`, so the whole library
scan 500s until somebody deletes a row by hand. That is issue #64's failure in a
different table (issue #256).

Also indexed here, without constraining anything:

* `ebooks.file_hash` / `audiobooks.file_hash` — read per file per scan for
  auto-pairing and duplicate detection. Two identical files at different paths
  are legitimate, so this is an index, not a unique.
* `book_pairs.ebook_id` / `book_pairs.audiobook_id` — the library browse's
  paired/unpaired filter runs `EXISTS (SELECT 1 FROM book_pairs WHERE ebook_id =
  ebooks.id)` for every row of every page.

And `uq_book_pairs_pair`, which is belt-and-braces: `create_pair` already returns
409 on a duplicate pairing, so this only stops that check-then-insert from being
the sole guarantee.

Order inside upgrade():

1. Collapse duplicate `file_path` rows (see :func:`dedupe_file_paths`). MUST come
   first — creating a unique index on a table that already carries a duplicate
   fails, which is most of the point.
2. Collapse duplicate `(ebook_id, audiobook_id)` pairs, for the same reason.
3. Create the indexes and the constraint.

Postgres caveat, accepted deliberately: a btree entry must fit in roughly 2,700
bytes, and `file_path` is `String(2000)`, which a multibyte path could exceed —
the INSERT would then fail with "index row size ... exceeds btree version 4
maximum". Real library paths are two orders of magnitude shorter than that (the
longest in production is well under 200 bytes), and a path that long would break
the filesystem tooling around it first. The alternative — a functional unique
index on `md5(file_path)` — is Postgres-only, so it would not exist on the SQLite
schema the tests build, and it silently converts a duplicate-path bug into a
hash-collision bug. If a deployment ever hits the limit, that is the fix; until
then the plain column index is the one both databases can agree on.

Revision ID: 0011_book_path_indexes
Revises: 0010_syncmap_epub_hash
Create Date: 2026-09-02

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
# NOTE: keep this at or under 32 chars — see the note in
# 0002_conflict_resolution.py (alembic_version is a VARCHAR(32)).
revision: str = "0011_book_path_indexes"
down_revision: Union[str, None] = "0010_syncmap_epub_hash"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# (table, the book_pairs/bookmarks/user_progress column pointing at it,
#  library_check_results.item_type value, user_progress.media_type value)
_MEDIA = (
    ("ebooks", "ebook_id", "ebook", "EBOOK"),
    ("audiobooks", "audiobook_id", "audiobook", "AUDIOBOOK"),
)


def _exec(conn, sql: str, params: dict | None = None):
    return conn.execute(sa.text(sql), params or {})


def _delete_bookmarks(conn, where: str, params: dict) -> None:
    """Delete bookmarks matching `where`, children first.

    The `bookmarks -> bookmark_logs / position_hints` cascades are declared on
    the ORM relationships, not on the database, so a bare DELETE would leave
    orphans (Postgres) or fail the FK check (SQLite with foreign_keys on).
    """
    _exec(conn, f"DELETE FROM position_hints WHERE bookmark_id IN "
                f"(SELECT id FROM bookmarks WHERE {where})", params)
    _exec(conn, f"DELETE FROM bookmark_logs WHERE bookmark_id IN "
                f"(SELECT id FROM bookmarks WHERE {where})", params)
    _exec(conn, f"DELETE FROM bookmarks WHERE {where}", params)


def _delete_pair(conn, pair_id: int) -> None:
    """Remove a pair and everything hanging off it.

    Only reached for a pair that duplicates another one — same two files, or the
    same pairing recorded twice. The surviving pair keeps its own sync map and
    bookmarks; this drops the redundant copy. A bare `DELETE FROM book_pairs` is
    wrong: `BookPair`'s cascades to sync maps and bookmarks are ORM-level, so the
    children have to go first, and in dependency order.
    """
    p = {"p": pair_id}
    _exec(conn, "DELETE FROM sync_points WHERE sync_map_id IN "
                "(SELECT id FROM sync_maps WHERE book_pair_id = :p)", p)
    _exec(conn, "DELETE FROM sync_maps WHERE book_pair_id = :p", p)
    # `audio_transcripts` names the column `pair_id`, not `book_pair_id`.
    _exec(conn, "DELETE FROM audio_transcripts WHERE pair_id = :p", p)
    _exec(conn, "DELETE FROM transcription_queue WHERE book_pair_id = :p", p)
    _delete_bookmarks(conn, "book_pair_id = :p", p)
    _exec(conn, "DELETE FROM user_progress WHERE book_pair_id = :p", p)
    _exec(conn, "DELETE FROM book_pairs WHERE id = :p", p)


def _absorb(conn, col: str, item_type: str, media_type: str,
            loser: int, keeper: int) -> None:
    """Move everything that references `loser` onto `keeper`.

    `keeper` and `loser` are two rows for the *same file*, so every reference to
    the loser is a reference to the keeper. Where re-pointing would collide with
    a row the keeper already has, the loser's row is dropped rather than kept —
    both describe the same file, and the keeper's is the one the application has
    been reading. The alternative (skip, keep both) leaves the duplicate in place
    and the unique index cannot be created at all.
    """
    other_col = "audiobook_id" if col == "ebook_id" else "ebook_id"

    # ---- book_pairs ------------------------------------------------------
    for pair in _exec(conn,
                      f"SELECT id, {other_col} AS other FROM book_pairs "
                      f"WHERE {col} = :l", {"l": loser}).all():
        clash = _exec(conn,
                      f"SELECT 1 FROM book_pairs WHERE {col} = :k "
                      f"AND {other_col} = :o", {"k": keeper, "o": pair.other}).first()
        if clash:
            _delete_pair(conn, pair.id)
        else:
            _exec(conn, f"UPDATE book_pairs SET {col} = :k WHERE id = :i",
                  {"k": keeper, "i": pair.id})

    # ---- bookmarks -------------------------------------------------------
    # `ux_bookmarks_user_<scope>` is partial: it only applies to standalone rows
    # (`book_pair_id IS NULL`). A bookmark scoped to a pair cannot collide here,
    # so it is always re-pointed.
    for bm in _exec(conn,
                    f"SELECT id, user_id, book_pair_id FROM bookmarks "
                    f"WHERE {col} = :l", {"l": loser}).all():
        clash = None
        if bm.book_pair_id is None:
            clash = _exec(conn,
                          f"SELECT 1 FROM bookmarks WHERE user_id = :u "
                          f"AND book_pair_id IS NULL AND {col} = :k",
                          {"u": bm.user_id, "k": keeper}).first()
        if clash:
            _delete_bookmarks(conn, "id = :i", {"i": bm.id})
        else:
            _exec(conn, f"UPDATE bookmarks SET {col} = :k WHERE id = :i",
                  {"k": keeper, "i": bm.id})

    # ---- user_progress ---------------------------------------------------
    # `ux_user_progress_user_<scope>` is partial on the media type, so the clash
    # test has to carry it too (issue #64's indexes).
    for up in _exec(conn,
                    f"SELECT id, user_id FROM user_progress "
                    f"WHERE {col} = :l AND media_type = :m",
                    {"l": loser, "m": media_type}).all():
        clash = _exec(conn,
                      f"SELECT 1 FROM user_progress WHERE user_id = :u "
                      f"AND media_type = :m AND {col} = :k",
                      {"u": up.user_id, "m": media_type, "k": keeper}).first()
        if clash:
            _exec(conn, "DELETE FROM user_progress WHERE id = :i", {"i": up.id})
        else:
            _exec(conn, f"UPDATE user_progress SET {col} = :k WHERE id = :i",
                  {"k": keeper, "i": up.id})

    # Rows in the *other* scope, or with a NULL media_type, still point at a row
    # that is about to disappear. They cannot collide (the partial index does not
    # cover them), so move them across unconditionally.
    _exec(conn, f"UPDATE user_progress SET {col} = :k WHERE {col} = :l",
          {"k": keeper, "l": loser})

    # ---- library_check_results ------------------------------------------
    # No FK (it is keyed by (item_type, item_id)), but leaving it behind would
    # attach a stale integrity result to whatever id is reused next. Unique on
    # (item_type, item_id, check_type); it is a cache, so a clash is dropped.
    for res in _exec(conn,
                     "SELECT id, check_type FROM library_check_results "
                     "WHERE item_type = :t AND item_id = :l",
                     {"t": item_type, "l": loser}).all():
        clash = _exec(conn,
                      "SELECT 1 FROM library_check_results WHERE item_type = :t "
                      "AND item_id = :k AND check_type = :c",
                      {"t": item_type, "k": keeper, "c": res.check_type}).first()
        if clash:
            _exec(conn, "DELETE FROM library_check_results WHERE id = :i",
                  {"i": res.id})
        else:
            _exec(conn, "UPDATE library_check_results SET item_id = :k WHERE id = :i",
                  {"k": keeper, "i": res.id})


def dedupe_file_paths(conn) -> None:
    """Collapse each set of same-`file_path` rows onto its lowest id.

    Lowest id, not newest: the low row is the one every existing pair, bookmark
    and progress row was created against, so keeping it makes the fewest
    references move. All the rows describe one file, so which survives is
    otherwise arbitrary.

    MUST run before the unique indexes are created. Separated from
    :func:`upgrade` so it can be exercised without Postgres (see
    tests/test_migration_0011_data.py); the SQL is plain and portable.
    """
    for table, col, item_type, media_type in _MEDIA:
        rows = _exec(conn,
                     f"SELECT id, file_path FROM {table} WHERE file_path IN "
                     f"(SELECT file_path FROM {table} "
                     f" GROUP BY file_path HAVING COUNT(*) > 1) "
                     f"ORDER BY file_path, id").all()

        keeper_of: dict[str, int] = {}
        for row in rows:
            keeper = keeper_of.setdefault(row.file_path, row.id)
            if keeper == row.id:
                continue
            _absorb(conn, col, item_type, media_type, row.id, keeper)
            _exec(conn, f"DELETE FROM {table} WHERE id = :i", {"i": row.id})


def dedupe_book_pairs(conn) -> None:
    """Collapse duplicate `(ebook_id, audiobook_id)` pairings onto the lowest id.

    `create_pair` has always rejected these with a 409, so this should find
    nothing; it runs because `uq_book_pairs_pair` cannot be created if it ever
    did. Note this runs *after* :func:`dedupe_file_paths`, which can itself
    re-point two pairs onto the same two books.
    """
    rows = _exec(conn,
                 "SELECT id FROM book_pairs p WHERE EXISTS ("
                 "  SELECT 1 FROM book_pairs o WHERE o.ebook_id = p.ebook_id "
                 "    AND o.audiobook_id = p.audiobook_id AND o.id < p.id)").all()
    for row in rows:
        _delete_pair(conn, row.id)


def upgrade() -> None:
    conn = op.get_bind()
    dedupe_file_paths(conn)
    dedupe_book_pairs(conn)

    op.create_index("ux_ebooks_file_path", "ebooks", ["file_path"], unique=True)
    op.create_index("ix_ebooks_file_hash", "ebooks", ["file_hash"])
    op.create_index("ux_audiobooks_file_path", "audiobooks", ["file_path"], unique=True)
    op.create_index("ix_audiobooks_file_hash", "audiobooks", ["file_hash"])
    op.create_index("ix_book_pairs_ebook_id", "book_pairs", ["ebook_id"])
    op.create_index("ix_book_pairs_audiobook_id", "book_pairs", ["audiobook_id"])

    # SQLite has no ALTER TABLE ADD CONSTRAINT, so the constraint goes on inside
    # a batch operation (a no-op wrapper on Postgres, a table rebuild on SQLite).
    with op.batch_alter_table("book_pairs") as batch:
        batch.create_unique_constraint("uq_book_pairs_pair", ["ebook_id", "audiobook_id"])


def downgrade() -> None:
    # The collapsed rows are not restorable; only the schema comes off.
    with op.batch_alter_table("book_pairs") as batch:
        batch.drop_constraint("uq_book_pairs_pair", type_="unique")

    op.drop_index("ix_book_pairs_audiobook_id", table_name="book_pairs")
    op.drop_index("ix_book_pairs_ebook_id", table_name="book_pairs")
    op.drop_index("ix_audiobooks_file_hash", table_name="audiobooks")
    op.drop_index("ux_audiobooks_file_path", table_name="audiobooks")
    op.drop_index("ix_ebooks_file_hash", table_name="ebooks")
    op.drop_index("ux_ebooks_file_path", table_name="ebooks")
