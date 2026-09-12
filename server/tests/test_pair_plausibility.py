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
    MIN_BYTES_PER_HOUR,
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
