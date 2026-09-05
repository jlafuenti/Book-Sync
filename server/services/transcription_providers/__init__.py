"""
Transcription Providers Package

Factory function that returns the appropriate transcription provider(s)
based on the current system settings.
"""

import logging
from typing import List, Optional

from services.transcription_providers.base import (
    TranscriptionProvider,
    ProviderUnavailableError,
    TranscriptionError,
    TranscriptionPaused,
)
from services.transcription_providers.local import LocalWhisperProvider
from services.transcription_providers.remote import RemoteWhisperProvider

logger = logging.getLogger(__name__)

# Remote-worker request timeout, in seconds (issue #248). `POST /v1/transcribe`
# is a single blocking request that returns only when the whole book is
# transcribed, so this must exceed the longest job you expect — 24 h by
# default. Lowering it makes long books fail; it is not a dead-worker
# detector. The minimum is a sanity guard against a typo, nothing more.
# `routers.settings.DEFAULT_SETTINGS` mirrors the default and must match.
DEFAULT_REMOTE_TIMEOUT_SEC = 86400
MIN_REMOTE_TIMEOUT_SEC = 60

# Whisper language codes offered by the `transcription_language` setting
# (issue #246). `""` means auto: detect once on the first chunk of a file and
# pin that answer for the rest of it, rather than re-detecting per chunk.
#
# Deliberately a short list rather than all ~99 Whisper languages: it is a
# validation allow-list and a UI dropdown, and an unvalidated free-text code
# fails every chunk of every job with nothing on screen explaining why. Adding
# a code here is a one-line change — `routers.settings` validates against this
# tuple and `web/src/pages/SystemPage.jsx` mirrors it.
SUPPORTED_LANGUAGES = (
    "",      # auto-detect (once per file, then pinned)
    "ar", "cs", "da", "de", "el", "en", "es", "fi", "fr", "he", "hi", "hu",
    "id", "it", "ja", "ko", "nl", "no", "pl", "pt", "ro", "ru", "sv", "tr",
    "uk", "vi", "zh",
)

