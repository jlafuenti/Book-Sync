"""record which ebook file a sync map was aligned against (issue #295)

A sync map's ``(chapter, sentence_index)`` coordinates only mean anything
relative to one ebook file. Re-convert the book, replace the file with another
edition, or change how it parses, and the map starts naming text that is not in
the document the reader renders — pair 260 resolved 10:00 of audio to a sentence
that occurs nowhere in its current EPUB.

``epub_file_hash`` is the composite hash (``services/file_hash.py``) of the
ebook file at alignment time, stamped by ``sync_engine.save_sync_map``. The
drift audit (``GET /api/troubleshoot/sync-map-audit``) compares it to the file's
hash now: a mismatch is proof the map describes a different file.

Backfilled to NULL — "provenance never recorded" — rather than to the current
file's hash, which would assert the very thing the column exists to verify. The
audit reads NULL as *unknown* and falls back to sampling the map's stored
sentence previews against the book's text.

Revision ID: 0010_syncmap_epub_hash
Revises: 0009_multi_file_folders
Create Date: 2026-08-24

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
# NOTE: keep this at or under 32 chars — see the note in
# 0002_conflict_resolution.py (alembic_version is a VARCHAR(32)).
revision: str = "0010_syncmap_epub_hash"
down_revision: Union[str, None] = "0009_multi_file_folders"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "sync_maps", sa.Column("epub_file_hash", sa.String(length=64), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("sync_maps", "epub_file_hash")
