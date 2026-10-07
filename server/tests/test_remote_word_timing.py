"""
The remote provider keeps the worker's per-word timing (issue #835), on both
response paths: the normal transcribe response and the cached-result pre-flight.
A worker that predates word timing sends no `words` key and must still work.
"""

from unittest.mock import patch

import httpx
import pytest

from tests.test_remote_transcription_provider import (
    _idle_worker_handler,
    _json_response,
    _mock_transport_provider,
)

WORDS = [
    {"text": "One", "start_ms": 0, "end_ms": 400, "probability": 0.98, "extra": "x"},
    {"text": "two.", "start_ms": 450, "end_ms": 900, "probability": 0.5},
]
KEPT = [
    {"text": "One", "start_ms": 0, "end_ms": 400, "probability": 0.98},
    {"text": "two.", "start_ms": 450, "end_ms": 900, "probability": 0.5},
]


async def _transcribe(tmp_path, handler):
    audio_file = tmp_path / "book.m4b"
    audio_file.write_bytes(b"fake audio bytes")
    provider, fake_async_client = _mock_transport_provider("http://fake-orin:9000", "k", handler)
    with patch("services.transcription_providers.remote.httpx.AsyncClient", side_effect=fake_async_client):
        return await provider.transcribe(str(audio_file))


@pytest.mark.asyncio
async def test_normal_response_keeps_words(tmp_path):
    def on_transcribe(request):
        return _json_response(200, {"sentences": [
            {"text": "One two.", "start_ms": 0, "end_ms": 900, "words": WORDS},
        ]})

    sentences = await _transcribe(tmp_path, _idle_worker_handler(on_transcribe))
    assert sentences[0].words == KEPT


@pytest.mark.asyncio
async def test_normal_response_without_words_gives_empty_list(tmp_path):
    def on_transcribe(request):
        return _json_response(200, {"sentences": [
            {"text": "One two.", "start_ms": 0, "end_ms": 900},
        ]})

    sentences = await _transcribe(tmp_path, _idle_worker_handler(on_transcribe))
    assert sentences[0].words == []
    assert sentences[0].text == "One two."


@pytest.mark.asyncio
async def test_cached_result_path_keeps_words(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/result/book.m4b":
            return _json_response(200, {"sentences": [
                {"text": "One two.", "start_ms": 0, "end_ms": 900, "words": WORDS},
                {"text": "Three.", "start_ms": 1000, "end_ms": 1500},
            ]})
        raise AssertionError(f"Unexpected request: {request.url}")

    sentences = await _transcribe(tmp_path, handler)
    assert sentences[0].words == KEPT
    assert sentences[1].words == []


@pytest.mark.asyncio
async def test_a_word_missing_its_timing_is_dropped(tmp_path):
    def on_transcribe(request):
        return _json_response(200, {"sentences": [
            {"text": "One two.", "start_ms": 0, "end_ms": 900,
             "words": [WORDS[0], {"text": "two."}]},
        ]})

    sentences = await _transcribe(tmp_path, _idle_worker_handler(on_transcribe))
    assert sentences[0].words == [KEPT[0]]
