"""
Shared ebook->audio matching algorithm (issue #41).

This is the single source of truth for turning a snippet of extracted EPUB text
into a sync point. It is hand-mirrored in Kotlin at
`android/app/src/main/java/com/booksync/sync/SyncMatcher.kt` so the Android app can
match offline; the two implementations are pinned to each other by the golden
vectors in `tests/fixtures/sync_parity/` (see the README there).

**Any change here must change the Kotlin side and the fixtures in the same PR.**

The algorithm, in order:

1. Normalize the extracted text (lowercase, all whitespace -> space, strip anything
   outside ``[a-z0-9 ]``, collapse runs of spaces).
2. Build one concatenated normalized transcript per candidate chapter (the hint
   chapter first, then expanding outward +/-10), remembering where each sentence
   starts so a character offset maps back to a sync point.
3. **Pass 1 - exact.** Progressive substring search (200/150/100/60/30 char prefixes,
   plus a retry that skips the first 30 chars to survive chapter headings) across
   *all* candidate chapters. An exact match anywhere beats a fuzzy match, so a weak
   fuzzy hit in the hint chapter can never shadow the true location next door.
4. **Pass 2 - fuzzy.** Only if pass 1 found nothing: sliding-window bigram Dice
   similarity over the first 150 chars, taking the best score across all candidate
   chapters, accepted at >= 0.60. This tolerates transcription wording differences
   ("Mr." vs "mister", mishears) that defeat substring search.
5. Whichever pass hit, nudge an interpolated point (confidence == 0) to the nearest
   genuinely transcribed neighbour.

Sync points are duck-typed: only ``epub_chapter``, ``epub_sentence_index``,
``epub_text_preview`` and ``confidence`` are read.
"""

import re
from dataclasses import dataclass, field
from typing import Protocol

# Whitespace variants that must collapse to a plain space before punctuation is
# stripped (non-breaking space in particular is very common in EPUBs).
_WHITESPACE_VARIANTS = "\n\r\t    ​ "

_NON_SEARCHABLE = re.compile(r"[^a-z0-9 ]")
_SPACE_RUN = re.compile(r" +")

#: Prefix lengths tried by the exact pass, longest first.
SEARCH_LENGTHS = [200, 150, 100, 60, 30]
#: Chars skipped by the exact pass's second attempt (drops a chapter heading).
HEADING_SKIP = 30
#: Length of the needle handed to the fuzzy pass.
FUZZY_NEEDLE_LEN = 150
#: Minimum Dice similarity for the fuzzy pass to accept a window.
FUZZY_THRESHOLD = 0.60


class MatchablePoint(Protocol):
    """The only attributes the matcher reads off a sync point."""

    epub_chapter: int
    epub_sentence_index: int
    epub_text_preview: str | None
    confidence: float


def normalize_for_search(text: str) -> str:
    """Normalize text for matching - mirrored by Kotlin ``SyncMatcher.normalizeForSearch``."""
    t = text.lower()
    for ch in _WHITESPACE_VARIANTS:
        t = t.replace(ch, " ")
    t = _NON_SEARCHABLE.sub("", t)
    t = _SPACE_RUN.sub(" ", t)
    return t.strip()


def bigram_set(s: str) -> set[str]:
    """Character-bigram set of a normalized string."""
    return {s[i : i + 2] for i in range(len(s) - 1)}


def dice_similarity(a: set[str], b: set[str]) -> float:
    """Dice coefficient between two bigram sets: 2*|A and B| / (|A|+|B|), 0..1."""
    if not a or not b:
        return 0.0
    return 2.0 * len(a & b) / (len(a) + len(b))


