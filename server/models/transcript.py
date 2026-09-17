"""
AudioTranscript model.

Stores the raw transcription result for an audiobook pair, persisted
immediately after transcription completes and independently of EPUB alignment.
This allows alignment to fail and be retried without re-transcribing.
"""

from datetime import datetime
from typing import Optional
from sqlalchemy import String, DateTime, Integer, Text, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from database import Base
from utils import utcnow


class AudioTranscript(Base):
    """
    Cached transcription for a book pair, keyed to the audiobook file path.

    sentences_json: JSON array of {"text": str, "start_ms": int, "end_ms": int}
    audiobook_path: informational -- the path a cache hit still requires, but a
    path cannot tell two different recordings dropped at the same location
    apart (issue #588: a downloader "upgrade", a re-rip, or an Audiobookshelf
    merge/re-encode can replace the file in place without moving it).

    audio_file_hash / audio_duration_seconds: the fingerprint of the audio
    that was actually transcribed -- a snapshot of `AudioBook.file_hash` /
    `AudioBook.duration_seconds` taken when this row was written. Both NULL on
    a transcript that predates this column (issue #588's migration): that is
    "unknown provenance", not "known unchanged" -- `services.queue_manager`'s
    cache check treats it as "trust the path match, and stamp today's
    fingerprint for next time" rather than forcing every pre-existing
    transcript to re-transcribe the moment this ships.

    `AudioBook.file_hash` is a whole-file hash, so a Tandem tag write-back
    (`services.tag_writer`) changes it exactly like an actual replacement
    would. `_refresh_write_back_hash` (routers/library.py, issue #533's
    pattern) and `services.audio_change.refresh_after_write_back` move this
    row's `audio_file_hash` forward in the same request/scan pass that wrote
    the tags, so a write-back never looks like a replacement here.
    """

    __tablename__ = "audio_transcripts"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    pair_id: Mapped[int] = mapped_column(
        ForeignKey("book_pairs.id"), unique=True, nullable=False, index=True
    )
    audiobook_path: Mapped[str] = mapped_column(String(2000), nullable=False)
    audio_file_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    audio_duration_seconds: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    sentence_count: Mapped[int] = mapped_column(Integer, nullable=False)
    sentences_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False
    )

    def __repr__(self) -> str:
        return (
            f"<AudioTranscript(pair={self.pair_id}, "
            f"sentences={self.sentence_count}, created={self.created_at})>"
        )
