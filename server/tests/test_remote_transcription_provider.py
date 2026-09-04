"""
Tests for RemoteWhisperProvider's re-attach logic, specifically its ability to
tell "the Orin finished the job" apart from "the Orin process crashed and
restarted mid-job" — both look like active=false on /v1/status, but only the
former has a cached result waiting at /v1/result/{filename}.
"""

import asyncio
from unittest.mock import patch

import httpx
import pytest

from services.transcription_providers import remote as remote_module
from services.transcription_providers.base import (
    ProviderUnavailableError,
    TranscriptionPaused,
)
from services.transcription_providers.remote import RemoteWhisperProvider


def _json_response(status_code: int, payload: dict) -> httpx.Response:
    return httpx.Response(status_code, json=payload)


@pytest.mark.asyncio
async def test_reattach_detects_restart_and_raises_retriable_error(tmp_path):
    """
    Simulates: our upload gets a 409 (Orin already transcribing our file, from
    instance "orin-a"), we poll and see it still active on "orin-a", then the
    Orin crashes and restarts — /v1/status now reports a different
    instance_id. The provider must raise ProviderUnavailableError instead of
    fetching /v1/result (which would be a guaranteed 404 for the same reason
    the real bug produced one).
    """
    audio_file = tmp_path / "book.m4b"
    audio_file.write_bytes(b"fake audio bytes")

    poll_responses = [
        {"active": True, "progress": 0.4, "message": "Transcribing", "instance_id": "orin-a"},
        {"active": True, "progress": 0.5, "message": "Transcribing", "instance_id": "orin-a"},
        {"active": False, "progress": 0.0, "message": "Idle", "instance_id": "orin-b"},
    ]

    result_endpoint_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal result_endpoint_calls

        if request.url.path == "/v1/result/book.m4b":
            # The provider does one legitimate pre-flight check for a cached
            # result before ever uploading. Anything beyond that is the bug
            # we're guarding against: fetching /v1/result after the Orin has
            # visibly restarted, which is a guaranteed 404.
            result_endpoint_calls += 1
            return _json_response(404, {"detail": "Result not found or expired"})

        if request.url.path == "/v1/checkpoint":
            return _json_response(200, {"exists": False})

        if request.url.path == "/v1/transcribe":
            return _json_response(
                409,
                {
                    "detail": "A transcription is already in progress.",
                    "instance_id": "orin-a",
                    "current_job": {"file": "book.m4b", "progress": 0.4, "message": "Transcribing"},
                },
            )

        if request.url.path == "/v1/status":
            return _json_response(200, poll_responses.pop(0))

        raise AssertionError(f"Unexpected request: {request.url}")

    transport = httpx.MockTransport(handler)
    real_async_client = httpx.AsyncClient

    def fake_async_client(*args, **kwargs):
        kwargs.pop("timeout", None)
        return real_async_client(*args, transport=transport, **kwargs)

    provider = RemoteWhisperProvider(remote_url="http://fake-orin:9000")

    with patch("services.transcription_providers.remote.httpx.AsyncClient", side_effect=fake_async_client), \
         patch("services.transcription_providers.remote.asyncio.sleep", return_value=None):
        with pytest.raises(ProviderUnavailableError, match="restarted"):
            await provider.transcribe(str(audio_file))

    assert result_endpoint_calls == 1, (
        "Only the pre-flight cached-result check should hit /v1/result — the "
        "provider must not fetch it again after detecting the Orin restarted, "
        "since that result is guaranteed gone."
    )


def _mock_transport_provider(remote_url: str, api_key: str, handler):
    """Build a RemoteWhisperProvider whose httpx.AsyncClient is patched to `handler`."""
    transport = httpx.MockTransport(handler)
    real_async_client = httpx.AsyncClient

    def fake_async_client(*args, **kwargs):
        kwargs.pop("timeout", None)
        return real_async_client(*args, transport=transport, **kwargs)

    return RemoteWhisperProvider(remote_url=remote_url, api_key=api_key), fake_async_client


