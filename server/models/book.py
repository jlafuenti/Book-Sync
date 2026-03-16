"""
Book models: EBook, AudioBook, and BookPair (the link between them).
"""

import enum
from datetime import datetime
from sqlalchemy import String, Text, DateTime, Integer, BigInteger, Boolean, Enum, ForeignKey, Float, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base


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
        DateTime, default=datetime.utcnow, nullable=False
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
        DateTime, default=datetime.utcnow, nullable=False
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
    ignored_fields: Mapped[list] = mapped_column(JSON, nullable=False, default=list)

    # Relationships
    ebook = relationship("EBook", back_populates="pairs")
    audiobook = relationship("AudioBook", back_populates="pairs")
    sync_map = relationship("SyncMap", back_populates="book_pair", uselist=False, cascade="all, delete-orphan")
    bookmarks = relationship("Bookmark", back_populates="book_pair", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<BookPair(id={self.id}, status='{self.status}')>"
