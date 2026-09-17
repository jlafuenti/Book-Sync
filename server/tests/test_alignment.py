"""
Alignment tests: synthetic books reproducing the drift the old chunked DTW
suffered (minutes-off sync after a region of inserted/garbled narration).

Uses local stand-in dataclasses (same fields as EpubSentence /
TranscribedSentence) so the tests run without torch/whisper/ebooklib
installed — alignment.py is duck-typed and import-light.
"""
import os
import random
import sys
from dataclasses import dataclass, field
from typing import List

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.alignment import align_texts, align_texts_with_diagnostics


@dataclass
class EpubSentence:
    chapter: int
    sentence_index: int
    text: str
    chapter_title: str = ""


@dataclass
class TranscribedSentence:
    text: str
    start_ms: int
    end_ms: int
    words: List[dict] = field(default_factory=list)


def _make_clean_book(num=1000, garbage_at=300, garbage_len=40):
    """Distinct sentences, one-to-one with audio, plus a short garbage run."""
    words = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf",
             "hotel", "india", "juliet", "kilo", "lima", "mike", "november"]
    epub, whisper = [], []
    t = 0
    for i in range(num):
        text = " ".join(words[(i + j) % len(words)] + str(i * 7 + j) for j in range(12))
        epub.append(EpubSentence(chapter=i // 50, sentence_index=i % 50, text=text))
        if garbage_at <= i < garbage_at + garbage_len:
            whisper.append(TranscribedSentence(text="la la la humming noise", start_ms=t, end_ms=t + 3000))
        else:
            whisper.append(TranscribedSentence(text=text, start_ms=t, end_ms=t + 3000))
        t += 3000
    return epub, whisper


def _make_realistic_book():
    """
    The scenario that made the old chunked DTW drift by minutes:
    - limited vocabulary (many similar sentences, like real prose)
    - ~14% word-level transcription noise (drops + mishears)
    - 40-sentence narrator intro before the book starts
    - 150 sentences of extra/hallucinated narration inserted mid-book

    Returns (epub, whisper, truth) where truth[i] is the true start_ms of
    epub sentence i in the audio.
    """
    vocab = ("the a he she they said walked door room dark light slowly "
             "quickly turned looked saw heard old young man woman house "
             "street night day hand eyes face voice").split()
    rng = random.Random(7)

    def sentence():
        return " ".join(rng.choice(vocab) for _ in range(rng.randint(6, 18)))

    def noisy(s):
        out = []
        for w in s.split():
            r = rng.random()
            if r < 0.08:
                continue                      # dropped word
            elif r < 0.14:
                out.append(rng.choice(vocab))  # misheard word
            else:
                out.append(w)
        return " ".join(out)

    epub_texts = [sentence() for _ in range(1000)]
    epub = [EpubSentence(chapter=i // 50, sentence_index=i % 50, text=epub_texts[i])
            for i in range(1000)]

    whisper, truth = [], {}
    t = 0

    def emit(txt, dur=3000):
        nonlocal t
        whisper.append(TranscribedSentence(text=txt, start_ms=t, end_ms=t + dur))
        t += dur

    for k in range(40):
        emit(f"audible presents narrated by famous person {k}")
    for i in range(1000):
        if i == 350:
            for _ in range(150):
                emit(noisy(sentence()))       # extra narration not in the epub
        truth[i] = t
        emit(noisy(epub_texts[i]))
    return epub, whisper, truth


def test_no_drift_after_garbage_region():
    epub, whisper = _make_clean_book()
    points = align_texts(epub, whisper)
    assert len(points) == len(epub)
    # Sentence i is spoken at exactly i*3000 ms.
    for i in (500, 700, 900, 999):
        expected = i * 3000
        assert abs(points[i].audio_start_ms - expected) < 30_000, (
            f"sentence {i}: got {points[i].audio_start_ms}, expected ~{expected}"
        )


def test_no_drift_with_insertion_and_noise():
    """The old chunked DTW was 100-280 s off after the inserted region;
    anchor-based alignment must stay within 30 s everywhere."""
    epub, whisper, truth = _make_realistic_book()
    points = align_texts(epub, whisper)
    assert len(points) == len(epub)
    for i in (100, 400, 600, 800, 999):
        assert abs(points[i].audio_start_ms - truth[i]) < 30_000, (
            f"sentence {i}: got {points[i].audio_start_ms}, expected ~{truth[i]}"
        )


def test_monotonic_audio_times():
    epub, whisper, _ = _make_realistic_book()
    points = align_texts(epub, whisper)
    last = -1
    for p in points:
        assert p.audio_start_ms >= last
        last = p.audio_start_ms


def test_confidence_present_on_matches():
    epub, whisper = _make_clean_book(num=200, garbage_len=0)
    points = align_texts(epub, whisper)
    matched = [p for p in points if p.confidence > 0]
    assert len(matched) > len(points) * 0.8


# ---------------------------------------------------------------------------
# Degraded-map detection (issue #586): a reordered block of audio content
# stays monotonic (and so invisible to the LIS anchor filter's own output)
# unless something looks at what the filter rejected, not just what it kept.
# ---------------------------------------------------------------------------

def _make_distinct_book(num=900):
    """Every sentence text and length is unique — good anchor material."""
    words = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf",
             "hotel", "india", "juliet", "kilo", "lima", "mike", "november"]
    epub, epub_texts = [], []
    for i in range(num):
        text = " ".join(words[(i + j) % len(words)] + str(i * 7 + j) for j in range(12))
        epub.append(EpubSentence(chapter=i // 50, sentence_index=i % 50, text=text))
        epub_texts.append(text)
    return epub, epub_texts


def _whisper_from_position_source(epub_texts, position_source):
    """Build whisper sentences where audio position `pos` speaks the epub
    sentence at `position_source[pos]` — i.e. a permutation of reading order."""
    whisper = []
    t = 0
    for pos in range(len(position_source)):
        text = epub_texts[position_source[pos]]
        whisper.append(TranscribedSentence(text=text, start_ms=t, end_ms=t + 3000))
        t += 3000
    return whisper


def _make_swapped_blocks_book(num=900, block_a=(300, 400), block_b=(500, 600)):
    """Two equal-length audio blocks are swapped relative to reading order —
    the scenario from issue #586 (two ~33-minute blocks swapped mid-book)."""
    epub, epub_texts = _make_distinct_book(num)
    a0, a1 = block_a
    b0, b1 = block_b
    assert (a1 - a0) == (b1 - b0)
    position_source = list(range(num))
    position_source[a0:a1], position_source[b0:b1] = (
        position_source[b0:b1], position_source[a0:a1]
    )
    whisper = _whisper_from_position_source(epub_texts, position_source)
    return epub, whisper


def _make_scattered_swaps_book(num=300, swaps=((30, 270), (90, 210))):
    """A handful of single, isolated sentences swapped far apart — ordinary
    noise, not a reordered block. Must not trip degraded classification."""
    epub, epub_texts = _make_distinct_book(num)
    position_source = list(range(num))
    for a, b in swaps:
        position_source[a], position_source[b] = position_source[b], position_source[a]
    whisper = _whisper_from_position_source(epub_texts, position_source)
    return epub, whisper


def test_swapped_audio_blocks_flagged_degraded():
    epub, whisper = _make_swapped_blocks_book()
    points, diagnostics = align_texts_with_diagnostics(epub, whisper)
    assert len(points) == len(epub)
    assert diagnostics is not None
    assert diagnostics.degraded is True
    assert diagnostics.rejected_fraction >= 0.10
    assert diagnostics.displaced_run_length >= 3
    assert diagnostics.reason  # a human-readable explanation was recorded


def test_scattered_rejects_not_flagged_degraded():
    """A few scattered rejected anchors — no contiguous displaced run — must
    not trip degraded classification, even if the reject share looks elevated."""
    epub, whisper = _make_scattered_swaps_book()
    points, diagnostics = align_texts_with_diagnostics(epub, whisper)
    assert len(points) == len(epub)
    assert diagnostics is not None
    assert diagnostics.degraded is False


def test_in_order_book_with_noise_not_flagged_degraded():
    """The existing noisy-but-in-order fixture (word drops/mishears, an
    inserted narration block) must not be misclassified as degraded."""
    epub, whisper, _truth = _make_realistic_book()
    points, diagnostics = align_texts_with_diagnostics(epub, whisper)
    assert len(points) == len(epub)
    assert diagnostics is not None
    assert diagnostics.degraded is False


def test_align_texts_unchanged_by_diagnostics_collection():
    """`align_texts` (no diagnostics) and `align_texts_with_diagnostics` must
    produce identical points — collecting diagnostics is purely additive."""
    epub, whisper = _make_swapped_blocks_book()
    plain = align_texts(epub, whisper)
    with_diag, _diagnostics = align_texts_with_diagnostics(epub, whisper)
    assert [(p.audio_start_ms, p.audio_end_ms, p.confidence) for p in plain] == [
        (p.audio_start_ms, p.audio_end_ms, p.confidence) for p in with_diag
    ]


# ---------------------------------------------------------------------------
# Issue #595: a far-ahead outlier anchor surviving the LIS filter.
#
# #586's swapped-block fixtures above are dense on both sides of the reordered
# block — plenty of correctly-ordered candidate anchors compete for the same
# chain, so the plain longest-increasing-subsequence filter already prefers
# the long correct run over a lone bad anchor there. The failure this issue
# describes is different: a region with *no competing candidates at all*
# (noisy transcription, a stretch of short/ambiguous sentences), where a
# single false, far-ahead anchor survives for free — including it costs the
# LIS nothing, because nothing else could occupy that epub slot anyway. Every
# sentence in the gap it creates then gets pulled toward it by
# DTW/interpolation: exact, then displaced (peaking near the false anchor),
# decaying back to exact once real anchors resume — precisely the shape in
# the issue's own evidence table.
# ---------------------------------------------------------------------------

def _make_collision_free_distinct_book(num):
    """Like `_make_distinct_book`, but every token is a hash of its own
    (sentence, position) pair rather than a small cyclic word list plus a
    decimal suffix. `_make_distinct_book`'s scheme repeats its 14-word cycle
    every 14 sentences and its numeric suffix is close (few characters
    different) between nearby sentences — fine for the book-wide fixtures
    elsewhere in this file, but at this test's scale (needed for a gap wide
    enough to force the chunked-DTW fallback, matching the issue's own
    thousands-of-sentences scenario) that produces enough incidental
    similarity for `token_set_ratio` to score unrelated sentences above the
    anchor threshold — confounding this test's one deliberately-injected
    false anchor with accidental ones. A hash has no structural similarity
    between different inputs, which removes that risk entirely."""
    import hashlib

    def token(i, j):
        return "w" + hashlib.md5(f"{i}-{j}".encode()).hexdigest()[:10]

    epub, epub_texts = [], []
    for i in range(num):
        text = " ".join(token(i, j) for j in range(12))
        epub.append(EpubSentence(chapter=i // 50, sentence_index=i % 50, text=text))
        epub_texts.append(text)
    return epub, epub_texts


def _make_book_with_far_ahead_anchor(num=1500, bad_epub_idx=300, bad_whisper_idx=1100):
    """An otherwise perfectly in-order book, except epub sentence
    `bad_epub_idx`'s only spoken occurrence is placed at `bad_whisper_idx` —
    hundreds of sentences ahead of where it belongs — instead of at its own
    position. Its own slot, and everything between the two positions, carries
    only filler noise unrelated to any epub sentence, so nothing competes for
    that stretch: the false anchor is the only candidate the LIS filter ever
    sees there, which is exactly what lets it survive unfiltered today.
    """
    epub, epub_texts = _make_collision_free_distinct_book(num)
    whisper = []
    t = 0
    for pos in range(num):
        if pos == bad_whisper_idx:
            text = epub_texts[bad_epub_idx]  # the one false, far-ahead match
        elif bad_epub_idx <= pos < bad_whisper_idx:
            text = f"um uh well filler {pos} noise"  # no valid anchor here
        else:
            text = epub_texts[pos]
        whisper.append(TranscribedSentence(text=text, start_ms=t, end_ms=t + 3000))
        t += 3000
    truth = {i: i * 3000 for i in range(num)}
    return epub, whisper, truth


def test_far_ahead_anchor_does_not_displace_the_gap_it_creates():
    """A single false, far-ahead anchor must not survive the anchor filter
    just because nothing else competes for its epub slot — every sentence in
    the gap between the surrounding correct anchors must still track truth,
    not get pulled toward the false anchor's position hours away."""
    epub, whisper, truth = _make_book_with_far_ahead_anchor()
    points, diagnostics = align_texts_with_diagnostics(epub, whisper)
    assert len(points) == len(epub)

    # Well before and well after the injected anchor: unaffected either way.
    for i in (50, 150, 1450):
        assert abs(points[i].audio_start_ms - truth[i]) < 30_000, (
            f"sentence {i}: got {points[i].audio_start_ms}, expected ~{truth[i]}"
        )

    # Inside the gap the false anchor would otherwise pin: must track truth,
    # not the false anchor's audio position (~3,300,000 ms / 55 min). A wider
    # tolerance than the dense-anchor checks above — this whole stretch has
    # no anchors at all once the false one is rejected, so it is bridged by
    # interpolation/chunked-DTW between the two anchors bracketing the gap
    # rather than a per-sentence match — but still tight against a
    # ~40-minute pull.
    for i in (500, 700, 900):
        assert abs(points[i].audio_start_ms - truth[i]) < 120_000, (
            f"sentence {i}: got {points[i].audio_start_ms}, expected ~{truth[i]} "
            f"— looks pulled toward the false anchor instead"
        )

    # A single displaced anchor being rejected is ordinary filtering, not a
    # degraded map: issue #586's classifier keys on rejected anchors, and a
    # better filter now rejects this one too, but MIN_DISPLACED_RUN_LENGTH
    # (3) means one rejection alone must not trip degraded classification.
    assert diagnostics is not None
    assert diagnostics.degraded is False
