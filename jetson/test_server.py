"""
Tests for the shared-secret auth guard added in jetson/server.py (#38), the
oversized-chunk shrink/abort decision logic (#80), and the off-hours control
surface — lazy model load, idle unload, and pause/resume (#106).

Wired into CI via .github/workflows/jetson-tests.yml, path-filtered to
jetson/**. CI installs only the light deps (fastapi, uvicorn, httpx, pytest)
— NOT faster-whisper or nltk's data corpus. jetson/conftest.py stubs both
modules in sys.modules so server.py can be imported without either. Run
locally:

    cd jetson && python -m pytest test_server.py -v

Since #106 the startup event no longer loads the GPU model, so TestClient is
usable here; the transcription path itself is driven with a fake model plus
stubbed ffmpeg helpers.
"""

import importlib
import json
import os
import time

os.environ.setdefault("TRANSCRIPTION_API_KEY", "test-key")

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import server as jetson_server  # noqa: E402  (import after env var is set above)

AUTH = {"Authorization": "Bearer test-key"}


class _FakeWord:
    """Stands in for faster-whisper's Word (word_timestamps=True)."""

    def __init__(self, word, start, end, probability=0.9):
        self.word = word
        self.start = start
        self.end = end
        self.probability = probability


class _FakeSegment:
    def __init__(self, text, start, end, words=None):
        self.text = text
        self.start = start
        self.end = end
        # A segment from a transcribe() call made without word_timestamps has
        # no `words` attribute at all; leave it off unless the test gives some.
        if words is not None:
            self.words = words


class _FakeCT2:
    def unload_model(self):
        pass

    def load_model(self):
        pass


class _FakeInfo:
    """Stands in for faster-whisper's TranscriptionInfo (#246 needs .language)."""

    def __init__(self, language="en"):
        self.language = language


class _FakeWhisper:
    """Stands in for faster_whisper.WhisperModel."""

    def __init__(self, detected_language="en"):
        self.model = _FakeCT2()
        self.calls = 0
        # One dict per transcribe() call, so a test can assert what language
        # each chunk was given (#246).
        self.kwargs = []
        self._detected_language = detected_language

    @property
    def languages(self):
        """The `language=` kwarg each chunk was transcribed with, in order."""
        return [kw.get("language") for kw in self.kwargs]

    def transcribe(self, audio_array, **kwargs):
        self.calls += 1
        self.kwargs.append(kwargs)
        segment = _FakeSegment(
            "Hello there.", 0.0, 1.0,
            words=[_FakeWord(" Hello", 0.0, 0.4, 0.99), _FakeWord(" there.", 0.5, 1.0, 0.8)],
        )
        return iter([segment]), _FakeInfo(self._detected_language)


@pytest.fixture
def clean_state(monkeypatch, tmp_path):
    """Isolate module globals and the checkpoint directory per test."""
    monkeypatch.setattr(jetson_server, "CHECKPOINT_DIR", str(tmp_path / "ckpt"))
    monkeypatch.setattr(jetson_server, "model", None)
    monkeypatch.setattr(jetson_server, "_model_state", "unloaded")
    monkeypatch.setattr(jetson_server, "_job_status", jetson_server.JobStatus())
    # The result cache is a module global with a 24h TTL, so a finished job in
    # one test would otherwise still be served in the next one.
    jetson_server._recent_results.clear()
    jetson_server._pause_event.clear()
    yield
    jetson_server._recent_results.clear()
    jetson_server._pause_event.clear()


@pytest.fixture
def fake_audio_pipeline(monkeypatch):
    """Stub the ffmpeg-backed helpers so _transcribe_file can run anywhere."""
    monkeypatch.setattr(jetson_server, "_get_audio_duration", lambda path: 1800.0)
    monkeypatch.setattr(
        jetson_server, "load_audio_chunk",
        lambda path, start, dur, sr=16000: [0.0] * (dur * 16000),
    )
    monkeypatch.setattr(jetson_server, "_read_sys_mem_mb", lambda: {"MemAvailable": 9999})


@pytest.fixture
def client():
    """TestClient used *without* the lifespan context, so the startup event
    (and its idle-unload thread) doesn't run during unit tests."""
    return TestClient(jetson_server.app)


def test_refuses_to_start_without_api_key(monkeypatch):
    monkeypatch.delenv("TRANSCRIPTION_API_KEY", raising=False)
    try:
        with pytest.raises(RuntimeError, match="TRANSCRIPTION_API_KEY"):
            importlib.reload(jetson_server)
    finally:
        # Restore so later tests (and later files, if run in the same process)
        # see a valid module state again.
        monkeypatch.setenv("TRANSCRIPTION_API_KEY", "test-key")
        importlib.reload(jetson_server)


def test_verify_api_key_rejects_missing_header():
    with pytest.raises(HTTPException) as exc_info:
        jetson_server.verify_api_key(authorization="")
    assert exc_info.value.status_code == 401


def test_verify_api_key_rejects_wrong_scheme():
    with pytest.raises(HTTPException) as exc_info:
        jetson_server.verify_api_key(authorization="Basic test-key")
    assert exc_info.value.status_code == 401


def test_verify_api_key_rejects_wrong_token():
    with pytest.raises(HTTPException) as exc_info:
        jetson_server.verify_api_key(authorization="Bearer wrong-key")
    assert exc_info.value.status_code == 401


def test_verify_api_key_accepts_correct_token():
    jetson_server.verify_api_key(authorization="Bearer test-key")  # should not raise


def test_all_v1_routes_declare_the_auth_dependency():
    """Belt-and-suspenders: catch a route being added later without the guard."""
    v1_routes = [r for r in jetson_server.app.routes if getattr(r, "path", "").startswith("/v1/")]
    assert v1_routes, "expected at least one /v1/* route to exist"
    for route in v1_routes:
        dependant_calls = [d.call for d in route.dependant.dependencies]
        assert jetson_server.verify_api_key in dependant_calls, (
            f"{route.path} is missing the verify_api_key dependency"
        )


def test_is_chunk_oversized_true_when_ffmpeg_returns_excess_audio():
    # OVERSIZED_CHUNK_TOLERANCE is 1.5x; 200s actual for a 100s request exceeds it
    assert jetson_server._is_chunk_oversized(actual_chunk_sec=200, requested_chunk_size=100) is True


def test_is_chunk_oversized_false_within_tolerance():
    assert jetson_server._is_chunk_oversized(actual_chunk_sec=140, requested_chunk_size=100) is False


def test_shrink_chunk_size_halves_the_value():
    assert jetson_server._shrink_chunk_size(900) == 450


def test_shrink_chunk_size_retry_sequence_reaches_clean_failure():
    """Simulates the retry loop: keep shrinking until the pure function signals
    abort (None), matching the oversized -> shrink/retry -> clean failure path."""
    sizes = []
    size = jetson_server.DEFAULT_CHUNK_SIZE_SEC  # 900
    while True:
        size = jetson_server._shrink_chunk_size(size)
        if size is None:
            break
        sizes.append(size)
    # 900 -> 450 -> 225 -> 112 (still >= MIN_CHUNK_SIZE_SEC=112, so one more retry)
    # -> 56 (< 112, abort)
    assert sizes == [450, 225, 112]


