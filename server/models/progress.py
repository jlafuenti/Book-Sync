"""
User progress models for tracking reading/listening positions across devices.
"""

import enum
from datetime import datetime
from sqlalchemy import String, DateTime, Integer, Enum, ForeignKey, Boolean, Float
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base

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

    # Position tracking
    # E-book progress is typically stored as an EPUB CFI string or chapter/percentage
    epub_cfi: Mapped[str] = mapped_column(String(500), nullable=True)
    epub_chapter: Mapped[int] = mapped_column(Integer, nullable=True)
    epub_progress_percent: Mapped[float] = mapped_column(Float, nullable=True)
    
    # Audiobook progress is simply milliseconds elapsed
    audio_position_ms: Mapped[int] = mapped_column(Integer, nullable=True)

    # State tracking
    is_completed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
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

    def __repr__(self) -> str:
        return f"<UserProgress(user={self.user_id}, type={self.media_type})>"
