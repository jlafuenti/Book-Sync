"""
Text Alignment Service

Aligns EPUB sentences to Whisper-transcribed sentences using fuzzy
string matching and sequence alignment. This is the core algorithm
that creates the bridge between reading and listening.
"""

import logging
from dataclasses import dataclass
from typing import List, Tuple, Optional

from rapidfuzz import fuzz
import numpy as np

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

    # Get raw alignment
    raw_alignments = _chunk_align(epub_sentences, whisper_sentences)

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

    logger.info(f"Generated {len(aligned_points)} aligned points")
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
