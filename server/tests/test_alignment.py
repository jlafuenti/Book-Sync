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

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.alignment import (
    align_texts,
    align_texts_with_diagnostics,
    CHUNK_DRIFT_MAX_DEMOTION_FRACTION,
    CHUNK_DRIFT_MIN_RUN,
    _chunk_align,
    _diagnose_anchor_rejection,
    _filter_chunked_segment_drift,
)


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


# ---------------------------------------------------------------------------
# Issue #620: `degraded` false positives on a tiny anchor pool.
#
# A production re-run of #595's fix found 14 pairs marked `degraded` whose
# post-filter map the audit's own timing check confirmed clean (0 mismatches
# of 65-75 sampled points) — e.g. "6/8 raw anchors (75%) rejected ..., run of
# 6 displaced ~581 min" and "13/25 raw anchors (52%) rejected ..., run of 7
# displaced ~117 min". Both have so few raw anchors that a run of most of
# them is a tiny-sample artifact of `_expected_ms`'s local trend (fit through
# 2 and 12 kept anchors respectively), not evidence of a genuinely reordered
# block the way it is on a book with a healthy-sized anchor pool (hundreds,
# as in `test_swapped_audio_blocks_flagged_degraded` above, which keeps
# passing after this fix).
#
# These two tests reproduce the exact raw/kept/rejected/run shape of both
# real false positives directly against `_diagnose_anchor_rejection` — the
# point is the anchor-count floor, not a full synthetic book (issue #595's
# fixtures above already build an end-to-end book for the run-length rule).
# ---------------------------------------------------------------------------

def _tiny_pool_fixture(num_raw, kept_idxs, run_range, offset_ms):
    """`num_raw` raw anchors at epub indices 0..num_raw-1, one whisper
    sentence per index, all initially exactly on a `cadence_ms`-per-index
    trend line. `kept_idxs` names which indices are "kept" (present in
    `kept_anchors`, all still exactly on the trend line — two points on a
    line interpolate back to the same line, so every rejected index's
    `_expected_ms` is unaffected by which indices in between happen to be
    kept vs merely rejected-but-on-trend). `run_range` (a `range`) is a
    contiguous block of indices, disjoint from `kept_idxs`, displaced by
    `offset_ms` — the genuinely-displaced run. Every other index not in
    `kept_idxs` or `run_range` is "rejected but on-trend": excluded from
    `kept_anchors` (so it counts toward `rejected_fraction`) but not
    displaced (so it cannot extend the run) — mirroring how a small pool can
    carry ordinary scattered rejects alongside the one real run.
    """
    cadence_ms = 180_000  # 3 minutes/anchor, matching this file's other fixtures
    whisper_sentences = [
        TranscribedSentence(text="x", start_ms=i * cadence_ms, end_ms=i * cadence_ms + 3000)
        for i in range(num_raw)
    ]
    for i in run_range:
        assert i not in kept_idxs
        whisper_sentences[i].start_ms = i * cadence_ms + offset_ms

    raw_anchors = [(i, i, 0.9) for i in range(num_raw)]
    kept_anchors = [raw_anchors[i] for i in sorted(kept_idxs)]
    return raw_anchors, kept_anchors, whisper_sentences


def test_tiny_anchor_pool_75pct_rejected_with_clean_trend_not_degraded():
    """Issue #620's first real false positive: 8 raw anchors, only 2 kept
    (the endpoints), a run of 6 displaced ~581 min. Rejected fraction (75%)
    and run length (6) both clear the old thresholds comfortably — only the
    anchor-count floor keeps this from firing."""
    raw, kept, whisper = _tiny_pool_fixture(
        num_raw=8, kept_idxs={0, 7}, run_range=range(1, 7), offset_ms=581 * 60_000,
    )
    diagnostics = _diagnose_anchor_rejection(raw, kept, whisper)
    assert diagnostics.raw_anchor_count == 8
    assert diagnostics.kept_anchor_count == 2
    assert diagnostics.rejected_fraction == pytest.approx(0.75)
    assert diagnostics.displaced_run_length == 6
    assert diagnostics.degraded is False


