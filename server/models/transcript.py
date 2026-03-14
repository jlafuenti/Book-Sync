"""
AudioTranscript model.

Stores the raw transcription result for an audiobook pair, persisted
immediately after transcription completes and independently of EPUB alignment.
This allows alignment to fail and be retried without re-transcribing.
"""

from datetime import datetime
from sqlalchemy import String, DateTime, Integer, Text, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class AudioTranscript(Base):
    """
    Cached transcription for a book pair, keyed to the audiobook file path.

    sentences_json: JSON array of {"text": str, "start_ms": int, "end_ms": int}
    audiobook_path: stored so the cache can be invalidated if the audio file changes.
    """

    __tablename__ = "audio_transcripts"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    pair_id: Mapped[int] = mapped_column(
        ForeignKey("book_pairs.id"), unique=True, nullable=False, index=True
    )
    audiobook_path: Mapped[str] = mapped_column(String(2000), nullable=False)
    sentence_count: Mapped[int] = mapped_column(Integer, nullable=False)
    sentences_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )

    def __repr__(self) -> str:
        return (
            f"<AudioTranscript(pair={self.pair_id}, "
            f"sentences={self.sentence_count}, created={self.created_at})>"
        )
