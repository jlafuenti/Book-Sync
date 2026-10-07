"""
Local Whisper Transcription Provider

Wraps services/transcription.py (faster-whisper running on the Book Sync
server itself) behind the TranscriptionProvider interface.
"""

import asyncio
import logging
from typing import List, Optional, Callable

from services.transcription_providers.base import TranscriptionProvider, TranscriptionError
from services.transcription import TranscribedSentence

logger = logging.getLogger(__name__)


class LocalWhisperProvider(TranscriptionProvider):
    """Runs faster-whisper locally on the Book Sync server's CPU/GPU."""

    def __init__(self, language: str = ""):
        # ISO 639-1 code forced on every chunk, or "" to detect once per file
        # and pin that (issue #246). Set from the `transcription_language`
        # system setting by the provider factory.
        self.language = language

    async def transcribe(
        self,
        audio_path: str,
        progress_callback: Optional[Callable] = None,
    ) -> List[TranscribedSentence]:
        """Transcribe using the local Whisper installation."""
        try:
            from services.transcription import transcribe_audiobook

            sentences = await asyncio.to_thread(
                transcribe_audiobook,
                audio_path,
                progress_callback=progress_callback,
                language=self.language or None,
            )
            return sentences
        except ImportError as e:
            # The default image is remote-transcription-only — faster-whisper
            # isn't installed. Give an actionable message instead of a raw ImportError.
            logger.error(f"Local Whisper unavailable (faster-whisper not installed): {e}")
            raise TranscriptionError(
                "Local Whisper isn't installed in this image (remote-transcription-only "
                "build). Set TRANSCRIPTION_PROVIDER=remote, or rebuild the server image "
                "with --build-arg INSTALL_LOCAL_WHISPER=1."
            ) from e
        except Exception as e:
            logger.error(f"Local Whisper transcription failed: {e}", exc_info=True)
            raise TranscriptionError(f"Local transcription failed: {e}") from e

    async def is_available(self) -> bool:
        """
        Local Whisper is available if faster-whisper can be imported.
        We don't test model loading here — that happens at transcription time.
        """
        try:
            import faster_whisper  # noqa: F401
            return True
        except ImportError:
            return False

    def name(self) -> str:
        return "Local Whisper"