@pytest.mark.asyncio
async def test_sends_bearer_token_on_every_request_when_api_key_configured(tmp_path):
    """The Jetson server rejects unauthenticated requests (see #38) — every
    call the provider makes must carry the configured shared secret."""
    audio_file = tmp_path / "book.m4b"
    audio_file.write_bytes(b"fake audio bytes")

    seen_auth_headers = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_auth_headers.append(request.headers.get("authorization"))
        if request.url.path == "/v1/result/book.m4b":
            return _json_response(404, {"detail": "Result not found or expired"})
        if request.url.path == "/v1/checkpoint":
            return _json_response(200, {"exists": False})
        if request.url.path == "/v1/transcribe":
            return _json_response(
                200, {"sentences": [], "duration_seconds": 1.0, "processing_time_seconds": 0.1}
            )
        raise AssertionError(f"Unexpected request: {request.url}")

    provider, fake_async_client = _mock_transport_provider(
        "http://fake-orin:9000", "secret-key", handler
    )

    with patch("services.transcription_providers.remote.httpx.AsyncClient", side_effect=fake_async_client):
        await provider.transcribe(str(audio_file))

    assert seen_auth_headers, "expected at least one request to be made"
    assert all(h == "Bearer secret-key" for h in seen_auth_headers)


@pytest.mark.asyncio
async def test_omits_authorization_header_when_no_api_key_configured(tmp_path):
    audio_file = tmp_path / "book.m4b"
    audio_file.write_bytes(b"fake audio bytes")

    seen_auth_headers = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_auth_headers.append(request.headers.get("authorization"))
        if request.url.path == "/v1/result/book.m4b":
            return _json_response(404, {"detail": "Result not found or expired"})
        if request.url.path == "/v1/checkpoint":
            return _json_response(200, {"exists": False})
        if request.url.path == "/v1/transcribe":
            return _json_response(
                200, {"sentences": [], "duration_seconds": 1.0, "processing_time_seconds": 0.1}
            )
        raise AssertionError(f"Unexpected request: {request.url}")

    provider, fake_async_client = _mock_transport_provider("http://fake-orin:9000", "", handler)

    with patch("services.transcription_providers.remote.httpx.AsyncClient", side_effect=fake_async_client):
        await provider.transcribe(str(audio_file))

    assert seen_auth_headers, "expected at least one request to be made"
    assert all(h is None for h in seen_auth_headers)


# ---------------------------------------------------------------------------
# Off-hours pause / resume / unload (issue #106)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_is_available_ignores_whether_the_model_is_loaded():
    """The worker lazy-loads and idle-unloads its model (#106). Treating
    `model_loaded: false` as unhealthy would divert every job to the local
    fallback — which isn't even installed in the production image."""
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/health"
        return _json_response(200, {"status": "healthy", "model_loaded": False,
                                    "model_state": "unloaded"})

    provider, fake_async_client = _mock_transport_provider("http://fake-orin:9000", "k", handler)
    with patch("services.transcription_providers.remote.httpx.AsyncClient", side_effect=fake_async_client):
        assert await provider.is_available() is True


@pytest.mark.asyncio
async def test_is_available_false_when_worker_reports_unhealthy():
    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(200, {"status": "degraded"})

    provider, fake_async_client = _mock_transport_provider("http://fake-orin:9000", "k", handler)
    with patch("services.transcription_providers.remote.httpx.AsyncClient", side_effect=fake_async_client):
        assert await provider.is_available() is False


@pytest.mark.asyncio
async def test_paused_response_raises_transcription_paused(tmp_path):
    """A paused job is not a failure: the queue re-pends it with progress intact."""
    audio_file = tmp_path / "book.m4b"
    audio_file.write_bytes(b"fake audio bytes")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/result/book.m4b":
            return _json_response(404, {"detail": "Result not found or expired"})
        if request.url.path == "/v1/checkpoint":
            return _json_response(200, {"exists": False})
        if request.url.path == "/v1/transcribe":
            return _json_response(200, {
                "status": "paused",
                "completed_through_sec": 1800,
                "progress": 0.25,
            })
        raise AssertionError(f"Unexpected request: {request.url}")

    provider, fake_async_client = _mock_transport_provider("http://fake-orin:9000", "k", handler)
    with patch("services.transcription_providers.remote.httpx.AsyncClient", side_effect=fake_async_client):
        with pytest.raises(TranscriptionPaused) as exc:
            await provider.transcribe(str(audio_file))

    assert exc.value.completed_through_sec == 1800
    assert exc.value.progress == 0.25


