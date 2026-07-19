"""
Tests for services.chapter_repair — repairs m4b files whose legacy Nero
`chpl` chapter atom mutagen cannot parse (which blocks ABS tag writes,
since mutagen.mp4.MP4() raises while just opening the file).

Repair strategy under test: neutralize the unparseable atom by overwriting
its fourcc `chpl` -> `free` in place (4 bytes, no remux), verify with
mutagen, and only if the file's chapters lived solely in that atom (no
QuickTime chapter track for ffprobe to read) rebuild them via ffmpeg from
an ffprobe snapshot taken before the patch.
"""

import json
import struct
import subprocess as subprocess_module

import mutagen.mp4

from services import chapter_repair


def _atom(fourcc: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", 8 + len(payload)) + fourcc + payload


def _synthetic_m4b(with_chpl: bool = True) -> bytes:
    """Minimal atom layout mutagen's Atoms walker can traverse: an ftyp atom
    plus moov > udta > chpl. The chpl payload doesn't need to be parseable —
    only the atom tree structure (size fields) matters here."""
    ftyp = _atom(b"ftyp", b"M4A \x00\x00\x02\x00isomiso2")
    if with_chpl:
        chpl_payload = (
            b"\x01\x00\x00\x00"      # version/flags
            + b"\x00\x00\x00\x00"    # reserved
            + b"\x01"                # chapter count
            + b"\x00" * 8            # timestamp
            + b"\x05Intro"           # title length + title
        )
        udta = _atom(b"udta", _atom(b"chpl", chpl_payload))
    else:
        udta = _atom(b"udta", _atom(b"name", b"no chapters here"))
    moov = _atom(b"moov", udta)
    return ftyp + moov


def _ffprobe_json(titles):
    return json.dumps({
        "chapters": [
            {
                "id": i,
                "start_time": str(float(i * 10)),
                "end_time": str(float((i + 1) * 10)),
                "tags": {"title": t},
            }
            for i, t in enumerate(titles)
        ]
    }).encode()


class _FakeCompletedProcess:
    def __init__(self, returncode=0, stdout=b"", stderr=b""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


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


class TestFfmetadataEscape:
    def test_plain_text_passes_through(self):
        assert chapter_repair.ffmetadata_escape("Chapter One") == "Chapter One"

    def test_special_chars_are_escaped(self):
        assert chapter_repair.ffmetadata_escape("a=b") == "a\\=b"
        assert chapter_repair.ffmetadata_escape("a;b") == "a\\;b"
        assert chapter_repair.ffmetadata_escape("a#b") == "a\\#b"
        assert chapter_repair.ffmetadata_escape("a\\b") == "a\\\\b"
        assert chapter_repair.ffmetadata_escape("a\nb") == "a\\\nb"

    def test_backslash_escaped_before_other_chars(self):
        # A pre-existing "\=" must become "\\\=" (escaped backslash, then
        # escaped equals), not get double-processed.
        assert chapter_repair.ffmetadata_escape("\\=") == "\\\\\\="


class TestNeutralizeChpl:
    def test_renames_chpl_fourcc_to_free(self, tmp_path):
        original = _synthetic_m4b(with_chpl=True)
        filepath = tmp_path / "book.m4b"
        filepath.write_bytes(original)
        chpl_pos = original.find(b"chpl")
        assert chpl_pos != -1

        result = chapter_repair.neutralize_chpl(str(filepath))

        assert result is True
        patched = filepath.read_bytes()
        assert patched[chpl_pos:chpl_pos + 4] == b"free"
        # Everything except those 4 bytes is untouched.
        assert patched[:chpl_pos] == original[:chpl_pos]
        assert patched[chpl_pos + 4:] == original[chpl_pos + 4:]

    def test_returns_false_and_leaves_file_alone_without_chpl(self, tmp_path):
        original = _synthetic_m4b(with_chpl=False)
        filepath = tmp_path / "book.m4b"
        filepath.write_bytes(original)

        result = chapter_repair.neutralize_chpl(str(filepath))

        assert result is False
        assert filepath.read_bytes() == original


class TestCheckChapterEncoding:
    """Live, cheap detector for Troubleshoot Library: flags m4b files that
    mutagen can't open because of an unparseable chpl chapter atom."""

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


class TestFormatFfmpegError:
    def test_truncates_to_last_lines_dropping_banner(self):
        banner = "\n".join(f"banner line {i}" for i in range(20))
        real_error = (
            "[mov,mp4,m4a,3gp,3g2,mj2] error reading header\n"
            "Error opening input: Invalid data found when processing input\n"
            "Error opening input files: Invalid data found when processing input"
        )
        msg = chapter_repair._format_ffmpeg_error(
            "Failed to export metadata", f"{banner}\n{real_error}".encode()
        )
        assert "banner line" not in msg
        assert "error reading header" in msg
        assert "Invalid data found when processing input" in msg

    def test_corruption_signature_adds_guidance(self):
        msg = chapter_repair._format_ffmpeg_error(
            "Failed to export metadata", b"Invalid data found when processing input"
        )
        assert "Corrupt audiobooks" in msg

    def test_non_corruption_failure_has_no_guidance(self):
        msg = chapter_repair._format_ffmpeg_error(
            "Failed to export metadata", b"Permission denied"
        )
        assert "Corrupt audiobooks" not in msg


class TestRepairChapterEncoding:
    def test_neutralizes_chpl_and_succeeds_when_chapter_track_survives(
        self, monkeypatch, tmp_path
    ):
        filepath = tmp_path / "book.m4b"
        filepath.write_bytes(_synthetic_m4b(with_chpl=True))
        chpl_pos = filepath.read_bytes().find(b"chpl")
        ffmpeg_calls = []

        def fake_run(cmd, capture_output=True, timeout=None):
            if cmd[0] == "ffprobe":
                return _FakeCompletedProcess(stdout=_ffprobe_json(["Intro", "Middle"]))
            ffmpeg_calls.append(cmd)
            raise AssertionError(f"ffmpeg should not run on the happy path: {cmd}")

        monkeypatch.setattr(subprocess_module, "run", fake_run)
        monkeypatch.setattr(chapter_repair, "check_chapter_encoding", lambda p: (True, None))

        ok, error = chapter_repair.repair_chapter_encoding(str(filepath))

        assert ok is True
        assert error is None
        assert filepath.read_bytes()[chpl_pos:chpl_pos + 4] == b"free"
        assert ffmpeg_calls == []

    def test_rebuilds_chapters_when_they_lived_only_in_chpl(self, monkeypatch, tmp_path):
        filepath = tmp_path / "book.m4b"
        filepath.write_bytes(_synthetic_m4b(with_chpl=True))
        probe_calls = []
        inject_cmds = []
        meta_contents = []

        def fake_run(cmd, capture_output=True, timeout=None):
            if cmd[0] == "ffprobe":
                probe_calls.append(cmd)
                if len(probe_calls) == 1:
                    # Pre-patch snapshot: chapters exist (title needs escaping).
                    return _FakeCompletedProcess(stdout=_ffprobe_json(["Intro = one"]))
                # Post-patch: chapters gone -> they lived only in chpl.
                return _FakeCompletedProcess(stdout=_ffprobe_json([]))
            if "-f" in cmd and "ffmetadata" in cmd:
                export_path = cmd[-1]
                with open(export_path, "wb") as f:
                    f.write(b";FFMETADATA1\ntitle=Book Title\n")
                return _FakeCompletedProcess(returncode=0)
            if "-map_metadata" in cmd:
                assert "-map_chapters" in cmd
                assert cmd[cmd.index("-map_chapters") + 1] == "1"
                inject_cmds.append(cmd)
                meta_path = cmd[cmd.index("-i", cmd.index("-i") + 1) + 1]
                with open(meta_path, "rb") as f:
                    meta_contents.append(f.read())
                out_path = cmd[-1]
                with open(out_path, "wb") as f:
                    f.write(b"rebuilt m4b bytes")
                return _FakeCompletedProcess(returncode=0)
            raise AssertionError(f"unexpected command: {cmd}")

        monkeypatch.setattr(subprocess_module, "run", fake_run)
        monkeypatch.setattr(chapter_repair, "check_chapter_encoding", lambda p: (True, None))

        ok, error = chapter_repair.repair_chapter_encoding(str(filepath))

        assert ok is True
        assert error is None
        assert len(inject_cmds) == 1
        meta_text = meta_contents[0].decode("utf-8")
        assert "[CHAPTER]" in meta_text
        assert "title=Intro \\= one" in meta_text     # snapshot title, escaped
        assert "title=Book Title" in meta_text        # existing global tags preserved
        assert filepath.read_bytes() == b"rebuilt m4b bytes"

    def test_reports_failure_when_file_still_broken_after_neutralizing(
        self, monkeypatch, tmp_path
    ):
        filepath = tmp_path / "book.m4b"
        filepath.write_bytes(_synthetic_m4b(with_chpl=True))

        def fake_run(cmd, capture_output=True, timeout=None):
            if cmd[0] == "ffprobe":
                return _FakeCompletedProcess(stdout=_ffprobe_json(["Intro"]))
            raise AssertionError(f"unexpected command: {cmd}")

        monkeypatch.setattr(subprocess_module, "run", fake_run)
        monkeypatch.setattr(
            chapter_repair, "check_chapter_encoding",
            lambda p: (False, "chapter 0 title: still broken"),
        )

        ok, error = chapter_repair.repair_chapter_encoding(str(filepath))

        assert ok is False
        assert "still broken" in error

    def test_reports_failure_when_no_chpl_atom_exists(self, monkeypatch, tmp_path):
        filepath = tmp_path / "book.m4b"
        filepath.write_bytes(_synthetic_m4b(with_chpl=False))

        def fake_run(cmd, capture_output=True, timeout=None):
            if cmd[0] == "ffprobe":
                return _FakeCompletedProcess(stdout=_ffprobe_json([]))
            raise AssertionError(f"unexpected command: {cmd}")

        monkeypatch.setattr(subprocess_module, "run", fake_run)

        ok, error = chapter_repair.repair_chapter_encoding(str(filepath))

        assert ok is False
        assert "chpl" in error
