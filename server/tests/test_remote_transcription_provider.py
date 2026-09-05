"""
Tests for RemoteWhisperProvider's re-attach logic, specifically its ability to
tell "the Orin finished the job" apart from "the Orin process crashed and
restarted mid-job" — both look like active=false on /v1/status, but only the
former has a cached result waiting at /v1/result/{filename}.
"""

from unittest.mock import patch

import httpx
import pytest

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


def _mock_transport_provider(remote_url: str, api_key: str, handler, language: str = ""):
    """Build a RemoteWhisperProvider whose httpx.AsyncClient is patched to `handler`."""
    transport = httpx.MockTransport(handler)
    real_async_client = httpx.AsyncClient

    def fake_async_client(*args, **kwargs):
        kwargs.pop("timeout", None)
        return real_async_client(*args, transport=transport, **kwargs)

    provider = RemoteWhisperProvider(
        remote_url=remote_url, api_key=api_key, language=language
    )
    return provider, fake_async_client


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
# Language pinning (#246) and the worker's disk/size refusals (#238)
# ---------------------------------------------------------------------------

def _idle_worker_handler(on_transcribe, checkpoint=None):
    """Handler where nothing is cached and nothing is running, so `transcribe`
    goes straight to the upload — `on_transcribe` answers that POST."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/v1/result/"):
            return _json_response(404, {"detail": "Result not found or expired"})
        if request.url.path == "/v1/checkpoint":
            return _json_response(200, checkpoint or {"exists": False})
        if request.url.path in ("/v1/transcribe", "/v1/transcribe/resume"):
            return on_transcribe(request)
        raise AssertionError(f"Unexpected request: {request.url}")

    return handler


@pytest.mark.asyncio
async def test_transcribe_sends_the_configured_language(tmp_path):
    """The pin is useless if it stops at the server — it has to reach the
    worker, as a form field alongside the upload."""
    audio_file = tmp_path / "book.m4b"
    audio_file.write_bytes(b"fake audio bytes")
    bodies = []

    def on_transcribe(request: httpx.Request) -> httpx.Response:
        bodies.append(request.content)
        return _json_response(200, {"sentences": [], "language": "de"})

    provider, fake_async_client = _mock_transport_provider(
        "http://fake-orin:9000", "k", _idle_worker_handler(on_transcribe), language="de"
    )

    with patch("services.transcription_providers.remote.httpx.AsyncClient", side_effect=fake_async_client):
        await provider.transcribe(str(audio_file))

    assert len(bodies) == 1
    body = bodies[0]
    assert b'name="language"' in body, "no language part in the multipart upload"
    assert b"de" in body.split(b'name="language"', 1)[1][:64]


@pytest.mark.asyncio
async def test_transcribe_sends_no_language_field_when_auto(tmp_path):
    """Empty means auto — the worker must be free to detect, not handed ''."""
    audio_file = tmp_path / "book.m4b"
    audio_file.write_bytes(b"fake audio bytes")
    bodies = []

    def on_transcribe(request: httpx.Request) -> httpx.Response:
        bodies.append(request.content)
        return _json_response(200, {"sentences": []})

    provider, fake_async_client = _mock_transport_provider(
        "http://fake-orin:9000", "k", _idle_worker_handler(on_transcribe)
    )

    with patch("services.transcription_providers.remote.httpx.AsyncClient", side_effect=fake_async_client):
        await provider.transcribe(str(audio_file))

    assert b'name="language"' not in bodies[0]


@pytest.mark.asyncio
async def test_resume_sends_the_configured_language(tmp_path):
    """A resume is a small JSON call, not an upload — it needs the pin too."""
    import json

    audio_file = tmp_path / "book.m4b"
    audio_file.write_bytes(b"fake audio bytes")
    bodies = []

    def on_transcribe(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/transcribe/resume"
        bodies.append(json.loads(request.content))
        return _json_response(200, {"sentences": []})

    handler = _idle_worker_handler(
        on_transcribe,
        checkpoint={"exists": True, "completed_through_sec": 900, "audio_retained": True},
    )
    provider, fake_async_client = _mock_transport_provider(
        "http://fake-orin:9000", "k", handler, language="fr"
    )

    with patch("services.transcription_providers.remote.httpx.AsyncClient", side_effect=fake_async_client):
        await provider.transcribe(str(audio_file))

    assert bodies == [{"filename": "book.m4b", "size": 16, "language": "fr"}]


@pytest.mark.asyncio
async def test_507_from_the_worker_raises_with_an_out_of_disk_message(tmp_path):
    """507 is the worker refusing an upload it has no room for (#238). It is
    retriable — the disk may be freed — but the message has to say *disk*, or
    the operator sees five identical multi-GB retries and no explanation."""
    audio_file = tmp_path / "book.m4b"
    audio_file.write_bytes(b"fake audio bytes")
    posts = []

    def on_transcribe(request: httpx.Request) -> httpx.Response:
        posts.append(request.url.path)
        return _json_response(507, {"detail": "Worker is out of disk: 8 bytes free"})

    provider, fake_async_client = _mock_transport_provider(
        "http://fake-orin:9000", "k", _idle_worker_handler(on_transcribe)
    )

    with patch("services.transcription_providers.remote.httpx.AsyncClient", side_effect=fake_async_client):
        with pytest.raises(ProviderUnavailableError, match="(?i)disk"):
            await provider.transcribe(str(audio_file))

    assert len(posts) == 1, "507 must not be retried inside the provider's own loop"


@pytest.mark.asyncio
async def test_413_from_the_worker_is_not_retried(tmp_path):
    """The file will be exactly as large on the seventh attempt. Fail fast with
    a message naming the worker's cap instead of re-uploading it five times."""
    from services.transcription_providers.base import TranscriptionError

    audio_file = tmp_path / "book.m4b"
    audio_file.write_bytes(b"fake audio bytes")
    posts = []

    def on_transcribe(request: httpx.Request) -> httpx.Response:
        posts.append(request.url.path)
        return _json_response(413, {"detail": "Upload is larger than this worker accepts"})

    provider, fake_async_client = _mock_transport_provider(
        "http://fake-orin:9000", "k", _idle_worker_handler(on_transcribe)
    )

    with patch("services.transcription_providers.remote.httpx.AsyncClient", side_effect=fake_async_client):
        with pytest.raises(TranscriptionError, match="MAX_UPLOAD_BYTES"):
            await provider.transcribe(str(audio_file))

    assert len(posts) == 1
