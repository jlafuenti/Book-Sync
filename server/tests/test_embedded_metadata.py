"""`_read_embedded_metadata` — the tags the scan reads out of the file itself.

Split out of `extract_metadata` so the whole read runs off the event loop
(issue #203); `test_event_loop_blocking.py` pins *where* it runs, this file pins
*what* it returns. It is the widest untested surface in the scan path: every
book in the library goes through it, and a misread here is a wrong title or a
lost series on hundreds of rows at once.

The audio branches drive stub tag objects rather than real files. mutagen's
`FileType` is a mapping whose class name selects the tag dialect (`MP4` for
iTunes atoms, `MP3`/`FLAC` for ID3-style keys, anything else for the generic
fallback), and building genuine M4B and MP3 containers per case would test
mutagen rather than this code.
"""

import zipfile
from types import SimpleNamespace

import pytest

from routers.library import _read_embedded_metadata

OPF = (
    '<?xml version="1.0"?>'
    '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" '
    'unique-identifier="bookid">'
    '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/" '
    'xmlns:opf="http://www.idpf.org/2007/opf">{meta}</metadata>'
    '<manifest><item id="c1" href="c1.xhtml" media-type="application/xhtml+xml"/>'
    '</manifest><spine><itemref idref="c1"/></spine></package>'
)
CONTAINER = (
    '<?xml version="1.0"?>'
    '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">'
    '<rootfiles><rootfile full-path="OEBPS/content.opf" '
    'media-type="application/oebps-package+xml"/></rootfiles></container>'
)


def _epub(path, meta: str) -> str:
    """A minimal EPUB whose OPF `<metadata>` is exactly `meta`."""
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr("META-INF/container.xml", CONTAINER)
        z.writestr("OEBPS/content.opf", OPF.format(meta=meta))
        z.writestr("OEBPS/c1.xhtml", "<html><body><p>Text.</p></body></html>")
    return str(path)


# ------------------------------------------------------------------- EPUB


def test_epub_tags_are_read(tmp_path):
    path = _epub(tmp_path / "b.epub", (
        '<dc:identifier id="bookid">urn:uuid:x</dc:identifier>'
        '<dc:title>The Long Way Down</dc:title>'
        '<dc:creator>Ursula Example</dc:creator>'
        '<dc:description>&lt;p&gt;A blurb.&lt;/p&gt;</dc:description>'
        '<dc:publisher>Imaginary Press</dc:publisher>'
        '<dc:language>en-GB</dc:language>'
        '<dc:date>2019-04-02</dc:date>'
        '<dc:subject>Science Fiction</dc:subject>'
        '<dc:subject>Adventure</dc:subject>'
    ))

    meta = _read_embedded_metadata(path, "ebook")

    assert meta["title"] == "The Long Way Down"
    assert meta["author"] == "Ursula Example"
    assert meta["publisher"] == "Imaginary Press"
    assert meta["language"] == "en-GB"
    assert meta["publish_year"] == 2019
    assert meta["genres"] == "Science Fiction,Adventure"
    # The description is HTML in the file and Markdown in the database.
    assert "A blurb." in meta["description"]
    assert "<p>" not in meta["description"]


def test_epub_isbn_and_asin_identifiers_are_picked_apart(tmp_path):
    path = _epub(tmp_path / "b.epub", (
        '<dc:identifier id="bookid">urn:isbn:9780000000001</dc:identifier>'
        '<dc:identifier>urn:asin:B00EXAMPLE</dc:identifier>'
        '<dc:title>Identified</dc:title>'
    ))

    meta = _read_embedded_metadata(path, "ebook")

    assert meta["isbn"] == "9780000000001"
    assert meta["asin"] == "B00EXAMPLE"


