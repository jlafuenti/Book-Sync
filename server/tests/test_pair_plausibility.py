"""Issue #458 — flag a pair whose audio is implausibly short for its ebook.

A live library had a full-length novel EPUB (1.2 MB) matched to a 132-second
audio file. Every stage did what it was told: it scanned, matched, transcribed
and reached `synced` without a warning, and the sync map it produced was
meaningless — 29 sentences of audio aligned against a whole book. Nothing was
broken, because no stage ever asks whether the *pairing* is plausible, so a
truncated or failed download presents as a healthy synced pair. The user sees a
book that syncs to the wrong place, which is silent and hard to attribute.

**Why file size rather than word count.** Nothing stores a measure of how long a
book's text is, and the only way to get one is to unzip and parse the whole
EPUB — blocking work, on an endpoint that currently does a few selects, and
auto-matching creates pairs in bulk. `file_size` is already on the row, so this
check is a pure comparison that can run on every pair including a bulk
auto-match. It is cruder (images inflate an EPUB), which is why the band below
is deliberately enormous: #458 asks for order-of-magnitude mismatches, not
"this narrator reads fast".

The band is the whole design, so it is tested at its edges rather than only in
the middle.
"""

import pytest

from services.pair_plausibility import (
    MAX_BYTES_PER_HOUR,
    MAX_WORDS_PER_HOUR,
    MIN_BYTES_PER_HOUR,
    MIN_WORDS_PER_HOUR,
    check_pair_plausibility,
)


def _hours(h: float) -> float:
    return h * 3600.0


# ---------------------------------------------------------------------------
# The case from the issue
# ---------------------------------------------------------------------------


def test_the_reported_pair_is_flagged():
    """1.2 MB novel against 132 seconds of audio — about 32 MB per audio hour."""
    ok, detail = check_pair_plausibility(
        ebook_file_size=1_200_000, duration_seconds=132, is_abridged=False
    )
    assert ok is False
    # The detail is what the operator reads in Troubleshoot Library, so it has
    # to name the two numbers that disagree rather than just saying "implausible".
    assert "2 minutes" in detail or "132" in detail, detail
    assert "1.2" in detail or "1.1" in detail, detail


# ---------------------------------------------------------------------------
# Ordinary books must never be flagged — a false positive is worse than a miss
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "label, size, hours",
    [
        ("a typical novel", 600_000, 10.0),          # 60 KB/h
        ("a long novel", 1_200_000, 20.0),           # 60 KB/h
        ("a short novel", 250_000, 4.0),             # 62 KB/h
        ("a novella", 90_000, 1.5),                  # 60 KB/h
        ("an image-heavy EPUB", 8_000_000, 10.0),    # 800 KB/h — still fine
        ("a sparse plain-text EPUB", 120_000, 8.0),  # 15 KB/h — still fine
        ("a doorstop fantasy", 3_000_000, 48.0),     # 62 KB/h
    ],
)
def test_plausible_pairs_are_left_alone(label, size, hours):
    ok, detail = check_pair_plausibility(
        ebook_file_size=size, duration_seconds=_hours(hours), is_abridged=False
    )
    assert ok is True, f"{label} was flagged: {detail}"
    assert detail is None


# ---------------------------------------------------------------------------
# Both ends of the band
# ---------------------------------------------------------------------------


def test_audio_far_too_short_for_the_text_is_flagged():
    """The truncated-download case: lots of book, almost no audio."""
    ok, _ = check_pair_plausibility(
        ebook_file_size=1_000_000, duration_seconds=_hours(0.1), is_abridged=False
    )
    assert ok is False


def test_audio_far_too_long_for_the_text_is_flagged():
    """The other direction: a short story matched to a 20-hour recording.

    Worth catching for the same reason — the sync map would be equally
    meaningless, and it usually means the wrong audiobook was matched.
    """
    ok, _ = check_pair_plausibility(
        ebook_file_size=30_000, duration_seconds=_hours(20.0), is_abridged=False
    )
    assert ok is False


def test_the_band_edges_are_inclusive():
    """Exactly at a boundary is plausible; the check only fires outside it."""
    for ratio in (MIN_BYTES_PER_HOUR, MAX_BYTES_PER_HOUR):
        ok, _ = check_pair_plausibility(
            ebook_file_size=int(ratio * 5), duration_seconds=_hours(5.0),
            is_abridged=False,
        )
        assert ok is True, f"ratio {ratio} bytes/hour should be inside the band"


def test_the_band_is_wide_enough_to_be_about_orders_of_magnitude():
    """A guard on the constants themselves, not on behaviour.

    If someone later narrows this band to something 'tighter', the check starts
    firing on real books, and a warning that cries wolf gets ignored — taking
    the real findings with it. Two orders of magnitude is the point.
    """
    assert MAX_BYTES_PER_HOUR / MIN_BYTES_PER_HOUR >= 100


# ---------------------------------------------------------------------------
# When the check must stay quiet
# ---------------------------------------------------------------------------


