"""
Tests for the shared-secret auth guard added in jetson/server.py (#38).

Not wired into CI: the jetson service has its own hardware-specific
dependency stack (faster-whisper, nltk sentence-tokenizer data, ffmpeg) that
isn't installed in the main server's pytest environment. Run these on/near
the Jetson (or anywhere with jetson/requirements.txt installed):

    cd jetson && pip install -r requirements.txt && python -m pytest test_server.py -v

Only exercises verify_api_key() directly rather than going through
TestClient — the app's startup event loads the real faster-whisper model,
which needs a GPU and is out of scope for a unit test of the auth check.
"""

import importlib
import os

os.environ.setdefault("TRANSCRIPTION_API_KEY", "test-key")

import pytest
from fastapi import HTTPException

import server as jetson_server  # noqa: E402  (import after env var is set above)


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
