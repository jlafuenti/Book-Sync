"""Files Tandem writes into the library keep a mode other programs can read (issue #885).

`tempfile.mkstemp` creates `0600`, and that mode used to survive the replace.
These assert real POSIX modes, so they skip on Windows and run in CI.
"""

import os
import stat
import subprocess as subprocess_module
import sys
import tempfile
import zipfile
from types import SimpleNamespace

import pytest

from services import chapter_repair, library_writer, tag_writer
from tests.factories import write_epub

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")


def _mode(path):
    return stat.S_IMODE(os.stat(path).st_mode)


def _book(**fields):
    base = dict(title="New Title", author=None, series_list=None, description=None,
                publisher=None, language="en", publish_year=None, genres=None)
    base.update(fields)
    return SimpleNamespace(**base)


def _epub(tmp_path, mode):
    path = write_epub(str(tmp_path / "book.epub"), [("ch1.xhtml", "<html><body><p>One.</p></body></html>")])
    os.chmod(path, mode)
    return path


@pytest.mark.parametrize("mode", [0o644, 0o664, 0o640, 0o600])
def test_epub_write_back_keeps_the_mode(tmp_path, mode):
    path = _epub(tmp_path, mode)

    tag_writer.write_ebook_metadata(path, _book())

    assert _mode(path) == mode
    with zipfile.ZipFile(path) as z:
        assert z.testzip() is None
        opf = next(n for n in z.namelist() if n.endswith(".opf"))
        assert b"New Title" in z.read(opf)


def test_epub_write_back_stages_next_to_the_file_and_leaves_nothing_behind(tmp_path, monkeypatch):
    path = _epub(tmp_path, 0o644)
    staged_in = []
    real_replace = os.replace

    def spy_replace(src, dst):
        staged_in.append(os.path.dirname(src))
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", spy_replace)

    tag_writer.write_ebook_metadata(path, _book())

    # Same directory means an atomic rename, not a cross-filesystem copy.
    assert staged_in == [str(tmp_path)]
    assert os.listdir(tmp_path) == ["book.epub"]


def test_failed_epub_write_back_leaves_the_file_alone(tmp_path, monkeypatch):
    path = _epub(tmp_path, 0o644)
    before = open(path, "rb").read()

    def fail(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", fail)

    with pytest.raises(OSError):
        tag_writer.write_ebook_metadata(path, _book())

    assert open(path, "rb").read() == before
    assert _mode(path) == 0o644
    assert os.listdir(tmp_path) == ["book.epub"]


@pytest.mark.parametrize("mode", [0o644, 0o664, 0o600])
def test_chapter_remux_keeps_the_mode(tmp_path, monkeypatch, mode):
    path = tmp_path / "book.m4b"
    path.write_bytes(b"original audio")
    os.chmod(path, mode)

    def fake_run(cmd, capture_output=True, timeout=None, **kwargs):
        with open(cmd[-1], "wb") as out:
            out.write(b";FFMETADATA1\n" if cmd[-1].endswith(".txt") else b"remuxed audio")
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(subprocess_module, "run", fake_run)
    monkeypatch.setattr(chapter_repair, "snapshot_freeform_tags", lambda p: {})
    monkeypatch.setattr(chapter_repair, "restore_freeform_tags", lambda p, t: None)
    monkeypatch.setattr(chapter_repair, "_duration_is_plausible", lambda a, b: (True, None))

    ok, error = chapter_repair.remux_with_chapters(str(path), [{"start": 0, "end": 10, "title": "One"}])

    assert (ok, error) == (True, None)
    assert path.read_bytes() == b"remuxed audio"
    assert _mode(path) == mode


@pytest.mark.parametrize("move", [True, False])
@pytest.mark.parametrize("umask, expected", [(0o022, 0o644), (0o027, 0o640), (0o002, 0o664)])
async def test_placing_a_temp_file_in_the_library_uses_the_default_mode(tmp_path, monkeypatch, umask, expected, move):
    async def template(db, book_type):
        return "<Title>"

    monkeypatch.setattr(library_writer, "_get_template", template)
    monkeypatch.setattr(library_writer.settings, "ebook_dir", str(tmp_path))
    fd, source = tempfile.mkstemp(suffix=".epub")
    os.close(fd)
    assert _mode(source) == 0o600

    previous = os.umask(umask)
    try:
        target = await library_writer.place_file(None, "ebook", source, ".epub", {"title": "Placed"}, move=move)
    finally:
        os.umask(previous)
        if os.path.exists(source):
            os.remove(source)

    assert _mode(target) == expected