# ---------------------------------------------------------------------------
# Lazy model load + idle unload (#106)
# ---------------------------------------------------------------------------

def test_startup_does_not_load_the_model(clean_state, monkeypatch):
    """The whole point: an idle worker holds no GPU memory."""
    loaded = []
    monkeypatch.setattr(jetson_server, "load_model", lambda: loaded.append(1))
    monkeypatch.setattr(jetson_server, "MODEL_IDLE_UNLOAD_MIN", 0)  # no idle thread

    jetson_server.startup_event()

    assert loaded == []
    assert jetson_server.model is None


def test_ensure_model_loaded_loads_once(clean_state, monkeypatch):
    calls = []

    def _load():
        calls.append(1)
        jetson_server.model = _FakeWhisper()

    monkeypatch.setattr(jetson_server, "load_model", _load)

    jetson_server.ensure_model_loaded()
    jetson_server.ensure_model_loaded()

    assert calls == [1]
    assert jetson_server._model_state == "loaded"


def test_ensure_model_loaded_resets_state_when_loading_fails(clean_state, monkeypatch):
    def _boom():
        raise RuntimeError("no GPU")

    monkeypatch.setattr(jetson_server, "load_model", _boom)

    with pytest.raises(RuntimeError):
        jetson_server.ensure_model_loaded()
    assert jetson_server._model_state == "unloaded"


@pytest.mark.parametrize("now,last,active,loaded,minutes,expected", [
    (10_000, 0, False, True, 30, True),        # idle long enough
    (100, 0, False, True, 30, False),          # not idle long enough
    (10_000, 0, True, True, 30, False),        # a job is running
    (10_000, 0, False, False, 30, False),      # nothing loaded to release
    (10_000, 0, False, True, 0, False),        # 0 = never unload
])
def test_should_unload_idle_decision_table(now, last, active, loaded, minutes, expected):
    assert jetson_server._should_unload_idle(now, last, active, loaded, minutes) is expected


def test_unload_model_releases_and_is_idempotent(clean_state, monkeypatch):
    monkeypatch.setattr(jetson_server, "model", _FakeWhisper())
    monkeypatch.setattr(jetson_server, "_model_state", "loaded")

    assert jetson_server.unload_model() is True
    assert jetson_server.model is None
    assert jetson_server._model_state == "unloaded"
    assert jetson_server.unload_model() is False


def test_health_reports_model_state(clean_state, client):
    body = client.get("/v1/health", headers=AUTH).json()
    assert body["status"] == "healthy"
    assert body["model_state"] == "unloaded"
    assert body["model_loaded"] is False


def test_unload_endpoint_refuses_while_a_job_is_running(clean_state, client, monkeypatch):
    monkeypatch.setattr(jetson_server, "model", _FakeWhisper())
    jetson_server._job_status.active = True

    body = client.post("/v1/unload", headers=AUTH).json()
    assert body["unloaded"] is False
    assert jetson_server.model is not None


def test_unload_endpoint_releases_when_idle(clean_state, client, monkeypatch):
    monkeypatch.setattr(jetson_server, "model", _FakeWhisper())

    body = client.post("/v1/unload", headers=AUTH).json()
    assert body["unloaded"] is True
    assert body["model_state"] == "unloaded"


# ---------------------------------------------------------------------------
# Pause / resume (#106)
# ---------------------------------------------------------------------------

def _run_job(monkeypatch, tmp_path, filename="book.m4b", body=b"audio-bytes",
             detected_language="en"):
    audio = tmp_path / filename
    audio.write_bytes(body)
    fake = _FakeWhisper(detected_language=detected_language)
    monkeypatch.setattr(jetson_server, "model", fake)
    monkeypatch.setattr(jetson_server, "_model_state", "loaded")
    return audio, fake


def test_transcribe_runs_to_completion_without_a_pause(
    clean_state, fake_audio_pipeline, monkeypatch, tmp_path
):
    audio, fake = _run_job(monkeypatch, tmp_path)

    result = jetson_server._transcribe_file(str(audio), "book.m4b")

    assert result["sentences"], "expected transcribed sentences"
    assert "status" not in result
    assert fake.calls == 2  # 1800s of audio at the 900s default chunk


def test_pause_stops_at_a_chunk_boundary_and_keeps_progress(
    clean_state, fake_audio_pipeline, monkeypatch, tmp_path
):
    audio, fake = _run_job(monkeypatch, tmp_path)
    real_save = jetson_server._save_checkpoint

    def _save_then_pause(*args, **kwargs):
        real_save(*args, **kwargs)
        jetson_server._pause_event.set()  # window closed during the first chunk

    monkeypatch.setattr(jetson_server, "_save_checkpoint", _save_then_pause)

    result = jetson_server._transcribe_file(str(audio), "book.m4b")

    assert result["status"] == "paused"
    assert result["completed_through_sec"] == 900
    assert result["sentences_so_far"] == 1
    assert result["audio_retained"] is True
    assert fake.calls == 1, "must stop before starting another chunk"
    # The pause released the GPU — that's the reason for pausing at all.
    assert jetson_server.model is None


def test_paused_job_is_not_served_from_the_result_cache(
    clean_state, fake_audio_pipeline, monkeypatch, tmp_path, client
):
    """A partial transcript handed out by /v1/result would silently truncate
    the book — the client cannot tell it apart from a finished one."""
    audio, _ = _run_job(monkeypatch, tmp_path)
    # Read the size now: pausing moves the source next to the checkpoint.
    size = audio.stat().st_size
    jetson_server._job_status.current_file = "book.m4b"
    monkeypatch.setattr(
        jetson_server, "_save_checkpoint",
        lambda *a, **k: jetson_server._pause_event.set(),
    )

    jetson_server._transcribe_file(str(audio), "book.m4b")

    # Neither shape of the lookup may find it: not by name alone, and not by
    # the (filename, size) identity a current client sends.
    assert client.get("/v1/result/book.m4b", headers=AUTH).status_code == 404
    assert client.get(
        "/v1/result/book.m4b", params={"size": size}, headers=AUTH
    ).status_code == 404


def test_checkpoint_endpoint_reports_paused_progress_and_retained_audio(
    clean_state, fake_audio_pipeline, monkeypatch, tmp_path, client
):
    audio, _ = _run_job(monkeypatch, tmp_path)
    size = audio.stat().st_size
    real_save = jetson_server._save_checkpoint

    def _save_then_pause(*args, **kwargs):
        real_save(*args, **kwargs)
        jetson_server._pause_event.set()

    monkeypatch.setattr(jetson_server, "_save_checkpoint", _save_then_pause)
    jetson_server._transcribe_file(str(audio), "book.m4b")

    body = client.get(
        "/v1/checkpoint", params={"filename": "book.m4b", "size": size}, headers=AUTH
    ).json()

    assert body["exists"] is True
    assert body["completed_through_sec"] == 900
    assert body["progress"] == 0.5
    assert body["audio_retained"] is True


