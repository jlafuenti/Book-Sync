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
    checked_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("item_type", "item_id", "check_type", name="uq_check_item_domain"),
        Index("ix_check_ok", "check_type", "ok"),
    )
