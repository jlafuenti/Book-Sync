"""
Tests for RemoteWhisperProvider's re-attach logic, specifically its ability to
tell "the Orin finished the job" apart from "the Orin process crashed and
restarted mid-job" — both look like active=false on /v1/status, but only the
former has a cached result waiting at /v1/result/{filename}.
"""

from unittest.mock import patch

import httpx
import pytest

from services.transcription_providers.base import ProviderUnavailableError
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
