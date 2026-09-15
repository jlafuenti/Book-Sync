"""
A spine item is a content document because of its manifest media-type, not its file extension
(issue #561).

EPUB identifies content documents by `media-type`. Publisher tooling names XHTML files anything,
commonly `.xml` (`<item href="Text/ch1.xml" media-type="application/xhtml+xml"/>`). The zip walk
checked the extension instead, so every such document came out `""`: the book extracted 0
sentences, failed the integrity check as "likely encrypted or corrupt", and transcription refused
it.

The returned list stays one entry per spine itemref. Chapter numbers are spine indices and
sentence indices restart per chapter, so reading a previously skipped document moves no
coordinate in any chapter that was already readable.
"""

import importlib.util
import zipfile
from pathlib import Path

from sqlalchemy import create_engine, text

from services import epub_parser
from services.ebook_integrity import check_ebook_integrity
from services.epub_parser import (
    _extract_epub_documents_via_ebooklib,
    _extract_epub_documents_via_zip,
    extract_book_sentences,
)

XHTML = "application/xhtml+xml"


def _chapter_html(chapter: int, sentences: int = 12) -> str:
    body = " ".join(
        f"Sentence number {i} of chapter {chapter} is ordinary readable prose." for i in range(sentences)
    )
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Axis Test</title></head>'
        f"<body><h1>Chapter {chapter}</h1><p>{body}</p></body></html>"
    )