def test_epub_series_comes_out_of_the_title(tmp_path):
    """An EPUB's series is recovered from its title when the title carries one.

    Note what is *not* asserted here: ebooklib files a `<meta
    name="calibre:series">` under a synthetic `calibre` namespace rather than
    under `OPF`/`meta`, so the reader's Calibre branch never sees it and the
    title is the only route. Left as found — issue #202/#203 are about how the
    scan runs, not about what it reads.
    """
    path = _epub(tmp_path / "b.epub", (
        '<dc:identifier id="bookid">urn:uuid:x</dc:identifier>'
        '<dc:title>The Quartet #3: Third Movement</dc:title>'
    ))

    meta = _read_embedded_metadata(path, "ebook")

    assert meta["series"] == "The Quartet"
    assert meta["series_index"] == 3


def test_an_unreadable_epub_yields_no_metadata_rather_than_an_error(tmp_path):
    """A corrupt file must fall back to the filename, not fail the whole scan."""
    path = tmp_path / "broken.epub"
    path.write_bytes(b"this is not a zip archive")

    assert _read_embedded_metadata(str(path), "ebook") == {}


def test_a_non_epub_ebook_is_left_alone(tmp_path):
    """PDF/MOBI/AZW3 are in `EBOOK_EXTENSIONS`; only EPUB has tags we can read."""
    path = tmp_path / "book.pdf"
    path.write_bytes(b"%PDF-1.4")

    assert _read_embedded_metadata(str(path), "ebook") == {}


# ------------------------------------------------------------------ audio


class _Tags(dict):
    """Enough of a mutagen `FileType` to drive the tag reader.

    Real `FileType` is a mapping with `.tags` and `.info`, and its *class name*
    is what the reader switches on.
    """

    def __init__(self, mapping, length=None):
        super().__init__(mapping)
        self.tags = self
        self.info = SimpleNamespace(length=length)


class MP4(_Tags):
    pass


class MP3(_Tags):
    pass


class WMA(_Tags):
    """Neither dialect — exercises the generic key-sniffing fallback."""


@pytest.fixture
def audio(tmp_path):
    path = tmp_path / "book.m4b"
    path.write_bytes(b"placeholder")
    return str(path)


@pytest.fixture(autouse=True)
def no_ffprobe(monkeypatch):
    """ffprobe is not a dependency of these assertions; keep the tests hermetic."""
    from routers import library

    monkeypatch.setattr(library, "probe_duration_seconds", lambda *a, **k: None)


def _tagged(monkeypatch, tags):
    from routers import library

    monkeypatch.setattr(library.mutagen, "File", lambda *a, **k: tags)


def test_mp4_atoms_are_read(audio, monkeypatch):
    _tagged(monkeypatch, MP4({
        "\xa9nam": ["The Long Way Down"],
        "\xa9ART": ["Ursula Example"],
        "----:com.apple.iTunes:SERIES": [b"The Quartet"],
        "----:com.apple.iTunes:SERIES-PART": [b"4"],
        "\xa9des": ["<p>A blurb.</p>"],
        "\xa9day": ["2019-04-02"],
        "\xa9gen": ["Science Fiction"],
        "----:com.apple.iTunes:NARRATOR": [b"A Reader"],
        "\xa9pub": ["Imaginary Press"],
    }))

    meta = _read_embedded_metadata(audio, "audiobook")

    assert meta["title"] == "The Long Way Down"
    assert meta["author"] == "Ursula Example"
    assert meta["series"] == "The Quartet"
    assert meta["series_index"] == 4
    assert meta["publish_year"] == 2019
    assert meta["genres"] == "Science Fiction"
    assert meta["narrators"] == "A Reader"
    assert meta["publisher"] == "Imaginary Press"
    assert "A blurb." in meta["description"]


def test_mp4_falls_back_to_the_grouping_atom_for_the_series(audio, monkeypatch):
    _tagged(monkeypatch, MP4({
        "\xa9nam": ["Third Movement"],
        "\xa9grp": ["The Quartet #3"],
        "\xa9cmt": ["A comment blurb."],
        "\xa9com": ["Another Reader"],
    }))

    meta = _read_embedded_metadata(audio, "audiobook")

    assert meta["series"] == "The Quartet"
    assert meta["series_index"] == 3
    assert meta["narrators"] == "Another Reader"
    assert "A comment blurb." in meta["description"]


