"""`services.tag_writer` — metadata write-back to the file on disk (issue #255).

Extracted from `routers/library.py` so it can be exercised without an HTTP
request, a session or a library on disk. The EPUB half is driven against real
zip archives built by `tests.factories.write_epub`; the audio half drives stub
tag objects the way `test_embedded_metadata.py` does, because mutagen selects
its tag dialect on the *class name* of what `mutagen.File` returns, and
building genuine M4B/MP3/FLAC containers per case would test mutagen, not this.

Two pre-existing behaviours are pinned rather than fixed here, because the
extraction was a pure move:

* the EPUB writer has raised `AttributeError` on every call since the switch
  to defusedxml (issue #428) — `test_epub_write_back_currently_fails_on_defusedxml`
  pins that, and the round-trip tests reach the rest of the writer through
  `_defusedxml_shim`. Delete both when #428 is fixed;
* a container mutagen opens but which carries *no tags at all* is treated as
  unopenable, because mutagen's `FileType` is a mapping whose truthiness is
  its tag count (see `_read_embedded_metadata` for the same trap).
"""

import xml.etree.ElementTree as stdlib_ET
import zipfile
from types import SimpleNamespace

import defusedxml.ElementTree as ET
import pytest

from services import tag_writer
from tests.factories import write_epub

DC = "{http://purl.org/dc/elements/1.1/}"
OPF = "{http://www.idpf.org/2007/opf}"
CHAPTER = "<html><body><p>Chapter one.</p></body></html>"


def _book(**fields):
    base = dict(
        title=None, author=None, series=None, series_index=None, description=None,
        publisher=None, language=None, publish_year=None, genres=None,
    )
    base.update(fields)
    return SimpleNamespace(**base)


# ---------------------------------------------------------------- EPUB half


@pytest.fixture
def epub(tmp_path):
    return write_epub(str(tmp_path / "book.epub"), [("ch1.xhtml", CHAPTER)])


@pytest.fixture
def _defusedxml_shim(monkeypatch):
    """Install the two tree *builders* defusedxml does not re-export (#428).

    `defusedxml.ElementTree` wraps only the parsing entry points; the writer
    also calls `register_namespace` and `SubElement`, which the stdlib module
    provides on the same `Element` type. Borrowing them lets these tests reach
    the code past the first call. `raising=False` so the fixture becomes a
    harmless no-op once the writer stops needing it.
    """
    monkeypatch.setattr(ET, "register_namespace", stdlib_ET.register_namespace, raising=False)
    monkeypatch.setattr(ET, "SubElement", stdlib_ET.SubElement, raising=False)


def _metadata(path):
    with zipfile.ZipFile(path) as z:
        root = ET.fromstring(z.read("OEBPS/content.opf"))
    return next(c for c in root.iter() if c.tag.endswith("metadata"))


def _dc(meta, tag):
    return [c.text for c in meta if c.tag == DC + tag]


def _calibre(meta):
    return {
        m.attrib["name"]: m.attrib.get("content")
        for m in meta
        if m.tag == OPF + "meta" and m.attrib.get("name", "").startswith("calibre:")
    }


def test_epub_write_back_currently_fails_on_defusedxml(epub, caplog):
    """Pins issue #428. When that is fixed this test must be deleted, along
    with the `_defusedxml_shim` fixture the round-trip tests lean on."""
    with pytest.raises(AttributeError, match="register_namespace"):
        tag_writer.write_ebook_metadata(epub, _book(title="Never written"))

    assert "Failed to write EPUB metadata" in caplog.text
    assert _dc(_metadata(epub), "title") == ["Axis Test"]


@pytest.mark.usefixtures("_defusedxml_shim")
def test_epub_fields_round_trip_and_the_rest_of_the_archive_survives(epub):
    tag_writer.write_ebook_metadata(epub, _book(
        title="New Title", author="Ann Author", description="A blurb",
        publisher="Pub House", language="fr", publish_year=1999,
        series="The Series", series_index=2.0,
    ))

    meta = _metadata(epub)
    # The factory's original dc:title is replaced, not duplicated.
    assert _dc(meta, "title") == ["New Title"]
    assert _dc(meta, "creator") == ["Ann Author"]
    assert _dc(meta, "description") == ["A blurb"]
    assert _dc(meta, "publisher") == ["Pub House"]
    assert _dc(meta, "language") == ["fr"]
    assert _dc(meta, "date") == ["1999"]
    assert _calibre(meta) == {"calibre:series": "The Series", "calibre:series_index": "2.0"}
    with zipfile.ZipFile(epub) as z:
        assert z.read("OEBPS/ch1.xhtml").decode() == CHAPTER
        assert z.read("mimetype") == b"application/epub+zip"


@pytest.mark.usefixtures("_defusedxml_shim")
def test_an_empty_value_removes_the_dc_tag_it_previously_wrote(epub):
    tag_writer.write_ebook_metadata(epub, _book(title="T", description="Old blurb"))
    assert _dc(_metadata(epub), "description") == ["Old blurb"]

    tag_writer.write_ebook_metadata(epub, _book(title="T", description=None))
    assert _dc(_metadata(epub), "description") == []


