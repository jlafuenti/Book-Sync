"""multi-file audiobook folders the scanner refuses to import (issue #63)

``AudioBook`` has a single ``file_path``; a folder of per-track MP3s for one
book is not representable. The scanner used to import each track as its own
audiobook (polluting the library and auto-pairing) or miss the book silently.
It now detects such folders, skips their files, and records them in this table
so Troubleshoot Library can show them with the remediation: merge to a single
.m4b in Audiobookshelf, replace the folder with the merged file, rescan.

``fingerprint`` hashes the folder's sorted (name, size) list. ``dismissed``
survives rescans only while the fingerprint is unchanged; a folder that stops
qualifying (merged file, tracks removed) has its row deleted on rescan.

Revision ID: 0009_multi_file_folders
Revises: 0008_bookmark_syncmap_ver
Create Date: 2026-08-15
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
# NOTE: keep this at or under 32 chars — see the note in
# 0002_conflict_resolution.py (alembic_version is a VARCHAR(32)).
revision: str = "0009_multi_file_folders"
down_revision: Union[str, None] = "0008_bookmark_syncmap_ver"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "multi_file_audiobook_folders",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("folder_path", sa.String(length=2000), nullable=False),
        sa.Column("extension", sa.String(length=10), nullable=False),
        sa.Column("file_count", sa.Integer(), nullable=False),
        sa.Column("total_size", sa.BigInteger(), nullable=False),
        sa.Column("guessed_title", sa.String(length=500), nullable=True),
        sa.Column("guessed_author", sa.String(length=500), nullable=True),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("dismissed", sa.Boolean(), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("folder_path", "extension", name="uq_multi_file_folder_ext"),
    )
    op.create_index(
        op.f("ix_multi_file_audiobook_folders_id"),
        "multi_file_audiobook_folders", ["id"], unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_multi_file_audiobook_folders_id"),
        table_name="multi_file_audiobook_folders",
    )
    op.drop_table("multi_file_audiobook_folders")
