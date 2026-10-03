"""
A sentence never spans two block elements (issue #774).

The extractor used to flatten a whole chapter to one string and tokenise it with a
single `nltk.sent_tokenize` call. A paragraph with no sentence-final punctuation
(dialogue that trails off, a heading) then fused with the next block into one
"sentence", which blurs the sync points the readers jump to. All text here is
invented.
"""

import sys
import types

import pytest

from services import epub_parser
from services.epub_parser import (
    _build_sentences_from_documents,
    _extract_blocks_from_html,
    _extract_text_from_html,
    _split_block_into_sentences,
    _split_into_sentences,
)
from services.sync_matcher import normalize_for_search

LQ, RQ = "“", "”"


def _doc(body: str) -> str:
    return f"<html><head><title>T</title></head><body>{body}</body></html>"


def _texts(doc: str):
    return [s.text for s in _build_sentences_from_documents([doc])]


# --- the bug itself ---------------------------------------------------------


@pytest.mark.parametrize("ending", ["…" + RQ, "—" + RQ])
def test_unpunctuated_paragraph_is_not_fused_with_the_next(ending):
    first = f"<p>{LQ}I meant to say that the harbour was quiet when I left{ending}</p>"
    second = (
        f"<p>{LQ}Nobody believes the harbour was ever quiet,{RQ} said the old "
        f"ferryman with a shrug.</p>"
    )

    sentences = _texts(_doc(first + second))

    assert len(sentences) >= 2
    for s in sentences:
        assert not ("meant to say" in s and "ferryman" in s)
    assert any("meant to say" in s for s in sentences)
    assert any("ferryman" in s for s in sentences)


def test_heading_with_three_words_is_its_own_sentence():
    html = _doc("<h2>A Long Way Home</h2><p>The road wound down toward the sea.</p>")

    assert _texts(html) == ["A Long Way Home", "The road wound down toward the sea."]


def test_two_word_heading_yields_nothing_and_is_not_fused():
    html = _doc("<h2>Chapter Seven</h2><p>The road wound down toward the sea.</p>")

    sentences = _build_sentences_from_documents([html])

    assert [s.text for s in sentences] == ["The road wound down toward the sea."]
    assert sentences[0].chapter_title == "Chapter Seven"


# --- inline markup ----------------------------------------------------------


def test_inline_markup_stays_inside_one_sentence_with_single_spaces():
    html = _doc(
        "<p>The <i>quick</i> brown <span class='x'>fox</span> jumped over "
        "the <a href='#n'>garden</a> fence.</p>"
    )

    assert _texts(html) == ["The quick brown fox jumped over the garden fence."]


# --- inline boundaries add no space (issue #799) ----------------------------


@pytest.mark.parametrize(
    "markup, expected",
    [
        ("<p><b>Name</b>: some words follow here</p>", "Name: some words follow here"),
        ("<p><i>W</i>ord by word it grew longer</p>", "Word by word it grew longer"),
        ("<p>A <i>quiet</i>, <b>slow</b>; steady walk</p>", "A quiet, slow; steady walk"),
        # real whitespace in the source still yields exactly one space
        ("<p>one <i>two</i> three</p>", "one two three"),
        ("<p>one<i> two</i> three</p>", "one two three"),
        ("<p>one <i>two </i>three</p>", "one two three"),
        # whitespace-only text between inline elements is one space, not none
        ("<p><i>left</i> <i>right</i> side</p>", "left right side"),
        ("<p><i>left</i>\n  \t<i>right</i> side</p>", "left right side"),
        # directly adjacent inline elements add nothing
        ("<p><i>left</i><i>right</i> side</p>", "leftright side"),
        # nested inline elements
        (
            "<p><b><i>Na</i>me</b><span>: <em>nested</em></span> words</p>",
            "Name: nested words",
        ),
        # a single <br> still separates words
        ("<p>first line<br/>second line</p>", "first line second line"),
        ("<p>first<br>second</p>", "first second"),
        ("<p>first<br/>\nsecond</p>", "first second"),
    ],
)
def test_inline_boundaries_add_no_space(markup, expected):
    assert _extract_blocks_from_html(_doc(markup)) == [expected]


