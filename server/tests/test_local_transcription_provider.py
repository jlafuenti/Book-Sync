"""
Local Whisper provider: the language pin (issue #246).

Same bug as the worker had — `model.transcribe()` was called with no
`language`, so Whisper re-detected it from the first seconds of *every* chunk
independently. Here a chunk is a full hour, so one chunk that opens on music or
a foreign-language epigraph mis-detects and comes back as an hour of
transliterated garbage, which alignment then silently interpolates across.

faster-whisper isn't installed in the test image (the default server image
doesn't ship it either), so `faster_whisper` is stubbed in `sys.modules` and
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
        # faster-whisper returns (segment iterator, info)
        return iter(()), types.SimpleNamespace(language=self._detected)


@pytest.fixture
def fake_whisper(monkeypatch, tmp_path):
    """Install a fake `faster_whisper` module and a stubbed ffmpeg pipeline."""
    def _install(duration=3700.0, detected_language="fr"):
        model = _FakeWhisperModel(detected_language)
        module = types.ModuleType("faster_whisper")
        module.WhisperModel = lambda name, **kwargs: model
        monkeypatch.setitem(sys.modules, "faster_whisper", module)

        monkeypatch.setattr(transcription_service.settings, "app_data_dir", str(tmp_path))

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


def test_chunk_decode_follows_the_file_timestamps(monkeypatch):
    """Issue #795: decoded straight to raw PCM, a merged m4b whose frame
    timestamps overlap at the part joins yields more samples than its
    timeline (904 s for a 900 s chunk), so every timestamp in the chunk
    drifts late. The resampler's async mode makes ffmpeg follow the
    timestamps. Same fix as the Jetson worker's decode."""
    import subprocess

    calls = []

    def fake_run(cmd, capture_output=False, check=False, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout=b"\x00\x00" * 16, stderr=b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    transcription_service.load_audio_chunk("/x/book.m4b", 900, 900)
    assert len(calls) == 1
    cmd = calls[0]
    assert "-af" in cmd and cmd[cmd.index("-af") + 1] == "aresample=async=1"
