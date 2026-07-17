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

import mutagen.mp4

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


class TestCheckChapterEncoding:
    """Regression coverage for surfacing this issue in Troubleshoot Library:
    a live, cheap detector (no full decode) that flags m4b files mutagen
    can't open because of a non-UTF-8 chapter title."""

    def test_flags_chapter_title_decode_failure(self, monkeypatch, tmp_path):
        fake_file = tmp_path / "book.m4b"
        fake_file.write_bytes(b"")

        def fake_mp4_ctor(path):
            raise mutagen.mp4.MP4MetadataError(
                "chapter 0 title: 'utf-8' codec can't decode byte 0xc4 "
                "in position 27: invalid continuation byte"
            )

        monkeypatch.setattr(mutagen.mp4, "MP4", fake_mp4_ctor)

        ok, detail = chapter_repair.check_chapter_encoding(str(fake_file))

        assert ok is False
        assert "chapter 0 title" in detail

    def test_clean_file_is_ok(self, monkeypatch, tmp_path):
        fake_file = tmp_path / "book.m4b"
        fake_file.write_bytes(b"")

        monkeypatch.setattr(mutagen.mp4, "MP4", lambda path: {})

        ok, detail = chapter_repair.check_chapter_encoding(str(fake_file))

        assert ok is True
        assert detail is None

    def test_unrelated_error_is_out_of_scope_for_this_check(self, monkeypatch, tmp_path):
        fake_file = tmp_path / "book.m4b"
        fake_file.write_bytes(b"")

        def fake_mp4_ctor(path):
            raise mutagen.mp4.MP4StreamInfoError("not an MP4 file")

        monkeypatch.setattr(mutagen.mp4, "MP4", fake_mp4_ctor)

        ok, detail = chapter_repair.check_chapter_encoding(str(fake_file))

        assert ok is True
        assert detail is None

    def test_non_m4b_extension_is_skipped_without_opening_file(self, monkeypatch, tmp_path):
        fake_file = tmp_path / "book.mp3"
        fake_file.write_bytes(b"")

        def fail_if_called(path):
            raise AssertionError("should not attempt to open a non-m4b file")

        monkeypatch.setattr(mutagen.mp4, "MP4", fail_if_called)

        ok, detail = chapter_repair.check_chapter_encoding(str(fake_file))

        assert ok is True
        assert detail is None


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

    def test_export_failure_truncates_to_last_lines(self, monkeypatch, tmp_path):
        filepath = str(tmp_path / "book.m4b")
        banner = "\n".join(f"banner line {i}" for i in range(20))
        # Mirrors the real Winter's Heart ffmpeg output shape (multiple
        # tagged error lines), not an artificially short 1-2 line error.
        real_error = (
            "[mov,mp4,m4a,3gp,3g2,mj2] error reading header\n"
            "Error opening input: Invalid data found when processing input\n"
            "Error opening input files: Invalid data found when processing input"
        )

        def fake_run(cmd, capture_output=True, timeout=None):
            return _FakeCompletedProcess(
                returncode=1, stderr=f"{banner}\n{real_error}".encode()
            )

        monkeypatch.setattr(subprocess_module, "run", fake_run)

        ok, error = chapter_repair.repair_chapter_encoding(filepath)

        assert ok is False
        assert "banner line" not in error
        assert "error reading header" in error
        assert "Invalid data found when processing input" in error

    def test_export_failure_with_corruption_signature_adds_guidance(self, monkeypatch, tmp_path):
        filepath = str(tmp_path / "book.m4b")

        def fake_run(cmd, capture_output=True, timeout=None):
            return _FakeCompletedProcess(
                returncode=1,
                stderr=b"Invalid data found when processing input",
            )

        monkeypatch.setattr(subprocess_module, "run", fake_run)

        ok, error = chapter_repair.repair_chapter_encoding(filepath)

        assert ok is False
        assert "Corrupt audiobooks" in error

    def test_export_failure_without_corruption_signature_has_no_guidance(self, monkeypatch, tmp_path):
        filepath = str(tmp_path / "book.m4b")

        def fake_run(cmd, capture_output=True, timeout=None):
            return _FakeCompletedProcess(returncode=1, stderr=b"Permission denied")

        monkeypatch.setattr(subprocess_module, "run", fake_run)

        ok, error = chapter_repair.repair_chapter_encoding(filepath)

        assert ok is False
        assert "Corrupt audiobooks" not in error

    def test_reinject_failure_truncates_and_adds_guidance(self, monkeypatch, tmp_path):
        filepath = str(tmp_path / "book.m4b")
        banner = "\n".join(f"banner line {i}" for i in range(20))
        real_error = (
            "[mov,mp4,m4a] error reading trailer\n"
            "moov atom not found\n"
            "Error opening output file"
        )

        def fake_run(cmd, capture_output=True, timeout=None):
            if "-f" in cmd and "ffmetadata" in cmd:
                export_path = cmd[-1]
                with open(export_path, "wb") as f:
                    f.write(b";FFMETADATA1\n[CHAPTER]\ntitle=Fine\n")
                return _FakeCompletedProcess(returncode=0)
            return _FakeCompletedProcess(
                returncode=1,
                stderr=f"{banner}\n{real_error}".encode(),
            )

        monkeypatch.setattr(subprocess_module, "run", fake_run)

        ok, error = chapter_repair.repair_chapter_encoding(filepath)

        assert ok is False
        assert "banner line" not in error
        assert "moov atom not found" in error
        assert "Corrupt audiobooks" in error
