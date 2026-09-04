"""
Local Whisper provider: the language pin (issue #246).

Same bug as the worker had — `model.transcribe()` was called with no
`language`, so Whisper re-detected it from the first seconds of *every* chunk
independently. Here a chunk is a full hour, so one chunk that opens on music or
a foreign-language epigraph mis-detects and comes back as an hour of
transliterated garbage, which alignment then silently interpolates across.

torch/openai-whisper aren't installed in the test image (the default server
image doesn't ship them either), so `whisper` is stubbed in `sys.modules` and
the device probe is patched out.
"""

import sys
import types

import pytest

from services import transcription as transcription_service
from services.transcription_providers.local import LocalWhisperProvider


class _FakeAudioChunk:
    """Stands in for the numpy array: `transcribe_audiobook` only ever asks for
    its length, and a real hour of 16 kHz samples is 57M floats."""

    def __init__(self, samples: int):
        self._samples = samples

    def __len__(self) -> int:
        return self._samples


class _FakeWhisperModel:
    def __init__(self, detected_language: str):
        self.kwargs = []
        self._detected = detected_language

    @property
    def languages(self):
        return [kw.get("language") for kw in self.kwargs]

    def transcribe(self, audio, **kwargs):
        self.kwargs.append(kwargs)
        return {"segments": [], "language": self._detected}


@pytest.fixture
def fake_whisper(monkeypatch):
    """Install a fake `whisper` module and a stubbed ffmpeg pipeline."""
    def _install(duration=3700.0, detected_language="fr"):
        model = _FakeWhisperModel(detected_language)
        module = types.ModuleType("whisper")
        module.load_model = lambda name, device=None: model
        monkeypatch.setitem(sys.modules, "whisper", module)

        monkeypatch.setattr(transcription_service, "_get_whisper_device", lambda: "cpu")
        monkeypatch.setattr(transcription_service, "_get_audio_duration", lambda path: duration)
        monkeypatch.setattr(
            transcription_service, "load_audio_chunk",
            lambda path, start, dur, sr=16000: _FakeAudioChunk(dur * 16000),
        )
        return model

    return _install


def test_configured_language_is_passed_to_whisper(fake_whisper):
    model = fake_whisper(detected_language="de")

    transcription_service.transcribe_audiobook("book.m4b", language="en")

    assert model.languages == ["en", "en"]


def test_detected_language_is_reused_for_later_chunks(fake_whisper):
    """Unconfigured: chunk 1 detects, and its answer pins the rest of the file."""
    model = fake_whisper(detected_language="fr")

    transcription_service.transcribe_audiobook("book.m4b")

    assert model.languages == [None, "fr"]


@pytest.mark.asyncio
async def test_local_provider_passes_language_to_whisper(fake_whisper):
    """The setting has to reach the model, not stop at the provider."""
    model = fake_whisper()

    await LocalWhisperProvider(language="es").transcribe("book.m4b")

    assert model.languages == ["es", "es"]


@pytest.mark.asyncio
async def test_local_provider_defaults_to_auto_detect(fake_whisper):
    model = fake_whisper(detected_language="pt")

    await LocalWhisperProvider().transcribe("book.m4b")

    assert model.languages == [None, "pt"]
