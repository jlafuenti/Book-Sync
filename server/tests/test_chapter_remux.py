"""
The one chapter write-back remux (issue #192).

`PUT /api/library/audiobooks/{id}/chapters` and the chapter-encoding repair both
rewrite the user's only copy of a purchased audiobook. They used to do it with
two byte-identical, separately maintained ffmpeg invocations, each of which:

* lost every iTunes freeform atom (`----:com.apple.iTunes:NARRATOR`, `SERIES`,
  `SERIES-PART`, `ASIN`), because ffmpeg's mov muxer writes only its own fixed
  list of ilst atoms and never emits `----` — so editing one chapter title
  silently stripped the narrator and series metadata the scanner and the ABS
  enrichment read back out of the file;
* staged the replacement in the *system* temp dir and `shutil.move`d it over the
  original, which across filesystems (container `/tmp` to a mounted library) is
  copy-then-delete: a full disk or a crash mid-copy leaves the original
  truncated and the replacement half-written;
* never looked at the output before it overwrote the source.

`services.chapter_repair.remux_with_chapters` is now the single implementation,
and these are its rules.
"""

import os
import subprocess

import pytest

from services import chapter_repair


class _Result:
    def __init__(self, returncode=0, stderr=b""):
        self.returncode = returncode
        self.stderr = stderr
        self.stdout = b""


@pytest.fixture
def m4b(tmp_path):
    path = tmp_path / "library" / "book.m4b"
    path.parent.mkdir()
    path.write_bytes(b"the original audiobook bytes")
    return str(path)


CHAPTERS = [
    {"start": 0.0, "end": 10.0, "title": "One"},
    {"start": 10.0, "end": 25.0, "title": "Two"},
]


def _install_ffmpeg(monkeypatch, *, out_bytes=b"remuxed bytes", returncode=0,
                    seen=None):
    """A subprocess.run double for the export + reinject pair."""
    def _run(cmd, **kwargs):
        if seen is not None:
            seen.append(list(cmd))
        if "-f" in cmd and "ffmetadata" in cmd:
            with open(cmd[-1], "wb") as f:
                f.write(b";FFMETADATA1\ntitle=A Book\n")
            return _Result()
        if "-map_metadata" in cmd:
            if returncode == 0:
                with open(cmd[-1], "wb") as f:
                    f.write(out_bytes)
            return _Result(returncode=returncode, stderr=b"ffmpeg said no\n")
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(subprocess, "run", _run)


def _no_duration_check(monkeypatch, original=100.0, produced=100.0):
    seen = []

    def _probe(path):
        seen.append(path)
        return original if len(seen) == 1 else produced

    monkeypatch.setattr(chapter_repair, "probe_duration_seconds", _probe)
    return seen


# ---------------------------------------------------------------------------
# Staging and replacement
# ---------------------------------------------------------------------------

def test_the_temp_file_is_staged_next_to_the_target(monkeypatch, m4b):
    """Same directory means `os.replace` is an atomic rename rather than a
    cross-filesystem copy-then-delete."""
    seen = []
    _install_ffmpeg(monkeypatch, seen=seen)
    _no_duration_check(monkeypatch)

    ok, err = chapter_repair.remux_with_chapters(m4b, CHAPTERS, timeout=5)

    assert ok, err
    reinject = next(c for c in seen if "-map_metadata" in c)
    assert os.path.dirname(reinject[-1]) == os.path.dirname(m4b)
    assert os.path.splitext(reinject[-1])[1] == ".m4b"


def test_the_replacement_is_an_atomic_rename_not_a_move(monkeypatch, m4b):
    """`shutil.move` across filesystems is copy-then-delete, so a crash or a
    full disk mid-copy leaves the original truncated. `os.replace` within one
    directory cannot half-happen."""
    replaces = []
    real_replace = os.replace

    def _spy(src, dst):
        replaces.append((src, dst))
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", _spy)

    import shutil
    monkeypatch.setattr(shutil, "move", _forbidden("shutil.move"))

    _install_ffmpeg(monkeypatch)
    _no_duration_check(monkeypatch)

    ok, err = chapter_repair.remux_with_chapters(m4b, CHAPTERS, timeout=5)

    assert ok, err
    assert len(replaces) == 1
    assert replaces[0][1] == m4b


def _forbidden(name):
    def _f(*a, **k):
        raise AssertionError(f"{name} must not be used to replace the original")
    return _f


def test_a_successful_remux_leaves_the_new_bytes_and_no_temp_files(monkeypatch, m4b):
    _install_ffmpeg(monkeypatch, out_bytes=b"remuxed bytes")
    _no_duration_check(monkeypatch)

    ok, err = chapter_repair.remux_with_chapters(m4b, CHAPTERS, timeout=5)

    assert ok, err
    with open(m4b, "rb") as f:
        assert f.read() == b"remuxed bytes"
    assert os.listdir(os.path.dirname(m4b)) == ["book.m4b"]


