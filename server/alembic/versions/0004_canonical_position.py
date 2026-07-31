"""canonical position record + tagged position hints

Makes `bookmarks` the one canonical reading-position record and adds
`position_hints`, replacing the scheme where a single `bookmarks.epub_locator`
column was shared by every device and cleared whenever another client moved the
anchor. Clearing it destroyed positions: a reader that found no locator treated
it as "no position", opened at page one, and its autosave then wrote chapter 0
over a real position.

Also re-bases stored sync points onto the EPUB spine index. `epub_chapter` used
to be an index into *manifest* documents that produced sentences — an ordinal
neither reader could act on (both position by spine index).

Order matters inside upgrade():

1. Add the columns.
2. Persist each bookmark's `epub_text_preview` and percent **from the old sync
   map**, while old chapter numbers still mean what the sync points mean.
   Text is axis-independent, so these positions stay resolvable afterwards.
   `bookmarks.epub_chapter` itself is NOT remapped: it is ambiguous per row
   (sync-map space when the text matcher hit, spine space when it missed), so
   there is no correct remap for it. The text preview is the rescue.
3. Re-base `sync_points.epub_chapter` onto spine indices.
4. Backfill hints from the legacy locator/CFI columns.
5. Give standalone media canonical rows.

Revision ID: 0004_canonical_position
Revises: 0003_bookmark_locator_audio
Create Date: 2026-07-30

"""
import logging
import os
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
# NOTE: keep this at or under 32 chars — see 0002_conflict_resolution.py.
revision: str = "0004_canonical_position"
down_revision: Union[str, None] = "0003_bookmark_locator_audio"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

logger = logging.getLogger("alembic.runtime.migration")

hintkind = sa.Enum("READIUM_LOCATOR", "EPUBJS_CFI", name="hintkind")


def _remap_sync_points(conn) -> None:
    """Re-base sync_points.epub_chapter from the old compacted ordinal onto the
    EPUB spine index, by re-parsing each ebook.

    A remap rather than a re-alignment: it relabels chapters and leaves the
    alignment itself byte-identical. Books whose file is missing or unparseable
    are left alone and logged — their positions still resolve through the text
    preview rung of the restore ladder.
    """
    try:
        from services.epub_parser import (
            _extract_epub_documents_via_zip,
            old_chapter_to_spine_index,
        )
    except Exception as exc:  # pragma: no cover - import guard
        logger.warning("0004: parser unavailable (%s); skipping sync-point remap", exc)
        return

    rows = conn.execute(sa.text(
        "SELECT sm.id AS sync_map_id, e.file_path AS file_path "
        "FROM sync_maps sm "
        "JOIN book_pairs bp ON bp.id = sm.book_pair_id "
        "JOIN ebooks e ON e.id = bp.ebook_id"
    )).mappings().all()

    for row in rows:
        path = row["file_path"]
        if not path or not os.path.exists(path) or not path.lower().endswith(".epub"):
            logger.warning("0004: sync_map %s — ebook not parseable at %r, chapters left as-is",
                           row["sync_map_id"], path)
            continue
        try:
            documents = _extract_epub_documents_via_zip(path)
            mapping = old_chapter_to_spine_index(documents)
        except Exception as exc:
            logger.warning("0004: sync_map %s — parse failed (%s), chapters left as-is",
                           row["sync_map_id"], exc)
            continue

        if mapping == list(range(len(mapping))):
            continue  # identity: no skipped documents, nothing to re-base

        # Apply highest-first so an in-place UPDATE can't collide with a value
        # it is about to write (new indices are always >= old ones).
        for old_chapter in range(len(mapping) - 1, -1, -1):
            new_chapter = mapping[old_chapter]
            if new_chapter == old_chapter:
                continue
            conn.execute(
                sa.text(
                    "UPDATE sync_points SET epub_chapter = :new "
                    "WHERE sync_map_id = :smid AND epub_chapter = :old"
                ),
                {"new": new_chapter, "old": old_chapter, "smid": row["sync_map_id"]},
            )