def test_id3_frames_are_read(audio, monkeypatch):
    _tagged(monkeypatch, MP3({
        "TIT2": "The Long Way Down",
        "TPE1": "Ursula Example",
        "TALB": "The Quartet, Book 2",
        "TCON": "Science Fiction",
        "TDRC": "2019-04-02",
        "TPUB": "Imaginary Press",
        "TCOM": "A Reader",
    }))

    meta = _read_embedded_metadata(audio, "audiobook")

    assert meta["title"] == "The Long Way Down"
    assert meta["author"] == "Ursula Example"
    assert meta["series"] == "The Quartet"
    assert meta["series_index"] == 2
    assert meta["genres"] == "Science Fiction"
    assert meta["publish_year"] == 2019
    assert meta["publisher"] == "Imaginary Press"
    assert meta["narrators"] == "A Reader"


def test_id3_comment_frames_supply_the_description(audio, monkeypatch):
    _tagged(monkeypatch, MP3({
        "TIT2": "Commented",
        "COMM::eng": SimpleNamespace(text=["<p>From a COMM frame.</p>"]),
    }))

    meta = _read_embedded_metadata(audio, "audiobook")

    assert "From a COMM frame." in meta["description"]
    assert "<p>" not in meta["description"]


def test_an_unknown_container_falls_back_to_sniffing_common_keys(audio, monkeypatch):
    _tagged(monkeypatch, WMA({
        "title": ["Sniffed Title"],
        "artist": ["Sniffed Author"],
        "album": ["Sniffed Series"],
    }))

    meta = _read_embedded_metadata(audio, "audiobook")

    assert meta["title"] == "Sniffed Title"
    assert meta["author"] == "Sniffed Author"
    assert meta["series"] == "Sniffed Series"


def test_a_file_with_no_tags_still_yields_its_runtime(audio, monkeypatch):
    """mutagen's `FileType.__len__` is its tag count and it defines no `__bool__`.

    So a file with no tags at all is *falsy*, and a length read guarded on
    truthiness would be dropped for exactly the files where it is the only
    metadata worth having.
    """
    from routers import library

    _tagged(monkeypatch, MP4({}, length=3600.4))
    monkeypatch.setattr(library, "probe_duration_seconds", lambda *a, **k: None)

    meta = _read_embedded_metadata(audio, "audiobook")

    assert meta["duration_seconds"] == 3600
    assert "title" not in meta


def test_ffprobe_wins_over_the_container_header(audio, monkeypatch):
    """ffprobe is the authority; mutagen's `info.length` is the fallback."""
    from routers import library

    _tagged(monkeypatch, MP4({"\xa9nam": ["Timed"]}, length=10.0))
    monkeypatch.setattr(library, "probe_duration_seconds", lambda *a, **k: 7200)

    assert _read_embedded_metadata(audio, "audiobook")["duration_seconds"] == 7200


def test_no_duration_key_at_all_when_neither_source_can_supply_one(audio, monkeypatch):
    """Absent, not zero: callers read "no key" as "leave what is stored alone",
    and an unknown length cleanly disables the audio end zone."""
    _tagged(monkeypatch, MP4({"\xa9nam": ["Untimed"]}, length=None))

    assert "duration_seconds" not in _read_embedded_metadata(audio, "audiobook")


def test_a_container_mutagen_cannot_open_yields_no_metadata(audio, monkeypatch):
    from routers import library

    def _explode(*a, **k):
        raise OSError("truncated container")

    monkeypatch.setattr(library.mutagen, "File", _explode)

    assert _read_embedded_metadata(audio, "audiobook") == {}
