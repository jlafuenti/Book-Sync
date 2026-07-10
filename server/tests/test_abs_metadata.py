"""Tests for the SSRF guard integration in abs_metadata.fetch_abs_index
(issue #51). fetch_abs_index degrades gracefully (returns {}) on any error,
including an unsafe URL, so these assert on that behavior plus whether the
underlying httpx.get was actually reached.
"""

import httpx
import pytest

from services import abs_metadata


def test_fetch_abs_index_rejects_invalid_scheme(monkeypatch, caplog):
    def fake_get(*args, **kwargs):
        raise AssertionError("httpx.get should not be reached for an unsafe URL")
    monkeypatch.setattr(httpx, "get", fake_get)

    with caplog.at_level("WARNING"):
        result = abs_metadata.fetch_abs_index("file:///etc/passwd", "token", "")

    assert result == {}
    assert any("Failed to fetch ABS index" in r.message for r in caplog.records)


def test_fetch_abs_index_allows_private_url(monkeypatch):
    """Regression guard: the LAN-hosted ABS use case must still work now
    that a guard sits in front of it."""
    def fake_get(url, headers=None, params=None, timeout=None):
        if url.endswith("/api/libraries"):
            return httpx.Response(200, json={"libraries": [{"id": "1", "name": "Audiobooks", "mediaType": "book"}]})
        return httpx.Response(200, json={"results": []})
    monkeypatch.setattr(httpx, "get", fake_get)

    result = abs_metadata.fetch_abs_index("http://192.168.1.60:13378", "token", "")

    assert result == {}  # no items in the fake response, but no exception either
