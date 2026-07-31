"""
Bookmark and BookmarkLog models.

Bookmarks track the user's current reading/listening position.
BookmarkLog provides an audit trail of all position changes.
"""

import enum
from datetime import datetime
from sqlalchemy import (
    BigInteger, Boolean, DateTime, Enum, Float, ForeignKey, Index, Integer,
    String, Text, UniqueConstraint, text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base


class HintKind(str, enum.Enum):
    """Reader-specific precise position formats."""
    READIUM_LOCATOR = "readium_locator"   # Android / Readium
    EPUBJS_CFI = "epubjs_cfi"             # web / epub.js


# Whether a hint of this kind is only usable by the device that captured it.
# Readium locators encode href + progression as that device rendered the page;
# epub.js CFIs are derived from the EPUB DOM and so are portable between web
# clients.
HINT_DEVICE_SCOPED = {
    HintKind.READIUM_LOCATOR: True,
    HintKind.EPUBJS_CFI: False,
}


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
    # Scope: exactly one of book_pair_id, or ebook_id/audiobook_id for
    # standalone media, which previously had no canonical position row at all.
    book_pair_id: Mapped[int] = mapped_column(
        ForeignKey("book_pairs.id"), nullable=True, index=True
    )
    ebook_id: Mapped[int] = mapped_column(ForeignKey("ebooks.id"), nullable=True, index=True)
    audiobook_id: Mapped[int] = mapped_column(
        ForeignKey("audiobooks.id"), nullable=True, index=True
    )
    source: Mapped[BookmarkSource] = mapped_column(
        Enum(BookmarkSource), nullable=False, default=BookmarkSource.EBOOK
    )

    # EBook position. epub_chapter is the EPUB **spine index** — the same axis
    # both readers use (epub.js `book.spine.items`, Readium `readingOrder`).
    epub_chapter: Mapped[int] = mapped_column(Integer, nullable=True)
    epub_sentence_index: Mapped[int] = mapped_column(Integer, nullable=True)

    # Axis-independent anchor: the text at this position. Survives a re-parse,
    # a re-alignment, or a change of chapter numbering, and is the only anchor
    # that still resolves when a book has no sync map.
    epub_text_preview: Mapped[str] = mapped_column(Text, nullable=True)

    # 0-100 book-level progress. Both the Continue lists and the last-ditch
    # rung of the restore ladder.
    epub_progress_percent: Mapped[float] = mapped_column(Float, nullable=True)
    is_completed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Bumped whenever a text anchor changes (chapter / sentence / percent), NOT
    # on audio movement alone. A position hint records the revision it was
    # captured at, which is how staleness is judged without deleting anything.
    anchor_revision: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)

    # Audio position (in milliseconds)
    audio_position_ms: Mapped[int] = mapped_column(Integer, nullable=True)

    # Legacy mirrors of the current-anchor position hint, kept so installed
    # app builds that predate `position_hints` keep resuming. New code reads
    # and writes `hints`; these are written as a projection of it.
    epub_locator: Mapped[str] = mapped_column(Text, nullable=True)
    locator_audio_ms: Mapped[int] = mapped_column(Integer, nullable=True)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )
    synced_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)

    # Conflict-resolution contract (issue #54): the client-reported wall-clock
    # time the position was captured (not when the request reached the
    # server). Used to reject stale replays from offline devices instead of
    # blindly overwriting a newer position (last-write-wins).
    captured_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    device_id: Mapped[str] = mapped_column(String(200), nullable=True)
    device_name: Mapped[str] = mapped_column(String(200), nullable=True)

    # One canonical row per user per book, whichever way the book is scoped.
    # Predicates are written as SQL text so they render identically on
    # Postgres (production) and SQLite (tests).
    __table_args__ = (
        Index(
            "ux_bookmarks_user_pair", "user_id", "book_pair_id", unique=True,
            sqlite_where=text("book_pair_id IS NOT NULL"),
            postgresql_where=text("book_pair_id IS NOT NULL"),
        ),
        Index(
            "ux_bookmarks_user_ebook", "user_id", "ebook_id", unique=True,
            sqlite_where=text("book_pair_id IS NULL AND ebook_id IS NOT NULL"),
            postgresql_where=text("book_pair_id IS NULL AND ebook_id IS NOT NULL"),
        ),
        Index(
            "ux_bookmarks_user_audiobook", "user_id", "audiobook_id", unique=True,
            sqlite_where=text("book_pair_id IS NULL AND audiobook_id IS NOT NULL"),
            postgresql_where=text("book_pair_id IS NULL AND audiobook_id IS NOT NULL"),
        ),
    )

    # Relationships
    user = relationship("User", back_populates="bookmarks")
    book_pair = relationship("BookPair", back_populates="bookmarks")
    logs = relationship("BookmarkLog", back_populates="bookmark", cascade="all, delete-orphan")
    hints = relationship(
        "PositionHint", back_populates="bookmark", cascade="all, delete-orphan"
    )

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

    # Device attribution + client-captured event time, copied from the
    # Bookmark at the moment this log row was written — lets Session History
    # show which device made each change and when it actually happened.
    device_id: Mapped[str] = mapped_column(String(200), nullable=True)
    device_name: Mapped[str] = mapped_column(String(200), nullable=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)

    # Relationships
    bookmark = relationship("Bookmark", back_populates="logs")

    def __repr__(self) -> str:
        return f"<BookmarkLog(bookmark={self.bookmark_id}, at={self.changed_at})>"


class PositionHint(Base):
    """
    A reader-specific precise position (Readium locator or epub.js CFI),
    tagged with the anchor it was captured at.

    One row per (bookmark, device, kind), so two phones and a browser each keep
    their own and none can clobber another's — they used to share a single
    `bookmarks.epub_locator` column.

    A hint is **current** iff `anchor_revision == bookmark.anchor_revision`.
    When the anchor moves the hint is left alone; it simply stops being current,
    and becomes current again the moment its device re-captures. Nothing is ever
    deleted on an anchor move: an earlier design cleared hints instead, which
    left a reader with no position to restore and let a fresh chapter-0 write
    overwrite a real position.
    """

    __tablename__ = "position_hints"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    bookmark_id: Mapped[int] = mapped_column(
        ForeignKey("bookmarks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    device_id: Mapped[str] = mapped_column(String(200), nullable=False)
    hint_kind: Mapped[HintKind] = mapped_column(Enum(HintKind), nullable=False)
    hint_value: Mapped[str] = mapped_column(Text, nullable=False)

    # bookmarks.anchor_revision at the moment this hint was captured.
    anchor_revision: Mapped[int] = mapped_column(BigInteger, nullable=False)

    # Audio position when captured. Lets the reader decide whether a locator
    # still describes the page after the audio has moved on.
    audio_position_ms: Mapped[int] = mapped_column(Integer, nullable=True)

    captured_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    bookmark = relationship("Bookmark", back_populates="hints")

    __table_args__ = (
        UniqueConstraint(
            "bookmark_id", "device_id", "hint_kind", name="ux_position_hints"
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<PositionHint(bookmark={self.bookmark_id}, device={self.device_id}, "
            f"kind={self.hint_kind}, rev={self.anchor_revision})>"
        )