@pytest.mark.usefixtures("_defusedxml_shim")
def test_a_new_series_replaces_the_old_calibre_meta_and_no_index_writes_none(epub):
    tag_writer.write_ebook_metadata(epub, _book(title="T", series="Old", series_index=1.0))
    tag_writer.write_ebook_metadata(epub, _book(title="T", series="New", series_index=None))

    assert _calibre(_metadata(epub)) == {"calibre:series": "New"}


@pytest.mark.usefixtures("_defusedxml_shim")
def test_no_series_on_the_book_leaves_the_existing_calibre_meta_alone(epub):
    tag_writer.write_ebook_metadata(epub, _book(title="T", series="Keep", series_index=3.0))
    tag_writer.write_ebook_metadata(epub, _book(title="Retitled", series=None))

    meta = _metadata(epub)
    assert _dc(meta, "title") == ["Retitled"]
    assert _calibre(meta) == {"calibre:series": "Keep", "calibre:series_index": "3.0"}


@pytest.mark.usefixtures("_defusedxml_shim")
def test_the_opf_is_found_by_extension_when_container_xml_is_unreadable(tmp_path):
    path = str(tmp_path / "no-container.epub")
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("META-INF/container.xml", "this is not xml")
        z.writestr(
            "book.opf",
            '<package xmlns="http://www.idpf.org/2007/opf">'
            '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"></metadata></package>',
        )

    tag_writer.write_ebook_metadata(path, _book(title="Found"))

    with zipfile.ZipFile(path) as z:
        root = ET.fromstring(z.read("book.opf"))
    assert [c.text for c in root.iter() if c.tag == DC + "title"] == ["Found"]


def test_non_epub_and_missing_paths_are_skipped_without_error(tmp_path):
    mobi = tmp_path / "book.mobi"
    mobi.write_bytes(b"mobi bytes")
    tag_writer.write_ebook_metadata(str(mobi), _book(title="T"))
    assert mobi.read_bytes() == b"mobi bytes"

    tag_writer.write_ebook_metadata(str(tmp_path / "absent.epub"), _book(title="T"))
    assert not (tmp_path / "absent.epub").exists()


@pytest.mark.usefixtures("_defusedxml_shim")
@pytest.mark.parametrize("entries", [
    {"mimetype": "application/epub+zip"},                          # no OPF anywhere
    {"x.opf": '<package xmlns="http://www.idpf.org/2007/opf"/>'},  # OPF, no metadata
])
def test_an_archive_without_a_usable_opf_is_left_untouched(tmp_path, entries):
    path = tmp_path / "odd.epub"
    with zipfile.ZipFile(path, "w") as z:
        for name, content in entries.items():
            z.writestr(name, content)
    before = path.read_bytes()

    tag_writer.write_ebook_metadata(str(path), _book(title="T"))

    assert path.read_bytes() == before


def test_a_file_that_is_not_a_zip_raises_so_the_caller_can_report_it(tmp_path):
    path = tmp_path / "broken.epub"
    path.write_bytes(b"definitely not a zip archive")

    with pytest.raises(zipfile.BadZipFile):
        tag_writer.write_ebook_metadata(str(path), _book(title="T"))


# --------------------------------------------------------------- audio half