def test_an_abridged_audiobook_is_never_flagged():
    """#458 names this as the obvious false-positive class, and the flag exists.

    An abridgement legitimately has far less audio than the ebook has text.
    """
    ok, detail = check_pair_plausibility(
        ebook_file_size=1_200_000, duration_seconds=132, is_abridged=True
    )
    assert ok is True
    assert detail is None


@pytest.mark.parametrize(
    "size, duration",
    [
        (None, 36000.0),    # never scanned / unknown size
        (0, 36000.0),
        (600_000, None),    # duration_seconds not populated yet (#127)
        (600_000, 0),
    ],
)
def test_missing_inputs_are_not_a_finding(size, duration):
    """Absent data means "cannot judge", never "bad".

    `duration_seconds` in particular is not always populated when a pair is
    created (#127), and reporting every such pair as implausible would bury the
    real findings on day one.
    """
    ok, detail = check_pair_plausibility(
        ebook_file_size=size, duration_seconds=duration, is_abridged=False
    )
    assert ok is True
    assert detail is None


# ---------------------------------------------------------------------------
# Issue #620: a words-per-hour bound.
#
# `file_size` can miss what a direct word count catches: two real production
# pairs (an abridgement and a one-hour excerpt) had file sizes that happened
# to keep `bytes_per_hour` inside the band above — exactly the imprecision
# the module docstring already names ("images and embedded fonts inflate an
# EPUB well beyond its text") — while their words-per-hour was wildly outside
# a real unabridged reading's range.
# ---------------------------------------------------------------------------


def test_an_abridgement_shaped_pair_is_flagged_by_word_rate():
    """The reported abridgement: 87,398 words against 2.99 h (~29.3k words/h)
    — not flagged by the byte-based check alone (a plausible file_size), but
    caught once word_count is supplied."""
    ok, detail = check_pair_plausibility(
        ebook_file_size=600_000,  # otherwise-ordinary file size
        duration_seconds=_hours(2.99),
        is_abridged=False,
        word_count=87_398,
    )
    assert ok is False
    assert "87,398" in detail
    assert "words" in detail.lower()


def test_an_excerpt_shaped_pair_is_flagged_by_word_rate():
    """The reported one-hour excerpt: 87,996 words against 1.09 h (~80.8k
    words/h)."""
    ok, detail = check_pair_plausibility(
        ebook_file_size=600_000,
        duration_seconds=_hours(1.09),
        is_abridged=False,
        word_count=87_996,
    )
    assert ok is False
    assert "87,996" in detail


@pytest.mark.parametrize(
    "label, words, hours",
    [
        ("low end of a real reading", 80_000, 10.0),    # 8,000 words/h
        ("typical pace", 100_000, 10.0),                # 10,000 words/h
        ("high end of a real reading", 120_000, 10.0),  # 12,000 words/h
        ("a short novella", 24_000, 3.0),                # 8,000 words/h
    ],
)
def test_a_normal_8_to_12k_words_per_hour_pair_is_not_flagged(label, words, hours):
    ok, detail = check_pair_plausibility(
        ebook_file_size=600_000, duration_seconds=_hours(hours),
        is_abridged=False, word_count=words,
    )
    assert ok is True, f"{label} was flagged: {detail}"
    assert detail is None


def test_the_word_rate_band_edges_are_inclusive():
    for rate in (MIN_WORDS_PER_HOUR, MAX_WORDS_PER_HOUR):
        ok, _ = check_pair_plausibility(
            ebook_file_size=600_000, duration_seconds=_hours(5.0),
            is_abridged=False, word_count=int(rate * 5),
        )
        assert ok is True, f"rate {rate} words/hour should be inside the band"


def test_the_word_rate_band_has_margin_around_a_real_8_to_12k_reading():
    """A guard on the constants: the band has to comfortably contain a real
    unabridged reading (8,000-12,000 words/h) with margin on both sides, the
    same "order-of-magnitude mismatches only" philosophy as the byte band."""
    assert MIN_WORDS_PER_HOUR < 8_000
    assert MAX_WORDS_PER_HOUR > 12_000


def test_an_abridged_audiobook_is_never_flagged_by_word_rate():
    """Same exemption as the byte-based check — an abridgement legitimately
    has far less audio than the ebook has text."""
    ok, detail = check_pair_plausibility(
        ebook_file_size=600_000, duration_seconds=_hours(2.99),
        is_abridged=True, word_count=87_398,
    )
    assert ok is True
    assert detail is None


def test_missing_word_count_falls_back_to_the_byte_check_only():
    """No word_count means "cannot judge on word rate" — never treated as a
    finding on its own, and the byte-based check still runs as before."""
    ok, detail = check_pair_plausibility(
        ebook_file_size=600_000, duration_seconds=_hours(10.0),
        is_abridged=False, word_count=None,
    )
    assert ok is True
    assert detail is None


def test_a_plausible_word_rate_still_lets_the_byte_check_flag_a_pair():
    """An implausible pair can be implausible on either signal — a normal
    word rate does not exempt a pair whose file size vs. audio length is
    still an order of magnitude off (issue #458's original case)."""
    ok, detail = check_pair_plausibility(
        ebook_file_size=1_200_000, duration_seconds=132,
        is_abridged=False, word_count=None,
    )
    assert ok is False