def test_block_boundaries_still_separate_blocks():
    html = _doc("<div>alpha<p>beta</p>gamma</div><p>delta</p>")

    assert _extract_blocks_from_html(html) == ["alpha", "beta", "gamma", "delta"]


def test_sentence_text_has_no_space_before_inline_punctuation():
    html = _doc("<p><b>Speaker</b>: the harbour was quiet that night. <i>Wh</i>en it rained, nobody left.</p>")

    assert _texts(html) == [
        "Speaker: the harbour was quiet that night.",
        "When it rained, nobody left.",
    ]


def test_no_sentence_contains_a_newline():
    html = _doc(
        "<p>First line of the verse<br/>second line of the verse<br/>third line "
        "of the verse.</p><div>Plain loose text sits here for a moment.</div>"
    )

    for text in _texts(html):
        assert "\n" not in text


# --- property over a moderately complex document ---------------------------


COMPLEX = _doc(
    "<div class='chapter'>"
    "<h1>The Salt Road</h1>"
    "Loose words opening the chapter before any paragraph appears."
    "<p>The caravan left at dawn, <em>slowly</em>, with every wagon "
    "<b>full</b> of salt. Nobody spoke for an hour.</p>"
    f"<p>{LQ}Will it rain today?{RQ} asked the smallest child.</p>"
    "<ul><li>Three barrels of water for the trip</li>"
    "<li>Four coils of rope and a lantern</li></ul>"
    "<blockquote><p>Travel light, travel early, and never trust a short cut."
    "</p><p>The hills forgive nothing at all</p></blockquote>"
    "<table><tr><td>Day one covered twelve miles</td>"
    "<td>Day two covered nine miles</td></tr></table>"
    "<p>The last verse trails off<br/><br/>and then the singing stops "
    "altogether.</p>"
    "<div><div><p>Deeply nested paragraph with enough words.</p></div></div>"
    "</div>"
)


def test_every_sentence_lies_inside_exactly_one_block():
    blocks = _extract_blocks_from_html(COMPLEX)
    assert len(blocks) == len(set(blocks)), "fixture blocks must be distinct"

    sentences = _texts(COMPLEX)
    assert sentences
    for s in sentences:
        assert "\n" not in s
        assert sum(1 for b in blocks if s in b) == 1, s


# --- short fragments --------------------------------------------------------


def test_trailing_fragment_joins_the_previous_sentence():
    block = f"{LQ}Why would you say that?{RQ} Anna asked."

    result = _split_block_into_sentences(block)

    assert len(result) == 1
    assert "Why would you say that?" in result[0]
    assert result[0].endswith("Anna asked.")


def test_leading_fragment_joins_the_following_sentence():
    result = _split_block_into_sentences(
        "Stop. The wagons rolled slowly across the frozen river."
    )

    assert result == ["Stop. The wagons rolled slowly across the frozen river."]


def test_block_with_fewer_than_three_words_yields_nothing():
    assert _split_block_into_sentences("Chapter Seven") == []
    assert _split_block_into_sentences("Yes.") == []


def test_block_of_only_short_sentences_is_kept_when_it_has_three_words():
    assert _split_block_into_sentences("Go on. Now.") == ["Go on. Now."]


# --- <br> -------------------------------------------------------------------


def test_single_br_keeps_one_block():
    html = _doc("<p>Roses are red<br/>and violets are blue<br>so say the old songs.</p>")

    assert _extract_blocks_from_html(html) == [
        "Roses are red and violets are blue so say the old songs."
    ]


def test_double_br_splits_into_two_blocks():
    html = _doc("<p>The first thought ended there<br/>\n<br/>The second began here.</p>")

    assert _extract_blocks_from_html(html) == [
        "The first thought ended there",
        "The second began here.",
    ]


# --- block structure --------------------------------------------------------


def test_loose_text_in_a_div_between_paragraphs_is_its_own_block():
    html = _doc("<div><p>Alpha beta gamma.</p>stray words between<p>Delta epsilon zeta.</p></div>")

    assert _extract_blocks_from_html(html) == [
        "Alpha beta gamma.",
        "stray words between",
        "Delta epsilon zeta.",
    ]


