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
    score_threshold: float = 0.85,
    uniqueness_margin: float = 0.05,
) -> List[Tuple[int, int, float]]:
    """
    Find high-confidence, unique anchor matches across the WHOLE book.

    Long sentences (>= min_len normalized chars) are nearly always unique in
    a novel, so a global fuzzy search with a high threshold gives reliable
    landmarks. We sample every stride-th candidate to bound runtime, require
    the best match to beat the second-best by uniqueness_margin (rejects
    repeated phrases), and keep only the longest increasing subsequence of
    whisper indices (rejects order-violating false positives).

    Returns [(epub_idx, whisper_idx, score 0..1)] sorted by epub_idx with
    strictly increasing whisper_idx.
    """
    from rapidfuzz import process

    whisper_norm = [_normalize_text(w.text) for w in whisper_sentences]
    candidates = [
        i for i in range(len(epub_sentences))
        if len(_normalize_text(epub_sentences[i].text)) >= min_len
    ]

    raw_anchors: List[Tuple[int, int, float]] = []
    for i in candidates[::stride]:
        e_norm = _normalize_text(epub_sentences[i].text)
        matches = process.extract(
            e_norm,
            whisper_norm,
            scorer=fuzz.token_set_ratio,
            limit=2,
            score_cutoff=score_threshold * 100,
        )
        if not matches:
            continue
        _, best_score, best_idx = matches[0]
        if len(matches) > 1 and (best_score - matches[1][1]) < uniqueness_margin * 100:
            continue  # ambiguous: this sentence appears more than once
        raw_anchors.append((i, best_idx, best_score / 100.0))

    raw_anchors.sort(key=lambda a: a[0])
    anchors = _longest_increasing_subsequence(raw_anchors)
    logger.info(
        f"Anchors: {len(candidates)} candidates, {len(raw_anchors)} raw, "
        f"{len(anchors)} after LIS filter"
    )
    return anchors


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


def _anchor_align(
    epub_sentences: List[EpubSentence],
    whisper_sentences: List[TranscribedSentence],
    max_segment: int = 600,
) -> List[Tuple[int, int, float]]:
    """
    Align using anchors as fixed waypoints, running DTW only on the bounded
    segments BETWEEN consecutive anchors. A bad region (music, credits,
    skipped front matter) can no longer poison the rest of the book — drift
    is confined to one inter-anchor segment.
    """
    n, m = len(epub_sentences), len(whisper_sentences)

    anchors = _find_anchors(epub_sentences, whisper_sentences)
    if len(anchors) < 3:
        logger.warning("Too few anchors (%d); falling back to chunked DTW", len(anchors))
        return _chunk_align(epub_sentences, whisper_sentences)

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
                # Huge gap between anchors: chunked DTW is acceptable here
                # because both endpoints are pinned — drift cannot escape
                # this segment.
                seg_alignments = _chunk_align(epub_seg, whisper_seg)
            for ei, wi, conf in seg_alignments:
                all_alignments.append((ei + es, wi + ws, conf))

        # Emit the real anchor itself (skip the virtual end anchor).
        if e1 < n:
            all_alignments.append((e1, w1, a_conf))

    all_alignments.sort(key=lambda t: t[0])
    return all_alignments


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


def align_texts(
    epub_sentences: List[EpubSentence],
    whisper_sentences: List[TranscribedSentence],
    min_confidence: float = 0.3,
) -> List[AlignedPoint]:
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

    Returns:
        List of AlignedPoint objects, one per EPUB sentence.
    """
    logger.info(
        f"Aligning {len(epub_sentences)} EPUB sentences to "
        f"{len(whisper_sentences)} Whisper sentences"
    )

    if not epub_sentences or not whisper_sentences:
        logger.warning("Empty sentence lists — nothing to align")
        return []

    # Get raw alignment (anchor-bounded DTW — drift confined between anchors)
    raw_alignments = _anchor_align(epub_sentences, whisper_sentences)

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
    return aligned_points


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