def test_tiny_anchor_pool_52pct_rejected_with_clean_trend_not_degraded():
    """Issue #620's second real false positive: 25 raw anchors, 12 kept, a
    run of 7 displaced ~117 min, plus 6 more scattered (on-trend) rejects
    elsewhere — the real pair's 13/25 (52%) rejected was not all one run."""
    kept_idxs = {0, 1, 3, 4, 6, 7, 16, 18, 19, 21, 22, 24}
    raw, kept, whisper = _tiny_pool_fixture(
        num_raw=25, kept_idxs=kept_idxs, run_range=range(9, 16), offset_ms=117 * 60_000,
    )
    diagnostics = _diagnose_anchor_rejection(raw, kept, whisper)
    assert diagnostics.raw_anchor_count == 25
    assert diagnostics.kept_anchor_count == 12
    assert diagnostics.rejected_fraction == pytest.approx(13 / 25)
    assert diagnostics.displaced_run_length == 7
    assert diagnostics.degraded is False


def test_swapped_audio_blocks_still_degraded_with_a_healthy_anchor_pool():
    """The anchor-count floor must not swallow a genuine reordering — same
    fixture and assertions as `test_swapped_audio_blocks_flagged_degraded`
    above, kept anchor count checked explicitly to show it clears the floor
    with real margin (kept anchors number in the dozens on a 900-sentence
    book with plenty of competing candidates on both sides of the swap)."""
    epub, whisper = _make_swapped_blocks_book()
    points, diagnostics = align_texts_with_diagnostics(epub, whisper)
    assert diagnostics is not None
    assert diagnostics.kept_anchor_count >= 20
    assert diagnostics.degraded is True


# ---------------------------------------------------------------------------
# Issue #620 part 2: systematic chunked-segment drift.
#
# Nine production pairs kept drifting after realign, with ordinary word rates
# and transcripts confirmed in book order — not a single bad anchor (#595)
# and not reordered audio (#586). The map ran progressively earlier or later,
# chapter by chapter, up to ~2.5h mid-book, then recovered. Investigated
# before coding, per the issue's own instruction not to guess:
#
# `_chunk_align` (used for any anchor-bounded segment over `max_segment`=600
# sentences — a sparse-anchor region can easily produce one) walks the epub
# side in FIXED `chunk_size`-sentence steps and sizes each chunk's whisper
# window from ONE ratio (`m/n`) averaged over the WHOLE segment. That average
# is only exactly right when the epub:whisper sentence-count ratio is uniform
# across the segment. It is not, wherever the epub and transcript sentence
# lists diverge in density within one region — more footnotes, captions or
# chapter headings than elsewhere (present in the epub, never spoken) is the
# concrete shape tried here, but an EPUB parser over/under-splitting one
# stretch differently than Whisper's own segmentation would produce the same
# thing. A wrong-sized window means the sentence that SHOULD match isn't
# even a candidate inside it, so DTW is forced to pick the best AVAILABLE
# (wrong) one instead — a *confident* match, to the wrong audio. The next
# chunk's window is rebased from that wrong position, so the error carries
# forward and compounds smoothly, one small step per chunk, until the true
# content reappears inside a window and it corrects. That is exactly the
# "runs increasingly off, then recovers" shape reported.
#
# Reproduced below directly against `_chunk_align` with real chunk_size/
# overlap defaults and perfectly distinct, unambiguous per-sentence text —
# isolating the windowing/chunking logic from ordinary fuzzy-match noise —
# before the fix (`test_chunk_align_can_drift_smoothly_...`) and after it
# (`test_filter_chunked_segment_drift_...`). A full end-to-end `align_texts`
# reproduction was attempted but not included: getting `_find_anchors` to
# reliably keep >=3 anchors bracketing a deliberately skewed region ran into
# a separate interaction with issue #595's own trend-consistency filter (a
# genuine, large content-density skew between two real anchors can itself
# look like the anchor is "displaced" from the straight-line trend its
# distant neighbours imply, occasionally dropping a valid boundary anchor)
# — noted as a related finding in the PR/issue comment, not fixed here, and
# not needed to reproduce or fix the chunking bug itself.
# ---------------------------------------------------------------------------

