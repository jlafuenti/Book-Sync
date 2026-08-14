"""
User progress models for tracking reading/listening positions across devices.
"""

import enum
from datetime import datetime
from sqlalchemy import (
    Boolean, DateTime, Enum, Float, ForeignKey, Index, Integer, String, text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base
from utils import utcnow

class ProgressType(str, enum.Enum):
    """Type of media being tracked."""
    EBOOK = "ebook"
    AUDIOBOOK = "audiobook"


class UserProgress(Base):
    """
    Tracks a user's progress through a specific piece of media (ebook or audiobook).
    Allows picking up where the user left off across devices.
    """

    __tablename__ = "user_progress"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    
    # What media is this progress for?
    book_pair_id: Mapped[int] = mapped_column(ForeignKey("book_pairs.id"), nullable=True, index=True)
    ebook_id: Mapped[int] = mapped_column(ForeignKey("ebooks.id"), nullable=True, index=True)
    audiobook_id: Mapped[int] = mapped_column(ForeignKey("audiobooks.id"), nullable=True, index=True)
    
    media_type: Mapped[ProgressType] = mapped_column(Enum(ProgressType), nullable=False)

    # Position tracking. Chapter is the EPUB spine index — the portable anchor.
    # An `epub_cfi` column used to mirror the web reader's hint here for old app
    # builds; it is dropped (issue #102, migration 0007). Precise per-device
    # positions live in `position_hints`.
    epub_chapter: Mapped[int] = mapped_column(Integer, nullable=True)
    epub_progress_percent: Mapped[float] = mapped_column(Float, nullable=True)
    
    # Audiobook progress is simply milliseconds elapsed
    audio_position_ms: Mapped[int] = mapped_column(Integer, nullable=True)

    # State tracking
    is_completed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )

    # Conflict-resolution contract (issue #54): the client-reported wall-clock
    # time the position was captured (not when the request reached the
    # server). Used to reject stale replays from offline devices instead of
    # blindly overwriting a newer position (last-write-wins).
    captured_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)

    # Device ID that last updated this progress. Read for attribution/display
    # (device_name below is the human-readable counterpart), and used to
    # resolve conflicts alongside captured_at.
    device_id: Mapped[str] = mapped_column(String(200), nullable=True)
    device_name: Mapped[str] = mapped_column(String(200), nullable=True)

    # One projection row per user per media item (issue #64). Without this the
    # duplicate was only *assumed* not to exist: two concurrent first reads each
    # inserted one, and every later `scalar_one_or_none()` on that media raised
    # `MultipleResultsFound` — a permanent 500 on one book until a row was
    # deleted by hand.
    #
    # Predicates are written as SQL text so they render identically on Postgres
    # (production) and SQLite (tests), matching the `Bookmark` indexes. The enum
    # is stored by member *name*, hence 'EBOOK' / 'AUDIOBOOK'.
    __table_args__ = (
        Index(
            "ux_user_progress_user_ebook", "user_id", "ebook_id", unique=True,
            sqlite_where=text("media_type = 'EBOOK' AND ebook_id IS NOT NULL"),
            postgresql_where=text("media_type = 'EBOOK' AND ebook_id IS NOT NULL"),
        ),
        Index(
            "ux_user_progress_user_audiobook", "user_id", "audiobook_id", unique=True,
            sqlite_where=text("media_type = 'AUDIOBOOK' AND audiobook_id IS NOT NULL"),
            postgresql_where=text("media_type = 'AUDIOBOOK' AND audiobook_id IS NOT NULL"),
        ),
    )

    def __repr__(self) -> str:
        return f"<UserProgress(user={self.user_id}, type={self.media_type})>"