__all__ = [
    "TranscriptionProvider",
    "ProviderUnavailableError",
    "TranscriptionError",
    "TranscriptionPaused",
    "LocalWhisperProvider",
    "RemoteWhisperProvider",
    "get_transcription_provider",
    "DEFAULT_REMOTE_TIMEOUT_SEC",
    "MIN_REMOTE_TIMEOUT_SEC",
    "SUPPORTED_LANGUAGES",
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


async def _load_remote_key() -> str:
    """Load the decrypted Jetson shared secret from the credential store."""
    from database import async_session
    from services import credentials as credential_store

    try:
        async with async_session() as db:
            return await credential_store.get_credential(db, "transcription_remote") or ""
    except Exception as e:
        logger.warning(f"Could not load transcription remote key from DB: {e}")
        return ""


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
    remote_key = await _load_remote_key()

    provider_mode = str(db_settings.get("transcription_provider", "remote_with_fallback"))
    remote_url = str(db_settings.get("transcription_remote_url", ""))
    # `POST /v1/transcribe` blocks for the whole job, so this read timeout must
    # outlast the longest book — hence the 24 h default. It is a default, not a
    # floor: an operator with a short library may legitimately want less
    # (issue #248). MIN_REMOTE_TIMEOUT_SEC is only a sanity guard so a typo
    # cannot abandon every job seconds in.
    remote_timeout = max(
        int(db_settings.get("transcription_remote_timeout", DEFAULT_REMOTE_TIMEOUT_SEC)),
        MIN_REMOTE_TIMEOUT_SEC,
    )
    logger.info(f"Remote transcription timeout in effect: {remote_timeout}s")

    # "" = auto-detect. Both providers detect once per file and pin that for
    # the rest of it; a value here skips detection entirely (issue #246).
    language = str(db_settings.get("transcription_language", "") or "").strip().lower()
    if language:
        logger.info(f"Transcription language pinned to '{language}'")

    if provider_mode == "local":
        logger.info("Using Local Whisper provider")
        return LocalWhisperProvider(language=language)

    elif provider_mode == "remote":
        logger.info(f"Using Remote Whisper provider: {remote_url}")
        if not remote_url:
            raise TranscriptionError(
                "Remote transcription URL is not configured. "
                "Set it in Settings → Transcription."
            )
        return RemoteWhisperProvider(
            remote_url, timeout=remote_timeout, api_key=remote_key, language=language
        )

    else:  # "remote_with_fallback" (default)
        logger.info(f"Using Remote-with-Fallback provider: {remote_url}")
        # Say once, at construction, that the fallback leg is imaginary on this
        # image (issue #191). Without it the only clue is a "will retry the
        # remote" message hours later, which reads like the setting is doing
        # something it isn't.
        if not await LocalWhisperProvider().is_available():
            logger.warning(
                "Local Whisper is not installed in this image, so the fallback "
                "leg does not exist — 'remote_with_fallback' behaves as 'remote' "
                "(remote failures are retried, never run locally)."
            )
        return FallbackProvider(
            remote_url=remote_url,
            remote_timeout=remote_timeout,
            remote_key=remote_key,
            language=language,
        )


class FallbackProvider(TranscriptionProvider):
    """
    Tries the remote provider first; if it's unavailable or fails with
    ``ProviderUnavailableError``, falls back to local Whisper.
    """

    def __init__(
        self,
        remote_url: str,
        remote_timeout: int = 7200,
        remote_key: str = "",
        language: str = "",
    ):
        self._remote = (
            RemoteWhisperProvider(
                remote_url, timeout=remote_timeout, api_key=remote_key, language=language
            )
            if remote_url else None
        )
        self._local = LocalWhisperProvider(language=language)
        # Which provider actually ran the current job. Pause/unload requests
        # have to reach *that* one, not whichever we'd pick if asked afresh.
        self._selected = None

    async def transcribe(self, audio_path, progress_callback=None):
        # Why the remote leg gave up, or None if there was no remote to try.
        # Carried into the error below so the operator reads the real cause
        # rather than a message about a component that isn't even installed.
        remote_failure: Optional[str] = None

        # Try remote first (if configured)
        if self._remote:
            try:
                is_up = await self._remote.is_available()
                if is_up:
                    logger.info("Remote provider is available — using it")
                    self._selected = self._remote
                    return await self._remote.transcribe(audio_path, progress_callback)
                else:
                    remote_failure = "health check failed"
                    logger.warning("Remote provider health check failed — falling back to local")
            except TranscriptionPaused:
                # Not a failure. The remote stopped at a checkpoint because the
                # off-hours window closed; falling back to local here would redo
                # hours of already-completed work. Let the queue re-pend it.
                raise
            except TranscriptionError:
                # `base.py` defines TranscriptionError as non-recoverable, and
                # the remote raises it for exactly one thing worth saying out
                # loud: the audio can't be decoded, re-import the file. A local
                # attempt would fail on the same bytes and replace that with a
                # message about the wrong component (issue #191).
                raise
            except ProviderUnavailableError as e:
                remote_failure = str(e)
                logger.warning(f"Remote provider unavailable: {e} — falling back to local")
            except Exception as e:
                remote_failure = str(e)
                logger.warning(f"Remote provider error: {e} — falling back to local")
        else:
            logger.info("No remote URL configured — using local provider")

        # The remote failed and there is nothing to fall back *to*. The stock
        # image ships without local Whisper, so entering that leg raises a
        # TranscriptionError, which the queue treats as permanent: one worker
        # reboot would fail the book with `retry_count=0` and an error naming
        # local Whisper. Re-raising as unavailable hands it to the retry ladder
        # instead (issue #191).
        #
        # With no remote configured at all there is nothing to retry, so the
        # local leg's own error is the honest answer and we let it through.
        if remote_failure is not None and not await self._local.is_available():
            raise ProviderUnavailableError(
                f"Remote transcription unavailable ({remote_failure}) and local "
                f"Whisper is not installed in this image — will retry the remote"
            )

        # Fallback to local
        logger.info("Using local Whisper as fallback")
        self._selected = self._local
        return await self._local.transcribe(audio_path, progress_callback)

    async def request_pause(self) -> bool:
        """Delegate to the provider that's actually running the job."""
        target = self._selected or self._remote
        return await target.request_pause() if target else False

    async def release_resources(self) -> None:
        # Always ask the remote to unload: it owns the GPU memory the off-hours
        # window exists to free, whether or not it ran the last job.
        if self._remote:
            await self._remote.release_resources()

    async def discard_checkpoint(self, audio_path: str) -> None:
        if self._remote:
            await self._remote.discard_checkpoint(audio_path)

    async def is_available(self) -> bool:
        """Available if either remote or local is available."""
        if self._remote and await self._remote.is_available():
            return True
        return await self._local.is_available()

    def name(self) -> str:
        if self._remote:
            return f"Remote with Fallback ({self._remote.remote_url})"
        return "Local Whisper (no remote configured)"