def test_checkpoint_endpoint_reports_nothing_for_an_unknown_file(clean_state, client):
    body = client.get(
        "/v1/checkpoint", params={"filename": "nope.m4b", "size": 1}, headers=AUTH
    ).json()
    assert body == {"exists": False}


def test_resume_continues_from_the_checkpoint_without_an_upload(
    clean_state, fake_audio_pipeline, monkeypatch, tmp_path, client
):
    audio, fake = _run_job(monkeypatch, tmp_path)
    size = audio.stat().st_size
    real_save = jetson_server._save_checkpoint
    pause_once = {"done": False}

    def _save_then_pause(*args, **kwargs):
        real_save(*args, **kwargs)
        if not pause_once["done"]:
            pause_once["done"] = True
            jetson_server._pause_event.set()

    monkeypatch.setattr(jetson_server, "_save_checkpoint", _save_then_pause)
    first = jetson_server._transcribe_file(str(audio), "book.m4b")
    assert first["status"] == "paused"

    # The upload's temp file is gone (moved into the checkpoint dir).
    assert not audio.exists()

    # Second window: resume with no audio in the request body.
    monkeypatch.setattr(jetson_server, "model", _FakeWhisper())
    monkeypatch.setattr(jetson_server, "_model_state", "loaded")
    r = client.post(
        "/v1/transcribe/resume", json={"filename": "book.m4b", "size": size}, headers=AUTH
    )

    assert r.status_code == 200
    body = r.json()
    assert "status" not in body, "the resumed job should have finished"
    # Chunk 1's sentence came from the checkpoint, chunk 2 from this run.
    assert len(body["sentences"]) == 2
    assert [s["start_ms"] for s in body["sentences"]] == [0, 900_000]

    # Completion cleans up both the checkpoint and the retained audio.
    after = client.get(
        "/v1/checkpoint", params={"filename": "book.m4b", "size": size}, headers=AUTH
    ).json()
    assert after == {"exists": False}


def test_resume_404s_when_the_audio_was_not_retained(clean_state, client):
    r = client.post(
        "/v1/transcribe/resume", json={"filename": "gone.m4b", "size": 12}, headers=AUTH
    )
    assert r.status_code == 404


def test_delete_checkpoint_removes_progress_and_audio(
    clean_state, fake_audio_pipeline, monkeypatch, tmp_path, client
):
    audio, _ = _run_job(monkeypatch, tmp_path)
    size = audio.stat().st_size
    real_save = jetson_server._save_checkpoint

    def _save_then_pause(*args, **kwargs):
        real_save(*args, **kwargs)
        jetson_server._pause_event.set()

    monkeypatch.setattr(jetson_server, "_save_checkpoint", _save_then_pause)
    jetson_server._transcribe_file(str(audio), "book.m4b")

    params = {"filename": "book.m4b", "size": size}
    assert client.request("DELETE", "/v1/checkpoint", params=params, headers=AUTH).json() == {
        "deleted": True
    }
    assert client.get("/v1/checkpoint", params=params, headers=AUTH).json() == {"exists": False}


def test_pause_endpoint_is_a_noop_when_nothing_is_running(clean_state, client):
    body = client.post("/v1/pause", headers=AUTH).json()
    assert body["paused_requested"] is False
    assert not jetson_server._pause_event.is_set()


def test_pause_endpoint_sets_the_flag_for_a_running_job(clean_state, client):
    jetson_server._job_status.active = True
    jetson_server._job_status.current_file = "book.m4b"

    body = client.post("/v1/pause", headers=AUTH).json()

    assert body["paused_requested"] is True
    assert body["current_file"] == "book.m4b"
    assert jetson_server._pause_event.is_set()


def test_a_stale_pause_flag_does_not_stop_the_next_job(
    clean_state, fake_audio_pipeline, monkeypatch, tmp_path
):
    """A pause that arrived with nothing running must not leak into the job
    that starts afterwards."""
    audio, fake = _run_job(monkeypatch, tmp_path)
    jetson_server._pause_event.set()

    result = jetson_server._transcribe_file(str(audio), "book.m4b")

    assert "status" not in result
    assert fake.calls == 2


# ---------------------------------------------------------------------------
# Whisper language: env default, detect-once-and-pin, per-job override (#246)
# ---------------------------------------------------------------------------

def test_configured_language_is_passed_to_the_model(
    clean_state, fake_audio_pipeline, monkeypatch, tmp_path
):
    """WHISPER_LANGUAGE pins every chunk — no per-chunk re-detection at all."""
    monkeypatch.setattr(jetson_server, "WHISPER_LANGUAGE", "en")
    audio, fake = _run_job(monkeypatch, tmp_path, detected_language="de")

    jetson_server._transcribe_file(str(audio), "book.m4b")

    assert fake.calls == 2
    assert fake.languages == ["en", "en"]


def test_detected_language_is_reused_for_later_chunks(
    clean_state, fake_audio_pipeline, monkeypatch, tmp_path
):
    """Unconfigured: chunk 1 auto-detects, and its answer pins the rest.

    A later chunk that opens on music or a foreign epigraph would otherwise be
    detected independently and transliterated into garbage.
    """
    monkeypatch.setattr(jetson_server, "WHISPER_LANGUAGE", None)
    audio, fake = _run_job(monkeypatch, tmp_path, detected_language="fr")

    result = jetson_server._transcribe_file(str(audio), "book.m4b")

    assert fake.languages == [None, "fr"]
    assert result["language"] == "fr"


def test_a_per_job_language_overrides_the_env_default(
    clean_state, fake_audio_pipeline, monkeypatch, tmp_path
):
    monkeypatch.setattr(jetson_server, "WHISPER_LANGUAGE", "en")
    audio, fake = _run_job(monkeypatch, tmp_path)

    jetson_server._transcribe_file(str(audio), "book.m4b", language="es")

    assert fake.languages == ["es", "es"]


def test_the_transcribe_endpoint_accepts_a_language_form_field(
    clean_state, fake_audio_pipeline, monkeypatch, tmp_path, client
):
    monkeypatch.setattr(jetson_server, "WHISPER_LANGUAGE", "en")
    _, fake = _run_job(monkeypatch, tmp_path)

    r = client.post(
        "/v1/transcribe",
        files={"audio_file": ("book.m4b", b"audio-bytes", "audio/mpeg")},
        data={"language": "it"},
        headers=AUTH,
    )

    assert r.status_code == 200, r.text
    assert r.json()["language"] == "it"
    assert fake.languages == ["it", "it"]


