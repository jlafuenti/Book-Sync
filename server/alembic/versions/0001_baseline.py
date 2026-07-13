"""baseline schema

Captures the full schema as it stood when Alembic was introduced (issue #53) —
i.e. everything the old ``init_db()`` produced: ``create_all`` plus the ~50
hand-written ``ALTER TABLE ... ADD COLUMN`` / ``ALTER COLUMN ... TYPE``
statements, including their server defaults and the JSONB column types.

Existing databases are ``alembic stamp``ed at this revision (they already have
this schema); fresh databases build it from scratch via ``alembic upgrade head``.

Revision ID: 0001_baseline
Revises:
Create Date: 2026-07-13

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0001_baseline"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Native Postgres enum types. ``bookmarksource`` is shared by two tables, so we
# create each type once up front (checkfirst) and reference these objects with
# ``create_type=False`` in the columns below — otherwise the second table using a
# type would emit a duplicate ``CREATE TYPE`` and fail.
pairstatus = postgresql.ENUM(
    "UNMATCHED", "AUTO_MATCHED", "MANUAL_MATCHED", "TRANSCRIBING", "SYNCED", "ERROR",
    name="pairstatus", create_type=False,
)
bookmarksource = postgresql.ENUM(
    "EBOOK", "AUDIOBOOK", name="bookmarksource", create_type=False,
)
progresstype = postgresql.ENUM(
    "EBOOK", "AUDIOBOOK", name="progresstype", create_type=False,
)

# Reused JSONB-on-Postgres / JSON-elsewhere type (matches models.book.JSON_OR_JSONB).
_json = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    bind = op.get_bind()
    pairstatus.create(bind, checkfirst=True)
    bookmarksource.create(bind, checkfirst=True)
    progresstype.create(bind, checkfirst=True)

    op.create_table(
        "audiobooks",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("author", sa.String(length=500), nullable=True),
        sa.Column("filename", sa.String(length=1000), nullable=False),
        sa.Column("file_path", sa.String(length=2000), nullable=False),
        sa.Column("file_hash", sa.String(length=64), nullable=True),
        sa.Column("file_size", sa.BigInteger(), nullable=True),
        sa.Column("duration_seconds", sa.Integer(), nullable=True),
        sa.Column("format", sa.String(length=10), nullable=False),
        sa.Column("series", sa.String(length=500), nullable=True),
        sa.Column("series_index", sa.Float(), nullable=True),
        sa.Column("metadata_source", sa.String(length=50), nullable=True),
        sa.Column("metadata_pattern", sa.String(length=500), nullable=True),
        sa.Column("uploaded_at", sa.DateTime(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("publisher", sa.String(length=500), nullable=True),
        sa.Column("publish_year", sa.Integer(), nullable=True),
        sa.Column("language", sa.String(length=50), nullable=True),
        sa.Column("genres", sa.String(length=1000), nullable=True),
        sa.Column("tags", sa.String(length=1000), nullable=True),
        sa.Column("isbn", sa.String(length=100), nullable=True),
        sa.Column("asin", sa.String(length=100), nullable=True),
        sa.Column("narrators", sa.String(length=500), nullable=True),
        sa.Column("is_explicit", sa.Boolean(), nullable=True),
        sa.Column("is_abridged", sa.Boolean(), nullable=True),
        sa.Column("cover_path", sa.String(length=2000), nullable=True),
        sa.Column("import_source", sa.String(length=50), nullable=True),
        sa.Column("external_id", sa.String(length=200), nullable=True),
        sa.Column("acknowledged", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("auto_pair_excluded_hashes", _json, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "ebooks",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("author", sa.String(length=500), nullable=True),
        sa.Column("filename", sa.String(length=1000), nullable=False),
        sa.Column("file_path", sa.String(length=2000), nullable=False),
        sa.Column("file_hash", sa.String(length=64), nullable=True),
        sa.Column("file_size", sa.BigInteger(), nullable=True),
        sa.Column("format", sa.String(length=10), nullable=False),
        sa.Column("uploaded_at", sa.DateTime(), nullable=False),
        sa.Column("series", sa.String(length=500), nullable=True),
        sa.Column("series_index", sa.Float(), nullable=True),
        sa.Column("metadata_source", sa.String(length=50), nullable=True),
        sa.Column("metadata_pattern", sa.String(length=500), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("publisher", sa.String(length=500), nullable=True),
        sa.Column("publish_year", sa.Integer(), nullable=True),
        sa.Column("language", sa.String(length=50), nullable=True),
        sa.Column("genres", sa.String(length=1000), nullable=True),
        sa.Column("tags", sa.String(length=1000), nullable=True),
        sa.Column("isbn", sa.String(length=100), nullable=True),
        sa.Column("asin", sa.String(length=100), nullable=True),
        sa.Column("narrators", sa.String(length=500), nullable=True),
        sa.Column("is_explicit", sa.Boolean(), nullable=True),
        sa.Column("is_abridged", sa.Boolean(), nullable=True),
        sa.Column("cover_path", sa.String(length=2000), nullable=True),
        sa.Column("import_source", sa.String(length=50), nullable=True),
        sa.Column("external_id", sa.String(length=200), nullable=True),
        sa.Column("acknowledged", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("auto_pair_excluded_hashes", _json, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "import_jobs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("source_key", sa.String(length=50), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("trigger", sa.String(length=20), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("items_added", sa.Integer(), nullable=False),
        sa.Column("items_skipped", sa.Integer(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "import_source_credentials",
        sa.Column("source_key", sa.String(length=50), nullable=False),
        sa.Column("blob_encrypted", sa.LargeBinary(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("source_key"),
    )
    op.create_table(
        "import_sources",
        sa.Column("source_key", sa.String(length=50), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("auto_sync_enabled", sa.Boolean(), nullable=False),
        sa.Column("cadence_hours", sa.Integer(), nullable=False),
        sa.Column("last_sync_at", sa.DateTime(), nullable=True),
        sa.Column("last_status", sa.String(length=20), nullable=True),
        sa.Column("last_message", sa.Text(), nullable=True),
        sa.Column("progress_current", sa.Integer(), nullable=True),
        sa.Column("progress_total", sa.Integer(), nullable=True),
        sa.Column("progress_title", sa.String(length=500), nullable=True),
        sa.Column("config", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("source_key"),
    )
    op.create_table(
        "library_check_results",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("item_type", sa.String(length=20), nullable=False),
        sa.Column("item_id", sa.Integer(), nullable=False),
        sa.Column("check_type", sa.String(length=40), nullable=False),
        sa.Column("file_path", sa.String(length=2000), nullable=True),
        sa.Column("file_size", sa.BigInteger(), nullable=True),
        sa.Column("file_mtime", sa.Float(), nullable=True),
        sa.Column("ok", sa.Boolean(), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("checked_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("item_type", "item_id", "check_type", name="uq_check_item_domain"),
    )
    op.create_table(
        "system_settings",
        sa.Column("key", sa.String(length=100), nullable=False),
        sa.Column("value", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("key"),
    )
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("username", sa.String(length=50), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("hashed_password", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False, server_default=sa.text("'user'")),
        sa.Column("is_admin", sa.Boolean(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("theme", sa.String(length=50), nullable=False, server_default=sa.text("'blueprint'")),
        sa.Column("must_reset_password", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("token_version", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("action", sa.String(length=50), nullable=False),
        sa.Column("target_user_id", sa.Integer(), nullable=True),
        sa.Column("details", sa.Text(), nullable=True),
        sa.Column("ip_address", sa.String(length=45), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "book_pairs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("ebook_id", sa.Integer(), nullable=False),
        sa.Column("audiobook_id", sa.Integer(), nullable=False),
        sa.Column("status", pairstatus, nullable=False),
        sa.Column("matched_at", sa.DateTime(), nullable=True),
        sa.Column("synced_at", sa.DateTime(), nullable=True),
        sa.Column("ignored_fields", _json, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("acknowledged", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.ForeignKeyConstraint(["audiobook_id"], ["audiobooks.id"], ),
        sa.ForeignKeyConstraint(["ebook_id"], ["ebooks.id"], ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "audio_transcripts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("pair_id", sa.Integer(), nullable=False),
        sa.Column("audiobook_path", sa.String(length=2000), nullable=False),
        sa.Column("sentence_count", sa.Integer(), nullable=False),
        sa.Column("sentences_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["pair_id"], ["book_pairs.id"], ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "bookmarks",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("book_pair_id", sa.Integer(), nullable=False),
        sa.Column("source", bookmarksource, nullable=False),
        sa.Column("epub_chapter", sa.Integer(), nullable=True),
        sa.Column("epub_sentence_index", sa.Integer(), nullable=True),
        sa.Column("audio_position_ms", sa.Integer(), nullable=True),
        sa.Column("epub_locator", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("synced_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["book_pair_id"], ["book_pairs.id"], ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "sync_maps",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("book_pair_id", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("total_sentences", sa.Integer(), nullable=False),
        sa.Column("total_chapters", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["book_pair_id"], ["book_pairs.id"], ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("book_pair_id"),
    )
    op.create_table(
        "transcription_queue",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("book_pair_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("progress", sa.Float(), nullable=False),
        sa.Column("message", sa.String(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["book_pair_id"], ["book_pairs.id"], ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "user_progress",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("book_pair_id", sa.Integer(), nullable=True),
        sa.Column("ebook_id", sa.Integer(), nullable=True),
        sa.Column("audiobook_id", sa.Integer(), nullable=True),
        sa.Column("media_type", progresstype, nullable=False),
        sa.Column("epub_cfi", sa.String(length=500), nullable=True),
        sa.Column("epub_chapter", sa.Integer(), nullable=True),
        sa.Column("epub_progress_percent", sa.Float(), nullable=True),
        sa.Column("audio_position_ms", sa.Integer(), nullable=True),
        sa.Column("is_completed", sa.Boolean(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("device_id", sa.String(length=200), nullable=True),
        sa.ForeignKeyConstraint(["audiobook_id"], ["audiobooks.id"], ),
        sa.ForeignKeyConstraint(["book_pair_id"], ["book_pairs.id"], ),
        sa.ForeignKeyConstraint(["ebook_id"], ["ebooks.id"], ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "bookmark_logs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("bookmark_id", sa.Integer(), nullable=False),
        sa.Column("source", bookmarksource, nullable=False),
        sa.Column("prev_epub_chapter", sa.Integer(), nullable=True),
        sa.Column("prev_epub_sentence_index", sa.Integer(), nullable=True),
        sa.Column("prev_audio_position_ms", sa.Integer(), nullable=True),
        sa.Column("new_epub_chapter", sa.Integer(), nullable=True),
        sa.Column("new_epub_sentence_index", sa.Integer(), nullable=True),
        sa.Column("new_audio_position_ms", sa.Integer(), nullable=True),
        sa.Column("changed_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["bookmark_id"], ["bookmarks.id"], ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "sync_points",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("sync_map_id", sa.Integer(), nullable=False),
        sa.Column("epub_chapter", sa.Integer(), nullable=False),
        sa.Column("epub_sentence_index", sa.Integer(), nullable=False),
        sa.Column("epub_text_preview", sa.String(length=200), nullable=True),
        sa.Column("audio_start_ms", sa.Integer(), nullable=False),
        sa.Column("audio_end_ms", sa.Integer(), nullable=False),
        sa.Column("audio_text", sa.Text(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False, server_default=sa.text("0")),
        sa.ForeignKeyConstraint(["sync_map_id"], ["sync_maps.id"], ),
        sa.PrimaryKeyConstraint("id"),
    )

    # Indexes (from index=True / unique=True columns and explicit Index()).
    op.create_index(op.f("ix_import_jobs_source_key"), "import_jobs", ["source_key"], unique=False)
    op.create_index("ix_check_ok", "library_check_results", ["check_type", "ok"], unique=False)
    op.create_index(op.f("ix_library_check_results_id"), "library_check_results", ["id"], unique=False)
    op.create_index(op.f("ix_library_check_results_item_id"), "library_check_results", ["item_id"], unique=False)
    op.create_index(op.f("ix_users_email"), "users", ["email"], unique=True)
    op.create_index(op.f("ix_users_username"), "users", ["username"], unique=True)
    op.create_index(op.f("ix_audit_logs_action"), "audit_logs", ["action"], unique=False)
    op.create_index(op.f("ix_audit_logs_created_at"), "audit_logs", ["created_at"], unique=False)
    op.create_index(op.f("ix_audio_transcripts_pair_id"), "audio_transcripts", ["pair_id"], unique=True)
    op.create_index(op.f("ix_bookmarks_book_pair_id"), "bookmarks", ["book_pair_id"], unique=False)
    op.create_index(op.f("ix_bookmarks_user_id"), "bookmarks", ["user_id"], unique=False)
    op.create_index(op.f("ix_transcription_queue_book_pair_id"), "transcription_queue", ["book_pair_id"], unique=False)
    op.create_index(op.f("ix_transcription_queue_id"), "transcription_queue", ["id"], unique=False)
    op.create_index(op.f("ix_user_progress_audiobook_id"), "user_progress", ["audiobook_id"], unique=False)
    op.create_index(op.f("ix_user_progress_book_pair_id"), "user_progress", ["book_pair_id"], unique=False)
    op.create_index(op.f("ix_user_progress_ebook_id"), "user_progress", ["ebook_id"], unique=False)
    op.create_index(op.f("ix_user_progress_user_id"), "user_progress", ["user_id"], unique=False)
    op.create_index(op.f("ix_bookmark_logs_bookmark_id"), "bookmark_logs", ["bookmark_id"], unique=False)
    op.create_index(op.f("ix_sync_points_sync_map_id"), "sync_points", ["sync_map_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_sync_points_sync_map_id"), table_name="sync_points")
    op.drop_index(op.f("ix_bookmark_logs_bookmark_id"), table_name="bookmark_logs")
    op.drop_index(op.f("ix_user_progress_user_id"), table_name="user_progress")
    op.drop_index(op.f("ix_user_progress_ebook_id"), table_name="user_progress")
    op.drop_index(op.f("ix_user_progress_book_pair_id"), table_name="user_progress")
    op.drop_index(op.f("ix_user_progress_audiobook_id"), table_name="user_progress")
    op.drop_index(op.f("ix_transcription_queue_id"), table_name="transcription_queue")
    op.drop_index(op.f("ix_transcription_queue_book_pair_id"), table_name="transcription_queue")
    op.drop_index(op.f("ix_bookmarks_user_id"), table_name="bookmarks")
    op.drop_index(op.f("ix_bookmarks_book_pair_id"), table_name="bookmarks")
    op.drop_index(op.f("ix_audio_transcripts_pair_id"), table_name="audio_transcripts")
    op.drop_index(op.f("ix_audit_logs_created_at"), table_name="audit_logs")
    op.drop_index(op.f("ix_audit_logs_action"), table_name="audit_logs")
    op.drop_index(op.f("ix_users_username"), table_name="users")
    op.drop_index(op.f("ix_users_email"), table_name="users")
    op.drop_index(op.f("ix_library_check_results_item_id"), table_name="library_check_results")
    op.drop_index(op.f("ix_library_check_results_id"), table_name="library_check_results")
    op.drop_index("ix_check_ok", table_name="library_check_results")
    op.drop_index(op.f("ix_import_jobs_source_key"), table_name="import_jobs")

    op.drop_table("sync_points")
    op.drop_table("bookmark_logs")
    op.drop_table("user_progress")
    op.drop_table("transcription_queue")
    op.drop_table("sync_maps")
    op.drop_table("bookmarks")
    op.drop_table("audio_transcripts")
    op.drop_table("book_pairs")
    op.drop_table("audit_logs")
    op.drop_table("users")
    op.drop_table("system_settings")
    op.drop_table("library_check_results")
    op.drop_table("import_sources")
    op.drop_table("import_source_credentials")
    op.drop_table("import_jobs")
    op.drop_table("ebooks")
    op.drop_table("audiobooks")

    bind = op.get_bind()
    pairstatus.drop(bind, checkfirst=True)
    bookmarksource.drop(bind, checkfirst=True)
    progresstype.drop(bind, checkfirst=True)
