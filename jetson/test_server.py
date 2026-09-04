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
import os

os.environ.setdefault("TRANSCRIPTION_API_KEY", "test-key")

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import server as jetson_server  # noqa: E402  (import after env var is set above)

AUTH = {"Authorization": "Bearer test-key"}


class _FakeSegment:
    def __init__(self, text, start, end):
        self.text = text
        self.start = start
        self.end = end


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
        return iter([_FakeSegment("Hello there.", 0.0, 1.0)]), _FakeInfo(self._detected_language)


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
    jetson_server._job_status.current_file = "book.m4b"
    monkeypatch.setattr(
        jetson_server, "_save_checkpoint",
        lambda *a, **k: jetson_server._pause_event.set(),
    )

    jetson_server._transcribe_file(str(audio), "book.m4b")

    assert client.get("/v1/result/book.m4b", headers=AUTH).status_code == 404


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
