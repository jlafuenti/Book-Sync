"""
Text Alignment Service

Aligns EPUB sentences to Whisper-transcribed sentences using fuzzy
string matching and sequence alignment. This is the core algorithm
that creates the bridge between reading and listening.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List, Tuple, Optional, TYPE_CHECKING

from rapidfuzz import fuzz
import numpy as np

if TYPE_CHECKING:
    # Type-hint-only imports: transcription pulls in torch/whisper and
    # epub_parser pulls in ebooklib/nltk; alignment only needs the dataclass
    # shapes (duck-typed at runtime), so keep this module import-light.
    from services.transcription import TranscribedSentence
    from services.epub_parser import EpubSentence

logger = logging.getLogger(__name__)


@dataclass
class AlignedPoint:
    """
    A single alignment point: one EPUB sentence matched to an audio range.
    This is what gets stored as a SyncPoint in the database.
    """
    epub_chapter: int
    epub_sentence_index: int
    epub_text_preview: str
    audio_start_ms: int
    audio_end_ms: int
    confidence: float  # 0.0 to 1.0, how confident is the match


# ---------------------------------------------------------------------------
# Degraded-map detection (issue #586)
#
# The anchor filter in `_find_anchors` keeps only the longest increasing
# subsequence (LIS) of raw fuzzy matches, so an audio file with a reordered
# block of content never breaks monotonicity — it just gets quietly dropped
# and the gap is filled by DTW/interpolation between the anchors on either
# side. That produces a map that is internally consistent (monotonic,
# plausible-looking timestamps) but wrong for the whole displaced block: real
# text correctly matched to the wrong audio position. Nothing in the anchor
# filter's own output said so until now — `_diagnose_anchor_rejection` looks
# at what the filter *rejected*, not just what it kept.
# ---------------------------------------------------------------------------

#: A rejected anchor is "displaced" (part of a reordered block) rather than
#: just imprecise (a stray mismatch, a repeated phrase) when its actual audio
#: time is off from the kept sequence's local trend by more than this. Ordinary
#: transcription noise does not move a whole sentence by minutes; a swapped
#: block of narration does (the real case behind issue #586 was offset by
#: 30-45 minutes).
DISPLACED_ANCHOR_OFFSET_MS = 3 * 60 * 1000  # 3 minutes

#: A run of at least this many *consecutive* rejected candidates (no kept
#: anchor in between), all displaced in the same direction, is treated as one
#: contiguous displaced block rather than coincidental scattered noise. Below
#: this a handful of isolated rejects (repeated phrasing, a mishear) is normal
#: and must not trip degraded classification.
MIN_DISPLACED_RUN_LENGTH = 3

#: Share of raw anchors the LIS filter has to reject before a map is even
#: considered for degraded classification. A real reordering rejects a large
#: chunk of the anchors that fall inside the displaced block(s); a healthy book
#: with ordinary noise rejects a small handful out of hundreds.
DEGRADED_REJECTED_FRACTION = 0.10

#: Minimum number of anchors that must *survive* the LIS/consistency filter
#: before the fraction/run rule above is even considered (issue #620). A
#: production re-run of #595's fix found 14 pairs marked degraded whose
#: post-filter map the audit's own timing check confirmed clean (0 mismatches
#: of 65-75 sampled points) — e.g. "6/8 raw anchors (75%) rejected ..., run of
#: 6 displaced ~581 min" (2 kept) and "13/25 rejected ..., run of 7 displaced
#: ~117 min" (12 kept). `_expected_ms`'s local trend is fit through nothing
#: but the kept anchors themselves; with only a couple of them the "trend" is
#: barely more than a straight line between two points, and a handful of
#: ordinary fuzzy-match candidates landing on one side of it by chance reads
#: as a "displaced run" exactly as easily as a genuine reordered block would.
#: A book with a real reordering and a healthy-sized anchor pool (hundreds,
#: for a full novel — see `test_swapped_audio_blocks_flagged_degraded`) clears
#: this floor with room to spare; both of issue #620's real false positives
#: (kept=2, kept=12) sit well under it.
MIN_KEPT_ANCHORS_FOR_DEGRADED = 20


@dataclass
class AlignmentDiagnostics:
    """What the anchor filter saw, for degraded-map detection (issue #586).

    Not persisted itself — `sync_engine.save_sync_map` stamps `degraded` /
    `degraded_reason` onto the `SyncMap` row from this.
    """
    raw_anchor_count: int
    kept_anchor_count: int
    rejected_anchor_count: int
    rejected_fraction: float
    #: Length of the longest contiguous run of rejected anchors displaced in
    #: the same direction by more than `DISPLACED_ANCHOR_OFFSET_MS`.
    displaced_run_length: int
    #: Mean signed offset (ms) of that run, or None if there is no such run.
    displaced_offset_ms: Optional[int]
    degraded: bool
    reason: str = ""


def _expected_ms(epub_idx: int, kept_with_ms: List[Tuple[int, float]]) -> Optional[float]:
    """Linearly interpolate (or, at the ends, hold) the audio time the kept
    anchor sequence's local trend predicts for `epub_idx`."""
    if not kept_with_ms:
        return None
    import bisect
    idxs = [k[0] for k in kept_with_ms]
    pos = bisect.bisect_left(idxs, epub_idx)
    if pos == 0:
        return kept_with_ms[0][1]
    if pos == len(kept_with_ms):
        return kept_with_ms[-1][1]
    e0, m0 = kept_with_ms[pos - 1]
    e1, m1 = kept_with_ms[pos]
    if e1 == e0:
        return m0
    frac = (epub_idx - e0) / (e1 - e0)
    return m0 + frac * (m1 - m0)


def _diagnose_anchor_rejection(
    raw_anchors: List[Tuple[int, int, float]],
    kept_anchors: List[Tuple[int, int, float]],
    whisper_sentences: List["TranscribedSentence"],
) -> AlignmentDiagnostics:
    """Classify what the LIS anchor filter rejected.

    `raw_anchors` and `kept_anchors` are both sorted by `epub_idx`, and
    `kept_anchors` is a subsequence of `raw_anchors` (that's what the LIS
    filter guarantees) — so walking `raw_anchors` in order and checking
    membership in `kept_anchors` finds exactly the *contiguous* runs of
    candidates the filter dropped, with no kept anchor breaking up a run.
    """
    raw_n = len(raw_anchors)
    kept_n = len(kept_anchors)
    rejected_n = raw_n - kept_n
    rejected_fraction = (rejected_n / raw_n) if raw_n else 0.0

    empty = AlignmentDiagnostics(
        raw_anchor_count=raw_n, kept_anchor_count=kept_n,
        rejected_anchor_count=rejected_n, rejected_fraction=rejected_fraction,
        displaced_run_length=0, displaced_offset_ms=None, degraded=False,
    )
    if raw_n == 0 or rejected_n == 0:
        return empty

    kept_epub_idxs = {a[0] for a in kept_anchors}
    kept_with_ms = sorted(
        (a[0], float(whisper_sentences[a[1]].start_ms)) for a in kept_anchors
    )

    best_run_len = 0
    best_run_offset: Optional[float] = None
    cur_run: List[float] = []

    def flush():
        nonlocal best_run_len, best_run_offset
        if len(cur_run) >= MIN_DISPLACED_RUN_LENGTH and len(cur_run) > best_run_len:
            best_run_len = len(cur_run)
            best_run_offset = sum(cur_run) / len(cur_run)

    for epub_idx, whisper_idx, _score in raw_anchors:
        if epub_idx in kept_epub_idxs:
            flush()
            cur_run = []
            continue
        expected = _expected_ms(epub_idx, kept_with_ms)
        if expected is None:
            flush()
            cur_run = []
            continue
        offset = whisper_sentences[whisper_idx].start_ms - expected
        if abs(offset) > DISPLACED_ANCHOR_OFFSET_MS and (
            not cur_run or (offset > 0) == (cur_run[-1] > 0)
        ):
            cur_run.append(offset)
        else:
            flush()
            cur_run = [offset] if abs(offset) > DISPLACED_ANCHOR_OFFSET_MS else []
    flush()

    degraded = (
        rejected_fraction >= DEGRADED_REJECTED_FRACTION
        and best_run_len >= MIN_DISPLACED_RUN_LENGTH
        and kept_n >= MIN_KEPT_ANCHORS_FOR_DEGRADED
    )
    reason = ""
    if (
        rejected_fraction >= DEGRADED_REJECTED_FRACTION
        and best_run_len >= MIN_DISPLACED_RUN_LENGTH
        and kept_n < MIN_KEPT_ANCHORS_FOR_DEGRADED
    ):
        logger.info(
            "Anchor rejection looked degraded (%d/%d rejected, run of %d) but "
            "only %d anchors survived the filter — below "
            "MIN_KEPT_ANCHORS_FOR_DEGRADED (%d), so the trend it was judged "
            "against is too sparse to trust; not classifying as degraded.",
            rejected_n, raw_n, best_run_len, kept_n, MIN_KEPT_ANCHORS_FOR_DEGRADED,
        )
    if degraded:
        offset_min = (best_run_offset or 0) / 60_000
        reason = (
            f"{rejected_n}/{raw_n} raw anchors ({rejected_fraction:.0%}) rejected by the "
            f"increasing-sequence filter, including a run of {best_run_len} consecutive "
            f"anchors displaced ~{offset_min:.0f} min from the kept sequence — the audio "
            f"likely contains a reordered block."
        )
        logger.warning(
            "Degraded alignment: %d/%d anchors rejected (%.0f%%), displaced run of "
            "%d anchors offset ~%.0f min",
            rejected_n, raw_n, rejected_fraction * 100, best_run_len, offset_min,
        )

    return AlignmentDiagnostics(
        raw_anchor_count=raw_n, kept_anchor_count=kept_n,
        rejected_anchor_count=rejected_n, rejected_fraction=rejected_fraction,
        displaced_run_length=best_run_len,
        displaced_offset_ms=int(best_run_offset) if best_run_offset is not None else None,
        degraded=degraded, reason=reason,
    )


def _normalize_text(text: str) -> str:
    """Normalize text for comparison by lowering and stripping punctuation."""
    import re
    text = text.lower().strip()
    text = re.sub(r'[^\w\s]', '', text)
    text = re.sub(r'\s+', ' ', text)
    return text


def _compute_similarity_matrix(
    epub_sentences: List[EpubSentence],
    whisper_sentences: List[TranscribedSentence],
) -> List[List[float]]:
    """
    Compute a similarity matrix between EPUB and Whisper sentences.
    Uses rapidfuzz token_sort_ratio for fuzzy matching.

    Returns a 2D list where matrix[i][j] is the similarity score
    between epub_sentences[i] and whisper_sentences[j].
    """
    logger.info(
        f"Computing similarity matrix: {len(epub_sentences)} x {len(whisper_sentences)}"
    )

    matrix = []
    for epub_sent in epub_sentences:
        row = []
        epub_norm = _normalize_text(epub_sent.text)
        for whisper_sent in whisper_sentences:
            whisper_norm = _normalize_text(whisper_sent.text)
            score = fuzz.token_sort_ratio(epub_norm, whisper_norm) / 100.0
            row.append(score)
        matrix.append(row)

    return matrix


def _align_with_dtw(
    epub_sentences: List[EpubSentence],
    whisper_sentences: List[TranscribedSentence],
    similarity_matrix: List[List[float]],
) -> List[Tuple[int, int, float]]:
    """
    Use Dynamic Time Warping (DTW) to find the optimal alignment
    between EPUB and Whisper sentence sequences.

    Both sequences should be roughly in the same order (they're both
    the same book), so DTW is ideal — it handles insertions/deletions
    (extra or missing sentences) while preserving order.

    Returns list of (epub_index, whisper_index, confidence) tuples.
    """
    n = len(epub_sentences)
    m = len(whisper_sentences)

    if n == 0 or m == 0:
        return []

    # DTW cost matrix (we want to maximize similarity, so use 1 - score as cost)
    cost = np.full((n + 1, m + 1), float('inf'))
    cost[0][0] = 0.0

    # Backtracking matrix
    # 0 = diagonal (match), 1 = up (skip epub), 2 = left (skip whisper)
    back = np.zeros((n + 1, m + 1), dtype=int)

    for i in range(1, n + 1):
        for j in range(1, m + 1):
            sim = similarity_matrix[i - 1][j - 1]
            match_cost = cost[i - 1][j - 1] + (1.0 - sim)
            skip_epub = cost[i - 1][j] + 0.5  # Penalty for skipping
            skip_whisper = cost[i][j - 1] + 0.5

            if match_cost <= skip_epub and match_cost <= skip_whisper:
                cost[i][j] = match_cost
                back[i][j] = 0  # diagonal
            elif skip_epub <= skip_whisper:
                cost[i][j] = skip_epub
                back[i][j] = 1  # up
            else:
                cost[i][j] = skip_whisper
                back[i][j] = 2  # left

    # Backtrack to find the alignment path
    alignments = []
    i, j = n, m
    while i > 0 and j > 0:
        if back[i][j] == 0:  # diagonal = match
            confidence = similarity_matrix[i - 1][j - 1]
            alignments.append((i - 1, j - 1, confidence))
            i -= 1
            j -= 1
        elif back[i][j] == 1:  # up = skip epub sentence
            i -= 1
        else:  # left = skip whisper sentence
            j -= 1

    alignments.reverse()
    return alignments


def _find_anchors(
    epub_sentences: List[EpubSentence],
    whisper_sentences: List[TranscribedSentence],
    min_len: int = 40,
    stride: int = 10,
    key_len: int = 40,
) -> List[Tuple[int, int, float]]:
    """
    Find unique anchor matches across the WHOLE book.

    For every stride-th EPUB sentence of at least `min_len` normalized
    characters, take a `key_len`-character slice from its middle and look for
    it in the whole transcript, concatenated. A slice that occurs exactly once
    is a landmark: it anchors that EPUB sentence to the transcript sentence the
    slice starts in. A slice that occurs more than once is a repeated phrase and
    is skipped; one that never occurs is transcription noise and is skipped. The
    longest increasing subsequence of whisper indices then drops order-violating
    matches, as before.

    This used to fuzzy-score each EPUB sentence against individual transcript
    sentences with `token_set_ratio` (issues #648, #650). That scorer rates a
    SHORT fragment ~100 against a long sentence that merely contains its words,
    and narration is full of them ("He said.", "She nodded."). Two such
    fragments far apart always tied, so the uniqueness check rejected nearly
    every candidate as "ambiguous": a real 31-hour book kept 2 anchors, whose
    "best matches" were a median 3 characters long. With nothing in between,
    whole books were proportionally chunk-aligned between three pins and
    drifted by up to 2.6 hours. Matching a slice against the CONCATENATED
    transcript also sidesteps Whisper's sentence splitting entirely, because it
    never compares one sentence with one sentence. On the same books it keeps
    hundreds of anchors, puts every scored chapter within a minute of the
    narration, and aligns ~40x faster.

    Returns (anchors, raw_anchors): `anchors` is [(epub_idx, whisper_idx, score
    0..1)] sorted by epub_idx with strictly increasing whisper_idx; `raw_anchors`
    is every candidate that was found exactly once, before the LIS filter —
    `anchors` is a subsequence of it. Callers that only want the filtered
    anchors (the alignment path) use `anchors`; degraded-map detection (issue
    #586) diffs the two to see what got rejected and why. An exact unique match
    is certain rather than scored, so its score is 1.0.
    """
    import bisect

    parts: List[str] = []
    starts: List[int] = []
    pos = 0
    for w in whisper_sentences:
        norm = _normalize_text(w.text)
        starts.append(pos)
        parts.append(norm)
        pos += len(norm) + 1          # the joining space
    transcript = " ".join(parts)

    candidates = [
        i for i in range(len(epub_sentences))
        if len(_normalize_text(epub_sentences[i].text)) >= min_len
    ]

    def _locate(i: int) -> Optional[Tuple[int, int, float]]:
        e_norm = _normalize_text(epub_sentences[i].text)
        mid = len(e_norm) // 2
        key = e_norm[max(0, mid - key_len // 2): mid + key_len // 2]
        at = transcript.find(key)
        if at < 0:
            return None  # not narrated as written, or transcribed differently
        if transcript.find(key, at + 1) >= 0:
            return None  # ambiguous: the audio says this more than once
        return (i, bisect.bisect_right(starts, at) - 1, 1.0)

    raw_anchors: List[Tuple[int, int, float]] = []
    for i in candidates[::stride]:
        hit = _locate(i)
        if hit:
            raw_anchors.append(hit)

    # Search the two boundary regions at full density. Sampling every stride-th
    # sentence lands the outermost anchors up to `stride` sentences inside the
    # narration, and the virtual end anchors then pin whatever lies beyond them
    # to the very start and end of the audio. When the EPUB carries text the
    # audiobook never narrates — back matter, a bonus excerpt, front matter
    # (issue #648) — the last narrated sentences share a segment with that
    # unnarrated block and get squeezed early. Anchoring right up to where the
    # narration actually stops confines the unnarrated text to a segment of its
    # own, with no narrated sentence left inside it. Only the boundaries are
    # scanned densely, so the cost is bounded by the size of those regions.
    if raw_anchors:
        first_e = raw_anchors[0][0]
        last_e = raw_anchors[-1][0]
        sampled = {a[0] for a in raw_anchors}
        edge = [i for i in candidates if (i < first_e or i > last_e) and i not in sampled]
        for i in edge:
            hit = _locate(i)
            if hit:
                raw_anchors.append(hit)

    raw_anchors.sort(key=lambda a: a[0])
    anchors = _filter_consistent_anchors(raw_anchors, whisper_sentences)
    logger.info(
        f"Anchors: {len(candidates)} candidates, {len(raw_anchors)} raw, "
        f"{len(anchors)} after LIS + consistency filter"
    )
    return anchors, raw_anchors


def _longest_increasing_subsequence(
    anchors: List[Tuple[int, int, float]],
) -> List[Tuple[int, int, float]]:
    """Longest subsequence with strictly increasing whisper indices.
    O(n^2) DP — anchors number in the hundreds, so this is instant."""
    n = len(anchors)
    if n == 0:
        return []
    lengths = [1] * n
    prev = [-1] * n
    for i in range(1, n):
        for j in range(i):
            if anchors[j][1] < anchors[i][1] and lengths[j] + 1 > lengths[i]:
                lengths[i] = lengths[j] + 1
                prev[i] = j
    best_end = max(range(n), key=lambda k: lengths[k])
    result = []
    k = best_end
    while k != -1:
        result.append(anchors[k])
        k = prev[k]
    result.reverse()
    return result


# ---------------------------------------------------------------------------
# Outlier-anchor filtering (issue #595)
#
# Plain LIS maximizes chain *length* only — it has no notion of "consistent
# pace". A single false anchor with an inflated whisper index rides along for
# free whenever nothing else competes for its epub slot (a noisy stretch, a
# run of short/ambiguous sentences): including it costs the chain nothing,
# because no correct candidate exists there to lose. DTW/interpolation on
# either side of it is then pulled toward the wrong position across the whole
# gap — exact, then a region displaced hours ahead, decaying back to exact
# once real anchors resume. That is the shape in the issue's own evidence,
# and `test_far_ahead_anchor_does_not_displace_the_gap_it_creates` in
# test_alignment.py reproduces it with a single injected anchor.
#
# The fix looks at what the *plain* LIS kept, not just what it rejected
# (`_diagnose_anchor_rejection`, issue #586, already does the rejected side):
# judge each kept anchor against the local trend its *other* kept neighbours
# imply, drop the ones that are inconsistent with it, and re-run LIS on the
# reduced pool — which also lets back in any genuinely consistent raw anchors
# that were only excluded because they conflicted with the bad one.
# ---------------------------------------------------------------------------

#: Refinement passes are bounded, not unbounded-looped: each pass removes at
#: most one anchor, so this is also the max number of outliers one call can
#: ever remove. Real books have a handful at most; the cap guards against a
#: pathological input looping needlessly.
MAX_ANCHOR_REFINEMENT_PASSES = 20


def _trend_outliers(
    kept: List[Tuple[int, int, float]],
    whisper_sentences: List["TranscribedSentence"],
    tolerance_ms: int = DISPLACED_ANCHOR_OFFSET_MS,
) -> List[Tuple[Tuple[int, int, float], float]]:
    """(anchor, |actual - expected| ms) for every kept anchor whose audio
    position is far from what their *other* kept neighbours' local trend
    predicts, worst first.

    Leave-one-out via `_expected_ms`: each anchor is judged against the trend
    implied by the rest of the kept set (never against itself), so a single
    outlier is checked against the consistent majority around it. Fewer than
    3 kept anchors carries no trend to check against, and is left alone.
    """
    if len(kept) < 3:
        return []
    kept_with_ms = [(a[0], float(whisper_sentences[a[1]].start_ms)) for a in kept]
    outliers = []
    for idx, (epub_idx, ms) in enumerate(kept_with_ms):
        others = kept_with_ms[:idx] + kept_with_ms[idx + 1:]
        expected = _expected_ms(epub_idx, others)
        if expected is None:
            continue
        deviation = abs(ms - expected)
        if deviation > tolerance_ms:
            outliers.append((kept[idx], deviation))
    outliers.sort(key=lambda pair: pair[1], reverse=True)
    return outliers


def _filter_consistent_anchors(
    raw_anchors: List[Tuple[int, int, float]],
    whisper_sentences: List["TranscribedSentence"],
) -> List[Tuple[int, int, float]]:
    """The longest increasing subsequence of `raw_anchors`, refined to drop
    anchors that are individually inconsistent with their kept neighbours'
    local trend (issue #595) — see the module comment above for why plain LIS
    alone lets a lone far-ahead anchor through.

    Only the single *worst* outlier is removed per pass, not every anchor
    `_trend_outliers` flags in one go: a genuinely bad anchor distorts the
    local trend enough that its innocent neighbours can look inconsistent
    too, in the very same pass, purely because the bad one is still
    contaminating their "expected" value. Removing the worst offender first
    and re-checking on a clean(er) trend is what keeps a single bad anchor
    from taking a correct neighbour down with it.

    Each pass removes that one anchor from the candidate pool (not just from
    the kept list) and re-runs LIS, so previously-blocked raw anchors that
    only lost out to the bad one get a chance to be kept instead.
    `raw_anchors` itself is untouched — issue #586's degraded diagnostics
    diffs the caller's original raw list against this function's (now
    possibly smaller) result, and that still holds: the result is built by
    filtering `raw_anchors` down, so it stays a subsequence of it.
    """
    pool = list(raw_anchors)
    kept = _longest_increasing_subsequence(pool)
    for _ in range(MAX_ANCHOR_REFINEMENT_PASSES):
        outliers = _trend_outliers(kept, whisper_sentences)
        if not outliers:
            break
        worst_anchor, _deviation = outliers[0]
        pool = [a for a in pool if a != worst_anchor]
        kept = _longest_increasing_subsequence(pool)
    return kept


# ---------------------------------------------------------------------------
# Chunked-segment drift (issue #620)
#
# `_chunk_align` walks the epub side in FIXED `chunk_size`-sentence steps and
# sizes each chunk's whisper-side window from ONE ratio (`m/n`) averaged over
# the WHOLE segment it was given. That average is only ever exactly right if
# the true epub:whisper sentence-count ratio is uniform across the segment.
# When it is not — a stretch with more footnotes, captions or chapter
# headings than the rest (present in the epub, never spoken), or any other
# place the epub and transcript sentence lists diverge in density — a
# chunk's window ends up the wrong size, the sentence it should match is not
# even a candidate inside it, and DTW is forced to pick the best AVAILABLE
# (wrong) candidate instead: a *confident* match, just to the wrong audio.
# The next chunk's window is then rebased from that wrong position
# (`whisper_start = whisper_start + last_wi + 1`), so the error carries
# forward and compounds — smoothly, one small step per chunk, not a single
# jump — until the true content reappears inside a window and it corrects.
# That is exactly the "runs increasingly off, then recovers" shape reported:
# reproduced directly in this fix's regression test with a segment whose
# middle third has extra unspoken content and nothing else unusual about it.
#
# A segment `_chunk_align` is asked to cover is always bounded by two real
# (or virtual book-start/end) anchors, which gives a reference this problem
# doesn't have to solve for itself: those two anchors' own timestamps imply
# a straight line across the segment, independent of chunking entirely.
# `_filter_chunked_segment_drift` checks the chunked output against that
# line and demotes a sustained displaced run back to "unmatched" — the
# existing re-interpolation pass in `_align_texts_impl` then fills the run
# in from whatever real matches remain on either side of it (for a fully
# displaced run, that is the segment's own two bracketing anchors, i.e. the
# same straight line, which is a strictly better estimate than a run of
# confidently wrong chunked matches).
# ---------------------------------------------------------------------------

#: A chunked-segment point is "drifted" from the segment's own
#: boundary-to-boundary straight line when it is off by more than this.
#: Matches `DISPLACED_ANCHOR_OFFSET_MS` — ordinary pacing variation within a
#: book (a slower scene, a pause) is seconds, not minutes.
CHUNK_DRIFT_OFFSET_MS = 3 * 60 * 1000  # 3 minutes

#: A run shorter than this is left alone — chunked-segment output is one
#: point per *sentence*, far denser than the widely-spaced samples the
#: audit's own run-length rules (`MIN_DISPLACED_RUN_LENGTH`,
#: `TIMING_MISMATCH_RUN_LENGTH`, both 3) check, so a run long enough to mean
#: something here needs to be longer too: a handful of sentences legitimately
#: narrated at an unusual pace (a long pause, a musical cue noted in the
#: transcript) can drift briefly without being wrong, and must not be thrown
#: away on that alone.
CHUNK_DRIFT_MIN_RUN = 15

#: Safety bound (issue #635). `expected_ms` is a SINGLE straight line drawn
#: across the *entire* segment, from one bracketing anchor to the other. That
#: line is only an accurate reference when the epub:whisper density is
#: uniform across the whole segment — but a sparse-anchor segment big enough
#: to need `_chunk_align` in the first place (up to a whole book, when
#: `len(anchors) < 3`) is exactly where density is least likely to be
#: uniform end-to-end. Wherever it is not, points that `_chunk_align` matched
#: CORRECTLY still sit far from that single chord, for the whole remainder
#: of the segment on the far side of the density change: a real book with a
#: faster- or slower-paced stretch (dialogue vs. descriptive narration, not
#: a parsing anomaly) is enough to trigger it. Because every point past that
#: change deviates from the chord in the same direction, the run-length rule
#: above (`CHUNK_DRIFT_MIN_RUN`) does nothing to contain it — "sustained
#: displacement" and "sustained but correct, relative to a bad reference"
#: look identical to it. The production numbers behind #635 show the result:
#: up to 98% of a segment's matches demoted, timing mismatches going UP after
#: "fixing" them.
#:
#: A genuinely displaced run — the case this filter exists to catch — is a
#: MINORITY of a gap's points; #628's own regression fixture demotes under
#: 2% of a 3,248-point segment for a real localized error. So: if applying
#: the run-demotion above would demote more than this fraction of a
#: segment's points, the straight-line reference itself is the more likely
#: thing that's wrong, not `_chunk_align`'s output — leave the chunked
#: output untouched rather than collapsing most of a segment into a
#: two-point interpolation that #635 shows is typically a worse estimate
#: than what chunked DTW already had. 30% (vs. observed regressions of
#: 39%-98%, and a real correction under 2%) leaves comfortable headroom on
#: both sides; there is no single "correct" number, only "far below the
#: regressions and far above real corrections seen so far."
CHUNK_DRIFT_MAX_DEMOTION_FRACTION = 0.30


def _filter_chunked_segment_drift(
    seg_alignments: List[Tuple[int, int, float]],
    epub_span: int,
    boundary_start_ms: float,
    boundary_end_ms: float,
    whisper_sentences: List["TranscribedSentence"],
    ws: int,
) -> List[Tuple[int, int, float]]:
    """Demote a sustained run of `_chunk_align` output that drifted far from
    the segment's own boundary-to-boundary straight line — see the module
    comment above for why chunked DTW can produce exactly this shape.

    `seg_alignments` is local to the segment (`(local_epub_idx,
    local_whisper_idx, confidence)`, `_chunk_align`'s own return shape); `ws`
    is the segment's whisper-side start offset into `whisper_sentences`,
    needed to look up each local whisper index's real timestamp. `epub_span`
    is the segment's epub length — the "x-axis" of the line from
    `boundary_start_ms` (at local epub index 0) to `boundary_end_ms` (at
    `epub_span`).

    Demoted points come back with confidence 0.0, not dropped: the caller's
    own re-interpolation pass (`_align_texts_impl`) fills a confidence-0
    point in from whatever real matches remain on either side of it.

    Bailing out (issue #635): if the runs this would demote add up to more
    than `CHUNK_DRIFT_MAX_DEMOTION_FRACTION` of the segment, `seg_alignments`
    is returned completely unmodified — see that constant's comment. This is
    an all-or-nothing decision per call (per segment), not per run: once the
    reference line has been shown untrustworthy for one part of a segment,
    nothing else it flags in the same segment is good evidence either.
    """
    if epub_span <= 0 or not seg_alignments:
        return seg_alignments

    def expected_ms(local_epub_idx: int) -> float:
        frac = local_epub_idx / epub_span
        return boundary_start_ms + frac * (boundary_end_ms - boundary_start_ms)

    # First pass: find candidate runs without mutating anything, so the
    # total demoted share can be checked before committing to any of it.
    runs: List[List[int]] = []
    current: List[int] = []
    for idx, (ei, wi, _conf) in enumerate(seg_alignments):
        actual_ms = whisper_sentences[wi + ws].start_ms
        if abs(actual_ms - expected_ms(ei)) > CHUNK_DRIFT_OFFSET_MS:
            current.append(idx)
        else:
            if len(current) >= CHUNK_DRIFT_MIN_RUN:
                runs.append(current)
            current = []
    if len(current) >= CHUNK_DRIFT_MIN_RUN:
        runs.append(current)

    if not runs:
        return seg_alignments

    demoted_count = sum(len(run) for run in runs)
    demoted_fraction = demoted_count / len(seg_alignments)
    if demoted_fraction > CHUNK_DRIFT_MAX_DEMOTION_FRACTION:
        logger.warning(
            "_filter_chunked_segment_drift: bailing out — would demote "
            "%d/%d points (%.0f%%) of a %d-sentence segment, over the "
            "%.0f%% safety bound; the boundary-to-boundary reference is "
            "more likely wrong than the chunked output here, so it is left "
            "untouched (issue #635)",
            demoted_count, len(seg_alignments),
            100 * demoted_fraction, epub_span,
            100 * CHUNK_DRIFT_MAX_DEMOTION_FRACTION,
        )
        return seg_alignments

    demoted_idx = {i for run in runs for i in run}
    return [
        (ei, wi, 0.0 if idx in demoted_idx else conf)
        for idx, (ei, wi, conf) in enumerate(seg_alignments)
    ]


def _anchor_align(
    epub_sentences: List[EpubSentence],
    whisper_sentences: List[TranscribedSentence],
    max_segment: int = 600,
    collect_diagnostics: bool = False,
) -> Tuple[List[Tuple[int, int, float]], Optional["AlignmentDiagnostics"]]:
    """
    Align using anchors as fixed waypoints, running DTW only on the bounded
    segments BETWEEN consecutive anchors. A bad region (music, credits,
    skipped front matter) can no longer poison the rest of the book — drift
    is confined to one inter-anchor segment.

    Returns (alignments, diagnostics); diagnostics is None unless
    `collect_diagnostics` is set (issue #586) — computing it is cheap relative
    to the anchor search itself, but callers that don't need it (most of them,
    most of the time) shouldn't pay even that.
    """
    n, m = len(epub_sentences), len(whisper_sentences)

    anchors, raw_anchors = _find_anchors(epub_sentences, whisper_sentences)
    diagnostics = (
        _diagnose_anchor_rejection(raw_anchors, anchors, whisper_sentences)
        if collect_diagnostics else None
    )
    if len(anchors) < 3:
        logger.warning("Too few anchors (%d); falling back to chunked DTW", len(anchors))
        whole_book = _chunk_align(epub_sentences, whisper_sentences)
        if n > 0 and whisper_sentences:
            whole_book = _filter_chunked_segment_drift(
                whole_book, n, 0.0, whisper_sentences[-1].end_ms, whisper_sentences, 0,
            )
        return whole_book, diagnostics

    # Virtual anchors pin the first/last segments.
    waypoints = [(-1, -1, 1.0)] + anchors + [(n, m, 1.0)]

    all_alignments: List[Tuple[int, int, float]] = []
    for (e0, w0, _), (e1, w1, a_conf) in zip(waypoints, waypoints[1:]):
        es, ee = e0 + 1, e1          # epub_sentences[es:ee]
        ws, we = w0 + 1, w1          # whisper_sentences[ws:we]

        if es < ee and ws < we:
            epub_seg = epub_sentences[es:ee]
            whisper_seg = whisper_sentences[ws:we]
            if len(epub_seg) <= max_segment and len(whisper_seg) <= max_segment:
                sim = _compute_similarity_matrix(epub_seg, whisper_seg)
                seg_alignments = _align_with_dtw(epub_seg, whisper_seg, sim)
            else:
                # Huge gap between anchors: the segment's *inputs* are
                # bounded either way, but chunked DTW's own interior output
                # is not pinned to the boundary at every point in between —
                # `_filter_chunked_segment_drift` below is what actually
                # keeps drift from escaping (issue #620; see the module
                # comment above it).
                seg_alignments = _chunk_align(epub_seg, whisper_seg)
                boundary_start_ms = (
                    whisper_sentences[w0].start_ms if w0 >= 0 else 0.0
                )
                boundary_end_ms = (
                    whisper_sentences[w1].start_ms if w1 < m
                    else whisper_sentences[-1].end_ms
                )
                seg_alignments = _filter_chunked_segment_drift(
                    seg_alignments, ee - es, boundary_start_ms, boundary_end_ms,
                    whisper_sentences, ws,
                )
            for ei, wi, conf in seg_alignments:
                all_alignments.append((ei + es, wi + ws, conf))

        # Emit the real anchor itself (skip the virtual end anchor).
        if e1 < n:
            all_alignments.append((e1, w1, a_conf))

    all_alignments.sort(key=lambda t: t[0])
    return all_alignments, diagnostics


def _repair_outliers(
    aligned_points: List["AlignedPoint"],
    window: int = 2,
    max_deviation_ms: int = 60_000,
) -> List["AlignedPoint"]:
    """
    Post-process pass:
    1. Any matched point (confidence > 0) whose audio_start_ms deviates from
       the median of its matched neighbors by more than max_deviation_ms is
       demoted to confidence 0 (so it gets re-interpolated).
    2. Enforce non-decreasing audio_start_ms across the whole list.
    Points are assumed ordered by (chapter, sentence_index) book order.
    """
    matched_idx = [i for i, p in enumerate(aligned_points) if p.confidence > 0]

    for pos, i in enumerate(matched_idx):
        neighbors = [
            aligned_points[matched_idx[j]].audio_start_ms
            for j in range(max(0, pos - window), min(len(matched_idx), pos + window + 1))
            if j != pos
        ]
        if not neighbors:
            continue
        neighbors.sort()
        median = neighbors[len(neighbors) // 2]
        if abs(aligned_points[i].audio_start_ms - median) > max_deviation_ms:
            p = aligned_points[i]
            logger.info(
                f"Outlier demoted: ch{p.epub_chapter} s{p.epub_sentence_index} "
                f"audio={p.audio_start_ms}ms vs neighbor median {median}ms"
            )
            p.confidence = 0.0

    last_ms = 0
    for p in aligned_points:
        if p.audio_start_ms < last_ms:
            p.audio_start_ms = last_ms
            p.audio_end_ms = max(p.audio_end_ms, last_ms)
            p.confidence = 0.0
        last_ms = p.audio_start_ms
    return aligned_points


def _chunk_align(
    epub_sentences: List[EpubSentence],
    whisper_sentences: List[TranscribedSentence],
    chunk_size: int = 200,
    overlap: int = 20,
) -> List[Tuple[int, int, float]]:
    """
    For large books, compute alignment in chunks to avoid memory issues
    with the full similarity matrix.

    The chunking uses overlapping windows with heuristic boundary matching.
    """
    n = len(epub_sentences)
    m = len(whisper_sentences)

    # If small enough, do direct alignment
    if n <= chunk_size and m <= chunk_size:
        sim_matrix = _compute_similarity_matrix(epub_sentences, whisper_sentences)
        return _align_with_dtw(epub_sentences, whisper_sentences, sim_matrix)

    logger.info(f"Using chunked alignment (chunk_size={chunk_size})")

    # Estimate the ratio of epub sentences to whisper sentences
    ratio = m / n if n > 0 else 1.0

    all_alignments = []
    epub_start = 0
    whisper_start = 0

    while epub_start < n:
        epub_end = min(epub_start + chunk_size, n)
        whisper_end = min(whisper_start + int(chunk_size * ratio) + overlap, m)

        epub_chunk = epub_sentences[epub_start:epub_end]
        whisper_chunk = whisper_sentences[whisper_start:whisper_end]

        if not epub_chunk or not whisper_chunk:
            break

        sim_matrix = _compute_similarity_matrix(epub_chunk, whisper_chunk)
        chunk_alignments = _align_with_dtw(epub_chunk, whisper_chunk, sim_matrix)

        # Offset indices back to global positions
        for ei, wi, conf in chunk_alignments:
            all_alignments.append((ei + epub_start, wi + whisper_start, conf))

        # Advance to next chunk, using the last alignment to set the starting point
        if chunk_alignments:
            _, last_wi, _ = chunk_alignments[-1]
            whisper_start = whisper_start + last_wi + 1
        else:
            whisper_start += int(chunk_size * ratio)

        epub_start = epub_end

    return all_alignments


def _align_texts_impl(
    epub_sentences: List[EpubSentence],
    whisper_sentences: List[TranscribedSentence],
    min_confidence: float = 0.3,
    collect_diagnostics: bool = False,
) -> Tuple[List[AlignedPoint], Optional[AlignmentDiagnostics]]:
    """
    Align EPUB sentences to Whisper transcribed sentences.

    This is the main entry point for the alignment algorithm. It:
    1. Computes sentence-level similarity using fuzzy matching
    2. Uses DTW to find the optimal global alignment
    3. Filters low-confidence matches
    4. Interpolates timestamps for unmatched EPUB sentences

    Args:
        epub_sentences: Sentences extracted from the EPUB
        whisper_sentences: Sentences from Whisper transcription
        min_confidence: Minimum similarity score to keep a match
        collect_diagnostics: also compute `AlignmentDiagnostics` (issue #586).
            Shared by `align_texts` (which discards it, to stay a drop-in for
            every existing caller/test) and `align_texts_with_diagnostics`.

    Returns:
        (aligned_points, diagnostics) — one AlignedPoint per EPUB sentence;
        diagnostics is None unless `collect_diagnostics` is set.
    """
    logger.info(
        f"Aligning {len(epub_sentences)} EPUB sentences to "
        f"{len(whisper_sentences)} Whisper sentences"
    )

    if not epub_sentences or not whisper_sentences:
        logger.warning("Empty sentence lists — nothing to align")
        return [], None

    # Get raw alignment (anchor-bounded DTW — drift confined between anchors)
    raw_alignments, diagnostics = _anchor_align(
        epub_sentences, whisper_sentences, collect_diagnostics=collect_diagnostics
    )

    # Build a map from epub index to whisper index + confidence
    epub_to_whisper = {}
    for epub_idx, whisper_idx, confidence in raw_alignments:
        if confidence >= min_confidence:
            epub_to_whisper[epub_idx] = (whisper_idx, confidence)

    logger.info(
        f"DTW produced {len(raw_alignments)} alignments, "
        f"{len(epub_to_whisper)} above confidence threshold"
    )

    # Generate AlignedPoints for all EPUB sentences
    aligned_points = []

    for i, epub_sent in enumerate(epub_sentences):
        if i in epub_to_whisper:
            whisper_idx, confidence = epub_to_whisper[i]
            ws = whisper_sentences[whisper_idx]
            aligned_points.append(AlignedPoint(
                epub_chapter=epub_sent.chapter,
                epub_sentence_index=epub_sent.sentence_index,
                epub_text_preview=epub_sent.text[:200],
                audio_start_ms=ws.start_ms,
                audio_end_ms=ws.end_ms,
                confidence=confidence,
            ))
        else:
            # Interpolate timestamp from nearest matched neighbors
            start_ms, end_ms = _interpolate_timestamp(
                i, epub_to_whisper, whisper_sentences, len(epub_sentences)
            )
            aligned_points.append(AlignedPoint(
                epub_chapter=epub_sent.chapter,
                epub_sentence_index=epub_sent.sentence_index,
                epub_text_preview=epub_sent.text[:200],
                audio_start_ms=start_ms,
                audio_end_ms=end_ms,
                confidence=0.0,  # Interpolated, not directly matched
            ))

    # Repair outliers / enforce monotonic times, then re-interpolate
    # timestamps for everything that is now unmatched.
    aligned_points = _repair_outliers(aligned_points)
    import bisect
    matched_ms = {i: (p.audio_start_ms, p.audio_end_ms)
                  for i, p in enumerate(aligned_points) if p.confidence > 0}
    matched_keys = sorted(matched_ms.keys())
    for i, p in enumerate(aligned_points):
        if p.confidence > 0:
            continue
        pos = bisect.bisect_left(matched_keys, i)
        prev_k = matched_keys[pos - 1] if pos > 0 else None
        next_k = matched_keys[pos] if pos < len(matched_keys) else None
        if prev_k is not None and next_k is not None:
            frac = (i - prev_k) / (next_k - prev_k)
            start = int(matched_ms[prev_k][1] + frac * (matched_ms[next_k][0] - matched_ms[prev_k][1]))
        elif prev_k is not None:
            start = matched_ms[prev_k][1]
        elif next_k is not None:
            start = max(0, matched_ms[next_k][0] - 1000)
        else:
            start = 0
        p.audio_start_ms = start
        p.audio_end_ms = start + 1000

    # Final clamp: interpolation between overlapping segments could step
    # backwards slightly; guarantee non-decreasing start times.
    last_ms = 0
    for p in aligned_points:
        if p.audio_start_ms < last_ms:
            p.audio_start_ms = last_ms
            p.audio_end_ms = max(p.audio_end_ms, last_ms)
        last_ms = p.audio_start_ms

    logger.info(f"Generated {len(aligned_points)} aligned points "
                f"({len(matched_keys)} matched, {len(aligned_points) - len(matched_keys)} interpolated)")
    return aligned_points, diagnostics


def align_texts(
    epub_sentences: List[EpubSentence],
    whisper_sentences: List[TranscribedSentence],
    min_confidence: float = 0.3,
) -> List[AlignedPoint]:
    """Align EPUB sentences to Whisper transcribed sentences.

    See `_align_texts_impl` for the algorithm. This is the long-standing
    entry point — every existing caller and test gets exactly the same
    return type and values as before; nothing here changes them (issue #586
    added degraded-map detection as a separate, opt-in return via
    `align_texts_with_diagnostics`, not by altering this one).
    """
    points, _ = _align_texts_impl(epub_sentences, whisper_sentences, min_confidence)
    return points


def align_texts_with_diagnostics(
    epub_sentences: List[EpubSentence],
    whisper_sentences: List[TranscribedSentence],
    min_confidence: float = 0.3,
) -> Tuple[List[AlignedPoint], Optional[AlignmentDiagnostics]]:
    """Same as `align_texts`, but also returns `AlignmentDiagnostics` — what
    the anchor filter rejected, and whether that means the map is degraded
    (issue #586). Used by the production pipeline (`queue_manager.py`,
    `realign.py`) so `sync_engine.save_sync_map` can stamp the verdict onto
    the `SyncMap` row; `align_texts` stays a thin wrapper around the same
    implementation so the anchor search is never computed twice.
    """
    return _align_texts_impl(
        epub_sentences, whisper_sentences, min_confidence, collect_diagnostics=True
    )


def _interpolate_timestamp(
    epub_idx: int,
    epub_to_whisper: dict,
    whisper_sentences: List[TranscribedSentence],
    total_epub: int,
) -> Tuple[int, int]:
    """
    For an unmatched EPUB sentence, estimate its audio timestamp by
    interpolating between the nearest matched neighbors.
    """
    # Find nearest matched predecessor
    prev_idx = None
    prev_ms = 0
    for j in range(epub_idx - 1, -1, -1):
        if j in epub_to_whisper:
            prev_idx = j
            w_idx = epub_to_whisper[j][0]
            prev_ms = whisper_sentences[w_idx].end_ms
            break

    # Find nearest matched successor
    next_idx = None
    next_ms = whisper_sentences[-1].end_ms if whisper_sentences else 0
    for j in range(epub_idx + 1, total_epub):
        if j in epub_to_whisper:
            next_idx = j
            w_idx = epub_to_whisper[j][0]
            next_ms = whisper_sentences[w_idx].start_ms
            break

    # Linear interpolation
    if prev_idx is not None and next_idx is not None:
        span = next_idx - prev_idx
        offset = epub_idx - prev_idx
        frac = offset / span if span > 0 else 0
        interp_ms = int(prev_ms + frac * (next_ms - prev_ms))
        return interp_ms, interp_ms + 1000  # Assume ~1s per sentence
    elif prev_idx is not None:
        return prev_ms, prev_ms + 1000
    elif next_idx is not None:
        return max(0, next_ms - 1000), next_ms
    else:
        return 0, 1000  # Fallback
