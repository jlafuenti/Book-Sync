"""
Tests for services.chapter_repair — repairs m4b files whose embedded Nero-style
chapter titles were written in a non-UTF-8 encoding (e.g. Windows-1252),
which makes mutagen.mp4.MP4() raise on load and blocks ABS tag writes.

The repair works by re-using the same ffmpeg ffmetadata export/reinject
plumbing as routers.chapters.update_audiobook_chapters, but reading the
exported metadata as raw bytes (not a hard-coded utf-8 text open) so a
lenient decode can fix the bad title bytes before ffmpeg re-injects them.
"""

import subprocess as subprocess_module

from services import chapter_repair


class TestDecodeLenient:
    def test_valid_utf8_passes_through_unchanged(self):
        assert chapter_repair.decode_lenient("Ä".encode("utf-8")) == "Ä"

    def test_cp1252_byte_decodes_via_fallback(self):
        # 0xC4 alone is an invalid UTF-8 continuation byte, but is 'Ä' in cp1252.
        raw = b"\xc4"
        assert chapter_repair.decode_lenient(raw) == "Ä"

    def test_unmappable_bytes_fall_back_to_latin1_replace(self):
        # latin-1 maps every byte 0-255, so this never raises even though
        # it's a last-resort fallback with no real fallback below it.
        raw = b"\xc4\xff\x00"
        result = chapter_repair.decode_lenient(raw)
        assert isinstance(result, str)


class TestFixFfmetadataBytes:
    def test_fixes_bad_title_line_leaves_others_untouched(self):
        raw = (
            b";FFMETADATA1\n"
            b"major_brand=M4B\n"
            b"[CHAPTER]\n"
            b"TIMEBASE=1/1000\n"
            b"START=0\n"
            b"END=5000\n"
            b"title=Chapter \xc4ne\n"
        )
        fixed = chapter_repair.fix_ffmetadata_bytes(raw)
        assert fixed.encode("utf-8")  # must not raise
        assert "title=Chapter Äne" in fixed
        assert "major_brand=M4B" in fixed


class _FakeCompletedProcess:
    def __init__(self, returncode=0, stderr=b""):
        self.returncode = returncode
        self.stderr = stderr


class TestRepairChapterEncoding:
    def test_repairs_bad_title_and_reinjects(self, monkeypatch, tmp_path):
        filepath = str(tmp_path / "book.m4b")

        def fake_run(cmd, capture_output=True, timeout=None):
            if "-f" in cmd and "ffmetadata" in cmd:
                export_path = cmd[-1]
                with open(export_path, "wb") as f:
                    f.write(
                        b";FFMETADATA1\n"
                        b"[CHAPTER]\n"
                        b"title=Chapter \xc4ne\n"
                    )
                return _FakeCompletedProcess(returncode=0)
            elif "-map_metadata" in cmd:
                meta_path = cmd[cmd.index("-i", cmd.index("-i") + 1) + 1]
                with open(meta_path, "rb") as f:
                    content = f.read()
                content.decode("utf-8")  # must not raise
                assert "title=Chapter Äne".encode("utf-8") in content
                out_path = cmd[-1]
                with open(out_path, "wb") as f:
                    f.write(b"fake m4b bytes")
                return _FakeCompletedProcess(returncode=0)
            raise AssertionError(f"unexpected command: {cmd}")

        monkeypatch.setattr(subprocess_module, "run", fake_run)

        ok, error = chapter_repair.repair_chapter_encoding(filepath)

        assert ok is True
        assert error is None
        with open(filepath, "rb") as f:
            assert f.read() == b"fake m4b bytes"

    def test_export_failure_returns_error(self, monkeypatch, tmp_path):
        filepath = str(tmp_path / "book.m4b")

        def fake_run(cmd, capture_output=True, timeout=None):
            return _FakeCompletedProcess(returncode=1, stderr=b"ffmpeg export exploded")

        monkeypatch.setattr(subprocess_module, "run", fake_run)

        ok, error = chapter_repair.repair_chapter_encoding(filepath)

        assert ok is False
        assert error is not None
        assert "export exploded" in error

    def test_reinject_failure_returns_error(self, monkeypatch, tmp_path):
        filepath = str(tmp_path / "book.m4b")

        def fake_run(cmd, capture_output=True, timeout=None):
            if "-f" in cmd and "ffmetadata" in cmd:
                export_path = cmd[-1]
                with open(export_path, "wb") as f:
                    f.write(b";FFMETADATA1\n[CHAPTER]\ntitle=Fine\n")
                return _FakeCompletedProcess(returncode=0)
            return _FakeCompletedProcess(returncode=1, stderr=b"ffmpeg reinject exploded")

        monkeypatch.setattr(subprocess_module, "run", fake_run)

        ok, error = chapter_repair.repair_chapter_encoding(filepath)

        assert ok is False
        assert error is not None
        assert "reinject exploded" in error
