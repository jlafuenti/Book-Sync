"""
Whole-book text extraction joins inline text the way stored sentences do
(issue #810, follow-up to #799).

`_extract_blocks_from_html` (what the stored sentences are built from) adds
nothing at an inline element boundary: `<i>W</i>ord` is `Word`. The older
`get_text("\\n")` extraction put a break at every tag, so `extract_book_text` /
`extract_spine_chapter_texts` read `W\\nord`. Those two feed the sync-map drift
audit (its needle search) and the plausibility word count, so a stored sentence
with an inline-split word was not found in its own chapter, and the word was
counted twice.

Migration 0004 still needs the old extraction byte for byte: it already ran in
production and a fresh install replays it. That path is pinned at the bottom.
"""

import pytest

from services import sync_map_audit
from services.epub_parser import (
    _extract_text_from_html,
    _legacy_extract_text_from_html,
    extract_book_text,
    extract_epub_sentences,
    extract_spine_chapter_texts,
    old_chapter_to_spine_index,
)
from services.pair_plausibility import estimate_word_count
from services.sync_matcher import normalize_for_search
from tests.factories import make_sync_map, write_epub
from models.book import AudioBook, BookPair, EBook, PairStatus


def _doc(body: str) -> str:
    return f"<html><head><title>t</title></head><body>{body}</body></html>"


SPLIT_WORD = _doc(
    "<p>The <i>W</i>ord carried across the water and every sailor on the "
    "quay turned to listen.</p>"
)


# ---------- the text itself ----------

def test_inline_boundary_inside_a_word_adds_nothing():
    assert _extract_text_from_html(_doc("<p><i>W</i>ord</p>")) == "Word"


def test_inline_boundary_before_punctuation_adds_nothing():
    assert _extract_text_from_html(_doc("<p><b>Name</b>: x</p>")) == "Name: x"


def test_whitespace_between_inline_elements_is_one_space():
    assert _extract_text_from_html(_doc("<p><i>one</i> <b>two</b></p>")) == "one two"


def test_separate_blocks_stay_on_separate_lines():
    text = _extract_text_from_html(_doc("<h1>Title</h1><p>First.</p><div><p>Second.</p></div>"))
    assert text.splitlines() == ["Title", "First.", "Second."]


def test_br_still_separates_words():
    assert _extract_text_from_html(_doc("<p>one<br/>two</p>")) == "one two"


def test_script_style_and_head_are_dropped():
    html = ("<html><head><title>skip me</title><style>p{}</style></head>"
            "<body><script>var x=1;</script><p>Kept text.</p></body></html>")
    assert _extract_text_from_html(html) == "Kept text."


def test_empty_document_is_empty_text():
    assert _extract_text_from_html(_doc("")) == ""


# ---------- through a real EPUB ----------

def _epub(tmp_path, docs, name="book.epub"):
    return write_epub(
        tmp_path / name,
        [(f"c{i}.xhtml", d) for i, d in enumerate(docs)],
    )


def test_spine_chapter_texts_join_inline_text(tmp_path):
    path = _epub(tmp_path, [_doc("<p><i>W</i>ord one.</p>"), _doc("<p><b>Name</b>: x</p>")])
    assert extract_spine_chapter_texts(path) == ["Word one.", "Name: x"]


def test_extract_book_text_joins_inline_text_and_keeps_chapter_breaks(tmp_path):
    path = _epub(tmp_path, [_doc("<p><i>W</i>ord one.</p>"), _doc("<p><b>Name</b>: x</p>")])
    assert extract_book_text(path) == "Word one.\nName: x"


def test_every_stored_sentence_is_found_in_its_own_chapter(tmp_path):
    path = _epub(tmp_path, [
        _doc("<p>Alpha <i>b</i>eta gamma delta epsilon zeta.</p>"
             "<p><b>Name</b>: a short remark about the weather today.</p>"),
        SPLIT_WORD,
    ])
    chapters = [normalize_for_search(t) for t in extract_spine_chapter_texts(path)]
    sentences = extract_epub_sentences(path)
    assert sentences
    for s in sentences:
        assert normalize_for_search(s.text) in chapters[s.chapter], s.text


# ---------- word count ----------

async def test_a_split_word_is_counted_once(tmp_path):
    path = _epub(tmp_path, [_doc("<p>The <i>W</i>ord carried far.</p>")])
    # "The Word carried far." is four words; the old extraction said five.
    assert await estimate_word_count(path) == 4


# ---------- the drift audit ----------

async def test_audit_finds_a_sentence_with_an_inline_split_word(db, tmp_path):
    path = _epub(tmp_path, [SPLIT_WORD])
    eb = EBook(title="Axis Test", filename="book.epub", file_path=path)
    ab = AudioBook(title="Axis Test", filename="book.m4b", file_path=str(tmp_path / "book.m4b"))
    db.add_all([eb, ab])
    await db.flush()
    pair = BookPair(ebook_id=eb.id, audiobook_id=ab.id, status=PairStatus.SYNCED)
    db.add(pair)
    await db.commit()
    await db.refresh(pair)

    stored = [s.text for s in extract_epub_sentences(path)]
    assert any("Word carried" in t for t in stored)
    await make_sync_map(db, pair.id, [(0, i, i * 5000, t) for i, t in enumerate(stored)])

    [row] = await sync_map_audit.audit_sync_maps(db)

    assert row["sampled"] == len(stored)
    assert row["hit_rate"] == 1.0
    assert row["status"] == "healthy"


# ---------- migration 0004 keeps the legacy extraction ----------

def test_legacy_extraction_still_breaks_at_every_tag():
    """Pinned: migration 0004 already ran in production, and a fresh install
    replays it. Its chapter re-basing must see exactly what it saw then."""
    assert _legacy_extract_text_from_html(_doc("<p><i>W</i>ord</p>")) == "W\nord"
    assert _legacy_extract_text_from_html(
        _doc("<h1>Title</h1><p>The <b>old</b> ship.</p>")
    ) == "Title\nThe\nold\nship."


def test_old_chapter_to_spine_index_is_unchanged_for_migration_0004():
    """`<b>A</b><i>B</i><u>C</u>` is three lines (three words) to the legacy
    extraction, so that document counts as one that produced sentences and
    keeps its slot in the old numbering. The new extraction would read it as
    the single word `ABC` and drop it, which would shift every later chapter
    in the migration's remap."""
    prose = _doc("<p>The harbour lay still under a flat grey sky.</p>")
    split_only = _doc("<p><b>A</b><i>B</i><u>C</u></p>")
    blank = _doc("")
    assert old_chapter_to_spine_index([prose, split_only, blank, prose]) == [0, 1, 3]
    # The new extraction really does disagree, which is why the legacy one is kept.
    assert _extract_text_from_html(split_only) == "ABC"