@pytest.mark.asyncio
async def test_resumes_from_retained_audio_without_re_uploading(tmp_path):
    """When the worker still holds the audio for a paused job, resuming must
    not push the whole file over the wire again."""
    audio_file = tmp_path / "book.m4b"
    audio_file.write_bytes(b"fake audio bytes")

    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/v1/result/book.m4b":
            return _json_response(404, {"detail": "Result not found or expired"})
        if request.url.path == "/v1/checkpoint":
            assert request.url.params["filename"] == "book.m4b"
            assert request.url.params["size"] == str(audio_file.stat().st_size)
            return _json_response(200, {
                "exists": True, "completed_through_sec": 1800,
                "progress": 0.25, "audio_retained": True,
            })
        if request.url.path == "/v1/transcribe/resume":
            return _json_response(200, {
                "sentences": [{"text": "Hello.", "start_ms": 0, "end_ms": 900}],
                "duration_seconds": 7200.0, "processing_time_seconds": 12.0,
            })
        raise AssertionError(f"Unexpected request: {request.url}")

    provider, fake_async_client = _mock_transport_provider("http://fake-orin:9000", "k", handler)
    with patch("services.transcription_providers.remote.httpx.AsyncClient", side_effect=fake_async_client):
        sentences = await provider.transcribe(str(audio_file))

    assert [s.text for s in sentences] == ["Hello."]
    assert "/v1/transcribe/resume" in calls
    assert "/v1/transcribe" not in calls, "resume must not re-upload the audio"


@pytest.mark.asyncio
async def test_falls_back_to_upload_when_worker_dropped_the_audio(tmp_path):
    """Checkpoint survives but the retained audio doesn't (e.g. /tmp was
    cleared) — upload again; the worker still resumes from its checkpoint."""
    audio_file = tmp_path / "book.m4b"
    audio_file.write_bytes(b"fake audio bytes")

    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/v1/result/book.m4b":
            return _json_response(404, {"detail": "Result not found or expired"})
        if request.url.path == "/v1/checkpoint":
            return _json_response(200, {
                "exists": True, "completed_through_sec": 1800,
                "progress": 0.25, "audio_retained": False,
            })
        if request.url.path == "/v1/transcribe":
            return _json_response(
                200, {"sentences": [], "duration_seconds": 1.0, "processing_time_seconds": 0.1}
            )
        raise AssertionError(f"Unexpected request: {request.url}")

    provider, fake_async_client = _mock_transport_provider("http://fake-orin:9000", "k", handler)
    with patch("services.transcription_providers.remote.httpx.AsyncClient", side_effect=fake_async_client):
        await provider.transcribe(str(audio_file))

    assert "/v1/transcribe" in calls
    assert "/v1/transcribe/resume" not in calls


@pytest.mark.asyncio
async def test_checkpoint_preflight_failure_does_not_block_transcription(tmp_path):
    """The checkpoint probe is an optimisation. An older worker without the
    endpoint (404), or a flaky one, must not stop the job."""
    audio_file = tmp_path / "book.m4b"
    audio_file.write_bytes(b"fake audio bytes")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/result/book.m4b":
            return _json_response(404, {"detail": "Result not found or expired"})
        if request.url.path == "/v1/checkpoint":
            return _json_response(404, {"detail": "Not Found"})
        if request.url.path == "/v1/transcribe":
            return _json_response(
                200, {"sentences": [], "duration_seconds": 1.0, "processing_time_seconds": 0.1}
            )
        raise AssertionError(f"Unexpected request: {request.url}")

    provider, fake_async_client = _mock_transport_provider("http://fake-orin:9000", "k", handler)
    with patch("services.transcription_providers.remote.httpx.AsyncClient", side_effect=fake_async_client):
        assert await provider.transcribe(str(audio_file)) == []


@pytest.mark.asyncio
async def test_request_pause_posts_to_the_worker():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, request.headers.get("authorization")))
        return _json_response(200, {"paused_requested": True})

    provider, fake_async_client = _mock_transport_provider("http://fake-orin:9000", "k", handler)
    with patch("services.transcription_providers.remote.httpx.AsyncClient", side_effect=fake_async_client):
        assert await provider.request_pause() is True

    assert seen == [("POST", "/v1/pause", "Bearer k")]


@pytest.mark.asyncio
async def test_request_pause_returns_false_when_worker_unreachable():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    provider, fake_async_client = _mock_transport_provider("http://fake-orin:9000", "k", handler)
    with patch("services.transcription_providers.remote.httpx.AsyncClient", side_effect=fake_async_client):
        assert await provider.request_pause() is False