def _write_epub(path, docs, *, extra_manifest=""):
    """docs: [(href_under_OEBPS, media_type_or_None, content)] in spine order."""

    def _item(i, href, media_type):
        mt = f' media-type="{media_type}"' if media_type is not None else ""
        return f'<item id="id{i}" href="{href}"{mt}/>'

    manifest = "".join(_item(i, href, mt) for i, (href, mt, _) in enumerate(docs))
    spine = "".join(f'<itemref idref="id{i}"/>' for i in range(len(docs)))
    opf = (
        '<?xml version="1.0"?>'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid">'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        '<dc:title>Axis Test</dc:title><dc:identifier id="bookid">urn:uuid:axis</dc:identifier>'
        '<dc:language>en</dc:language></metadata>'
        f"<manifest>{manifest}{extra_manifest}</manifest><spine>{spine}</spine></package>"
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
        for href, _, content in docs:
            z.writestr(f"OEBPS/{href}", content)
    return str(path)


def _xml_book(n=6):
    return [(f"Text/ch{i}.xml", XHTML, _chapter_html(i)) for i in range(n)]


def test_xhtml_documents_with_an_xml_extension_are_read(tmp_path):
    epub = _write_epub(tmp_path / "xml.epub", _xml_book())

    docs = _extract_epub_documents_via_zip(epub)

    assert len(docs) == 6
    assert all(docs), "an application/xhtml+xml document with a .xml name left its spine slot empty"


def test_a_book_of_xml_named_documents_passes_the_integrity_check(tmp_path):
    epub = _write_epub(tmp_path / "xml.epub", _xml_book())

    sentences = extract_book_sentences(epub)
    ok, detail = check_ebook_integrity(epub)

    assert len(sentences) >= 50
    assert ok, detail
    assert sorted({s.chapter for s in sentences}) == list(range(6))


def test_xhtml_documents_with_no_extension_are_read(tmp_path):
    """Seen in real libraries alongside `.xml`: `<item href="Text/part0001" media-type=...>`."""
    docs = [(f"Text/part{i:04d}", XHTML, _chapter_html(i)) for i in range(6)]
    epub = _write_epub(tmp_path / "bare.epub", docs)

    assert all(_extract_epub_documents_via_zip(epub))
    assert check_ebook_integrity(epub)[0]


def test_text_html_media_type_is_a_content_document(tmp_path):
    """EPUB 2 files sometimes declare `text/html`, under any name."""
    epub = _write_epub(tmp_path / "html.epub", [("Text/part1.dat", "text/html", _chapter_html(0))])

    assert all(_extract_epub_documents_via_zip(epub))


def test_media_type_matching_ignores_case_and_parameters(tmp_path):
    epub = _write_epub(
        tmp_path / "params.epub",
        [("Text/ch0.xml", "Application/XHTML+XML; charset=utf-8", _chapter_html(0))],
    )

    assert all(_extract_epub_documents_via_zip(epub))


def test_a_non_content_spine_item_stays_empty_and_keeps_its_slot(tmp_path):
    svg = '<svg xmlns="http://www.w3.org/2000/svg"><text>Sentence one here. Sentence two here.</text></svg>'
    docs = [
        ("Text/ch0.xml", XHTML, _chapter_html(0)),
        ("Images/plate.svg", "image/svg+xml", svg),
        ("Misc/data.xml", "application/xml", "<data><p>Not a content document at all.</p></data>"),
        ("Text/ch3.xml", XHTML, _chapter_html(3)),
    ]
    epub = _write_epub(tmp_path / "mixed.epub", docs)

    extracted = _extract_epub_documents_via_zip(epub)
    sentences = extract_book_sentences(epub)

    assert len(extracted) == 4
    assert extracted[1] == "" and extracted[2] == ""
    assert extracted[0] and extracted[3]
    assert sorted({s.chapter for s in sentences}) == [0, 3]


def test_a_missing_media_type_falls_back_to_the_extension(tmp_path):
    docs = [
        ("Text/ch0.xhtml", None, _chapter_html(0)),
        ("Text/ch1.xml", None, _chapter_html(1)),
    ]
    epub = _write_epub(tmp_path / "nomt.epub", docs)

    extracted = _extract_epub_documents_via_zip(epub)

    assert len(extracted) == 2
    assert extracted[0], "no media-type and an .xhtml name must still be read"
    assert extracted[1] == "", "no media-type and a non-HTML name is not a content document"


def test_an_html_named_document_with_an_odd_media_type_is_still_read(tmp_path):
    """No regression for chapters that read before the fix.

    The extension rule used to admit any `.xhtml/.html/.htm` item whatever its media-type. Dropping
    those now would take sentences out of chapters that already have stored positions and sync
    points, so an HTML-named item still counts even when its media-type is mislabelled.
    """
    epub = _write_epub(tmp_path / "odd.epub", [("Text/ch0.xhtml", "text/xml", _chapter_html(0))])

    assert all(_extract_epub_documents_via_zip(epub))


def test_reading_xml_documents_moves_no_existing_coordinate(tmp_path):
    """Half the chapters `.xml`, half `.xhtml`.

    The `.xhtml` chapters must come out exactly as they did before the fix, when the `.xml`
    ones were empty slots. Only the `.xml` chapters gain sentences.
    """
    mixed = []
    for i in range(6):
        if i % 2:
            mixed.append((f"Text/ch{i}.xml", XHTML, _chapter_html(i)))
        else:
            mixed.append((f"Text/ch{i}.xhtml", XHTML, _chapter_html(i)))
    epub = _write_epub(tmp_path / "mixed.epub", mixed)

    # What the old extension rule produced: the `.xml` slots empty, the rest untouched.
    before = [
        (href, mt, content) if i % 2 == 0 else (f"Text/blank_{i}.xhtml", XHTML, "")
        for i, (href, mt, content) in enumerate(mixed)
    ]
    baseline = extract_book_sentences(_write_epub(tmp_path / "baseline.epub", before))
    assert sorted({s.chapter for s in baseline}) == [0, 2, 4]

    sentences = extract_book_sentences(epub)
    as_tuples = {(s.chapter, s.sentence_index, s.text) for s in sentences}

    for s in baseline:
        assert (s.chapter, s.sentence_index, s.text) in as_tuples
    assert sorted({s.chapter for s in sentences}) == list(range(6))


def test_the_ebooklib_fallback_also_reads_xml_named_documents(tmp_path):
    """Used only when the zip walk raises; it must not reintroduce the gap."""
    epub = _write_epub(tmp_path / "xml.epub", _xml_book(3))

    docs = _extract_epub_documents_via_ebooklib(epub)

    assert len(docs) == 3
    assert all(docs)


def test_an_xml_named_book_still_loads_when_the_zip_walk_fails(tmp_path, monkeypatch):
    epub = _write_epub(tmp_path / "xml.epub", _xml_book(3))

    def boom(_path):
        raise RuntimeError("forced")

    monkeypatch.setattr(epub_parser, "_extract_epub_documents_via_zip", boom)

    assert all(epub_parser._load_epub_documents(epub))


# ---------------------------------------------------------------------------
# The false failures cached on existing servers.
#
# 0019 cleared the "almost no text" rows the href bug wrote, but a Library verify run after 0.2.1
# re-cached the same message for books this bug still broke. `library_check_results` is reused
# while a file's size and mtime are unchanged, so those rows would outlive this fix too.
# ---------------------------------------------------------------------------

_MIGRATION = (
    Path(__file__).resolve().parent.parent / "alembic" / "versions" / "0020_epub_xml_content_docs.py"
)


def _load_migration():
    # By path: `server/alembic/` shares its name with the installed alembic package.
    spec = importlib.util.spec_from_file_location("migration_0020", _MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_migration_follows_0019():
    module = _load_migration()

    assert module.revision == "0020_epub_xml_content_docs"
    assert len(module.revision) <= 32
    assert module.down_revision == "0019_ebook_integrity_href_fix"


def test_the_migration_clears_only_the_false_almost_no_text_failures(tmp_path):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    from models.library_issue import LibraryCheckResult

    engine = create_engine(f"sqlite:///{tmp_path / 'm.db'}")
    LibraryCheckResult.__table__.create(engine)
    rows = [
        # (item_type, item_id, check_type, ok, detail)
        ("ebook", 1, "ebook_integrity", False,
         "ebook produced almost no text (0 sentences) — likely encrypted or corrupt"),
        ("ebook", 2, "ebook_integrity", False,
         "EPUB is DRM-encrypted (Adobe ADEPT) — re-import a DRM-free copy"),
        ("ebook", 3, "ebook_integrity", False,
         "EPUB is not a valid zip (corrupt download) — re-import required"),
        ("ebook", 4, "ebook_integrity", True, "ok (4000 sentences)"),
        ("audiobook", 5, "audio_integrity", False, "ebook produced almost no text (0 sentences)"),
    ]
    with engine.begin() as conn:
        for item_type, item_id, check_type, ok, detail in rows:
            conn.execute(
                text(
                    "INSERT INTO library_check_results (item_type, item_id, check_type, ok, detail, checked_at) "
                    "VALUES (:t, :i, :c, :ok, :d, CURRENT_TIMESTAMP)"
                ),
                {"t": item_type, "i": item_id, "c": check_type, "ok": ok, "d": detail},
            )

    module = _load_migration()
    with engine.begin() as conn:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            module.upgrade()

    with engine.connect() as conn:
        remaining = sorted(r[0] for r in conn.execute(text("SELECT item_id FROM library_check_results")))
    assert remaining == [2, 3, 4, 5]