def fuzzy_find_in_transcript(
    transcript: str,
    needle: str,
    threshold: float = FUZZY_THRESHOLD,
) -> tuple[int, float] | None:
    """
    Sliding-window fuzzy search: the offset in *transcript* whose window best matches
    *needle* by bigram Dice similarity, as ``(char_offset, score)``, or ``None`` if the
    best score is below *threshold*.

    Scans coarsely at ``max(10, len(needle) // 4)``, then refines around the best hit
    with step 1 for a tighter offset.
    """
    if len(needle) < 20 or len(transcript) < len(needle):
        return None

    needle_bigrams = bigram_set(needle)
    window = len(needle)
    step = max(10, window // 4)

    best_offset = -1
    best_score = 0.0
    for offset in range(0, len(transcript) - window + 1, step):
        score = dice_similarity(needle_bigrams, bigram_set(transcript[offset : offset + window]))
        if score > best_score:
            best_score = score
            best_offset = offset

    if best_offset < 0 or best_score < threshold:
        return None

    refined_offset = best_offset
    refined_score = best_score
    lo = max(0, best_offset - step)
    hi = min(len(transcript) - window, best_offset + step)
    for o in range(lo, hi + 1):
        s = dice_similarity(needle_bigrams, bigram_set(transcript[o : o + window]))
        if s > refined_score:
            refined_score = s
            refined_offset = o
    return refined_offset, refined_score


def nudge_to_confident_point(points: list[MatchablePoint], matched_idx: int) -> MatchablePoint:
    """
    If the matched point is interpolated (confidence == 0), prefer the nearest genuinely
    transcribed point (confidence > 0.5) within +/-3 list positions - its timestamp came
    from the transcript rather than from interpolation.
    """
    matched = points[matched_idx]
    if matched.confidence > 0:
        return matched
    lo = max(0, matched_idx - 3)
    hi = min(len(points) - 1, matched_idx + 3)
    nearby = [p for p in points[lo : hi + 1] if p.confidence > 0.5]
    if not nearby:
        return matched
    return min(nearby, key=lambda p: abs(p.epub_sentence_index - matched.epub_sentence_index))


@dataclass
class _ChapterTranscript:
    chapter: int
    points: list[MatchablePoint]
    transcript: str
    # (start_char_index, point_index) for each sentence that made it into the transcript
    boundaries: list[tuple[int, int]] = field(default_factory=list)

    def point_at_offset(self, match_index: int) -> MatchablePoint:
        matched_idx = 0
        for start_pos, idx in self.boundaries:
            if start_pos <= match_index:
                matched_idx = idx
            else:
                break
        return nudge_to_confident_point(self.points, matched_idx)


def _build_chapter_transcripts(
    points: list[MatchablePoint], chapter_hint: int
) -> list[_ChapterTranscript]:
    by_chapter: dict[int, list[MatchablePoint]] = {}
    for p in points:
        by_chapter.setdefault(p.epub_chapter, []).append(p)
    for ch in by_chapter:
        by_chapter[ch].sort(key=lambda p: p.epub_sentence_index)

    # Hint chapter first, then expanding outward: hint-1, hint+1, hint-2, hint+2, ...
    chapters_to_try = [chapter_hint]
    for d in range(1, 11):
        chapters_to_try.extend([chapter_hint - d, chapter_hint + d])

    built: list[_ChapterTranscript] = []
    for target in chapters_to_try:
        chapter_points = by_chapter.get(target)
        if not chapter_points:
            continue

        parts: list[str] = []
        boundaries: list[tuple[int, int]] = []
        pos = 0
        for idx, point in enumerate(chapter_points):
            preview = point.epub_text_preview
            if not preview:
                continue
            normalized = normalize_for_search(preview)
            if not normalized:
                continue
            boundaries.append((pos, idx))
            parts.append(normalized)
            pos += len(normalized) + 1  # +1 for the space separator

        if not parts:
            continue
        # Trailing space included so the transcript is byte-identical to Kotlin's.
        transcript = " ".join(parts) + " "
        built.append(_ChapterTranscript(target, chapter_points, transcript, boundaries))
    return built


def match_text_to_sync_points(
    sync_points: list[MatchablePoint],
    epub_text: str,
    chapter_hint: int,
) -> MatchablePoint | None:
    """Find the sync point matching extracted EPUB text, or ``None``."""
    normalized_epub = normalize_for_search(epub_text)
    if len(normalized_epub) < 10:
        return None

    chapter_transcripts = _build_chapter_transcripts(sync_points, chapter_hint)
    if not chapter_transcripts:
        return None

    search_lengths = sorted(
        {min(len(normalized_epub), length) for length in SEARCH_LENGTHS if min(len(normalized_epub), length) > 10},
        reverse=True,
    )

    # PASS 1 - exact substring, across every candidate chapter.
    for ct in chapter_transcripts:
        for search_len in search_lengths:
            match_index = ct.transcript.find(normalized_epub[:search_len])

            # Retry a bit into the text, in case a chapter heading leads the extract.
            if match_index < 0 and len(normalized_epub) > search_len + HEADING_SKIP:
                offset_text = normalized_epub[HEADING_SKIP : HEADING_SKIP + search_len]
                match_index = ct.transcript.find(offset_text)

            if match_index >= 0:
                return ct.point_at_offset(match_index)

    # PASS 2 - fuzzy, best score across every candidate chapter.
    needle = normalized_epub[:FUZZY_NEEDLE_LEN]
    best: tuple[_ChapterTranscript, int, float] | None = None
    for ct in chapter_transcripts:
        hit = fuzzy_find_in_transcript(ct.transcript, needle, FUZZY_THRESHOLD)
        if hit is None:
            continue
        offset, score = hit
        if best is None or score > best[2]:
            best = (ct, offset, score)

    if best is not None:
        ct, offset, _score = best
        return ct.point_at_offset(offset)

    return None