@pytest.mark.asyncio
async def test_release_resources_asks_the_worker_to_unload():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        return _json_response(200, {"model_state": "unloaded"})

    provider, fake_async_client = _mock_transport_provider("http://fake-orin:9000", "k", handler)
    with patch("services.transcription_providers.remote.httpx.AsyncClient", side_effect=fake_async_client):
        await provider.release_resources()

    assert seen == [("POST", "/v1/unload")]


@pytest.mark.asyncio
async def test_release_resources_swallows_transport_errors():
    """Best-effort housekeeping — an unreachable worker must not raise into
    the queue's watchdog tick."""
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    provider, fake_async_client = _mock_transport_provider("http://fake-orin:9000", "k", handler)
    with patch("services.transcription_providers.remote.httpx.AsyncClient", side_effect=fake_async_client):
        await provider.release_resources()  # should not raise


@pytest.mark.asyncio
async def test_discard_checkpoint_deletes_remote_state(tmp_path):
    audio_file = tmp_path / "book.m4b"
    audio_file.write_bytes(b"fake audio bytes")
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, dict(request.url.params)))
        return _json_response(200, {"deleted": True})

    provider, fake_async_client = _mock_transport_provider("http://fake-orin:9000", "k", handler)
    with patch("services.transcription_providers.remote.httpx.AsyncClient", side_effect=fake_async_client):
        await provider.discard_checkpoint(str(audio_file))

    assert seen == [(
        "DELETE", "/v1/checkpoint",
        {"filename": "book.m4b", "size": str(audio_file.stat().st_size)},
    )]


# ---------------------------------------------------------------------------
# Progress polling (issue #244)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_poll_progress_skips_callback_when_status_unchanged():
    """Polling stays at 2 Hz, but an identical status must not be re-reported
    — downstream every callback is a DB write."""
    import asyncio

    payloads = [
        {"active": True, "progress": 0.10, "message": "Transcribing: 1:00 / 20:00 (5%)"},
        {"active": True, "progress": 0.10, "message": "Transcribing: 1:00 / 20:00 (5%)"},
        {"active": True, "progress": 0.10, "message": "Transcribing: 1:00 / 20:00 (5%)"},
        {"active": True, "progress": 0.11, "message": "Transcribing: 1:07 / 20:00 (5%)"},
    ]
    stop_event = asyncio.Event()
    reported = []

    # Serve the payloads in order, then stop the loop.
    served = []

    def ordered_handler(request: httpx.Request) -> httpx.Response:
        if not payloads:
            stop_event.set()
            return _json_response(200, {"active": False})
        payload = payloads.pop(0)
        served.append(payload)
        if not payloads:
            stop_event.set()
        return _json_response(200, payload)

    provider, fake_async_client = _mock_transport_provider(
        "http://fake-orin:9000", "k", ordered_handler
    )

    with patch("services.transcription_providers.remote.httpx.AsyncClient", side_effect=fake_async_client):
        await provider._poll_progress(
            stop_event, lambda p, d, m: reported.append((p, m))
        )

    assert len(served) == 4, "all four polls should still have happened"
    assert reported == [
        (0.10, "Transcribing: 1:00 / 20:00 (5%)"),
        (0.11, "Transcribing: 1:07 / 20:00 (5%)"),
    ]


# ---------------------------------------------------------------------------
# The two poll loops must be able to give up (issue #195)
#
# Both used to be `while True:` with every transport error swallowed at DEBUG.
# A worker that died — or stayed busy forever — while the server was inside one
# of them left the item `in_progress` with no error recorded, and since the
# queue is strictly serial, nothing else was ever dispatched.
# ---------------------------------------------------------------------------

def _conflict_response(current_file: str, **extra) -> httpx.Response:
    job = {"file": current_file, "progress": 0.4, "message": "Transcribing"}
    job.update(extra)
    return _json_response(409, {
        "detail": "A transcription is already in progress.",
        "instance_id": "worker-a",
        "current_job": job,
    })