# ---------------------------------------------------------------------------
# Verifying the output before it replaces the source
# ---------------------------------------------------------------------------

def test_an_output_with_the_wrong_duration_never_replaces_the_original(monkeypatch, m4b):
    _install_ffmpeg(monkeypatch)
    _no_duration_check(monkeypatch, original=3600.0, produced=12.0)

    ok, err = chapter_repair.remux_with_chapters(m4b, CHAPTERS, timeout=5)

    assert not ok
    assert "duration" in err.lower()
    with open(m4b, "rb") as f:
        assert f.read() == b"the original audiobook bytes"
    assert os.listdir(os.path.dirname(m4b)) == ["book.m4b"], "the temp file must be cleaned up"


def test_a_duration_within_the_tolerance_is_accepted(monkeypatch, m4b):
    _install_ffmpeg(monkeypatch)
    _no_duration_check(monkeypatch, original=3600.0, produced=3600.4)

    ok, err = chapter_repair.remux_with_chapters(m4b, CHAPTERS, timeout=5)
    assert ok, err


def test_an_empty_output_never_replaces_the_original(monkeypatch, m4b):
    _install_ffmpeg(monkeypatch, out_bytes=b"")
    _no_duration_check(monkeypatch)

    ok, err = chapter_repair.remux_with_chapters(m4b, CHAPTERS, timeout=5)

    assert not ok
    with open(m4b, "rb") as f:
        assert f.read() == b"the original audiobook bytes"


def test_an_unmeasurable_original_does_not_block_the_remux(monkeypatch, m4b):
    """`probe_duration_seconds` returns None for "no opinion". A file we could
    never measure in the first place has no expectation to compare against, and
    refusing the edit would be worse than doing it."""
    _install_ffmpeg(monkeypatch)
    monkeypatch.setattr(chapter_repair, "probe_duration_seconds", lambda p: None)

    ok, err = chapter_repair.remux_with_chapters(m4b, CHAPTERS, timeout=5)
    assert ok, err


def test_a_failed_ffmpeg_leaves_the_original_and_cleans_up(monkeypatch, m4b):
    _install_ffmpeg(monkeypatch, returncode=1)
    _no_duration_check(monkeypatch)

    ok, err = chapter_repair.remux_with_chapters(m4b, CHAPTERS, timeout=5)

    assert not ok
    assert err
    with open(m4b, "rb") as f:
        assert f.read() == b"the original audiobook bytes"
    assert os.listdir(os.path.dirname(m4b)) == ["book.m4b"]


# ---------------------------------------------------------------------------
# iTunes freeform atoms
# ---------------------------------------------------------------------------

def test_freeform_tags_are_snapshotted_before_and_restored_after(monkeypatch, m4b):
    """ffmpeg's mov muxer never emits `----` atoms, so `-map_metadata` cannot
    carry them: they have to be read with mutagen before the remux and written
    back to the new file after it."""
    snapshot = {"----:com.apple.iTunes:NARRATOR": [b"A Narrator"],
                "----:com.apple.iTunes:SERIES": [b"A Series"]}
    calls = {}

    def _snapshot(path):
        calls["snapshot"] = path
        return snapshot

    def _restore(path, tags):
        calls["restore"] = (path, tags)

    monkeypatch.setattr(chapter_repair, "snapshot_freeform_tags", _snapshot)
    monkeypatch.setattr(chapter_repair, "restore_freeform_tags", _restore)
    _install_ffmpeg(monkeypatch)
    _no_duration_check(monkeypatch)

    ok, err = chapter_repair.remux_with_chapters(m4b, CHAPTERS, timeout=5)

    assert ok, err
    assert calls["snapshot"] == m4b, "the snapshot is taken from the original"
    restored_path, restored_tags = calls["restore"]
    assert restored_tags == snapshot
    # Restored onto the staged file, before it becomes the library's copy: a
    # file that appeared under the real name and only then grew its tags would
    # be visible to a concurrent scan in its untagged state.
    assert restored_path != m4b
    assert os.path.dirname(restored_path) == os.path.dirname(m4b)


def test_a_file_with_no_freeform_tags_still_remuxes(monkeypatch, m4b):
    monkeypatch.setattr(chapter_repair, "snapshot_freeform_tags", lambda p: {})
    _install_ffmpeg(monkeypatch)
    _no_duration_check(monkeypatch)

    ok, err = chapter_repair.remux_with_chapters(m4b, CHAPTERS, timeout=5)
    assert ok, err


def test_an_unreadable_snapshot_does_not_abort_the_remux(monkeypatch, m4b):
    """mutagen raises on the very files chapter_repair exists to fix. Losing
    tags we could not read is not a reason to refuse the chapter edit."""
    def _boom(path):
        raise ValueError("chapter 3 title: bad bytes")

    monkeypatch.setattr(chapter_repair, "snapshot_freeform_tags", _boom)
    _install_ffmpeg(monkeypatch)
    _no_duration_check(monkeypatch)

    ok, err = chapter_repair.remux_with_chapters(m4b, CHAPTERS, timeout=5)
    assert ok, err


