"""
Remote Whisper Transcription Provider (stub)

Uploads audio to a remote faster-whisper server (e.g. Jetson Orin Nano)
and returns the results. Full implementation in Task 4.
"""

import logging
from typing import List, Optional, Callable

from services.transcription_providers.base import (
    TranscriptionProvider,
    ProviderUnavailableError,
)
from services.transcription import TranscribedSentence

logger = logging.getLogger(__name__)


class RemoteWhisperProvider(TranscriptionProvider):
    """
    Sends audio to a remote faster-whisper HTTP API for transcription.
    Intended for use with the Jetson Orin Nano transcription server.

    Full implementation will be done in Task 4.
    """

    def __init__(self, remote_url: str, timeout: int = 7200):
        self.remote_url = remote_url.rstrip("/")
        self.timeout = timeout

    async def transcribe(
        self,
        audio_path: str,
        progress_callback: Optional[Callable] = None,
    ) -> List[TranscribedSentence]:
        """Upload audio to the remote server and return transcribed sentences."""
        # TODO: Task 4 — implement HTTP upload + progress polling
        raise ProviderUnavailableError(
            "Remote transcription provider not yet implemented (Task 4)"
        )

    async def is_available(self) -> bool:
        """Check if the remote server is reachable by hitting /v1/health."""
        if not self.remote_url:
            return False

        try:
            import httpx
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(f"{self.remote_url}/v1/health")
                if resp.status_code == 200:
                    data = resp.json()
                    return data.get("model_loaded", False)
                return False
        except Exception as e:
            logger.debug(f"Remote provider health check failed: {e}")
            return False

    def name(self) -> str:
        return f"Remote Whisper ({self.remote_url})"
