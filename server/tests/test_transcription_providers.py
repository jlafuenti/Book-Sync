"""
`FallbackProvider` — when the local leg is allowed to run at all (issue #191).

The shipped default is ``remote_with_fallback`` on an image that has no local
Whisper, so "fall back to local" means "raise a `TranscriptionError` naming the
wrong component and permanently fail the book". Two rules keep that from
happening:

* fall back only if the local leg is actually installed; otherwise re-raise as
  `ProviderUnavailableError` so the queue's retry ladder rides out the blip;
* never swallow a `TranscriptionError` from the remote leg — `base.py` defines
  it as non-recoverable, and it carries the one actionable diagnosis the remote
  produces ("corrupt or incomplete source file, re-import required").
"""

import pytest

from services.transcription_providers import FallbackProvider
from services.transcription_providers.base import (
    ProviderUnavailableError,
    TranscriptionError,
    TranscriptionPaused,
)


class _StubLeg:
    """Minimal stand-in for one leg of the fallback pair."""

    def __init__(self, available=True, raises=None, sentences=None):
        self._available = available
        self._raises = raises
        self._sentences = sentences if sentences is not None else []
        self.transcribe_calls = 0

    async def is_available(self):
        return self._available

    async def transcribe(self, audio_path, progress_callback=None):
        self.transcribe_calls += 1
        if self._raises is not None:
            raise self._raises
        return self._sentences

    def name(self):
        return "stub"


def _provider(remote, local):
    provider = FallbackProvider(remote_url="http://worker.invalid:9000")
    provider._remote = remote
    provider._local = local
    return provider


@pytest.mark.asyncio
async def test_fallback_reraises_provider_unavailable_when_local_is_not_installed():
    """Health check fails and there is no local Whisper — retriable, not fatal."""
    remote = _StubLeg(available=False)
    local = _StubLeg(available=False)
    provider = _provider(remote, local)

    with pytest.raises(ProviderUnavailableError) as exc:
        await provider.transcribe("/audio/book.m4b")

    assert "local Whisper is not installed" in str(exc.value)
    assert local.transcribe_calls == 0, (
        "the local leg must not be entered when it cannot possibly work — its "
        "TranscriptionError would permanently fail the book"
    )


@pytest.mark.asyncio
async def test_fallback_reraises_when_remote_is_unavailable_and_local_is_missing():
    """Same rule when the remote leg raises mid-job rather than failing health."""
    remote = _StubLeg(available=True, raises=ProviderUnavailableError("worker rebooting"))
    local = _StubLeg(available=False)
    provider = _provider(remote, local)

    with pytest.raises(ProviderUnavailableError) as exc:
        await provider.transcribe("/audio/book.m4b")

    assert "worker rebooting" in str(exc.value), "the real reason must survive"
    assert local.transcribe_calls == 0


@pytest.mark.asyncio
async def test_fallback_still_uses_local_when_it_is_installed():
    """Guard against over-correcting: a real local install still takes the job."""
    remote = _StubLeg(available=False)
    local = _StubLeg(available=True, sentences=["s"])
    provider = _provider(remote, local)

    assert await provider.transcribe("/audio/book.m4b") == ["s"]
    assert local.transcribe_calls == 1


@pytest.mark.asyncio
async def test_fallback_does_not_swallow_transcription_error_from_remote():
    """`base.py` calls TranscriptionError non-recoverable — no fallback leg."""
    remote = _StubLeg(available=True, raises=TranscriptionError("corrupt source"))
    local = _StubLeg(available=True, sentences=["s"])
    provider = _provider(remote, local)

    with pytest.raises(TranscriptionError, match="corrupt source"):
        await provider.transcribe("/audio/book.m4b")

    assert local.transcribe_calls == 0


@pytest.mark.asyncio
async def test_fallback_still_lets_a_pause_through():
    """A pause is not a failure and must not be redone on the local leg."""
    remote = _StubLeg(available=True, raises=TranscriptionPaused("paused", 900, 0.5))
    local = _StubLeg(available=True, sentences=["s"])
    provider = _provider(remote, local)

    with pytest.raises(TranscriptionPaused):
        await provider.transcribe("/audio/book.m4b")

    assert local.transcribe_calls == 0


@pytest.mark.asyncio
async def test_unexpected_remote_error_still_falls_back_to_an_installed_local():
    """An error the contract doesn't name is retried locally when local exists."""
    remote = _StubLeg(available=True, raises=RuntimeError("something odd"))
    local = _StubLeg(available=True, sentences=["s"])
    provider = _provider(remote, local)

    assert await provider.transcribe("/audio/book.m4b") == ["s"]
    assert local.transcribe_calls == 1


@pytest.mark.asyncio
async def test_unexpected_remote_error_is_retriable_when_local_is_missing():
    """...but with no local leg it must reach the queue as retriable, not fatal."""
    remote = _StubLeg(available=True, raises=RuntimeError("something odd"))
    local = _StubLeg(available=False)
    provider = _provider(remote, local)

    with pytest.raises(ProviderUnavailableError, match="something odd"):
        await provider.transcribe("/audio/book.m4b")

    assert local.transcribe_calls == 0


@pytest.mark.asyncio
async def test_no_remote_configured_goes_straight_to_local():
    """With no remote URL there is nothing to retry — the local error is the truth."""
    local = _StubLeg(available=False, raises=TranscriptionError("not installed"))
    provider = _provider(None, local)

    with pytest.raises(TranscriptionError, match="not installed"):
        await provider.transcribe("/audio/book.m4b")

    assert local.transcribe_calls == 1
