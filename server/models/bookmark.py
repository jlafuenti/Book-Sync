"""
Bookmark and BookmarkLog models.

Bookmarks track the user's current reading/listening position.
BookmarkLog provides an audit trail of all position changes.
"""

import enum
from datetime import datetime
from sqlalchemy import String, DateTime, Integer, Enum, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base


class BookmarkSource(str, enum.Enum):
    """Where the bookmark was last updated from."""
    EBOOK = "ebook"
    AUDIOBOOK = "audiobook"


class Bookmark(Base):
    """
    A user's current position in a book pair.
    Stores BOTH the ebook position and audio position
    (computed via the SyncMap) so either can resume quickly.
    """

    __tablename__ = "bookmarks"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    book_pair_id: Mapped[int] = mapped_column(
        ForeignKey("book_pairs.id"), nullable=False, index=True
    )
    source: Mapped[BookmarkSource] = mapped_column(
        Enum(BookmarkSource), nullable=False, default=BookmarkSource.EBOOK
    )

    # EBook position
    epub_chapter: Mapped[int] = mapped_column(Integer, nullable=True)
    epub_sentence_index: Mapped[int] = mapped_column(Integer, nullable=True)

    # Audio position (in milliseconds)
    audio_position_ms: Mapped[int] = mapped_column(Integer, nullable=True)

    # Precise client-side EPUB locator/CFI. The client (Android) sends this so a
    # bookmark can resume at the exact reading position; the server stores and
    # echoes it back. Independent of the sync-map derived position.
    epub_locator: Mapped[str] = mapped_column(Text, nullable=True)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )
    synced_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)

    # Relationships
    user = relationship("User", back_populates="bookmarks")
    book_pair = relationship("BookPair", back_populates="bookmarks")
    logs = relationship("BookmarkLog", back_populates="bookmark", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return (
            f"<Bookmark(user={self.user_id}, pair={self.book_pair_id}, "
            f"ch={self.epub_chapter}, s={self.epub_sentence_index}, "
            f"audio={self.audio_position_ms}ms)>"
        )


class BookmarkLog(Base):
    """
    Audit log of bookmark position changes.
    Every time a bookmark is updated, the previous and new positions are logged.
    """

    __tablename__ = "bookmark_logs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    bookmark_id: Mapped[int] = mapped_column(
        ForeignKey("bookmarks.id"), nullable=False, index=True
    )
    source: Mapped[BookmarkSource] = mapped_column(Enum(BookmarkSource), nullable=False)

    # Previous position
    prev_epub_chapter: Mapped[int] = mapped_column(Integer, nullable=True)
    prev_epub_sentence_index: Mapped[int] = mapped_column(Integer, nullable=True)
    prev_audio_position_ms: Mapped[int] = mapped_column(Integer, nullable=True)

    # New position
    new_epub_chapter: Mapped[int] = mapped_column(Integer, nullable=True)
    new_epub_sentence_index: Mapped[int] = mapped_column(Integer, nullable=True)
    new_audio_position_ms: Mapped[int] = mapped_column(Integer, nullable=True)

    changed_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )

    # Relationships
    bookmark = relationship("Bookmark", back_populates="logs")

    def __repr__(self) -> str:
        return f"<BookmarkLog(bookmark={self.bookmark_id}, at={self.changed_at})>"