def test_resume_keeps_the_pinned_language(
    clean_state, fake_audio_pipeline, monkeypatch, tmp_path, client
):
    """The language detected before a pause is carried in the checkpoint, so
    the second half of the book isn't re-detected (possibly differently)."""
    monkeypatch.setattr(jetson_server, "WHISPER_LANGUAGE", None)
    audio, first_fake = _run_job(monkeypatch, tmp_path, detected_language="de")
    size = audio.stat().st_size
    real_save = jetson_server._save_checkpoint
    pause_once = {"done": False}

    def _save_then_pause(*args, **kwargs):
        real_save(*args, **kwargs)
        if not pause_once["done"]:
            pause_once["done"] = True
            jetson_server._pause_event.set()

    monkeypatch.setattr(jetson_server, "_save_checkpoint", _save_then_pause)
    assert jetson_server._transcribe_file(str(audio), "book.m4b")["status"] == "paused"
    assert first_fake.languages == [None]

    # Second window. This model would "detect" something else if asked.
    second_fake = _FakeWhisper(detected_language="fr")
    monkeypatch.setattr(jetson_server, "model", second_fake)
    monkeypatch.setattr(jetson_server, "_model_state", "loaded")

    r = client.post(
        "/v1/transcribe/resume", json={"filename": "book.m4b", "size": size}, headers=AUTH
    )

    assert r.status_code == 200, r.text
    assert second_fake.languages == ["de"], "the resumed chunk must reuse the pinned language"
    assert r.json()["language"] == "de"


# ---------------------------------------------------------------------------
# One-job guard: check and claim under a single lock (#236)
# ---------------------------------------------------------------------------

def test_a_second_transcribe_request_is_rejected_once_the_slot_is_claimed(
    clean_state, client, monkeypatch
):
    monkeypatch.setattr(
        jetson_server, "_transcribe_file",
        lambda *a, **k: pytest.fail("the second request must never start a job"),
    )
    assert jetson_server._try_claim_job("first.m4b", 1) is True

    r = client.post(
        "/v1/transcribe",
        files={"audio_file": ("second.m4b", b"audio-bytes", "audio/mpeg")},
        headers=AUTH,
    )

    assert r.status_code == 409
    assert r.json()["current_job"]["file"] == "first.m4b"


def test_a_second_resume_request_is_rejected_once_the_slot_is_claimed(
    clean_state, fake_audio_pipeline, monkeypatch, tmp_path, client
):
    """Resume has to go through the same claim — it starts the same job slot."""
    audio, _ = _run_job(monkeypatch, tmp_path)
    size = audio.stat().st_size
    real_save = jetson_server._save_checkpoint

    def _save_then_pause(*args, **kwargs):
        real_save(*args, **kwargs)
        jetson_server._pause_event.set()

    monkeypatch.setattr(jetson_server, "_save_checkpoint", _save_then_pause)
    jetson_server._transcribe_file(str(audio), "book.m4b")

    assert jetson_server._try_claim_job("other.m4b", 1) is True
    monkeypatch.setattr(
        jetson_server, "_transcribe_file",
        lambda *a, **k: pytest.fail("the second request must never start a job"),
    )

    r = client.post(
        "/v1/transcribe/resume", json={"filename": "book.m4b", "size": size}, headers=AUTH
    )

    assert r.status_code == 409
    assert r.json()["current_job"]["file"] == "other.m4b"


