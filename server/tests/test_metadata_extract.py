"""`services.metadata_extract` — what the library learns about a file (issue #255).

Extracted from `routers/library.py` so it can be driven without HTTP. The
embedded-tag reader has its own file (`test_embedded_metadata.py`) and the
pattern matcher its own (`test_filename_patterns.py`); this one covers the
pieces that had no direct test before the move: the small filename helpers,
the settings lookup with its legacy-key fallback, cover extraction for every
container kind, and the merge `extract_metadata` performs on top of the two
sources.

Audio covers use mutagen-shaped stubs for the same reason the tag tests do:
the reader branches on the *class name* of what `mutagen.File` returns.
"""

import zipfile
from types import SimpleNamespace

import pytest
from mutagen.id3 import APIC
from mutagen.mp4 import MP4Cover

from config import settings
from models.settings import SystemSetting
from routers.settings import DEFAULT_SETTINGS
from services import file_hash
from services import metadata_extract as mx
from tests.factories import write_epub

CHAPTER = "<html><body><p>Chapter one.</p></body></html>"


# ------------------------------------------------------------ pure helpers


def test_sanitize_filename_strips_path_and_shell_characters():
    assert mx.sanitize_filename('A: "Title"/With\\Bad*Chars?<>|') == "A_TitleWithBadChars"
    assert mx.sanitize_filename("") == ""
    assert mx.sanitize_filename(None) == ""


def test_extract_title_from_filename_drops_the_extension_and_audiobook_suffixes():
    assert mx.extract_title_from_filename("Dune (Unabridged).m4b") == "Dune"
    assert mx.extract_title_from_filename("Dune - Audiobook.mp3") == "Dune"
    assert mx.extract_title_from_filename("Plain.epub") == "Plain"


def test_is_path_pattern_means_the_pattern_spans_directories():
    assert mx.is_path_pattern("<Author>/<Title>")
    assert not mx.is_path_pattern("<Author> - <Title>")


def test_compute_file_hash_is_the_composite_hash(tmp_path):
    path = tmp_path / "f.bin"
    path.write_bytes(bytes(range(256)) * 40)

    assert mx.compute_file_hash(str(path)) == file_hash.hash_file(str(path))


# ------------------------------------------------------ get_filename_patterns


async def _configure(db, key, value):
    db.add(SystemSetting(key=key, value=value))
    await db.commit()


async def test_patterns_default_per_type_when_nothing_is_configured(db):
    assert await mx.get_filename_patterns(db, "ebook") == DEFAULT_SETTINGS["ebook_filename_patterns"]
    assert await mx.get_filename_patterns(db, "audiobook") == DEFAULT_SETTINGS["audiobook_filename_patterns"]


async def test_the_typed_key_is_split_on_newlines_and_beats_the_legacy_key(db):
    await _configure(db, "audiobook_filename_patterns", "<Author>/<Title>\n<Title>")
    await _configure(db, "filename_patterns", "<Legacy>")

    assert await mx.get_filename_patterns(db, "audiobook") == ["<Author>/<Title>", "<Title>"]


async def test_the_legacy_key_is_used_only_when_the_typed_key_is_absent(db):
    await _configure(db, "filename_patterns", "<Legacy>")

    assert await mx.get_filename_patterns(db, "ebook") == ["<Legacy>"]


async def test_an_empty_typed_value_falls_back_to_the_defaults_not_the_legacy_key(db):
    """Pins the current shape: an existing-but-empty row skips the legacy lookup."""
    await _configure(db, "ebook_filename_patterns", "")
    await _configure(db, "filename_patterns", "<Legacy>")

    assert await mx.get_filename_patterns(db, "ebook") == DEFAULT_SETTINGS["ebook_filename_patterns"]


# ------------------------------------------------------------ cover images


@pytest.fixture
def covers_dir(monkeypatch, tmp_path):
    path = tmp_path / "covers"
    monkeypatch.setattr(settings, "covers_dir", str(path))
    return path


def _epub_with_image(path, *, href, properties=None, data=b"\x89PNG-bytes"):
    props = f' properties="{properties}"' if properties else ""
    opf = (
        '<?xml version="1.0"?>'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid">'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        '<dc:title>Covered</dc:title><dc:identifier id="bookid">urn:uuid:c</dc:identifier>'
        "</metadata><manifest>"
        '<item id="c1" href="c1.xhtml" media-type="application/xhtml+xml"/>'
        f'<item id="img" href="{href}" media-type="image/png"{props}/>'
        '</manifest><spine><itemref idref="c1"/></spine></package>'
    )
    container = (
        '<?xml version="1.0"?>'
        '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">'
        '<rootfiles><rootfile full-path="OEBPS/content.opf" '
        'media-type="application/oebps-package+xml"/></rootfiles></container>'
    )
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr("META-INF/container.xml", container)
        z.writestr("OEBPS/content.opf", opf)
        z.writestr("OEBPS/c1.xhtml", CHAPTER)
        z.writestr(f"OEBPS/{href}", data)
    return str(path)


def test_an_epub_cover_item_is_saved_under_a_sanitized_title(covers_dir, tmp_path):
    epub = _epub_with_image(tmp_path / "b.epub", href="art/front.png", properties="cover-image")

    url = mx._extract_and_save_cover(epub, "ebook", 7, "The Title: Part 1")

    assert url == "/api/files/covers/The_Title_Part_1_7.png"
    assert (covers_dir / "The_Title_Part_1_7.png").read_bytes() == b"\x89PNG-bytes"