def test_nested_blocks_do_not_merge():
    html = _doc("<div><p>Alpha beta gamma.</p><p>Delta epsilon zeta.</p></div>")

    assert _extract_blocks_from_html(html) == ["Alpha beta gamma.", "Delta epsilon zeta."]


def test_script_style_and_head_are_ignored_and_empty_blocks_skipped():
    html = (
        "<html><head><title>Ignored title</title><style>p {}</style></head><body>"
        "<script>var x = 1;</script><p>   </p><p>Kept paragraph here.</p></body></html>"
    )

    assert _extract_blocks_from_html(html) == ["Kept paragraph here."]


def test_comments_are_not_text():
    html = _doc("<p>Before <!-- hidden note --> after the comment.</p>")

    assert _extract_blocks_from_html(html) == ["Before after the comment."]


# --- _build_sentences_from_documents ---------------------------------------


def test_build_uses_spine_index_and_counts_sentences_per_document():
    docs = [
        _doc("<p>The first chapter opens quietly in the dark. It ends just as quietly.</p>"),
        "",
        _doc("<h1>Part Two</h1><p>Rain began at noon. The streets emptied fast.</p>"),
    ]

    sentences = _build_sentences_from_documents(docs)

    assert [(s.chapter, s.sentence_index) for s in sentences] == [
        (0, 0),
        (0, 1),
        (2, 0),
        (2, 1),
    ]
    assert sentences[0].chapter_title == ""  # first block is a full sentence (> 10 words)
    assert sentences[2].chapter_title == "Part Two"
    assert sentences[2].text == "Rain began at noon."


def test_document_with_only_tiny_blocks_is_skipped_without_shifting_chapters():
    docs = [_doc("<h1>Title</h1>"), _doc("<p>A real paragraph with words.</p>")]

    sentences = _build_sentences_from_documents(docs)

    assert [s.chapter for s in sentences] == [1]


def test_long_first_block_is_not_a_chapter_title():
    long_first = "word " * 11 + "end."
    sentences = _build_sentences_from_documents([_doc(f"<p>{long_first}</p>")])

    assert sentences[0].chapter_title == ""


# --- normalisation invariance ----------------------------------------------


def test_normalised_text_matches_the_legacy_extraction():
    """The web client mirrors the old get_text("\\n") extraction, so what the
    matcher sees must not change when only the grouping does."""
    doc = _doc(
        "<h1>The Long Voyage Out</h1>"
        "<p>The <i>old</i> ship creaked as the <span>crew</span> hauled the "
        "sails. Nobody on deck said a word.</p>"
        "<div><p>Far below, the <b>engine</b> room was hot and loud.</p>"
        "<p>She <em>never</em> looked back at the harbour again.</p></div>"
    )

    new = [s.text for s in _build_sentences_from_documents([doc])]
    old = _split_into_sentences(_extract_text_from_html(doc))

    assert normalize_for_search(" ".join(new)) == normalize_for_search(" ".join(old))


# --- MOBI path --------------------------------------------------------------


def test_mobi_html_is_split_per_block(monkeypatch, tmp_path):
    workdir = tmp_path / "unpacked"
    workdir.mkdir()
    html_path = workdir / "book.html"
    html_path.write_text(
        _doc(
            "<p>The kettle was still warm when we got back and "
            "nobody had touched the door</p>"
            "<p>Then somebody laughed out loud in the hall.</p>"
        ),
        encoding="utf-8",
    )
    fake = types.SimpleNamespace(extract=lambda path: (str(workdir), str(html_path)))
    monkeypatch.setitem(sys.modules, "mobi", fake)

    sentences = epub_parser.extract_mobi_sentences("/library/book.mobi")

    assert [s.chapter for s in sentences] == [0, 0]
    assert [s.sentence_index for s in sentences] == [0, 1]
    assert "somebody laughed" not in sentences[0].text
    assert "kettle" not in sentences[1].text
