"""
Local Whisper Transcription Provider

Wraps the existing services/transcription.py (OpenAI Whisper running on the
Book Sync server itself) behind the TranscriptionProvider interface.
"""

import asyncio
import logging
from typing import List, Optional, Callable

from services.transcription_providers.base import TranscriptionProvider, TranscriptionError
from services.transcription import TranscribedSentence

logger = logging.getLogger(__name__)


class LocalWhisperProvider(TranscriptionProvider):
    """Runs OpenAI Whisper locally on the Book Sync server's CPU/GPU."""

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
            )
            return sentences
        except ImportError as e:
            # The default image is remote-transcription-only — torch/openai-whisper
            # aren't installed. Give an actionable message instead of a raw ImportError.
            logger.error(f"Local Whisper unavailable (ML deps not installed): {e}")
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
        Local Whisper is available if torch + whisper can be imported.
        We don't test model loading here — that happens at transcription time.
        """
        try:
            import torch
            import whisper
            return True
        except ImportError:
            return False

    def name(self) -> str:
        return "Local Whisper"
