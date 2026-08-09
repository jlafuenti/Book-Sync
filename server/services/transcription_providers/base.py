"""
Transcription Provider — Abstract Base Class

Defines the interface that all transcription providers (local Whisper, remote
faster-whisper on Jetson, etc.) must implement.
"""

import logging
from abc import ABC, abstractmethod
from typing import List, Optional, Callable

from services.transcription import TranscribedSentence

logger = logging.getLogger(__name__)


class ProviderUnavailableError(Exception):
    """Raised when a provider cannot be reached (triggers fallback)."""
    pass


class TranscriptionError(Exception):
    """Raised on a non-recoverable transcription error (no fallback)."""
    pass


class TranscriptionPaused(Exception):
    """
    Raised when a provider stopped early at a safe point and kept its progress
    (issue #106).

    Not a failure: the queue re-pends the item with its progress intact and no
    retry burned, and the next dispatch resumes from the provider's checkpoint.

    Attributes:
        completed_through_sec: How far into the audio the checkpoint reaches.
        progress: Fraction 0.0–1.0 of the audio transcribed so far.
    """

    def __init__(self, message: str, completed_through_sec: float = 0.0, progress: float = 0.0):
        super().__init__(message)
        self.completed_through_sec = completed_through_sec
        self.progress = progress


class TranscriptionProvider(ABC):
    """
    Abstract base for transcription backends.

    Every provider must be able to:
      - Transcribe an audio file and return timestamped sentences
      - Report whether it is currently reachable / healthy
      - Identify itself by name (for logging and UI display)
    """

    @abstractmethod
    async def transcribe(
        self,
        audio_path: str,
        progress_callback: Optional[Callable[[float, Optional[float]], None]] = None,
    ) -> List[TranscribedSentence]:
        """
        Transcribe an audio file.

        Args:
            audio_path: Absolute path to the audio file on the Book Sync server.
            progress_callback: Optional callback(fraction, total_duration_sec).
                fraction is 0.0–1.0, total_duration_sec may be None.

        Returns:
            List of TranscribedSentence with text, start_ms, end_ms.

        Raises:
            ProviderUnavailableError: Provider cannot be reached (triggers fallback).
            TranscriptionError: Non-recoverable error (no fallback).
        """
        ...

    @abstractmethod
    async def is_available(self) -> bool:
        """Return True if this provider is reachable and ready to accept work."""
        ...

    @abstractmethod
    def name(self) -> str:
        """Human-readable provider name for logging and UI."""
        ...

    # --- Off-hours support (issue #106) ------------------------------------
    # Optional: a provider that can't stop cleanly mid-job simply runs to
    # completion, which is the correct behaviour for any backend that isn't
    # competing for the Jetson's GPU memory.

    async def request_pause(self) -> bool:
        """
        Ask the provider to stop at its next safe point, keeping its progress.

        Returns True if the request was accepted. The default is False —
        "this backend can't pause" — and the caller lets the job finish.
        """
        return False

    async def release_resources(self) -> None:
        """
        Ask the provider to free expensive resources (model weights) now.

        Called when the off-hours window closes. A no-op by default.
        """
        return None

    async def discard_checkpoint(self, audio_path: str) -> None:
        """
        Drop any saved partial progress for ``audio_path``.

        Called when a queue item is cancelled, so a paused job's checkpoint
        (and any audio the worker retained for it) doesn't linger. No-op by
        default.
        """
        return None
