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