def _make_locally_skewed_segment(n_spoken_per_region, drop_fraction_per_region):
    """One `_chunk_align`-sized segment built from regions with different
    LOCAL epub:whisper ratios. Each region emits `n_spoken` epub sentences
    that ARE spoken (1:1 with a whisper sentence of identical, globally
    distinct text — no fuzzy-match ambiguity anywhere) interleaved with
    epub-only "unspoken" sentences at `drop_fraction` extra per spoken one
    (e.g. 1.0 means one unspoken epub sentence for every spoken one, doubling
    the local epub:whisper ratio for that region only).

    Returns `(epub, whisper, truth)`; `truth[i]` is the correct `start_ms`
    for spoken epub sentence `i`, or `None` for an unspoken one.
    """
    epub, whisper, truth = [], [], {}
    t = 0
    idx = 0
    drop_counter = 0.0

    for n_spoken, drop_frac in zip(n_spoken_per_region, drop_fraction_per_region):
        for _ in range(n_spoken):
            drop_counter += drop_frac
            while drop_counter >= 1.0:
                epub.append(EpubSentence(chapter=0, sentence_index=idx,
                                         text=f"unspoken filler sentence number {idx}"))
                truth[idx] = None
                idx += 1
                drop_counter -= 1.0
            text = f"spoken distinct sentence number {idx}"
            epub.append(EpubSentence(chapter=0, sentence_index=idx, text=text))
            whisper.append(TranscribedSentence(text=text, start_ms=t, end_ms=t + 3000))
            truth[idx] = t
            t += 3000
            idx += 1

    return epub, whisper, truth


def _make_skewed_gap_segment():
    """A 1200-epub/900-whisper segment: 300 sentences at the normal (1:1)
    ratio, then 300 with one unspoken sentence per spoken one (2x local
    ratio — the middle third, matching a mid-book region with unusually
    dense front matter/footnotes/headings), then back to normal — well over
    `_chunk_align`'s default `chunk_size` (200), so this exercises real
    chunk-boundary bookkeeping, not a single chunk."""
    return _make_locally_skewed_segment(
        n_spoken_per_region=[300, 300, 300],
        drop_fraction_per_region=[0.0, 1.0, 0.0],
    )


def test_chunk_align_can_drift_smoothly_on_a_locally_skewed_ratio():
    """Documents the underlying mechanism directly against `_chunk_align`
    itself (unfixed at that level on purpose — `_filter_chunked_segment_drift`
    is what actually keeps this from reaching a sync map, see the test
    below): a confidently-wrong, smoothly-growing offset through the skewed
    region, exactly the "runs increasingly off, then recovers toward the
    edges" shape from the issue."""
    epub, whisper, truth = _make_skewed_gap_segment()

    alignments = _chunk_align(epub, whisper, chunk_size=200, overlap=20)
    got = {ei: whisper[wi].start_ms for ei, wi, conf in alignments if conf > 0}

    # Deep in the skewed region: confidently wrong by several minutes.
    worst = max(
        abs(got[i] - truth[i]) for i in range(600, 1000) if i in got and truth.get(i) is not None
    )
    assert worst > 5 * 60_000, (
        f"expected a multi-minute confident drift inside the skewed region, got {worst}ms"
    )

    # And it is not a random scatter: nearby points move in the same
    # direction by a similar amount (a smooth ramp, not noise) — checked at
    # two points ~100 sentences apart within the region.
    assert 900 in got and 1000 in got
    off_900 = got[900] - truth[900]
    off_1000 = got[1000] - truth[1000]
    assert off_900 > 0 and off_1000 > 0
    assert abs(off_900 - off_1000) < abs(off_900)  # both large, same sign, close in size

    # And it recovers: right at the segment's true end, back near truth.
    last_i = max(truth)
    assert got[last_i] is not None
    assert abs(got[last_i] - truth[last_i]) < 5000


def _make_bounded_drift_segment():
    """A 3,320-epub/~3,248-point segment: a long, ordinary 3,000-sentence
    run, a short 60-sentence region with double local epub:whisper density
    (footnotes/headings, as in `_make_skewed_gap_segment`), then a short
    200-sentence tail back to normal.

    Shaped to keep the genuinely-drifted run a small MINORITY of the whole
    segment (issue #635's own complaint about `_make_skewed_gap_segment`,
    below, is that its skewed third is not a realistic shape: a single
    skewed region compounds through everything downstream of it until
    `_chunk_align` gets a chance to resync, so a short tail keeps that
    compounding confined instead of consuming the whole remainder)."""
    return _make_locally_skewed_segment(
        n_spoken_per_region=[3000, 60, 200],
        drop_fraction_per_region=[0.0, 1.0, 0.0],
    )


