"""
`services.word_timing.word_starts_for`: per-token audio start times for one
ebook sentence, derived from the worker's word timing (issue #835).

All text here is synthetic.
"""

import pytest

from services.word_timing import word_starts_for


def _words(tokens, start=1000, step=300):
    return [
        {"text": t, "start_ms": start + i * step, "end_ms": start + i * step + step - 20}
        for i, t in enumerate(tokens)
    ]


def _check_invariants(text, starts, start_ms, end_ms):
    assert starts is not None
    assert len(starts) == len(text.split())
    assert all(isinstance(x, int) for x in starts)
    assert starts == sorted(starts)
    assert all(start_ms <= x <= end_ms for x in starts)


def test_identical_sentences_take_each_words_start():
    text = "The red fox ran home."
    words = _words(text.split())
    starts = word_starts_for(text, words, 1000, 2500)
    assert starts == [w["start_ms"] for w in words]


def test_an_asr_error_in_the_middle_is_interpolated():
    text = "The red fox ran quickly home."
    heard = ["The", "red", "box", "ran", "quickly", "home."]
    words = _words(heard)
    starts = word_starts_for(text, words, 1000, 2800)
    _check_invariants(text, starts, 1000, 2800)
    # "fox" was misheard as "box": it sits between its neighbours' starts.
    assert words[1]["start_ms"] < starts[2] < words[3]["start_ms"]
    # Every other token is anchored exactly.
    for i in (0, 1, 3, 4, 5):
        assert starts[i] == words[i]["start_ms"]


def test_whisper_shorter_than_the_ebook_interpolates_the_tail():
    text = "One two three four five six seven eight"
    words = _words(text.split()[:5])  # the worker lost the last three words
    starts = word_starts_for(text, words, 1000, 3000)
    _check_invariants(text, starts, 1000, 3000)
    assert starts[:5] == [w["start_ms"] for w in words]
    assert starts[4] < starts[5] < starts[6] < starts[7] <= 3000


def test_whisper_longer_than_the_ebook_ignores_the_extras():
    text = "One two three four"
    heard = ["Well", "one", "two", "three", "four", "indeed"]
    words = _words(heard)
    starts = word_starts_for(text, words, 1000, 3000)
    _check_invariants(text, starts, 1000, 3000)
    assert starts == [words[i]["start_ms"] for i in (1, 2, 3, 4)]


def test_leading_tokens_spread_from_the_sentence_start():
    text = "Alpha beta gamma delta epsilon zeta"
    heard = ["gamma", "delta", "epsilon", "zeta"]
    words = _words(heard, start=2000)
    starts = word_starts_for(text, words, 1000, 4000)
    _check_invariants(text, starts, 1000, 4000)
    assert starts[0] == 1000
    assert 1000 < starts[1] < 2000
    assert starts[2:] == [w["start_ms"] for w in words]


def test_punctuation_and_case_do_not_block_a_match():
    text = "“Hello,” said Mary — quietly."
    heard = ["hello", "said", "mary", "quietly"]
    words = _words(heard)
    starts = word_starts_for(text, words, 1000, 2500)
    _check_invariants(text, starts, 1000, 2500)
    # The dash token has no letters or digits, so it is interpolated between
    # "Mary" and "quietly"; everything else is anchored.
    assert starts[0] == words[0]["start_ms"]
    assert starts[1] == words[1]["start_ms"]
    assert starts[2] == words[2]["start_ms"]
    assert starts[4] == words[3]["start_ms"]
    assert starts[2] < starts[3] < starts[4]


def test_uses_str_split_so_a_no_break_space_separates_tokens():
    text = "alpha beta gamma"
    assert text.split() == ["alpha", "beta", "gamma"]
    words = _words(["alpha", "beta", "gamma"])
    starts = word_starts_for(text, words, 1000, 2000)
    assert starts == [w["start_ms"] for w in words]


def test_a_single_token_sentence_has_too_few_anchors():
    assert word_starts_for("Hello", _words(["Hello"]), 1000, 1400) is None


def test_too_few_anchors_gives_none():
    text = "The red fox ran home"
    words = _words(["Completely", "different", "words", "entirely", "here"])
    assert word_starts_for(text, words, 1000, 2500) is None


def test_fewer_than_half_anchored_gives_none():
    text = "a1 b2 c3 d4 e5 f6"
    heard = ["a1", "x", "y", "z", "f6"]
    # 2 of 6 tokens anchor, below the half threshold.
    assert word_starts_for(text, _words(heard), 1000, 3000) is None


def test_no_words_or_no_tokens_gives_none():
    assert word_starts_for("One two", [], 0, 1000) is None
    assert word_starts_for("   ", _words(["One", "two"]), 0, 1000) is None


def test_whisper_times_outside_the_sentence_are_clamped():
    text = "One two three"
    words = [
        {"text": "One", "start_ms": 500, "end_ms": 700},
        {"text": "two", "start_ms": 800, "end_ms": 900},
        {"text": "three", "start_ms": 5000, "end_ms": 5200},
    ]
    starts = word_starts_for(text, words, 1000, 2000)
    _check_invariants(text, starts, 1000, 2000)


def test_non_monotonic_word_times_are_made_non_decreasing():
    text = "One two three four"
    words = [
        {"text": "One", "start_ms": 1000, "end_ms": 1100},
        {"text": "two", "start_ms": 1900, "end_ms": 2000},
        {"text": "three", "start_ms": 1500, "end_ms": 1600},
        {"text": "four", "start_ms": 2100, "end_ms": 2200},
    ]
    starts = word_starts_for(text, words, 1000, 2300)
    _check_invariants(text, starts, 1000, 2300)


@pytest.mark.parametrize("n_extra", [0, 1, 4, 9])
def test_length_and_order_invariants_across_shapes(n_extra):
    base = "The quick brown fox jumps over the lazy dog".split()
    text = " ".join(base + [f"x{i}" for i in range(n_extra)])
    words = _words(base)
    starts = word_starts_for(text, words, 1000, 5000)
    _check_invariants(text, starts, 1000, 5000)