def test_a_failed_restore_does_not_undo_a_good_remux(monkeypatch, m4b):
    def _boom(path, tags):
        raise ValueError("could not write tags")

    monkeypatch.setattr(chapter_repair, "snapshot_freeform_tags",
                        lambda p: {"----:com.apple.iTunes:NARRATOR": [b"X"]})
    monkeypatch.setattr(chapter_repair, "restore_freeform_tags", _boom)
    _install_ffmpeg(monkeypatch)
    _no_duration_check(monkeypatch)

    ok, err = chapter_repair.remux_with_chapters(m4b, CHAPTERS, timeout=5)
    assert ok, err
    with open(m4b, "rb") as f:
        assert f.read() == b"remuxed bytes"


# ---------------------------------------------------------------------------
# The ffmetadata the remux writes
# ---------------------------------------------------------------------------

def test_the_chapters_written_are_the_ones_supplied(monkeypatch, m4b):
    seen = []
    written = {}

    def _run(cmd, **kwargs):
        seen.append(list(cmd))
        if "-f" in cmd and "ffmetadata" in cmd:
            with open(cmd[-1], "wb") as f:
                f.write(b";FFMETADATA1\ntitle=A Book\n"
                        b"[CHAPTER]\nTIMEBASE=1/1000\nSTART=0\nEND=1\ntitle=Stale\n")
            return _Result()
        meta_path = cmd[cmd.index("-i", cmd.index("-i") + 1) + 1]
        with open(meta_path, "rb") as f:
            written["meta"] = f.read().decode("utf-8")
        with open(cmd[-1], "wb") as f:
            f.write(b"remuxed bytes")
        return _Result()

    monkeypatch.setattr(subprocess, "run", _run)
    _no_duration_check(monkeypatch)

    ok, err = chapter_repair.remux_with_chapters(m4b, CHAPTERS, timeout=5)

    assert ok, err
    meta = written["meta"]
    assert "title=One" in meta and "title=Two" in meta
    assert "title=Stale" not in meta, "the file's old chapters must be dropped"
    assert "title=A Book" in meta, "everything that is not a chapter is kept"
    assert "START=10000" in meta and "END=25000" in meta


def test_special_characters_in_a_title_are_escaped(monkeypatch, m4b):
    written = {}

    def _run(cmd, **kwargs):
        if "-f" in cmd and "ffmetadata" in cmd:
            with open(cmd[-1], "wb") as f:
                f.write(b";FFMETADATA1\n")
            return _Result()
        meta_path = cmd[cmd.index("-i", cmd.index("-i") + 1) + 1]
        with open(meta_path, "rb") as f:
            written["meta"] = f.read().decode("utf-8")
        with open(cmd[-1], "wb") as f:
            f.write(b"remuxed bytes")
        return _Result()

    monkeypatch.setattr(subprocess, "run", _run)
    _no_duration_check(monkeypatch)

    ok, _ = chapter_repair.remux_with_chapters(
        m4b, [{"start": 0.0, "end": 1.0, "title": "Part 1 = Intro; #5"}], timeout=5)

    assert ok
    assert "title=Part 1 \\= Intro\\; \\#5" in written["meta"]


def test_the_reinject_maps_chapters_and_not_only_metadata(monkeypatch, m4b):
    """`-map_metadata` alone does not map chapters; without `-map_chapters` the
    remux silently re-copies the original ones and reports success."""
    seen = []
    _install_ffmpeg(monkeypatch, seen=seen)
    _no_duration_check(monkeypatch)

    assert chapter_repair.remux_with_chapters(m4b, CHAPTERS, timeout=5)[0]

    reinject = next(c for c in seen if "-map_metadata" in c)
    assert reinject[reinject.index("-map_chapters") + 1] == "1"
    assert reinject[reinject.index("-map_metadata") + 1] == "1"


def test_non_utf8_exported_metadata_is_decoded_leniently(monkeypatch, m4b):
    """ffmpeg copies chapter title bytes through without validating encoding, so
    a title written in Windows-1252 by another tool must not crash the read."""
    def _run(cmd, **kwargs):
        if "-f" in cmd and "ffmetadata" in cmd:
            with open(cmd[-1], "wb") as f:
                f.write(b";FFMETADATA1\n[CHAPTER]\nTIMEBASE=1/1000\n"
                        b"START=0\nEND=5000\ntitle=Chapter \xc4ne\n")
            return _Result()
        with open(cmd[-1], "wb") as f:
            f.write(b"remuxed bytes")
        return _Result()

    monkeypatch.setattr(subprocess, "run", _run)
    _no_duration_check(monkeypatch)

    ok, err = chapter_repair.remux_with_chapters(m4b, CHAPTERS, timeout=5)
    assert ok, err