@pytest.mark.asyncio
async def test_reattach_gives_up_when_the_worker_stops_responding(tmp_path):
    """The 409 said the worker holds our file, then it goes dark for good.

    Without a cap this polls forever and the queue never dispatches again.
    """
    audio_file = tmp_path / "book.m4b"
    audio_file.write_bytes(b"fake audio bytes")

    status_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal status_calls
        if request.url.path == "/v1/result/book.m4b":
            return _json_response(404, {"detail": "Result not found or expired"})
        if request.url.path == "/v1/checkpoint":
            return _json_response(200, {"exists": False})
        if request.url.path == "/v1/transcribe":
            return _conflict_response("book.m4b")
        if request.url.path == "/v1/status":
            status_calls += 1
            raise httpx.ConnectError("worker is gone", request=request)
        raise AssertionError(f"Unexpected request: {request.url}")

    provider, fake_async_client = _mock_transport_provider("http://fake-orin:9000", "k", handler)

    with patch("services.transcription_providers.remote.httpx.AsyncClient", side_effect=fake_async_client), \
         patch("services.transcription_providers.remote.asyncio.sleep", return_value=None):
        with pytest.raises(ProviderUnavailableError, match="unreachable"):
            await asyncio.wait_for(provider.transcribe(str(audio_file)), timeout=5)

    assert status_calls == remote_module.REATTACH_MAX_POLL_ERRORS


@pytest.mark.asyncio
async def test_reattach_survives_a_brief_poll_blip(tmp_path):
    """The cap must not be so eager that a couple of dropped polls kill the job."""
    audio_file = tmp_path / "book.m4b"
    audio_file.write_bytes(b"fake audio bytes")

    polls = [
        "boom",
        "boom",
        {"active": True, "progress": 0.6, "instance_id": "worker-a"},
        "boom",
        {"active": False, "progress": 1.0, "instance_id": "worker-a"},
    ]
    result_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal result_calls
        if request.url.path == "/v1/result/book.m4b":
            result_calls += 1
            if result_calls == 1:
                return _json_response(404, {"detail": "Result not found or expired"})
            return _json_response(200, {
                "sentences": [{"text": "Hello.", "start_ms": 0, "end_ms": 900}],
                "duration_seconds": 7200.0, "processing_time_seconds": 12.0,
            })
        if request.url.path == "/v1/checkpoint":
            return _json_response(200, {"exists": False})
        if request.url.path == "/v1/transcribe":
            return _conflict_response("book.m4b")
        if request.url.path == "/v1/status":
            nxt = polls.pop(0)
            if nxt == "boom":
                raise httpx.ConnectError("blip", request=request)
            return _json_response(200, nxt)
        raise AssertionError(f"Unexpected request: {request.url}")

    provider, fake_async_client = _mock_transport_provider("http://fake-orin:9000", "k", handler)

    with patch("services.transcription_providers.remote.httpx.AsyncClient", side_effect=fake_async_client), \
         patch("services.transcription_providers.remote.asyncio.sleep", return_value=None):
        sentences = await asyncio.wait_for(provider.transcribe(str(audio_file)), timeout=5)

    assert [s.text for s in sentences] == ["Hello."]


@pytest.mark.asyncio
async def test_reattach_gives_up_at_the_deadline_even_while_polls_succeed(tmp_path):
    """A worker that answers happily but never finishes still has to time out."""
    audio_file = tmp_path / "book.m4b"
    audio_file.write_bytes(b"fake audio bytes")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/result/book.m4b":
            return _json_response(404, {"detail": "Result not found or expired"})
        if request.url.path == "/v1/checkpoint":
            return _json_response(200, {"exists": False})
        if request.url.path == "/v1/transcribe":
            return _conflict_response("book.m4b")
        if request.url.path == "/v1/status":
            return _json_response(200, {"active": True, "progress": 0.4, "instance_id": "worker-a"})
        raise AssertionError(f"Unexpected request: {request.url}")

    provider, fake_async_client = _mock_transport_provider("http://fake-orin:9000", "k", handler)
    clock = iter([0.0] + [float(n) * 10_000 for n in range(1, 50)])

    with patch("services.transcription_providers.remote.httpx.AsyncClient", side_effect=fake_async_client), \
         patch("services.transcription_providers.remote.asyncio.sleep", return_value=None), \
         patch("services.transcription_providers.remote.time.monotonic", side_effect=lambda: next(clock)):
        with pytest.raises(ProviderUnavailableError, match="re-attach"):
            await asyncio.wait_for(provider.transcribe(str(audio_file)), timeout=5)


