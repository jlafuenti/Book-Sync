"""Tests for the SSRF guard integration in abs_metadata.fetch_abs_index
(issue #51). fetch_abs_index degrades gracefully (returns {}) on any error,
including an unsafe URL, so these assert on that behavior plus whether the
underlying httpx.get was actually reached.
"""

import httpx
import mutagen.mp4
import pytest

from services import abs_metadata, chapter_repair


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

    result = abs_metadata.fetch_abs_index("http://192.0.2.60:13378", "token", "")

    assert result == {}  # no items in the fake response, but no exception either


class TestWriteMetadataToFile:
    """Regression coverage for the enrich-abs UI-reports-success-on-failure bug:
    a tag-write exception (e.g. a chapter title with invalid UTF-8 bytes) must
    be reported back to the caller, not just logged as a WARNING."""

    def test_returns_error_message_on_write_failure(self, monkeypatch, caplog, tmp_path):
        fake_file = tmp_path / "book.m4b"
        fake_file.write_bytes(b"")

        class FakeMP4(dict):
            def save(self):
                raise UnicodeDecodeError(
                    "utf-8", b"\xc4", 0, 1, "invalid continuation byte"
                )

        import mutagen.mp4
        monkeypatch.setattr(mutagen.mp4, "MP4", lambda path: FakeMP4())

        with caplog.at_level("WARNING"):
            ok, error = abs_metadata.write_metadata_to_file(
                str(fake_file), {"title": "Antiagon Fire"}
            )

        assert ok is False
        assert error is not None
        assert "invalid continuation byte" in error
        assert any("Failed to write tags" in r.message for r in caplog.records)

    def test_returns_success_with_no_error(self, monkeypatch, tmp_path):
        fake_file = tmp_path / "book.m4b"
        fake_file.write_bytes(b"")

        class FakeMP4(dict):
            def save(self):
                pass

        import mutagen.mp4
        monkeypatch.setattr(mutagen.mp4, "MP4", lambda path: FakeMP4())

        ok, error = abs_metadata.write_metadata_to_file(
            str(fake_file), {"title": "Antiagon Fire"}
        )

        assert ok is True
        assert error is None

    def test_unsupported_extension_returns_false_with_no_error(self, tmp_path):
        fake_file = tmp_path / "book.mp3"
        fake_file.write_bytes(b"")

        ok, error = abs_metadata.write_metadata_to_file(
            str(fake_file), {"title": "Antiagon Fire"}
        )

        assert ok is False
        assert error is None

    def test_chapter_title_decode_failure_triggers_repair_and_retries(
        self, monkeypatch, tmp_path
    ):
        """Regression: a chapter-title UnicodeDecodeError (mutagen's
        MP4Chapters._parse_chpl raises this while just constructing MP4())
        must trigger a repair attempt via services.chapter_repair, then retry
        the write once, rather than immediately failing."""
        fake_file = tmp_path / "book.m4b"
        fake_file.write_bytes(b"")

        attempts = []

        class FakeMP4(dict):
            def save(self):
                pass

        def fake_mp4_ctor(path):
            attempts.append(path)
            if len(attempts) == 1:
                raise mutagen.mp4.MP4MetadataError(
                    "chapter 0 title: 'utf-8' codec can't decode byte 0xc4 "
                    "in position 27: invalid continuation byte"
                )
            return FakeMP4()

        monkeypatch.setattr(mutagen.mp4, "MP4", fake_mp4_ctor)
        monkeypatch.setattr(
            chapter_repair, "repair_chapter_encoding", lambda path: (True, None)
        )

        ok, error = abs_metadata.write_metadata_to_file(
            str(fake_file), {"title": "Antiagon Fire"}
        )

        assert ok is True
        assert error is None
        assert len(attempts) == 2  # first attempt failed, retry after repair succeeded

    def test_chapter_title_decode_failure_with_failed_repair_returns_original_error(
        self, monkeypatch, tmp_path
    ):
        fake_file = tmp_path / "book.m4b"
        fake_file.write_bytes(b"")

        def fake_mp4_ctor(path):
            raise mutagen.mp4.MP4MetadataError(
                "chapter 0 title: 'utf-8' codec can't decode byte 0xc4 "
                "in position 27: invalid continuation byte"
            )

        monkeypatch.setattr(mutagen.mp4, "MP4", fake_mp4_ctor)
        monkeypatch.setattr(
            chapter_repair, "repair_chapter_encoding",
            lambda path: (False, "ffmpeg not found"),
        )

        ok, error = abs_metadata.write_metadata_to_file(
            str(fake_file), {"title": "Antiagon Fire"}
        )

        assert ok is False
        assert "chapter 0 title" in error
