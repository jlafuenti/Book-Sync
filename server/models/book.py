"""
Book models: EBook, AudioBook, and BookPair (the link between them).
"""

import enum
from datetime import datetime
from sqlalchemy import (
    String, Text, DateTime, Integer, BigInteger, Boolean, Enum, ForeignKey,
    Float, Index, JSON, UniqueConstraint, inspect,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base
from utils import utcnow

# Portable JSON everywhere, but real JSONB on Postgres so fresh databases built
# from these models match the production schema (which the old boot-time ALTER
# block created as JSONB). SQLite (tests) still gets plain JSON.
JSON_OR_JSONB = JSON().with_variant(JSONB(), "postgresql")


class PairStatus(str, enum.Enum):
    """Status of an ebook/audiobook pairing."""
    UNMATCHED = "unmatched"
    AUTO_MATCHED = "auto_matched"
    MANUAL_MATCHED = "manual_matched"
    TRANSCRIBING = "transcribing"
    SYNCED = "synced"
    ERROR = "error"


class EBook(Base):
    """An uploaded or discovered EPUB/ebook file."""

    __tablename__ = "ebooks"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    author: Mapped[str] = mapped_column(String(500), nullable=True)
    filename: Mapped[str] = mapped_column(String(1000), nullable=False)
    file_path: Mapped[str] = mapped_column(String(2000), nullable=False)
    file_hash: Mapped[str] = mapped_column(String(64), nullable=True)
    file_size: Mapped[int] = mapped_column(BigInteger, nullable=True)
    format: Mapped[str] = mapped_column(String(10), default="epub")  # epub, pdf, etc.
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False
    )
    series: Mapped[str] = mapped_column(String(500), nullable=True)
    series_index: Mapped[float] = mapped_column(Float, nullable=True)
    metadata_source: Mapped[str] = mapped_column(String(50), nullable=True)  # 'embedded', 'pattern', 'filename'
    metadata_pattern: Mapped[str] = mapped_column(String(500), nullable=True)  # the pattern that matched, if any

    # Extended metadata fields
    description: Mapped[str] = mapped_column(Text, nullable=True)
    publisher: Mapped[str] = mapped_column(String(500), nullable=True)
    publish_year: Mapped[int] = mapped_column(Integer, nullable=True)
    language: Mapped[str] = mapped_column(String(50), nullable=True)
    genres: Mapped[str] = mapped_column(String(1000), nullable=True)  # comma-separated
    tags: Mapped[str] = mapped_column(String(1000), nullable=True)  # comma-separated
    isbn: Mapped[str] = mapped_column(String(100), nullable=True)
    asin: Mapped[str] = mapped_column(String(100), nullable=True)
    narrators: Mapped[str] = mapped_column(String(500), nullable=True)
    is_explicit: Mapped[bool] = mapped_column(Boolean, nullable=True, default=False)
    is_abridged: Mapped[bool] = mapped_column(Boolean, nullable=True, default=False)
    cover_path: Mapped[str] = mapped_column(String(2000), nullable=True)

    # Import provenance — set when this row was created via an automated import source.
    import_source: Mapped[str] = mapped_column(String(50), nullable=True)
    external_id: Mapped[str] = mapped_column(String(200), nullable=True)

    # New-items inbox: cleared once user acknowledges or pairs this item
    acknowledged: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Hashes of audiobooks this ebook must not be auto-paired with (set on manual unpair)
    auto_pair_excluded_hashes: Mapped[list] = mapped_column(JSON_OR_JSONB, nullable=False, default=list)

    # `file_path` is the identity key: the scan, the ACSM/convert path and the
    # importers all look a row up by it with `.scalar_one_or_none()`. Every
    # insert is check-then-insert on one worker, so nothing *produces* a
    # duplicate today — but one arriving any other way (a manual insert, a
    # restore from an older dump, a second worker later) makes every one of
    # those lookups raise `MultipleResultsFound`, and the whole library scan
    # 500s until a row is deleted by hand. That is issue #64 again, in a
    # different table; here it is unrepresentable instead (issue #256).
    #
    # `file_hash` is the auto-pair and duplicate-detection key, read per file
    # per scan; indexed for the lookup, not constrained — two identical files at
    # different paths are legitimate.
    __table_args__ = (
        Index("ux_ebooks_file_path", "file_path", unique=True),
        Index("ix_ebooks_file_hash", "file_hash"),
    )

    # Relationships
    pairs = relationship("BookPair", back_populates="ebook", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<EBook(id={self.id}, title='{self.title}')>"


class AudioBook(Base):
    """An uploaded or discovered audiobook file."""

    __tablename__ = "audiobooks"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    author: Mapped[str] = mapped_column(String(500), nullable=True)
    filename: Mapped[str] = mapped_column(String(1000), nullable=False)
    file_path: Mapped[str] = mapped_column(String(2000), nullable=False)
    file_hash: Mapped[str] = mapped_column(String(64), nullable=True)
    file_size: Mapped[int] = mapped_column(BigInteger, nullable=True)
    duration_seconds: Mapped[int] = mapped_column(Integer, nullable=True)
    format: Mapped[str] = mapped_column(String(10), default="mp3")
    series: Mapped[str] = mapped_column(String(500), nullable=True)
    series_index: Mapped[float] = mapped_column(Float, nullable=True)
    metadata_source: Mapped[str] = mapped_column(String(50), nullable=True)  # 'embedded', 'pattern', 'filename'
    metadata_pattern: Mapped[str] = mapped_column(String(500), nullable=True)  # the pattern that matched, if any
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False
    )

    # Extended metadata fields
    description: Mapped[str] = mapped_column(Text, nullable=True)
    publisher: Mapped[str] = mapped_column(String(500), nullable=True)
    publish_year: Mapped[int] = mapped_column(Integer, nullable=True)
    language: Mapped[str] = mapped_column(String(50), nullable=True)
    genres: Mapped[str] = mapped_column(String(1000), nullable=True)  # comma-separated
    tags: Mapped[str] = mapped_column(String(1000), nullable=True)  # comma-separated
    isbn: Mapped[str] = mapped_column(String(100), nullable=True)
    asin: Mapped[str] = mapped_column(String(100), nullable=True)
    narrators: Mapped[str] = mapped_column(String(500), nullable=True)
    is_explicit: Mapped[bool] = mapped_column(Boolean, nullable=True, default=False)
    is_abridged: Mapped[bool] = mapped_column(Boolean, nullable=True, default=False)
    cover_path: Mapped[str] = mapped_column(String(2000), nullable=True)

    # Import provenance — set when this row was created via an automated import source.
    import_source: Mapped[str] = mapped_column(String(50), nullable=True)
    external_id: Mapped[str] = mapped_column(String(200), nullable=True)

    # New-items inbox: cleared once user acknowledges or pairs this item
    acknowledged: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Hashes of ebooks this audiobook must not be auto-paired with (set on manual unpair)
    auto_pair_excluded_hashes: Mapped[list] = mapped_column(JSON_OR_JSONB, nullable=False, default=list)

    # Same reasoning as `EBook` above (issue #256): `file_path` is the identity
    # key every scan lookup assumes is unique, `file_hash` is a hot read.
    __table_args__ = (
        Index("ux_audiobooks_file_path", "file_path", unique=True),
        Index("ix_audiobooks_file_hash", "file_hash"),
    )

    # Relationships
    pairs = relationship("BookPair", back_populates="audiobook", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<AudioBook(id={self.id}, title='{self.title}')>"


class BookPair(Base):
    """
    A matched pair linking an ebook to its audiobook.
    This is the central entity that sync maps and bookmarks reference.
    """

    __tablename__ = "book_pairs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    ebook_id: Mapped[int] = mapped_column(ForeignKey("ebooks.id"), nullable=False)
    audiobook_id: Mapped[int] = mapped_column(ForeignKey("audiobooks.id"), nullable=False)
    status: Mapped[PairStatus] = mapped_column(
        Enum(PairStatus), default=PairStatus.UNMATCHED, nullable=False
    )
    matched_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    synced_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    ignored_fields: Mapped[list] = mapped_column(JSON_OR_JSONB, nullable=False, default=list)

    # New-pairs inbox: cleared once user resolves/skips all discrepancies or manually acknowledges
    acknowledged: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Both FKs are read as predicates, not just joined through: the library
    # browse's paired/unpaired filter runs `EXISTS (SELECT 1 FROM book_pairs
    # WHERE ebook_id = ebooks.id)` per row on every page. Unindexed that is a
    # sequential scan per item.
    #
    # The unique constraint is belt-and-braces: `create_pair` already rejects a
    # duplicate pairing with 409, so this only stops the check-then-insert being
    # the *sole* guarantee (issue #256).
    __table_args__ = (
        Index("ix_book_pairs_ebook_id", "ebook_id"),
        Index("ix_book_pairs_audiobook_id", "audiobook_id"),
        UniqueConstraint("ebook_id", "audiobook_id", name="uq_book_pairs_pair"),
    )

    # Relationships
    ebook = relationship("EBook", back_populates="pairs")
    audiobook = relationship("AudioBook", back_populates="pairs")
    sync_map = relationship("SyncMap", back_populates="book_pair", uselist=False, cascade="all, delete-orphan")
    bookmarks = relationship("Bookmark", back_populates="book_pair", cascade="all, delete-orphan")

    @property
    def sync_map_version(self) -> "int | None":
        """The live `sync_maps.version`, for clients that cache sync points.

        Re-transcription rebuilds the map under a new version (issue #55), and a
        client holding stale points converts positions with timestamps that no
        longer exist. Surfacing the version on the pair listing lets them notice
        without downloading the whole map.

        Returns None when the relationship isn't loaded — an async lazy-load
        raises, and several endpoints (`POST /pairs`, for one) never eager-load
        it. Callers must therefore read null as *unknown*, not as "no map", and
        leave their cache alone.
        """
        if "sync_map" in inspect(self).unloaded:
            return None
        return self.sync_map.version if self.sync_map else None

    def __repr__(self) -> str:
        return f"<BookPair(id={self.id}, status='{self.status}')>"
