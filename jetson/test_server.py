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


class _FakeWhisper:
    """Stands in for faster_whisper.WhisperModel."""

    def __init__(self):
        self.model = _FakeCT2()
        self.calls = 0

    def transcribe(self, audio_array, **kwargs):
        self.calls += 1
        return iter([_FakeSegment("Hello there.", 0.0, 1.0)]), object()


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

def _run_job(monkeypatch, tmp_path, filename="book.m4b", body=b"audio-bytes"):
    audio = tmp_path / filename
    audio.write_bytes(body)
    fake = _FakeWhisper()
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