def test_filter_chunked_segment_drift_corrects_the_skewed_gap():
    """The regression test for the original #628 fix: applying
    `_filter_chunked_segment_drift` to the same chunked output, with the
    segment's own true boundary (epub index 0 -> ms 0; last spoken index ->
    its true ms — exactly what `_anchor_align` passes in from two real
    anchors), demotes the wrongly-confident run instead of leaving it
    standing.

    Uses `_make_bounded_drift_segment` rather than the original
    `_make_skewed_gap_segment` fixture (kept above, still exercising
    `_chunk_align` directly): that fixture's skewed region is a full third
    of the segment, which compounds into ~38% of the whole segment reading
    confidently wrong — enough to trip the #635 safety bound below and mask
    the very thing this test is supposed to pin. A real confirmed-displaced
    run is a small minority of a gap's points; this fixture keeps it one
    (61/3,248, under 2%) so this test still exercises the original
    run-demotion behavior instead of the bailout path."""
    epub, whisper, truth = _make_bounded_drift_segment()
    raw = _chunk_align(epub, whisper, chunk_size=200, overlap=20)

    fixed = _filter_chunked_segment_drift(
        raw, len(epub) - 1, 0.0, whisper[-1].start_ms, whisper, ws=0,
    )

    # Every point still confidently matched (conf > 0) after filtering is
    # close to truth — the filter does not leave a wrong high-confidence
    # match standing.
    for ei, wi, conf in fixed:
        if conf > 0 and truth.get(ei) is not None:
            assert abs(whisper[wi].start_ms - truth[ei]) <= 3 * 60_000 + 1, (
                f"idx {ei}: still confidently wrong by "
                f"{(whisper[wi].start_ms - truth[ei]) / 1000:.0f}s after filtering"
            )

    # And it actually did something: a real, substantial run was demoted
    # (not the whole segment, and not nothing) — well under the safety
    # bound, so this is the ordinary demotion path, not the #635 bailout.
    demoted = sum(1 for _, _, conf in fixed if conf == 0.0)
    assert demoted >= CHUNK_DRIFT_MIN_RUN
    assert demoted < len(raw)
    assert demoted / len(raw) < CHUNK_DRIFT_MAX_DEMOTION_FRACTION


def test_filter_chunked_segment_drift_leaves_a_clean_segment_alone():
    """A segment with a uniform ratio throughout (no skew) must come back
    unchanged — the filter should never touch a healthy chunked segment."""
    epub, whisper, truth = _make_locally_skewed_segment(
        n_spoken_per_region=[600], drop_fraction_per_region=[0.0],
    )
    raw = _chunk_align(epub, whisper, chunk_size=200, overlap=20)

    fixed = _filter_chunked_segment_drift(
        raw, len(epub) - 1, 0.0, whisper[-1].start_ms, whisper, ws=0,
    )

    assert fixed == raw
    assert all(conf > 0 for _, _, conf in fixed)


def test_align_texts_through_a_whole_book_fallback_does_not_regress():
    """Sanity check that wiring the filter into the `len(anchors) < 3`
    whole-book fallback path (issue #620) does not break ordinary alignment
    when there is no skew at all — `align_texts`'s own existing behaviour on
    a clean book is unaffected."""
    epub, whisper = _make_clean_book(num=200, garbage_len=0)
    points = align_texts(epub, whisper)
    assert len(points) == len(epub)
    matched = [p for p in points if p.confidence > 0]
    assert len(matched) > len(points) * 0.8


# ---------------------------------------------------------------------------
# Issue #635: #628's own fix was the regression. Realigning the 12 affected
# production pairs on 0.4.1 made every one of them worse — e.g. one pair went
# from 12,268 matched points on 0.3.0 down to 252 on 0.4.1, with the audit's
# timing-mismatch count going UP (35/71 -> 70/71). Root cause: `expected_ms`
# is a single straight line drawn from one bracketing anchor to the other
# across the WHOLE segment. That line is only an accurate reference when the
# epub:whisper density is uniform end-to-end — and a segment large enough to
# need `_chunk_align` (up to a whole book, on the `len(anchors) < 3`
# fallback) is exactly where it is least likely to be. Wherever density
# genuinely varies across a long segment (an ordinary pacing difference
# between a faster and a slower stretch — nothing wrong with the match
# itself), every point on the far side of that change sits far from the
# single chord, in the same direction, so the run-length rule does nothing
# to contain it: "sustained real displacement" and "sustained but correct,
# judged against a bad reference" look identical to it. Reproduced below
# directly against `_filter_chunked_segment_drift`, isolating the reference
# problem from any `_chunk_align` behavior.
# ---------------------------------------------------------------------------

def _make_two_pace_reference(n, pace_break, pace_before_ms, pace_after_ms):
    """`n` whisper sentences at a uniform pace up to `pace_break`, a
    different uniform pace after it — an ordinary two-stretch book (brisk
    dialogue, then slower descriptive narration), no anomaly at all. Returns
    `(whisper_sentences, true_ms)` where `true_ms(i)` is sentence `i`'s
    correct start time."""
    def true_ms(i):
        if i < pace_break:
            return i * pace_before_ms
        return pace_break * pace_before_ms + (i - pace_break) * pace_after_ms

    whisper = [
        TranscribedSentence(text=f"sentence number {i}", start_ms=true_ms(i), end_ms=true_ms(i) + 1000)
        for i in range(n)
    ]
    return whisper, true_ms