def test_an_ordinary_image_named_cover_is_used_when_nothing_is_marked(covers_dir, tmp_path):
    epub = _epub_with_image(tmp_path / "b.epub", href="images/cover.png")

    assert mx._extract_and_save_cover(epub, "ebook", 8, "T") == "/api/files/covers/T_8.png"


def test_an_epub_without_a_cover_yields_none(covers_dir, tmp_path):
    epub = write_epub(str(tmp_path / "plain.epub"), [("ch1.xhtml", CHAPTER)])

    assert mx._extract_and_save_cover(epub, "ebook", 9, "T") is None
    assert not any(covers_dir.iterdir())


class MP4(dict):
    pass


class MP3:
    def __init__(self, tags):
        self.tags = tags


class FLAC:
    def __init__(self, pictures):
        self.pictures = pictures


@pytest.fixture
def audio(tmp_path):
    path = tmp_path / "book.m4b"
    path.write_bytes(b"not really an audio container")
    return str(path)


def _opened(monkeypatch, obj):
    monkeypatch.setattr(mx.mutagen, "File", lambda *a, **k: obj)


def test_mp4_covr_atom_with_png_format_and_no_title_names_the_file_by_type(covers_dir, audio, monkeypatch):
    _opened(monkeypatch, MP4({"covr": [MP4Cover(b"png-bytes", imageformat=MP4Cover.FORMAT_PNG)]}))

    assert mx._extract_and_save_cover(audio, "audiobook", 3) == "/api/files/covers/audiobook_3.png"
    assert (covers_dir / "audiobook_3.png").read_bytes() == b"png-bytes"


def test_mp3_apic_frame_is_the_cover(covers_dir, audio, monkeypatch):
    frame = APIC(encoding=3, mime="image/jpeg", type=3, desc="", data=b"jpeg-bytes")
    _opened(monkeypatch, MP3(tags={"APIC:": frame}))

    assert mx._extract_and_save_cover(audio, "audiobook", 4, "Song") == "/api/files/covers/Song_4.jpg"
    assert (covers_dir / "Song_4.jpg").read_bytes() == b"jpeg-bytes"


def test_flac_pictures_are_the_cover(covers_dir, audio, monkeypatch):
    _opened(monkeypatch, FLAC(pictures=[SimpleNamespace(data=b"pic", mime="image/png")]))

    assert mx._extract_and_save_cover(audio, "audiobook", 5, "F") == "/api/files/covers/F_5.png"


def test_a_container_without_art_or_that_mutagen_cannot_open_yields_none(covers_dir, audio, monkeypatch):
    _opened(monkeypatch, MP4({"\xa9nam": ["No art"]}))
    assert mx._extract_and_save_cover(audio, "audiobook", 6, "T") is None

    _opened(monkeypatch, None)
    assert mx._extract_and_save_cover(audio, "audiobook", 6, "T") is None


def test_a_reader_failure_is_logged_and_yields_none(covers_dir, audio, monkeypatch, caplog):
    def _explode(*a, **k):
        raise OSError("truncated")

    monkeypatch.setattr(mx.mutagen, "File", _explode)

    assert mx._extract_and_save_cover(audio, "audiobook", 6, "T") is None
    assert "Failed to extract cover" in caplog.text


# -------------------------------------------------------- extract_metadata


@pytest.fixture
def library(tmp_path, monkeypatch):
    """An ebook library root with one file in an `Author/Title.epub` layout."""
    root = tmp_path / "ebooks"
    (root / "Ann Author").mkdir(parents=True)
    path = write_epub(str(root / "Ann Author" / "Some Title.epub"), [("ch1.xhtml", CHAPTER)])
    return root, path


def _reads(monkeypatch, tags):
    monkeypatch.setattr(mx, "_read_embedded_metadata", lambda filepath, file_type: tags)


async def test_embedded_tags_win_over_the_pattern_and_the_source_says_both(db, library, monkeypatch):
    root, path = library
    await _configure(db, "ebook_filename_patterns", "<Author>/<Title>")
    _reads(monkeypatch, {"title": "Embedded Title", "isbn": "978", "duration_seconds": 12})

    meta = await mx.extract_metadata(path, "ebook", db, library_root=str(root))

    assert meta["title"] == "Embedded Title"
    assert meta["author"] == "Ann Author"          # from the path pattern
    assert meta["isbn"] == "978"
    assert meta["duration_seconds"] == 12
    assert meta["_metadata_source"] == "embedded+pattern"
    assert meta["_metadata_pattern"] == "<Author>/<Title>"


async def test_a_readable_duration_alone_does_not_flip_the_source(db, library, monkeypatch):
    root, path = library
    await _configure(db, "ebook_filename_patterns", "<Author>/<Title>")
    _reads(monkeypatch, {"duration_seconds": 9})

    meta = await mx.extract_metadata(path, "ebook", db, library_root=str(root))

    assert meta["duration_seconds"] == 9
    assert meta["_metadata_source"] == "pattern"


async def test_without_a_library_root_the_path_pattern_cannot_match_and_the_stem_is_the_title(
    db, library, monkeypatch
):
    _, path = library
    await _configure(db, "ebook_filename_patterns", "<Author>/<Title>")
    _reads(monkeypatch, {"author": "Tagged Author"})

    meta = await mx.extract_metadata(path, "ebook", db)

    assert meta["title"] == "Some Title"
    assert meta["author"] == "Tagged Author"
    assert meta["_metadata_source"] == "embedded"
    assert meta["_metadata_pattern"] is None
