"""
Transcription Providers Package

Factory function that returns the appropriate transcription provider(s)
based on the current system settings.
"""

import logging
from typing import List

from services.transcription_providers.base import (
    TranscriptionProvider,
    ProviderUnavailableError,
    TranscriptionError,
)
from services.transcription_providers.local import LocalWhisperProvider
from services.transcription_providers.remote import RemoteWhisperProvider

logger = logging.getLogger(__name__)

__all__ = [
    "TranscriptionProvider",
    "ProviderUnavailableError",
    "TranscriptionError",
    "LocalWhisperProvider",
    "RemoteWhisperProvider",
    "get_transcription_provider",
]


async def _load_settings_from_db() -> dict:
    """Load transcription-related settings from the database."""
    from database import async_session
    from sqlalchemy import select
    from models.settings import SystemSetting
    from routers.settings import DEFAULT_SETTINGS

    settings_dict = dict(DEFAULT_SETTINGS)

    try:
        async with async_session() as db:
            result = await db.execute(select(SystemSetting))
            for s in result.scalars().all():
                settings_dict[s.key] = s.value
    except Exception as e:
        logger.warning(f"Could not load settings from DB, using defaults: {e}")

    return settings_dict


async def get_transcription_provider() -> "TranscriptionProvider":
    """
    Factory that returns the right provider based on system settings.

    Settings key: ``transcription_provider``
      - ``"local"``                → LocalWhisperProvider only
      - ``"remote"``               → RemoteWhisperProvider only (no fallback)
      - ``"remote_with_fallback"`` → Try remote first; if unavailable, fall back to local

    For ``"remote_with_fallback"``, the returned provider is a
    :class:`FallbackProvider` that wraps both remote and local.
    """
    db_settings = await _load_settings_from_db()

    provider_mode = str(db_settings.get("transcription_provider", "remote_with_fallback"))
    remote_url = str(db_settings.get("transcription_remote_url", ""))
    remote_timeout = int(db_settings.get("transcription_remote_timeout", 7200))

    if provider_mode == "local":
        logger.info("Using Local Whisper provider")
        return LocalWhisperProvider()

    elif provider_mode == "remote":
        logger.info(f"Using Remote Whisper provider: {remote_url}")
        if not remote_url:
            raise TranscriptionError(
                "Remote transcription URL is not configured. "
                "Set it in Settings → Transcription."
            )
        return RemoteWhisperProvider(remote_url, timeout=remote_timeout)

    else:  # "remote_with_fallback" (default)
        logger.info(f"Using Remote-with-Fallback provider: {remote_url}")
        return FallbackProvider(
            remote_url=remote_url,
            remote_timeout=remote_timeout,
        )


class FallbackProvider(TranscriptionProvider):
    """
    Tries the remote provider first; if it's unavailable or fails with
    ``ProviderUnavailableError``, falls back to local Whisper.
    """

    def __init__(self, remote_url: str, remote_timeout: int = 7200):
        self._remote = RemoteWhisperProvider(remote_url, timeout=remote_timeout) if remote_url else None
        self._local = LocalWhisperProvider()

    async def transcribe(self, audio_path, progress_callback=None):
        # Try remote first (if configured)
        if self._remote:
            try:
                is_up = await self._remote.is_available()
                if is_up:
                    logger.info("Remote provider is available — using it")
                    return await self._remote.transcribe(audio_path, progress_callback)
                else:
                    logger.warning("Remote provider health check failed — falling back to local")
            except ProviderUnavailableError as e:
                logger.warning(f"Remote provider unavailable: {e} — falling back to local")
            except Exception as e:
                logger.warning(f"Remote provider error: {e} — falling back to local")
        else:
            logger.info("No remote URL configured — using local provider")

        # Fallback to local
        logger.info("Using local Whisper as fallback")
        return await self._local.transcribe(audio_path, progress_callback)

    async def is_available(self) -> bool:
        """Available if either remote or local is available."""
        if self._remote and await self._remote.is_available():
            return True
        return await self._local.is_available()

    def name(self) -> str:
        if self._remote:
            return f"Remote with Fallback ({self._remote.remote_url})"
        return "Local Whisper (no remote configured)"