def rescue_bookmark_anchors(conn) -> None:
    """Persist an axis-independent anchor for every existing bookmark.

    MUST run before :func:`_remap_sync_points`. While old chapter numbers still
    mean what the sync points mean, each bookmark's text preview is looked up
    and stored; text survives the re-basing that follows.

    `bookmarks.epub_chapter` itself is deliberately NOT remapped. It is
    ambiguous per row — sync-map space when the text matcher hit, spine space
    when it missed — so there is no correct remap for it. The preview is the
    rescue, and the percent is the coarse backstop.
    """
    conn.execute(sa.text(
        "UPDATE bookmarks SET epub_text_preview = ("
        "  SELECT sp.epub_text_preview FROM sync_points sp"
        "  JOIN sync_maps sm ON sm.id = sp.sync_map_id"
        "  WHERE sm.book_pair_id = bookmarks.book_pair_id"
        "    AND sp.epub_chapter = bookmarks.epub_chapter"
        "    AND sp.epub_sentence_index = bookmarks.epub_sentence_index"
        "    AND sp.epub_text_preview IS NOT NULL"
        "  LIMIT 1)"
        " WHERE bookmarks.book_pair_id IS NOT NULL"
        "   AND bookmarks.epub_text_preview IS NULL"
    ))
    conn.execute(sa.text(
        "UPDATE bookmarks SET epub_progress_percent = ("
        "  SELECT up.epub_progress_percent FROM user_progress up"
        "  WHERE up.user_id = bookmarks.user_id"
        "    AND up.book_pair_id = bookmarks.book_pair_id"
        "    AND up.media_type = 'EBOOK'"
        "    AND up.epub_progress_percent IS NOT NULL"
        "  LIMIT 1)"
        " WHERE bookmarks.book_pair_id IS NOT NULL"
        "   AND bookmarks.epub_progress_percent IS NULL"
    ))