def test_try_claim_job_is_atomic(clean_state):
    """Two threads racing the claim: exactly one may win, every round."""
    import threading

    for round_no in range(200):
        jetson_server._release_job()
        barrier = threading.Barrier(2)
        results = []
        results_lock = threading.Lock()

        def _claim():
            barrier.wait()
            won = jetson_server._try_claim_job("book.m4b", 1)
            with results_lock:
                results.append(won)

        threads = [threading.Thread(target=_claim) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert results.count(True) == 1, f"round {round_no}: {results}"


def test_the_job_slot_is_released_when_the_copy_fails(clean_state, client, monkeypatch):
    """A failure between the claim and the handoff must not wedge the worker."""
    def _boom(*args, **kwargs):
        raise OSError("No space left on device")

    monkeypatch.setattr(jetson_server.shutil, "copyfileobj", _boom)

    r = client.post(
        "/v1/transcribe",
        files={"audio_file": ("book.m4b", b"audio-bytes", "audio/mpeg")},
        headers=AUTH,
    )

    assert r.status_code == 500
    assert jetson_server._job_status.active is False


def test_slot_is_released_after_transcription_raises(
    clean_state, fake_audio_pipeline, monkeypatch, tmp_path, client
):
    def _boom(path):
        raise RuntimeError("Could not determine audio duration")

    monkeypatch.setattr(jetson_server, "_get_audio_duration", _boom)
    _run_job(monkeypatch, tmp_path)

    r = client.post(
        "/v1/transcribe",
        files={"audio_file": ("book.m4b", b"audio-bytes", "audio/mpeg")},
        headers=AUTH,
    )

    assert r.status_code == 500
    assert jetson_server._job_status.active is False


def test_the_result_is_cached_under_the_filename_the_job_was_given(
    clean_state, fake_audio_pipeline, monkeypatch, tmp_path, client
):
    """_transcribe_file must use its own argument, not the global slot — a
    second request overwriting current_file used to file the first job's
    transcript under the second job's name."""
    audio, _ = _run_job(monkeypatch, tmp_path)
    jetson_server._job_status.current_file = "someone-elses.m4b"

    jetson_server._transcribe_file(str(audio), "book.m4b")

    assert client.get("/v1/result/book.m4b", headers=AUTH).status_code == 200
    assert client.get("/v1/result/someone-elses.m4b", headers=AUTH).status_code == 404


def test_the_job_is_not_reported_done_until_its_result_is_fetchable(
    clean_state, fake_audio_pipeline, monkeypatch, tmp_path, client
):
    """#847: a re-attached server fetches /v1/result the moment /v1/status says
    the job is no longer active, so "not active" has to imply "fetchable".
    Steps in the success path are recorded in order, each with the job's
    `active` flag and whether /v1/result would answer 200 at that moment."""
    audio, _ = _run_job(monkeypatch, tmp_path)
    steps = []

    def _observe(label):
        steps.append((
            label,
            jetson_server._job_status.active,
            client.get(
                "/v1/result/book.m4b",
                params={"size": audio.stat().st_size},
                headers=AUTH,
            ).status_code,
        ))

    real_cache = jetson_server._cache_result
    real_delete = jetson_server._delete_checkpoint

    def _cache(*args, **kwargs):
        _observe("before_cache")
        real_cache(*args, **kwargs)
        _observe("after_cache")

    def _delete(*args, **kwargs):
        _observe("checkpoint_delete")
        real_delete(*args, **kwargs)

    monkeypatch.setattr(jetson_server, "_cache_result", _cache)
    monkeypatch.setattr(jetson_server, "_delete_checkpoint", _delete)

    jetson_server._transcribe_file(str(audio), "book.m4b")

    labels = [s[0] for s in steps]
    assert labels == ["before_cache", "after_cache", "checkpoint_delete"]
    # The result is cached while the job is still active ...
    assert steps[0][1] is True and steps[1][1] is True
    # ... and fetchable before the checkpoint disappears.
    assert steps[2][2] == 200
    # Once the job flips to inactive, nothing is left to wait for.
    assert jetson_server._job_status.active is False
    assert client.get(
        "/v1/result/book.m4b",
        params={"size": audio.stat().st_size},
        headers=AUTH,
    ).status_code == 200


# ---------------------------------------------------------------------------
# Upload cap and disk guard (#238)
# ---------------------------------------------------------------------------

def test_upload_over_the_size_limit_is_rejected_with_413_before_the_body_is_read(
    clean_state, client, monkeypatch
):
    monkeypatch.setattr(jetson_server, "MAX_UPLOAD_BYTES", 16)
    monkeypatch.setattr(
        jetson_server, "_transcribe_file",
        lambda *a, **k: pytest.fail("the endpoint must not be entered at all"),
    )

    r = client.post(
        "/v1/transcribe",
        files={"audio_file": ("book.m4b", b"x" * 4096, "audio/mpeg")},
        headers=AUTH,
    )

    assert r.status_code == 413
    assert jetson_server._job_status.active is False


def test_chunked_upload_over_the_limit_is_cut_off_with_413(clean_state, client, monkeypatch):
    """A body with no Content-Length still has to be bounded — otherwise the
    header check is trivially bypassed."""
    monkeypatch.setattr(jetson_server, "MAX_UPLOAD_BYTES", 64)
    monkeypatch.setattr(
        jetson_server, "_transcribe_file",
        lambda *a, **k: pytest.fail("the endpoint must not be entered at all"),
    )

    def _chunks():
        for _ in range(8):
            yield b"x" * 256

    r = client.post(
        "/v1/transcribe",
        content=_chunks(),
        headers={**AUTH, "Content-Type": "multipart/form-data; boundary=abc123"},
    )

    assert r.status_code == 413


def test_upload_is_refused_with_507_when_the_disk_is_nearly_full(
    clean_state, client, monkeypatch, tmp_path
):
    """The upload costs twice its size in temp space (spooled body + copy), so
    a job that cannot fit is refused up front instead of ENOSPC-ing halfway."""
    import collections
    import tempfile as _tempfile

    # A private temp dir, so "nothing was written" is an assertion about this
    # request rather than about whatever else is using the system temp.
    upload_tmp = tmp_path / "uploads"
    upload_tmp.mkdir()
    monkeypatch.setattr(_tempfile, "gettempdir", lambda: str(upload_tmp))

    usage = collections.namedtuple("usage", "total used free")
    monkeypatch.setattr(jetson_server.shutil, "disk_usage", lambda path: usage(100, 92, 8))
    monkeypatch.setattr(
        jetson_server, "_transcribe_file",
        lambda *a, **k: pytest.fail("the endpoint must not be entered at all"),
    )

    r = client.post(
        "/v1/transcribe",
        files={"audio_file": ("book.m4b", b"x" * 512, "audio/mpeg")},
        headers=AUTH,
    )

    assert r.status_code == 507
    assert "disk" in r.json()["detail"].lower()
    assert list(upload_tmp.iterdir()) == [], "left a temp file behind"
    assert jetson_server._job_status.active is False


def test_abandoned_upload_temp_files_are_swept_from_the_checkpoint_volume(
    clean_state, monkeypatch, tmp_path
):
    """An OoM-kill mid-upload leaves multiple GB on the volume the free-space
    guard measures — which would then refuse every later job."""
    import tempfile as _tempfile

    upload_tmp = tmp_path / "ckpt" / "tmp"
    upload_tmp.mkdir(parents=True)
    monkeypatch.setattr(_tempfile, "gettempdir", lambda: str(upload_tmp))

    stale = upload_tmp / "tmpabc123.m4b"
    stale.write_bytes(b"abandoned")
    os.utime(stale, (0, 0))
    fresh = upload_tmp / "tmpdef456.m4b"
    fresh.write_bytes(b"in flight")

    jetson_server._cleanup_stale_uploads()

    assert not stale.exists()
    assert fresh.exists()


def test_startup_creates_the_tmpdir_named_by_the_environment(clean_state, monkeypatch, tmp_path):
    """On a fresh checkpoint volume the directory TMPDIR names does not exist
    yet. Python's tempfile.gettempdir() silently drops a TMPDIR that does not
    exist and answers /tmp instead — so creating "whatever gettempdir says"
    creates /tmp and leaves uploads on the container's own filesystem, which is
    exactly what #238 set out to stop. The directory must come from the raw
    environment value, and tempfile's cached answer must be reset afterwards."""
    import tempfile as _tempfile

    wanted = tmp_path / "volume" / "tmp"
    assert not wanted.exists()
    monkeypatch.setenv("TMPDIR", str(wanted))
    monkeypatch.setattr(_tempfile, "tempdir", None)
    monkeypatch.setattr(jetson_server, "MODEL_IDLE_UNLOAD_MIN", 0)

    jetson_server.startup_event()

    assert wanted.is_dir()
    assert os.path.realpath(_tempfile.gettempdir()) == os.path.realpath(str(wanted))


def test_a_shared_system_temp_dir_is_never_swept(clean_state, monkeypatch, tmp_path):
    """On a dev box TMPDIR is the system temp, shared with every other process."""
    import tempfile as _tempfile

    elsewhere = tmp_path / "system-temp"
    elsewhere.mkdir()
    monkeypatch.setattr(_tempfile, "gettempdir", lambda: str(elsewhere))
    someone_elses = elsewhere / "not-ours.dat"
    someone_elses.write_bytes(b"x")
    os.utime(someone_elses, (0, 0))

    jetson_server._cleanup_stale_uploads()

    assert someone_elses.exists()


def test_upload_under_the_limit_still_transcribes(
    clean_state, fake_audio_pipeline, monkeypatch, tmp_path, client
):
    _, fake = _run_job(monkeypatch, tmp_path)

    r = client.post(
        "/v1/transcribe",
        files={"audio_file": ("book.m4b", b"audio-bytes", "audio/mpeg")},
        headers=AUTH,
    )

    assert r.status_code == 200, r.text
    assert r.json()["sentences"]
    assert fake.calls == 2
# Result cache identity: filename + byte size (issue #181)
#
# The cache was keyed on the bare filename, and `audiobook.m4b` /
# `Unabridged.m4b` are ordinary names in a real library. Queue book A, let it
# finish, queue same-named book B: the pre-flight GET returned A's transcript,
# the upload was skipped, and A's sentences were saved as B's — aligned against
# B's EPUB, `pair.status = SYNCED`, "Sync complete!", no error anywhere.
#
# Identity is now (filename, size), the same pair the checkpoint routes already
# use.
# ---------------------------------------------------------------------------

def test_result_cache_round_trips_for_matching_filename_and_size(
    clean_state, fake_audio_pipeline, monkeypatch, tmp_path, client
):
    audio, _ = _run_job(monkeypatch, tmp_path, body=b"book-a-bytes")
    size = audio.stat().st_size

    jetson_server._transcribe_file(str(audio), "book.m4b")

    resp = client.get("/v1/result/book.m4b", params={"size": size}, headers=AUTH)
    assert resp.status_code == 200
    assert resp.json()["sentences"]


def test_result_cache_does_not_serve_a_different_file_of_the_same_name(
    clean_state, fake_audio_pipeline, monkeypatch, tmp_path, client
):
    """The whole bug: two books, one basename, silently swapped transcripts."""
    audio, _ = _run_job(monkeypatch, tmp_path, body=b"book-a-bytes")

    jetson_server._transcribe_file(str(audio), "book.m4b")

    other_size = audio.stat().st_size + 12_345
    resp = client.get("/v1/result/book.m4b", params={"size": other_size}, headers=AUTH)
    assert resp.status_code == 404


def test_result_cache_is_keyed_on_the_argument_not_on_job_status(
    clean_state, fake_audio_pipeline, monkeypatch, tmp_path, client
):
    """A second request that claimed the slot must not rename our cache entry."""
    audio, _ = _run_job(monkeypatch, tmp_path, body=b"book-a-bytes")
    jetson_server._job_status.current_file = "someone-elses.m4b"
    size = audio.stat().st_size

    jetson_server._transcribe_file(str(audio), "book.m4b")

    assert client.get(
        "/v1/result/book.m4b", params={"size": size}, headers=AUTH
    ).status_code == 200


def test_result_cache_serves_a_legacy_request_with_no_size(
    clean_state, fake_audio_pipeline, monkeypatch, tmp_path, client
):
    """A server that predates the size parameter must keep working (wire compat).

    `size` is optional, not required: making it required would 422 every
    re-attach fetch from an un-upgraded server and fail those books loudly.
    """
    audio, _ = _run_job(monkeypatch, tmp_path, body=b"book-a-bytes")

    jetson_server._transcribe_file(str(audio), "book.m4b")

    resp = client.get("/v1/result/book.m4b", headers=AUTH)
    assert resp.status_code == 200
    assert resp.json()["sentences"]


def test_a_legacy_request_with_no_size_refuses_an_ambiguous_name(
    clean_state, fake_audio_pipeline, monkeypatch, tmp_path, client
):
    """Two cached entries share the basename — a no-size lookup can't pick one.

    404 sends the old server down the upload path, which is slow but correct.
    Guessing would reproduce exactly the swap this issue is about.
    """
    a = tmp_path / "a" / "book.m4b"
    a.parent.mkdir()
    a.write_bytes(b"book-a-bytes")
    b = tmp_path / "b" / "book.m4b"
    b.parent.mkdir()
    b.write_bytes(b"book-b-bytes-which-are-longer")
    fake = _FakeWhisper()
    monkeypatch.setattr(jetson_server, "model", fake)
    monkeypatch.setattr(jetson_server, "_model_state", "loaded")

    jetson_server._transcribe_file(str(a), "book.m4b")
    jetson_server._transcribe_file(str(b), "book.m4b")

    assert client.get("/v1/result/book.m4b", headers=AUTH).status_code == 404
    # ...but each is still reachable by its own size.
    for path in (a, b):
        assert client.get(
            "/v1/result/book.m4b", params={"size": path.stat().st_size}, headers=AUTH
        ).status_code == 200


def test_expired_results_are_dropped_on_read(
    clean_state, fake_audio_pipeline, monkeypatch, tmp_path, client
):
    """Entries were only evicted when a *later* job finished, so on a lightly
    used worker a stale one stayed servable for days."""
    audio, _ = _run_job(monkeypatch, tmp_path, body=b"book-a-bytes")
    size = audio.stat().st_size

    jetson_server._transcribe_file(str(audio), "book.m4b")

    key = jetson_server._checkpoint_key_for("book.m4b", size)
    result, _stamp, name = jetson_server._recent_results[key]
    aged = time.time() - jetson_server.RESULT_CACHE_TTL_SEC - 60
    jetson_server._recent_results[key] = (result, aged, name)

    assert client.get(
        "/v1/result/book.m4b", params={"size": size}, headers=AUTH
    ).status_code == 404
    assert key not in jetson_server._recent_results


def test_status_reports_the_current_job_size(clean_state, client):
    jetson_server._job_status.active = True
    jetson_server._job_status.current_file = "book.m4b"
    jetson_server._job_status.current_size = 4242

    body = client.get("/v1/status", headers=AUTH).json()

    assert body["current_file"] == "book.m4b"
    assert body["current_size"] == 4242


def test_the_409_body_reports_the_current_job_size(clean_state):
    jetson_server._job_status.active = True
    jetson_server._job_status.current_file = "book.m4b"
    jetson_server._job_status.current_size = 4242

    conflict = jetson_server._active_job_conflict("other.m4b")

    assert conflict.status_code == 409
    assert json.loads(conflict.body)["current_job"]["size"] == 4242


def test_the_transcribe_endpoint_records_the_uploaded_size(
    clean_state, fake_audio_pipeline, monkeypatch, tmp_path, client
):
    """The 409 a *second* client gets has to carry a size, or it can't tell
    'that's my job' from 'that's a different book with my basename'."""
    _run_job(monkeypatch, tmp_path)
    body = b"uploaded-audio-bytes"
    seen = {}

    real = jetson_server._transcribe_file

    def _capture(audio_path, original_filename, *args, **kwargs):
        seen["size"] = jetson_server._job_status.current_size
        return real(audio_path, original_filename, *args, **kwargs)

    monkeypatch.setattr(jetson_server, "_transcribe_file", _capture)

    resp = client.post(
        "/v1/transcribe", headers=AUTH, files={"audio_file": ("book.m4b", body, "audio/mpeg")}
    )

    assert resp.status_code == 200
    assert seen["size"] == len(body)


# ---------------------------------------------------------------------------
# The worker reports its version, so the main server can say when it is behind
# ---------------------------------------------------------------------------


def test_health_reports_the_worker_version(clean_state, client):
    """`worker_version` is the release string this worker was built from. The
    main server compares it with its own APP_VERSION and tells the operator on
    the System page when the worker needs a rebuild; a worker that does not
    report one is shown as "version unknown" there, never as current."""
    body = client.get("/v1/health", headers=AUTH).json()
    assert body["worker_version"] == jetson_server.WORKER_VERSION
    parts = jetson_server.WORKER_VERSION.split(".")
    assert len(parts) == 3 and all(p.isdigit() for p in parts), (
        "WORKER_VERSION must be the plain MAJOR.MINOR.PATCH release string"
    )


def test_the_app_advertises_the_same_version(clean_state, client):
    """OpenAPI's `version` and /v1/health's `worker_version` come from one literal."""
    assert jetson_server.app.version == jetson_server.WORKER_VERSION


# ---------------------------------------------------------------------------
# Issue #795: decode chunks on the file's timeline, not by sample count
# ---------------------------------------------------------------------------


def _af(cmd):
    return cmd[cmd.index("-af") + 1] if "-af" in cmd else None


def test_chunk_decode_follows_the_file_timestamps():
    """A merged m4b can hold frames whose timestamps overlap at the part
    joins. Decoded straight to raw PCM, every sample is kept, so a 900 s chunk
    came back as 904 s and each Whisper timestamp in it drifted late. The
    resampler's async mode makes ffmpeg follow the timestamps instead."""
    cmd = jetson_server._chunk_decode_cmd("/x/book.m4b", 900, 900, 16000, fast_seek=True)
    assert _af(cmd) == "aresample=async=1"
    assert cmd.index("-ss") < cmd.index("-i")  # input seek, as before


def test_slow_seek_fallback_also_follows_the_file_timestamps():
    cmd = jetson_server._chunk_decode_cmd("/x/book.m4b", 900, 900, 16000, fast_seek=False)
    assert _af(cmd) == "aresample=async=1"
    assert cmd.index("-i") < cmd.index("-ss")


# ---------------------------------------------------------------------------
# Word timestamps (#835): sentences carry the words they were built from
# ---------------------------------------------------------------------------


@pytest.fixture
def real_sentence_split(monkeypatch):
    """conftest stubs nltk with sent_tokenize = [text]. Swap in a splitter that,
    like punkt, preserves the text and cuts after sentence-ending punctuation."""
    import re

    monkeypatch.setattr(
        jetson_server.nltk, "sent_tokenize",
        lambda text: [p for p in re.split(r"(?<=[.!?])\s+", text) if p],
    )


def _words(*triples):
    """_words((" the", 0.0, 0.2, 0.9), ...) -> list of _FakeWord."""
    return [_FakeWord(*t) for t in triples]


def _assert_word_invariant(sentences):
    for s in sentences:
        assert s.words, f"sentence without words: {s.text!r}"
        assert s.text.split() == [w["text"] for w in s.words]
        assert s.start_ms == s.words[0]["start_ms"]
        assert s.end_ms == s.words[-1]["end_ms"]


def test_each_segment_with_words_becomes_one_sentence(real_sentence_split):
    segments = [
        _FakeSegment(" The lamp was lit.", 0.0, 2.0, _words(
            (" The", 0.0, 0.3, 0.9), (" lamp", 0.4, 0.8, 0.8),
            (" was", 0.9, 1.2, 0.7), (" lit.", 1.3, 2.0, 0.6),
        )),
        _FakeSegment(" Nobody came.", 3.0, 4.0, _words(
            (" Nobody", 3.0, 3.5, 0.9), (" came.", 3.6, 4.0, 0.9),
        )),
    ]

    out = jetson_server._group_words_into_sentences(segments)

    assert [s.text for s in out] == ["The lamp was lit.", "Nobody came."]
    assert [(s.start_ms, s.end_ms) for s in out] == [(0, 2000), (3000, 4000)]
    assert out[0].words[1] == {
        "text": "lamp", "start_ms": 400, "end_ms": 800, "probability": 0.8,
    }
    _assert_word_invariant(out)


def test_a_sentence_spanning_two_segments_is_merged(real_sentence_split):
    segments = [
        _FakeSegment(" Since I knew you, I", 0.0, 2.0, _words(
            (" Since", 0.0, 0.3, 0.9), (" I", 0.4, 0.5, 0.9), (" knew", 0.6, 0.9, 0.9),
            (" you,", 1.0, 1.3, 0.9), (" I", 1.4, 1.5, 0.9),
        )),
        _FakeSegment(" have been troubled.", 2.0, 3.0, _words(
            (" have", 2.0, 2.3, 0.9), (" been", 2.4, 2.6, 0.9), (" troubled.", 2.7, 3.0, 0.9),
        )),
    ]

    out = jetson_server._group_words_into_sentences(segments)

    assert [s.text for s in out] == ["Since I knew you, I have been troubled."]
    assert len(out[0].words) == 8
    assert (out[0].start_ms, out[0].end_ms) == (0, 3000)
    _assert_word_invariant(out)


def test_a_segment_with_two_sentences_splits_on_the_word_boundary(real_sentence_split):
    # Character proration would put the boundary near 2.4s, nowhere near the
    # real 1.5s: the second sentence is one short word after a long pause.
    segments = [
        _FakeSegment(" The door was open. No.", 0.0, 3.0, _words(
            (" The", 0.0, 0.2, 0.9), (" door", 0.3, 0.6, 0.9),
            (" was", 0.7, 0.9, 0.9), (" open.", 1.0, 1.5, 0.9),
            (" No.", 2.5, 3.0, 0.9),
        )),
    ]

    out = jetson_server._group_words_into_sentences(segments)

    assert [s.text for s in out] == ["The door was open.", "No."]
    assert [(s.start_ms, s.end_ms) for s in out] == [(0, 1500), (2500, 3000)]
    _assert_word_invariant(out)


def test_hyphenated_pieces_are_glued_into_one_word(real_sentence_split):
    segments = [
        _FakeSegment(" parchment-pale skin.", 0.0, 2.0, _words(
            (" parchment", 0.0, 0.5, 0.9), ("-pale", 0.5, 0.9, 0.4), (" skin.", 1.0, 1.5, 0.9),
        )),
    ]

    out = jetson_server._group_words_into_sentences(segments)

    assert [w["text"] for w in out[0].words] == ["parchment-pale", "skin."]
    glued = out[0].words[0]
    assert (glued["start_ms"], glued["end_ms"], glued["probability"]) == (0, 900, 0.4)
    assert out[0].text == "parchment-pale skin."
    _assert_word_invariant(out)


def test_a_hyphenated_word_split_across_a_segment_boundary_is_glued(real_sentence_split):
    segments = [
        _FakeSegment(" sallow", 0.0, 1.0, _words((" sallow", 0.0, 0.6, 0.9))),
        _FakeSegment("-faced men.", 1.0, 2.0, _words(
            ("-faced", 0.6, 1.0, 0.9), (" men.", 1.1, 1.6, 0.9),
        )),
    ]

    out = jetson_server._group_words_into_sentences(segments)

    assert [w["text"] for w in out[0].words] == ["sallow-faced", "men."]
    assert out[0].words[0]["end_ms"] == 1000


def test_a_leading_unspaced_piece_with_no_predecessor_starts_a_word(real_sentence_split):
    segments = [_FakeSegment("-ish.", 0.0, 1.0, _words(("-ish.", 0.0, 1.0, 0.9)))]

    out = jetson_server._group_words_into_sentences(segments)

    assert [w["text"] for w in out[0].words] == ["-ish."]


def test_blank_words_are_dropped(real_sentence_split):
    segments = [
        _FakeSegment(" Hi.", 0.0, 1.0, _words(
            (" ", 0.0, 0.1, 0.9), (" Hi.", 0.2, 1.0, 0.9),
        )),
    ]

    out = jetson_server._group_words_into_sentences(segments)

    assert [w["text"] for w in out[0].words] == ["Hi."]
    assert out[0].start_ms == 200


def _run_of_words(n, start=0.0, step=0.5, gap_after=None, gap=2.0):
    """n words w0..w{n-1}, `step` apart; an extra `gap` after index gap_after."""
    out, t = [], start
    for i in range(n):
        out.append(_FakeWord(f" w{i}", t, t + 0.25, 0.9))
        t += step + (gap if i == gap_after else 0.0)
    return out


def test_a_sentence_over_the_word_cap_splits_at_its_largest_gap(real_sentence_split):
    # 60 words, no punctuation until the end, biggest pause after the 20th.
    words = _run_of_words(60, gap_after=19)
    segments = [_FakeSegment(" long.", 0.0, 40.0, words)]

    out = jetson_server._group_words_into_sentences(segments)

    assert [len(s.words) for s in out] == [20, 40]
    assert all(len(s.words) <= jetson_server.MAX_SENTENCE_WORDS for s in out)
    _assert_word_invariant(out)


def test_the_word_cap_recurses_until_every_piece_fits(real_sentence_split):
    words = _run_of_words(130, gap_after=100)
    segments = [_FakeSegment(" long.", 0.0, 80.0, words)]

    out = jetson_server._group_words_into_sentences(segments)

    assert sum(len(s.words) for s in out) == 130
    assert all(len(s.words) <= jetson_server.MAX_SENTENCE_WORDS for s in out)
    assert len(out) >= 3
    _assert_word_invariant(out)


def test_word_cap_ties_go_to_the_earliest_gap(real_sentence_split):
    words = _run_of_words(60)  # every gap identical
    segments = [_FakeSegment(" long.", 0.0, 40.0, words)]

    out = jetson_server._group_words_into_sentences(segments)

    assert len(out[0].words) == 1  # earliest gap: right after the first word
    assert all(len(s.words) <= jetson_server.MAX_SENTENCE_WORDS for s in out)


def test_exactly_the_cap_is_not_split(real_sentence_split):
    words = _run_of_words(jetson_server.MAX_SENTENCE_WORDS, gap_after=10)
    segments = [_FakeSegment(" long.", 0.0, 40.0, words)]

    out = jetson_server._group_words_into_sentences(segments)

    assert len(out) == 1


def test_a_segment_without_words_falls_back_to_character_proration(real_sentence_split):
    segments = [
        _FakeSegment(" One here. Two here.", 0.0, 2.0),          # no attribute
        _FakeSegment(" Another one.", 3.0, 4.0, words=[]),        # empty list
    ]

    out = jetson_server._group_words_into_sentences(segments)

    assert [s.text for s in out] == ["One here.", "Two here.", "Another one."]
    assert all(s.words == [] for s in out)
    assert (out[-1].start_ms, out[-1].end_ms) == (3000, 4000)


def test_wordless_and_worded_segments_keep_their_order(real_sentence_split):
    segments = [
        _FakeSegment(" Before.", 0.0, 1.0),
        _FakeSegment(" Middle part.", 2.0, 3.0, _words(
            (" Middle", 2.0, 2.4, 0.9), (" part.", 2.5, 3.0, 0.9),
        )),
        _FakeSegment(" After.", 4.0, 5.0, words=[]),
    ]

    out = jetson_server._group_words_into_sentences(segments)

    assert [s.text for s in out] == ["Before.", "Middle part.", "After."]
    assert [bool(s.words) for s in out] == [False, True, False]


def test_a_sentence_defaults_to_no_words():
    assert jetson_server.TranscribedSentence("x", 0, 1).words == []


# --- the wiring: transcribe(), offsets, checkpoint, response, cache ---------


def test_transcribe_asks_whisper_for_word_timestamps(
    clean_state, fake_audio_pipeline, monkeypatch, tmp_path
):
    audio, fake = _run_job(monkeypatch, tmp_path)

    jetson_server._transcribe_file(str(audio), "book.m4b")

    assert fake.kwargs and all(kw.get("word_timestamps") is True for kw in fake.kwargs)


def test_the_chunk_offset_applies_to_words_as_well_as_sentences(
    clean_state, fake_audio_pipeline, monkeypatch, tmp_path
):
    audio, _ = _run_job(monkeypatch, tmp_path)

    result = jetson_server._transcribe_file(str(audio), "book.m4b")

    second = result["sentences"][1]  # chunk 2 starts at 900 s
    assert second["start_ms"] == 900_000
    assert [(w["start_ms"], w["end_ms"]) for w in second["words"]] == [
        (900_000, 900_400), (900_500, 901_000),
    ]
    assert second["end_ms"] == second["words"][-1]["end_ms"]


def test_the_transcribe_response_carries_words_per_sentence(
    clean_state, fake_audio_pipeline, monkeypatch, tmp_path, client
):
    _run_job(monkeypatch, tmp_path)

    r = client.post(
        "/v1/transcribe",
        files={"audio_file": ("book.m4b", b"audio-bytes", "audio/mpeg")},
        headers=AUTH,
    )

    assert r.status_code == 200, r.text
    first = r.json()["sentences"][0]
    assert first["words"] == [
        {"text": "Hello", "start_ms": 0, "end_ms": 400, "probability": 0.99},
        {"text": "there.", "start_ms": 500, "end_ms": 1000, "probability": 0.8},
    ]


def test_the_cached_result_carries_words(
    clean_state, fake_audio_pipeline, monkeypatch, tmp_path, client
):
    audio, _ = _run_job(monkeypatch, tmp_path)
    jetson_server._transcribe_file(str(audio), "book.m4b")

    r = client.get("/v1/result/book.m4b", headers=AUTH)

    assert r.status_code == 200
    assert [w["text"] for w in r.json()["sentences"][0]["words"]] == ["Hello", "there."]


def test_a_checkpoint_round_trips_words(tmp_path):
    ckpt = str(tmp_path / "job.json")
    word = {"text": "Hi.", "start_ms": 5, "end_ms": 9, "probability": 0.5}
    sentence = jetson_server.TranscribedSentence("Hi.", 5, 9, [word])

    jetson_server._save_checkpoint(ckpt, 10.0, 0, 900, [sentence])
    loaded = jetson_server._load_checkpoint(ckpt)

    assert loaded["sentences"][0]["words"] == [word]
    assert jetson_server.TranscribedSentence(**loaded["sentences"][0]) == sentence


def test_a_checkpoint_written_before_words_still_loads(tmp_path):
    ckpt = tmp_path / "old.json"
    ckpt.write_text(json.dumps({
        "version": 1, "total_duration": 10.0, "completed_through_sec": 0,
        "current_chunk_size": 900,
        "sentences": [{"text": "Old.", "start_ms": 0, "end_ms": 10}],
    }))

    loaded = jetson_server._load_checkpoint(str(ckpt))
    restored = [jetson_server.TranscribedSentence(**s) for s in loaded["sentences"]]

    assert restored == [jetson_server.TranscribedSentence("Old.", 0, 10)]
    assert restored[0].words == []


def test_a_resumed_job_keeps_the_words_banked_before_the_pause(
    clean_state, fake_audio_pipeline, monkeypatch, tmp_path, client
):
    audio, _ = _run_job(monkeypatch, tmp_path)
    size = audio.stat().st_size
    real_save = jetson_server._save_checkpoint
    pause_once = {"done": False}

    def _save_then_pause(*args, **kwargs):
        real_save(*args, **kwargs)
        if not pause_once["done"]:
            pause_once["done"] = True
            jetson_server._pause_event.set()

    monkeypatch.setattr(jetson_server, "_save_checkpoint", _save_then_pause)
    jetson_server._transcribe_file(str(audio), "book.m4b")
    monkeypatch.setattr(jetson_server, "model", _FakeWhisper())
    monkeypatch.setattr(jetson_server, "_model_state", "loaded")

    body = client.post(
        "/v1/transcribe/resume", json={"filename": "book.m4b", "size": size}, headers=AUTH
    ).json()

    assert [len(s["words"]) for s in body["sentences"]] == [2, 2]
    assert body["sentences"][0]["words"][0]["start_ms"] == 0
    assert body["sentences"][1]["words"][0]["start_ms"] == 900_000
