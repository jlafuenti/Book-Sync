"""
LibraryCheckResult — persisted results of the expensive library health checks
(audio + ebook integrity), doubling as a cache.

Cheap checks (missing files, zero-byte, unsupported formats, failed
transcriptions, failed ACSM imports) are recomputed live by the troubleshoot
router, so only the expensive integrity checks are stored here. The stored
`file_size` + `file_mtime` let a re-scan skip files that haven't changed.
"""

import datetime

from sqlalchemy import (
    Column, Integer, String, Boolean, BigInteger, Float, Text, DateTime,
    UniqueConstraint, Index,
)

from database import Base
from utils import utcnow


class LibraryCheckResult(Base):
    __tablename__ = "library_check_results"

    id = Column(Integer, primary_key=True, index=True)

    item_type = Column(String(20), nullable=False)   # "ebook" | "audiobook"
    item_id = Column(Integer, nullable=False, index=True)

    # "audio_integrity" | "ebook_integrity" — the check domain (one row per item
    # per domain). The specific failure category (drm vs unreadable) is derived
    # from `detail` at read time.
    check_type = Column(String(40), nullable=False)

    file_path = Column(String(2000), nullable=True)
    file_size = Column(BigInteger, nullable=True)
    file_mtime = Column(Float, nullable=True)

    ok = Column(Boolean, nullable=False, default=True)
    detail = Column(Text, nullable=True)
    checked_at = Column(DateTime, default=utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("item_type", "item_id", "check_type", name="uq_check_item_domain"),
        Index("ix_check_ok", "check_type", "ok"),
    )


class MultiFileAudiobookFolder(Base):
    """A folder of per-track audio files the scanner refused to import (issue #63).

    `AudioBook` has one `file_path`; a book ripped as `01.mp3 … 30.mp3` is not
    representable, and importing each track as its own audiobook polluted the
    library and auto-pairing. The scanner detects such folders, skips their
    files, and records them here so Troubleshoot Library can show them with the
    remediation (merge to a single .m4b in Audiobookshelf, replace the folder,
    rescan). One row per (folder, extension) group.

    `fingerprint` hashes the sorted (name, size) list; a dismissed row stays
    dismissed only while the fingerprint is unchanged. A folder that no longer
    qualifies on a rescan (merged file, tracks removed) has its row deleted.
    """
    __tablename__ = "multi_file_audiobook_folders"

    id = Column(Integer, primary_key=True, index=True)
    folder_path = Column(String(2000), nullable=False)
    extension = Column(String(10), nullable=False)          # ".mp3", ".m4a", …
    file_count = Column(Integer, nullable=False)
    total_size = Column(BigInteger, nullable=False, default=0)
    guessed_title = Column(String(500), nullable=True)
    guessed_author = Column(String(500), nullable=True)
    fingerprint = Column(String(64), nullable=False)
    dismissed = Column(Boolean, nullable=False, default=False)
    first_seen_at = Column(DateTime, default=utcnow, nullable=False)
    last_seen_at = Column(DateTime, default=utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("folder_path", "extension", name="uq_multi_file_folder_ext"),
    )
