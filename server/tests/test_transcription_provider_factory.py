"""
`get_transcription_provider` and the remote timeout knob (issue #248).

The timeout was clamped with `max(stored, 86400)`, so the System →
Transcription "Remote Timeout (s)" field was inert below 24 h: an admin could
save 1800, see it persisted, and still have the worker call block for a day.
The clamp existed for a real reason — `POST /v1/transcribe` is one blocking
request for the whole transcription, so the read timeout must outlast the job
— but that makes 24 h the right *default*, not a floor. What remains is a
small sanity floor so a typo cannot zero it.
"""

import logging

import pytest

from services import transcription_providers as factory
from services.transcription_providers import RemoteWhisperProvider


@pytest.fixture
def stored_settings(monkeypatch):
    """Drive the factory from a fixed settings dict, no DB."""
    def _install(**overrides):
        db_settings = {
            "transcription_provider": "remote",
            "transcription_remote_url": "http://x",
        }
        db_settings.update(overrides)

        async def _fake_load():
            return dict(db_settings)

        async def _fake_key():
            return ""

        monkeypatch.setattr(factory, "_load_settings_from_db", _fake_load)
        monkeypatch.setattr(factory, "_load_remote_key", _fake_key)
        return db_settings

    return _install


async def test_configured_remote_timeout_is_honoured(stored_settings, caplog):
    stored_settings(transcription_remote_timeout=1800)

    with caplog.at_level(logging.INFO, logger="services.transcription_providers"):
        provider = await factory.get_transcription_provider()

    assert isinstance(provider, RemoteWhisperProvider)
    assert provider.timeout == 1800
    # The effective value is logged, so an operator can confirm from the logs
    # that the knob took rather than inferring it from a 24 h hang.
    assert "1800" in caplog.text


async def test_remote_timeout_default_is_24h(stored_settings):
    stored_settings()

    provider = await factory.get_transcription_provider()

    assert provider.timeout == 86400


async def test_remote_timeout_below_floor_is_raised_to_floor(stored_settings):
    """A typo that would abandon every job seconds in is floored, not honoured."""
    stored_settings(transcription_remote_timeout=10)

    provider = await factory.get_transcription_provider()

    assert provider.timeout == 60


def test_there_is_no_env_level_timeout_to_disagree_with_the_db_setting():
    """The env field was read by nothing and documented a default (7200) the
    queue never used. Keep it gone rather than have two sources of truth."""
    from config import settings as app_settings

    assert not hasattr(app_settings, "transcription_remote_timeout")


async def test_fallback_provider_gets_the_same_timeout(stored_settings):
    stored_settings(
        transcription_provider="remote_with_fallback", transcription_remote_timeout=1800
    )

    provider = await factory.get_transcription_provider()

    assert provider._remote.timeout == 1800


# ---------------------------------------------------------------------------
# Whisper language (issue #246)
# ---------------------------------------------------------------------------

async def test_configured_language_reaches_the_remote_provider(stored_settings):
    stored_settings(transcription_language="es")

    provider = await factory.get_transcription_provider()

    assert provider.language == "es"


async def test_language_defaults_to_auto(stored_settings):
    """Empty means "let the worker detect once and pin", not "force ''"."""
    stored_settings()

    provider = await factory.get_transcription_provider()

    assert provider.language == ""


async def test_configured_language_reaches_the_local_provider(stored_settings):
    stored_settings(transcription_provider="local", transcription_language="fr")

    provider = await factory.get_transcription_provider()

    assert provider.language == "fr"


async def test_fallback_provider_passes_the_language_to_both_halves(stored_settings):
    stored_settings(
        transcription_provider="remote_with_fallback", transcription_language="de"
    )

    provider = await factory.get_transcription_provider()

    assert provider._remote.language == "de"
    assert provider._local.language == "de"


def test_the_language_allow_list_is_lowercase_iso_639_1_plus_auto():
    """`routers.settings` validates against this list, and the web UI mirrors
    it — a stray uppercase or blank-with-whitespace entry would let a value
    through that faster-whisper then rejects mid-job."""
    assert "" in factory.SUPPORTED_LANGUAGES, "auto-detect must stay allowed"
    codes = [c for c in factory.SUPPORTED_LANGUAGES if c]
    assert codes == sorted(codes), "keep the list sorted so the UI reads predictably"
    assert all(c == c.lower() and 2 <= len(c) <= 3 and c.isalpha() for c in codes)