class _Tags(dict):
    """A mutagen-shaped stand-in; the subclass *name* selects the dialect.

    Seeded with one tag unless a test says otherwise: like mutagen's
    `FileType`, an empty mapping is falsy, and the writer treats falsy as
    "could not open".
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self:
            self["\xa9nam"] = ["Old"]
        self.saved = False

    def save(self):
        self.saved = True


class MP4(_Tags):
    pass


class FLAC(_Tags):
    pass


class WavPack(_Tags):
    pass


class MP3(_Tags):
    def __init__(self, tags=None):
        super().__init__()
        self.tags = tags

    def add_tags(self):
        self.tags = {}


@pytest.fixture
def audio(tmp_path):
    path = tmp_path / "book.m4b"
    path.write_bytes(b"not really an audio container")
    return str(path)


def _opened(monkeypatch, obj):
    monkeypatch.setattr(tag_writer.mutagen, "File", lambda *a, **k: obj)
    return obj


def test_mp4_atoms_are_written_including_both_series_spellings(audio, monkeypatch):
    tags = _opened(monkeypatch, MP4())

    tag_writer.write_audiobook_metadata(audio, _book(
        title="T", author="A", series="Saga", series_index=2.0,
        description="Blurb", genres="Fantasy", publish_year=2001,
    ))

    assert tags.saved
    assert tags["\xa9nam"] == ["T"] and tags["\xa9ART"] == ["A"]
    assert tags["\xa9grp"] == ["Saga #2"]
    assert tags["----:com.apple.iTunes:SERIES"] == [b"Saga"]
    assert tags["----:com.apple.iTunes:SERIES-PART"] == [b"2"]
    assert tags["trkn"] == [(2, 0)]
    assert tags["desc"] == ["Blurb"]
    assert tags["\xa9gen"] == ["Fantasy"]
    assert tags["\xa9day"] == ["2001"]


def test_mp4_series_without_an_index_writes_the_bare_name(audio, monkeypatch):
    tags = _opened(monkeypatch, MP4())

    tag_writer.write_audiobook_metadata(audio, _book(title="T", series="Saga"))

    assert tags["\xa9grp"] == ["Saga"]
    assert "----:com.apple.iTunes:SERIES-PART" not in tags
    assert "trkn" not in tags


def test_mp4_cleared_series_and_description_delete_the_tags(audio, monkeypatch):
    tags = _opened(monkeypatch, MP4({
        "\xa9grp": ["Old"], "\xa9alb": ["Old"], "desc": ["old blurb"],
        "----:com.apple.iTunes:SERIES": [b"Old"],
        "----:com.apple.iTunes:SERIES-PART": [b"1"],
    }))

    tag_writer.write_audiobook_metadata(audio, _book(title="T", series="", description=None))

    assert tags.saved
    assert set(tags) == {"\xa9nam"}


def test_mp3_id3_frames_are_written_and_tags_created_when_absent(audio, monkeypatch):
    tags = _opened(monkeypatch, MP3(tags=None))

    tag_writer.write_audiobook_metadata(audio, _book(
        title="T", author="A", series="Saga", series_index=3, description="Blurb",
        genres="Drama", publish_year=1987, publisher="Pub",
    ))

    assert tags.saved
    frames = tags.tags
    assert frames["TIT2"].text == ["T"]
    assert frames["TPE1"].text == ["A"]
    assert frames["TALB"].text == ["Saga"]
    assert frames["TRCK"].text == ["3"]
    assert frames["COMM"].text == ["Blurb"]
    assert frames["TCON"].text == ["Drama"]
    assert frames["TYER"].text == ["1987"]
    assert frames["TPUB"].text == ["Pub"]


def test_mp3_cleared_series_and_description_drop_their_frames(audio, monkeypatch):
    tags = _opened(monkeypatch, MP3(tags={"TALB": "old", "COMM": "old"}))

    tag_writer.write_audiobook_metadata(audio, _book(title="T", series="", description=None))

    assert "TALB" not in tags.tags and "COMM" not in tags.tags
    assert tags.tags["TIT2"].text == ["T"]


def test_vorbis_comments_are_written_for_flac_and_ogg(audio, monkeypatch):
    tags = _opened(monkeypatch, FLAC({"album": ["Old"], "description": ["old"]}))

    tag_writer.write_audiobook_metadata(audio, _book(
        title="T", author="A", series="Saga", series_index=1.0, description="Blurb",
        genres="Drama", publish_year=1987, publisher="Pub",
    ))

    assert tags.saved
    assert tags["title"] == ["T"] and tags["artist"] == ["A"]
    assert tags["album"] == ["Saga"] and tags["tracknumber"] == ["1"]
    assert tags["description"] == ["Blurb"] and tags["genre"] == ["Drama"]
    assert tags["date"] == ["1987"] and tags["organization"] == ["Pub"]

    tag_writer.write_audiobook_metadata(audio, _book(title="T", series="", description=None))
    assert "album" not in tags and "description" not in tags


def test_an_unsupported_container_is_not_saved(audio, monkeypatch):
    tags = _opened(monkeypatch, WavPack())

    tag_writer.write_audiobook_metadata(audio, _book(title="T"))

    assert not tags.saved and tags == {"\xa9nam": ["Old"]}


def test_a_container_mutagen_cannot_open_is_skipped(audio, monkeypatch):
    _opened(monkeypatch, None)
    tag_writer.write_audiobook_metadata(audio, _book(title="T"))  # no exception


def test_a_container_with_no_tags_at_all_is_treated_as_unopenable(audio, monkeypatch, caplog):
    """Pins the mutagen-truthiness trap: an untagged file gets nothing written."""
    tags = _opened(monkeypatch, MP4())
    tags.clear()

    tag_writer.write_audiobook_metadata(audio, _book(title="T"))

    assert not tags.saved and tags == {}
    assert "Mutagen could not open" in caplog.text


def test_a_missing_file_is_never_opened(tmp_path, monkeypatch):
    def _explode(*a, **k):
        raise AssertionError("mutagen.File must not be called for a missing path")

    monkeypatch.setattr(tag_writer.mutagen, "File", _explode)
    tag_writer.write_audiobook_metadata(str(tmp_path / "absent.m4b"), _book(title="T"))


def test_a_failing_save_is_logged_not_raised(audio, monkeypatch, caplog):
    def _boom():
        raise OSError("read-only filesystem")

    tags = _opened(monkeypatch, MP4())
    tags.save = _boom  # the class name must stay MP4 to reach the save
    tag_writer.write_audiobook_metadata(audio, _book(title="T"))

    assert "Failed to write audio metadata" in caplog.text