@pytest.mark.asyncio
async def test_wait_for_server_idle_gives_up_after_the_deadline(tmp_path):
    """A 409 naming *another* file, and that file never finishes."""
    audio_file = tmp_path / "book.m4b"
    audio_file.write_bytes(b"fake audio bytes")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/result/book.m4b":
            return _json_response(404, {"detail": "Result not found or expired"})
        if request.url.path == "/v1/checkpoint":
            return _json_response(200, {"exists": False})
        if request.url.path == "/v1/transcribe":
            return _conflict_response("other.m4b")
        if request.url.path == "/v1/status":
            return _json_response(200, {"active": True, "progress": 0.4, "instance_id": "worker-a"})
        raise AssertionError(f"Unexpected request: {request.url}")

    provider, fake_async_client = _mock_transport_provider("http://fake-orin:9000", "k", handler)
    clock = iter([0.0] + [float(n) * 10_000 for n in range(1, 50)])

    with patch("services.transcription_providers.remote.httpx.AsyncClient", side_effect=fake_async_client), \
         patch("services.transcription_providers.remote.asyncio.sleep", return_value=None), \
         patch("services.transcription_providers.remote.time.monotonic", side_effect=lambda: next(clock)):
        with pytest.raises(ProviderUnavailableError, match="waiting for the worker"):
            await asyncio.wait_for(provider.transcribe(str(audio_file)), timeout=5)


@pytest.mark.asyncio
async def test_wait_for_server_idle_gives_up_when_the_worker_stops_responding(tmp_path):
    audio_file = tmp_path / "book.m4b"
    audio_file.write_bytes(b"fake audio bytes")

    status_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal status_calls
        if request.url.path == "/v1/result/book.m4b":
            return _json_response(404, {"detail": "Result not found or expired"})
        if request.url.path == "/v1/checkpoint":
            return _json_response(200, {"exists": False})
        if request.url.path == "/v1/transcribe":
            return _conflict_response("other.m4b")
        if request.url.path == "/v1/status":
            status_calls += 1
            raise httpx.ConnectError("worker is gone", request=request)
        raise AssertionError(f"Unexpected request: {request.url}")

    provider, fake_async_client = _mock_transport_provider("http://fake-orin:9000", "k", handler)

    with patch("services.transcription_providers.remote.httpx.AsyncClient", side_effect=fake_async_client), \
         patch("services.transcription_providers.remote.asyncio.sleep", return_value=None):
        with pytest.raises(ProviderUnavailableError, match="unreachable"):
            await asyncio.wait_for(provider.transcribe(str(audio_file)), timeout=5)

    assert status_calls == remote_module.WAIT_FOR_IDLE_MAX_POLL_ERRORS


@pytest.mark.asyncio
async def test_wait_for_server_idle_recovers_from_a_brief_blip(tmp_path):
    """Two dropped polls then the worker frees up — the upload must proceed."""
    audio_file = tmp_path / "book.m4b"
    audio_file.write_bytes(b"fake audio bytes")

    polls = ["boom", "boom", {"active": False, "progress": 0.0, "instance_id": "worker-a"}]
    transcribe_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal transcribe_calls
        if request.url.path == "/v1/result/book.m4b":
            return _json_response(404, {"detail": "Result not found or expired"})
        if request.url.path == "/v1/checkpoint":
            return _json_response(200, {"exists": False})
        if request.url.path == "/v1/transcribe":
            transcribe_calls += 1
            if transcribe_calls == 1:
                return _conflict_response("other.m4b")
            return _json_response(200, {
                "sentences": [{"text": "Hello.", "start_ms": 0, "end_ms": 900}],
                "duration_seconds": 7200.0, "processing_time_seconds": 12.0,
            })
        if request.url.path == "/v1/status":
            nxt = polls.pop(0)
            if nxt == "boom":
                raise httpx.ConnectError("blip", request=request)
            return _json_response(200, nxt)
        raise AssertionError(f"Unexpected request: {request.url}")

    provider, fake_async_client = _mock_transport_provider("http://fake-orin:9000", "k", handler)

    with patch("services.transcription_providers.remote.httpx.AsyncClient", side_effect=fake_async_client), \
         patch("services.transcription_providers.remote.asyncio.sleep", return_value=None):
        sentences = await asyncio.wait_for(provider.transcribe(str(audio_file)), timeout=5)

    assert [s.text for s in sentences] == ["Hello."]
    assert transcribe_calls == 2
