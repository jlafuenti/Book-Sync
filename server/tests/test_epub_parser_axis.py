"""
The chapter axis: `EpubSentence.chapter` must be the EPUB **spine index**.

Both readers position themselves by spine index — the web reader via epub.js
`book.spine.items`, Android via Readium `publication.readingOrder`. The parser
used to build its own ordinal instead: an index into *manifest* documents that
produced sentences. That ordinal is meaningless to either reader, and drifted
from spine position three ways — manifest order differing from spine order,
skipped documents compacting the sequence, and the >=3-word sentence filter
deciding what counts as a document.

A sync point that says "chapter 39" has to mean the same thing to the server,
the phone and the browser, or a position resolved through it lands on the
wrong page.
"""

import zipfile

import pytest

from tests.factories import write_epub as _write_epub

from services.epub_parser import (
    _build_sentences_from_documents,
    extract_book_text,
    extract_epub_sentences,
    old_chapter_to_spine_index,
)

PROSE = (
    "<html><body><p>The harbour lay still under a flat grey sky. "
    "Althea counted the ships at anchor and found one missing. "
    "She would have to tell her father before nightfall.</p></body></html>"
)
MORE_PROSE = (
    "<html><body><p>Brashen kept his own counsel on the matter. "
    "The crew had seen worse crossings than this one. "
    "He set his shoulder to the capstan and heaved.</p></body></html>"
)
BLANK = "<html><body></body></html>"
IMAGE_ONLY = '<html><body><img src="plate01.jpg"/></body></html>'


# ---------- _build_sentences_from_documents ----------

def test_chapter_is_the_spine_index_not_a_compacted_counter():
    """Documents that yield no sentences must leave a gap, not shift everything
    down. Compacting is what made sync-map chapters disagree with the readers."""
    docs = [BLANK, PROSE, IMAGE_ONLY, MORE_PROSE]

    sentences = _build_sentences_from_documents(docs)

    chapters = sorted({s.chapter for s in sentences})
    assert chapters == [1, 3]


def test_a_book_with_no_empty_documents_is_unaffected():
    docs = [PROSE, MORE_PROSE]
    sentences = _build_sentences_from_documents(docs)
    assert sorted({s.chapter for s in sentences}) == [0, 1]


def test_sentence_index_still_counts_within_the_document():
    sentences = _build_sentences_from_documents([BLANK, PROSE])
    in_ch1 = [s for s in sentences if s.chapter == 1]
    assert [s.sentence_index for s in in_ch1] == list(range(len(in_ch1)))
    assert len(in_ch1) >= 2


# ---------- old -> new remap (used by the migration) ----------

def test_old_chapter_to_spine_index_maps_the_compacted_ordinal():
    """The migration needs to re-base stored sync points. Old chapter N was the
    Nth document that produced sentences."""
    docs = [BLANK, PROSE, IMAGE_ONLY, MORE_PROSE]
    assert old_chapter_to_spine_index(docs) == [1, 3]


def test_old_chapter_to_spine_index_is_identity_without_skips():
    assert old_chapter_to_spine_index([PROSE, MORE_PROSE]) == [0, 1]


# ---------- end-to-end extraction ----------

def test_extraction_follows_spine_order_not_manifest_order(tmp_path):
    """The manifest declares these back-to-front. Spine order is what the
    readers use, so it is what the sync map must be built on."""
    path = _write_epub(
        tmp_path / "reordered.epub",
        [("a.xhtml", PROSE), ("b.xhtml", MORE_PROSE)],
        manifest_order=["b.xhtml", "a.xhtml"],
    )

    sentences = extract_epub_sentences(path)

    first = next(s for s in sentences if s.chapter == 0)
    assert "harbour" in first.text or "Althea" in first.text
    second = next(s for s in sentences if s.chapter == 1)
    assert "Brashen" in second.text or "crew" in second.text


def test_front_matter_keeps_its_spine_slot(tmp_path):
    """A title page that yields no sentences must not pull chapter one down to
    index 0 — the reader would then open the wrong document."""
    path = _write_epub(
        tmp_path / "frontmatter.epub",
        [("title.xhtml", BLANK), ("ch1.xhtml", PROSE)],
    )

    sentences = extract_epub_sentences(path)

    assert sentences, "expected sentences from the prose document"
    assert {s.chapter for s in sentences} == {1}


def test_unreadable_spine_item_still_occupies_its_index(tmp_path):
    """A spine itemref whose file is missing from the zip is still a spine
    entry as far as the readers are concerned."""
    path = _write_epub(
        tmp_path / "missing.epub",
        [("gone.xhtml", BLANK), ("ch1.xhtml", PROSE)],
    )
    # Drop the first document from the archive, leaving its itemref behind.
    import shutil
    stripped = str(tmp_path / "stripped.epub")
    with zipfile.ZipFile(path) as src, zipfile.ZipFile(stripped, "w") as dst:
        for item in src.infolist():
            if item.filename != "OEBPS/gone.xhtml":
                dst.writestr(item, src.read(item.filename))

    sentences = extract_epub_sentences(stripped)

    assert {s.chapter for s in sentences} == {1}


# ---------- extract_book_text ----------

def test_extract_book_text_returns_every_spine_document(tmp_path):
    """The drift audit's haystack (issue #295): whole-book text, spine order,
    no sentence tokenization."""
    path = _write_epub(
        tmp_path / "text.epub",
        [("ch1.xhtml", PROSE), ("blank.xhtml", BLANK), ("ch2.xhtml", MORE_PROSE)],
    )

    text = extract_book_text(path)

    assert "The harbour lay still under a flat grey sky." in text
    assert "He set his shoulder to the capstan and heaved." in text
    # Spine order, not manifest or alphabetical.
    assert text.index("The harbour lay still") < text.index("Brashen kept his own")


def test_extract_book_text_agrees_with_the_sentence_extractor(tmp_path):
    """Both paths must see the same document, or the audit would flag maps the
    aligner built correctly."""
    path = _write_epub(
        tmp_path / "agree.epub", [("ch1.xhtml", PROSE), ("ch2.xhtml", MORE_PROSE)],
    )

    text = extract_book_text(path)

    for sentence in extract_epub_sentences(path):
        assert sentence.text in text


def test_extract_book_text_dispatches_on_the_mobi_extension(monkeypatch):
    """A pair can still be aligned against a .mobi (the Convert flow exists to
    move them off it), so the audit has to be able to read one."""
    from services import epub_parser

    monkeypatch.setattr(
        epub_parser, "extract_mobi_sentences",
        lambda path: [
            epub_parser.EpubSentence(0, 0, "The harbour lay still."),
            epub_parser.EpubSentence(0, 1, "One ship was missing."),
        ],
    )

    text = extract_book_text("/library/book.MOBI")

    assert text == "The harbour lay still.\nOne ship was missing."


def test_extract_book_text_raises_on_an_unreadable_file(tmp_path):
    path = tmp_path / "broken.epub"
    path.write_bytes(b"not a zip archive at all")

    with pytest.raises(RuntimeError):
        extract_book_text(str(path))