def _build_long_skewed_gap_case():
    """The shape #635 asks for: a long (10,000-point) anchor-bounded gap
    whose epub:whisper pacing varies (brisk dialogue for the first 6,000
    sentences, slower narration for the last 4,000 — an everyday pacing
    difference, not a defect), where only a couple hundred points (220,
    matching the issue's "a few hundred of several thousand") are genuinely
    displaced by a real matching error. Returns `(seg_alignments, whisper,
    true_ms, bad_lo, bad_hi)`."""
    n = 10_000
    pace_break = 6_000
    whisper, true_ms = _make_two_pace_reference(
        n, pace_break, pace_before_ms=2_500, pace_after_ms=4_500,
    )

    bad_lo, bad_hi = 7_000, 7_220
    seg_alignments = []
    for i in range(n):
        if bad_lo <= i < bad_hi:
            wrong_i = min(n - 1, i + 400)  # a real DTW error: minutes away
            seg_alignments.append((i, wrong_i, 0.95))
        else:
            seg_alignments.append((i, i, 0.95))  # correct match
    return seg_alignments, whisper, true_ms, bad_lo, bad_hi


def test_filter_chunked_segment_drift_regression_pin_for_635():
    """The regression pin: on the shape above, the filter must not destroy
    the correctly-matched majority of a segment just because a long gap's
    single boundary-to-boundary reference disagrees with most of it.

    Measured directly against this fixture before the #635 fix existed:
    `_filter_chunked_segment_drift` demoted 9,624 of the 10,000 points
    (96%) — including 9,780 - (9,624 - 220) = 9,404 points that were
    genuinely CORRECT matches — collapsing 10,000 matched points down to
    376, the same "collapses to near nothing" shape as the issue's own
    12,268 -> 252 production case. This test pins the fixed behavior: the
    safety bound (`CHUNK_DRIFT_MAX_DEMOTION_FRACTION`) must recognize a
    would-be 96% demotion as evidence against the reference, not the
    matches, and leave the segment untouched instead."""
    seg_alignments, whisper, true_ms, bad_lo, bad_hi = _build_long_skewed_gap_case()

    fixed = _filter_chunked_segment_drift(
        seg_alignments, len(whisper) - 1, 0.0, whisper[-1].start_ms, whisper, ws=0,
    )

    matched_before = sum(1 for _, _, conf in seg_alignments if conf > 0)
    matched_after = sum(1 for _, _, conf in fixed if conf > 0)
    assert matched_before == len(whisper)

    # The regression this pins: matched count must stay close to what went
    # in, not collapse toward a handful of surviving points.
    assert matched_after > matched_before * 0.9, (
        f"matched collapsed from {matched_before} to {matched_after} "
        f"({100 * matched_after / matched_before:.0f}%) — the long-gap "
        f"reference destroyed the correctly-matched majority, same shape "
        f"as issue #635's production regression"
    )

    # The bailout is all-or-nothing for the segment (see the constant's
    # comment): nothing is touched, including the 220 genuinely bad points.
    assert fixed == seg_alignments


def test_filter_chunked_segment_drift_safety_bound_preserves_correct_matches():
    """Same case, checked from the other direction: every one of the 9,780
    correct matches is still exactly correct after filtering — the bailout
    doesn't alter good data, it just declines to act on a reference it
    cannot trust for this segment. The 220 genuinely bad points also stay
    untouched (still wrong) — the explicit "ship safety first" tradeoff
    from #635: a realign that damages otherwise-good maps is worse than the
    bug it was fixing, so those 220 points need a future, smarter fix
    (e.g. a locally-scoped reference) rather than this bound catching them."""
    seg_alignments, whisper, true_ms, bad_lo, bad_hi = _build_long_skewed_gap_case()

    fixed = _filter_chunked_segment_drift(
        seg_alignments, len(whisper) - 1, 0.0, whisper[-1].start_ms, whisper, ws=0,
    )

    assert fixed == seg_alignments
    correct_untouched = sum(
        1 for i, (ei, wi, conf) in enumerate(fixed)
        if not (bad_lo <= i < bad_hi) and whisper[wi].start_ms == true_ms(ei)
    )
    assert correct_untouched == len(whisper) - (bad_hi - bad_lo)
