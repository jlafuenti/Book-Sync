"""
Manifest hrefs are URLs and must be percent-decoded before they name a zip entry (issue #554).

An EPUB whose content files have spaces in their names (Calibre/Sigil split output with the book
title in the file name) references them percent-encoded: `Text/Axis%20Test_split_002.html` for
the zip entry `OEBPS/Text/Axis Test_split_002.html`. The zip walk read the encoded name, got a
KeyError, and left the spine slot empty. A book named that way throughout extracted 0 sentences,
failed the integrity check as "likely encrypted or corrupt", and Troubleshoot filed it under
DRM. epub.js and Readium decode hrefs, so the same book reads fine in both clients.
"""

import importlib.util
import zipfile
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from services import epub_parser
from services.ebook_integrity import check_ebook_integrity
from services.epub_parser import (
    _extract_epub_documents_via_ebooklib,
    _extract_epub_documents_via_zip,
    extract_book_sentences,
)


def _chapter_html(chapter: int, sentences: int = 12) -> str:
    body = " ".join(
        f"Sentence number {i} of chapter {chapter} is ordinary readable prose." for i in range(sentences)
    )
    return f"<html><body><h1>Chapter {chapter}</h1><p>{body}</p></body></html>"


def _write_epub(path, docs):
    """docs: [(zip_name_under_OEBPS, manifest_href, html)] in spine order."""
    manifest = "".join(
        f'<item id="id{i}" href="{href}" media-type="application/xhtml+xml"/>'
        for i, (_, href, _) in enumerate(docs)
    )
    spine = "".join(f'<itemref idref="id{i}"/>' for i in range(len(docs)))
    opf = (
        '<?xml version="1.0"?>'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid">'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        '<dc:title>Axis Test</dc:title><dc:identifier id="bookid">urn:uuid:axis</dc:identifier>'
        '<dc:language>en</dc:language></metadata>'
        f"<manifest>{manifest}</manifest><spine>{spine}</spine></package>"
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
        for name, _, html in docs:
            z.writestr(f"OEBPS/{name}", html)
    return str(path)


def _escaped_book(n=6):
    return [
        (f"Text/Axis Test_split_00{i}.html", f"Text/Axis%20Test_split_00{i}.html", _chapter_html(i))
        for i in range(n)
    ]


def test_a_book_named_with_spaces_throughout_extracts_every_document(tmp_path):
    epub = _write_epub(tmp_path / "escaped.epub", _escaped_book())

    docs = _extract_epub_documents_via_zip(epub)

    assert len(docs) == 6
    assert all(docs), "a percent-encoded href left its spine slot empty"


def test_a_book_named_with_spaces_passes_the_integrity_check(tmp_path):
    epub = _write_epub(tmp_path / "escaped.epub", _escaped_book())

    sentences = extract_book_sentences(epub)
    ok, detail = check_ebook_integrity(epub)

    assert len(sentences) >= 50
    assert ok, detail
    assert sorted({s.chapter for s in sentences}) == list(range(6))


def test_decoding_moves_no_existing_coordinate(tmp_path):
    """Half the chapters escaped, half plain.

    Chapter numbers are spine indices and sentence indices restart per chapter, so the plain
    chapters must come out exactly as they did before the fix. Only the escaped chapters gain
    sentences. This is what makes the fix safe for positions and sync maps already stored.
    """
    mixed = []
    for i in range(6):
        if i % 2:
            mixed.append((f"Text/Axis Test_{i}.html", f"Text/Axis%20Test_{i}.html", _chapter_html(i)))
        else:
            mixed.append((f"Text/plain_{i}.html", f"Text/plain_{i}.html", _chapter_html(i)))
    epub = _write_epub(tmp_path / "mixed.epub", mixed)

    # What the plain chapters extract to when nothing is escaped at all.
    plain_only = [(n, h, html) if i % 2 == 0 else (f"Text/blank_{i}.html", f"Text/blank_{i}.html", "")
                  for i, (n, h, html) in enumerate(mixed)]
    baseline = extract_book_sentences(_write_epub(tmp_path / "baseline.epub", plain_only))

    sentences = extract_book_sentences(epub)
    as_tuples = {(s.chapter, s.sentence_index, s.text) for s in sentences}

    for s in baseline:
        assert (s.chapter, s.sentence_index, s.text) in as_tuples
    assert sorted({s.chapter for s in sentences}) == list(range(6))


def test_a_fragment_in_an_href_still_names_the_file(tmp_path):
    docs = [("Text/Axis Test.html", "Text/Axis%20Test.html#start", _chapter_html(0))]
    epub = _write_epub(tmp_path / "fragment.epub", docs)

    assert _extract_epub_documents_via_zip(epub) != [""]


@pytest.mark.parametrize(
    "zip_name, href",
    [
        ("Text/Café.html", "Text/Caf%C3%A9.html"),
        ("Text/Axis & Test.html", "Text/Axis%20%26%20Test.html"),
    ],
)
def test_other_escaped_characters_resolve(tmp_path, zip_name, href):
    epub = _write_epub(tmp_path / "chars.epub", [(zip_name, href, _chapter_html(0))])

    assert all(_extract_epub_documents_via_zip(epub))


def test_the_ebooklib_fallback_also_reads_escaped_hrefs(tmp_path):
    """Used only when the zip walk raises; it must not reintroduce the gap."""
    epub = _write_epub(tmp_path / "escaped.epub", _escaped_book(3))

    docs = _extract_epub_documents_via_ebooklib(epub)

    assert len(docs) == 3
    assert all(docs)


def test_an_escaped_book_still_loads_when_the_zip_walk_fails(tmp_path, monkeypatch):
    epub = _write_epub(tmp_path / "escaped.epub", _escaped_book(3))

    def boom(_path):
        raise RuntimeError("forced")

    monkeypatch.setattr(epub_parser, "_extract_epub_documents_via_zip", boom)

    assert all(epub_parser._load_epub_documents(epub))


# ---------------------------------------------------------------------------
# The false failures already cached on existing servers.
#
# `library_check_results` is reused while a file's size and mtime are unchanged, so the rows the
# bug wrote would outlive the fix. The migration drops exactly those; the next Library verify
# re-checks the files.
# ---------------------------------------------------------------------------

_MIGRATION = (
    Path(__file__).resolve().parent.parent / "alembic" / "versions" / "0019_ebook_integrity_href_fix.py"
)


def _run_migration_upgrade(engine):
    # By path: `server/alembic/` shares its name with the installed alembic package.
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    spec = importlib.util.spec_from_file_location("migration_0019", _MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with engine.begin() as conn:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            module.upgrade()


def test_the_migration_clears_only_the_false_almost_no_text_failures(tmp_path):
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

    _run_migration_upgrade(engine)

    with engine.connect() as conn:
        remaining = sorted(r[0] for r in conn.execute(text("SELECT item_id FROM library_check_results")))
    assert remaining == [2, 3, 4, 5]