def upgrade() -> None:
    conn = op.get_bind()
    is_sqlite = conn.dialect.name == "sqlite"

    # ---- 1. columns -------------------------------------------------------
    op.add_column("bookmarks", sa.Column("ebook_id", sa.Integer(), nullable=True))
    op.add_column("bookmarks", sa.Column("audiobook_id", sa.Integer(), nullable=True))
    op.add_column("bookmarks", sa.Column("epub_text_preview", sa.Text(), nullable=True))
    op.add_column("bookmarks", sa.Column("epub_progress_percent", sa.Float(), nullable=True))
    op.add_column("bookmarks", sa.Column(
        "is_completed", sa.Boolean(), nullable=False, server_default=sa.text("false")))
    op.add_column("bookmarks", sa.Column(
        "anchor_revision", sa.BigInteger(), nullable=False, server_default=sa.text("1")))

    op.alter_column("bookmarks", "book_pair_id", existing_type=sa.Integer(), nullable=True)

    op.create_index("ix_bookmarks_ebook_id", "bookmarks", ["ebook_id"])
    op.create_index("ix_bookmarks_audiobook_id", "bookmarks", ["audiobook_id"])
    if not is_sqlite:
        op.create_foreign_key(
            "fk_bookmarks_ebook_id", "bookmarks", "ebooks", ["ebook_id"], ["id"])
        op.create_foreign_key(
            "fk_bookmarks_audiobook_id", "bookmarks", "audiobooks", ["audiobook_id"], ["id"])

    op.create_index("ux_bookmarks_user_pair", "bookmarks", ["user_id", "book_pair_id"],
                    unique=True, postgresql_where=sa.text("book_pair_id IS NOT NULL"),
                    sqlite_where=sa.text("book_pair_id IS NOT NULL"))
    op.create_index("ux_bookmarks_user_ebook", "bookmarks", ["user_id", "ebook_id"],
                    unique=True,
                    postgresql_where=sa.text("book_pair_id IS NULL AND ebook_id IS NOT NULL"),
                    sqlite_where=sa.text("book_pair_id IS NULL AND ebook_id IS NOT NULL"))
    op.create_index("ux_bookmarks_user_audiobook", "bookmarks", ["user_id", "audiobook_id"],
                    unique=True,
                    postgresql_where=sa.text("book_pair_id IS NULL AND audiobook_id IS NOT NULL"),
                    sqlite_where=sa.text("book_pair_id IS NULL AND audiobook_id IS NOT NULL"))

    hintkind.create(conn, checkfirst=True)
    op.create_table(
        "position_hints",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("bookmark_id", sa.Integer(), nullable=False),
        sa.Column("device_id", sa.String(length=200), nullable=False),
        sa.Column("hint_kind", hintkind, nullable=False),
        sa.Column("hint_value", sa.Text(), nullable=False),
        sa.Column("anchor_revision", sa.BigInteger(), nullable=False),
        sa.Column("audio_position_ms", sa.Integer(), nullable=True),
        sa.Column("captured_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["bookmark_id"], ["bookmarks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("bookmark_id", "device_id", "hint_kind", name="ux_position_hints"),
    )
    op.create_index("ix_position_hints_bookmark_id", "position_hints", ["bookmark_id"])

    # ---- 2. rescue positions BEFORE the axis moves ------------------------
    rescue_bookmark_anchors(conn)

    # ---- 3. re-base sync points onto spine indices ------------------------
    _remap_sync_points(conn)

    # ---- 4. hints from the legacy columns ---------------------------------
    # Only where a device is known: an unattributable locator can't be
    # device-scoped, and treating it as current for everyone puts other
    # devices on the wrong page.
    conn.execute(sa.text(
        "INSERT INTO position_hints "
        "(bookmark_id, device_id, hint_kind, hint_value, anchor_revision, "
        " audio_position_ms, captured_at, updated_at) "
        "SELECT id, device_id, 'READIUM_LOCATOR', epub_locator, 1, "
        "       locator_audio_ms, captured_at, updated_at "
        "FROM bookmarks WHERE epub_locator IS NOT NULL AND device_id IS NOT NULL"
    ))
    conn.execute(sa.text(
        "INSERT INTO position_hints "
        "(bookmark_id, device_id, hint_kind, hint_value, anchor_revision, "
        " audio_position_ms, captured_at, updated_at) "
        "SELECT b.id, up.device_id, 'EPUBJS_CFI', up.epub_cfi, 1, "
        "       NULL, up.captured_at, up.updated_at "
        "FROM user_progress up "
        "JOIN bookmarks b ON b.book_pair_id = up.book_pair_id AND b.user_id = up.user_id "
        "WHERE up.epub_cfi IS NOT NULL AND up.device_id IS NOT NULL "
        "  AND up.media_type = 'EBOOK' AND up.book_pair_id IS NOT NULL"
    ))

    # ---- 5. canonical rows for standalone media ---------------------------
    # user_progress.epub_chapter is already a spine index on both clients, so
    # it carries over unchanged.
    for media, id_col in (("EBOOK", "ebook_id"), ("AUDIOBOOK", "audiobook_id")):
        conn.execute(sa.text(
            f"INSERT INTO bookmarks "
            f"(user_id, book_pair_id, {id_col}, source, epub_chapter, "
            f" epub_progress_percent, audio_position_ms, is_completed, anchor_revision, "
            f" updated_at, captured_at, device_id, device_name) "
            f"SELECT up.user_id, NULL, up.{id_col}, "
            f"       '{media}', up.epub_chapter, up.epub_progress_percent, "
            f"       up.audio_position_ms, up.is_completed, 1, "
            f"       up.updated_at, up.captured_at, up.device_id, up.device_name "
            f"FROM user_progress up "
            f"WHERE up.book_pair_id IS NULL AND up.{id_col} IS NOT NULL "
            f"  AND up.media_type = '{media}'"
        ))


def downgrade() -> None:
    conn = op.get_bind()
    is_sqlite = conn.dialect.name == "sqlite"

    # Standalone-scope rows have no place in the old schema (book_pair_id NOT NULL).
    conn.execute(sa.text("DELETE FROM bookmarks WHERE book_pair_id IS NULL"))

    op.drop_index("ix_position_hints_bookmark_id", table_name="position_hints")
    op.drop_table("position_hints")
    hintkind.drop(conn, checkfirst=True)

    op.drop_index("ux_bookmarks_user_audiobook", table_name="bookmarks")
    op.drop_index("ux_bookmarks_user_ebook", table_name="bookmarks")
    op.drop_index("ux_bookmarks_user_pair", table_name="bookmarks")
    if not is_sqlite:
        op.drop_constraint("fk_bookmarks_audiobook_id", "bookmarks", type_="foreignkey")
        op.drop_constraint("fk_bookmarks_ebook_id", "bookmarks", type_="foreignkey")
    op.drop_index("ix_bookmarks_audiobook_id", table_name="bookmarks")
    op.drop_index("ix_bookmarks_ebook_id", table_name="bookmarks")

    op.alter_column("bookmarks", "book_pair_id", existing_type=sa.Integer(), nullable=False)

    op.drop_column("bookmarks", "anchor_revision")
    op.drop_column("bookmarks", "is_completed")
    op.drop_column("bookmarks", "epub_progress_percent")
    op.drop_column("bookmarks", "epub_text_preview")
    op.drop_column("bookmarks", "audiobook_id")
    op.drop_column("bookmarks", "ebook_id")
