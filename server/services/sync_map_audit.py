"""
Sync-map / EPUB drift audit (issue #295).

A sync map is only meaningful relative to the ebook file it was aligned
against. Re-convert the book, replace the file with a different edition, or
change how it parses, and the map's `(chapter, sentence_index)` coordinates
start naming text that is not in the document the reader renders. That is what
happened to pair 260: the map resolved 10:00 of audio to "Gwendolyn felt herself
smile slightly.", a sentence that occurs nowhere in the EPUB now on disk. The
reader degrades gracefully, but nothing could say *which* of ~135 pairs were in
that state.

This module answers that, per pair, from two independent signals:

**Provenance** — `sync_maps.epub_file_hash` versus the file's hash right now.
A mismatch is proof; it needs no text at all. NULL is a map written before the
column existed: unknown, which is not the same as healthy.

**Evidence** — a bounded sample of the map's stored sentence previews, looked up
in the EPUB's whole-spine text. Whole-spine on purpose: a front-matter offset
(pair 84) shifts every chapter number without invalidating a thing, so checking
the preview against the chapter it *claims* would flag a healthy map. Only
"this text is not in this book at all" is drift.

Cost control, since the live library is ~135 pairs with maps of thousands of
points each:

* points are sampled, never loaded wholesale — one pass over a single map's ids,
  then one fetch of the sampled previews;
* the EPUB is parsed at most once per pair, and not at all when the hash already
  settled the verdict or when `sample_size=0`;
* file hashing and EPUB parsing run in threads, so a long audit doesn't stall
  the event loop.

Strictly read-only. Re-alignment is the caller's decision, made through the
machinery that already exists: `POST /api/transcription/{pair_id}/realign`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from typing import List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.book import BookPair, EBook
from models.sync_map import SyncMap, SyncPoint
from models.transcript import AudioTranscript
from services.file_hash import hash_file
from services.sync_matcher import normalize_for_search

logger = logging.getLogger(__name__)

#: Sampled points per pair. Twenty spread across a map is plenty to separate
#: "wrong book" (near-zero hits) from "right book" (near-total hits).
DEFAULT_SAMPLE_SIZE = 20

#: Below this share of sampled previews found in the book, the map is drifted.
#: A healthy map scores ~1.0 and a drifted one ~0.0; the midpoint is a wide
#: gutter, not a fine judgement.
STALE_HIT_RATE = 0.5

#: Needle length, in normalized characters, taken from a preview. A rung of the
#: reader's own search ladder (`sync_matcher.SEARCH_LENGTHS`), and short enough
#: that a preview truncated mid-word at its 200-char storage limit still matches.
NEEDLE_LEN = 60

#: The ladder's shortest rung. A preview below it normalizes to something that
#: matches half the book ("yes he said"), so it carries no evidence either way
#: and is not sampled.
MIN_NEEDLE_LEN = 30

#: The endpoint an operator re-aligns a flagged pair through. Templated so the
#: response can name it once instead of the client hard-coding it.
REALIGN_ENDPOINT = "/api/transcription/{pair_id}/realign"

# ---------------------------------------------------------------------------
# Timing check (issue #586)
#
# The checks above catch a map built against the wrong *file*. Neither one
# catches a map built against the right file whose audio content is
# reordered: `services.alignment`'s anchor filter keeps timestamps monotonic
# by dropping a displaced block's anchors and interpolating over the gap, so
# the map stays internally plausible while being minutes wrong for real text.
# `sync_maps.degraded` (stamped at alignment time, see `sync_engine.py`) is
# one signal; this is the other, independent one: for a sample of points,
# find that point's text in the cached transcript and compare timestamps.
#
# Locating a needle in the transcript is its own small alignment problem, and
# the first version of this check got it wrong in a way only real data
# exposed: `token_set_ratio` scores 100 whenever the *shorter* string's words
# are a subset of the longer one's, so a one- or two-word Whisper segment
# ("Okay.", a stray interjection, a VAD artifact) scores a trivial 100 against
# almost any real needle. On a live pair that turned into a ~95% false
# mismatch rate — the located time was essentially a random short segment, not
# the actual sentence — against a true ~11% (per the issue's own investigation:
# 667/5924). Two guards fix it: drop degenerate-short segments from the
# haystack (`MIN_TRANSCRIPT_SEGMENT_LEN`) and require the best match to clearly
# beat the second-best (`TIMING_MATCH_AMBIGUITY_MARGIN`) — an ambiguous match
# carries no evidence and is skipped, not counted either way, same treatment
# `_needle` already gives a too-short preview.
# ---------------------------------------------------------------------------

#: A sampled point's stored `audio_start_ms` and the cached transcript's own
#: timestamp for that same text disagree by more than this only when the
#: audio has actually moved — ordinary alignment slack is seconds, not
#: minutes. Matches the report in issue #586 (offsets of 30-45 minutes).
AUDIT_TIMING_MISMATCH_MS = 2 * 60 * 1000  # 2 minutes

#: Number of points sampled for the timing check specifically — larger than a
#: typical `sample_size` request, and independent of it. A reordered block is
#: *localized*: it shows up as consecutive mismatched samples in book order
#: (see `TIMING_MISMATCH_RUN_LENGTH` below), and a handful of samples spread
#: across a whole book rarely puts three of them inside one six-chapter block
#: by chance. Verified against the real pair from issue #586: a run of 3+
#: consecutive mismatches only appears reliably at 40+ samples; 20 (this
#: module's general `DEFAULT_SAMPLE_SIZE`) misses it more often than not. Still
#: bounded — a sample, not the whole map — and cheap: the fuzzy search is
#: O(sample_size) calls against a length-filtered haystack, run off the event
#: loop.
TIMING_SAMPLE_SIZE = 80

#: Rejects a transcript segment as match material below this normalized
#: length. This is what the 95%-false-mismatch bug above turns on: without it,
#: a one-word Whisper artifact is *always* available as a spuriously perfect
#: `token_set_ratio` match for every needle. Matches `MIN_NEEDLE_LEN`'s
#: reasoning on the needle side.
MIN_TRANSCRIPT_SEGMENT_LEN = 30

#: The best match must beat the second-best by at least this many rapidfuzz
#: score points to be trusted; otherwise the sample is skipped (not counted as
#: checked or mismatched). Real prose rarely repeats a 30+ character needle
#: near-verbatim, so a close runner-up means the needle isn't uniquely
#: locatable in this transcript — trusting the "best" pick there is exactly
#: how the 95%-false-mismatch bug happened.
TIMING_MATCH_AMBIGUITY_MARGIN = 15

#: Share of successfully-located sampled points that must mismatch before the
#: pair is even considered for flagging. Mirrors `alignment.py`'s
#: `DEGRADED_REJECTED_FRACTION` — the real pair from issue #586 sits at ~11%
#: overall (matching the investigation's own 667/5924), so this alone is not a
#: strong signal; it exists to rule out a handful of stray mismatches, not to
#: carry the verdict on its own.
AUDIT_TIMING_MISMATCH_FRACTION = 0.10

#: The verdict actually turns on this: the longest run of *consecutive*
#: sampled points (in book order) that all mismatch. A reordered block is
#: localized, so it shows up as a run; scattered noise (mis-transcribed words,
#: an occasional bad fuzzy match) does not cluster. Mirrors `alignment.py`'s
#: `MIN_DISPLACED_RUN_LENGTH`. Flagging requires both this run AND the
#: fraction above — the run alone would also fire on a short, coincidental
#: streak in a small sample.
TIMING_MISMATCH_RUN_LENGTH = 3

#: Minimum rapidfuzz score (0-100) to trust a needle's match against the
#: transcript. `NEEDLE_LEN`/`MIN_NEEDLE_LEN` above already keep needles long
#: enough to be near-unique in a novel, so this only needs to reject a bad
#: transcription of the same passage, not a different passage entirely.
TIMING_MATCH_SCORE_CUTOFF = 80

#: Issue #620: a small anchor pool can trip the alignment-time `degraded`
#: flag (`alignment.MIN_KEPT_ANCHORS_FOR_DEGRADED` softens this but does not
#: guarantee it never happens — a pair whose kept-anchor count happens to sit
#: just above that floor can still misfire). The timing check above is a
#: direct, sampled comparison against the cached transcript, not a heuristic
#: about what the anchor filter rejected — when it comes back clean
#: (`timing_status == "ok"`) over at least this many successfully-located
#: points, that is stronger evidence than the alignment-time flag, and the
#: flag alone no longer promotes the verdict to degraded. Below this many
#: checked points the timing check itself carries little weight, so the
#: alignment-time flag is left to stand rather than being overridden by a
#: thin sample.
MIN_TIMING_CHECKED_FOR_CLEAN_OVERRIDE = 20


def _needle(preview: Optional[str]) -> Optional[str]:
    """The searchable form of a stored preview, or None if it carries no
    evidence."""
    if not preview:
        return None
    normalized = normalize_for_search(preview)
    if len(normalized) < MIN_NEEDLE_LEN:
        return None
    return normalized[:NEEDLE_LEN]


def _evenly_spaced(items: List, count: int) -> List:
    """`count` items spread across `items`, endpoints included.

    Sampling the first N points would only ever audit the front matter — the
    part most likely to match by luck, and least likely to show drift that
    accumulates through a book.
    """
    if count <= 0 or not items:
        return []
    if count >= len(items):
        return list(items)
    step = (len(items) - 1) / (count - 1) if count > 1 else 0
    return [items[round(i * step)] for i in range(count)]


async def _sample_previews(
    db: AsyncSession, sync_map_id: int, sample_size: int
) -> List[str]:
    """Up to `sample_size` needles spread across one map's points.

    Reads a single column of ids for one map — never whole point rows, and never
    more than one map at a time.
    """
    ids = (await db.execute(
        select(SyncPoint.id)
        .where(SyncPoint.sync_map_id == sync_map_id)
        .order_by(SyncPoint.epub_chapter, SyncPoint.epub_sentence_index)
    )).scalars().all()

    # Over-sample: `_needle` discards previews that are NULL or too short to be
    # evidence, and without slack those would quietly shrink the sample below
    # what was asked for.
    chosen = _evenly_spaced(ids, min(len(ids), sample_size * 2))
    previews = (await db.execute(
        select(SyncPoint.epub_text_preview)
        .where(SyncPoint.id.in_(chosen))
        .order_by(SyncPoint.epub_chapter, SyncPoint.epub_sentence_index)
    )).scalars().all()

    needles = [n for n in (_needle(p) for p in previews) if n]
    return _evenly_spaced(needles, min(len(needles), sample_size))


async def _sample_timing_points(
    db: AsyncSession, sync_map_id: int, sample_size: int
) -> List[Tuple[int, int, str]]:
    """Up to `sample_size` (epub_chapter, audio_start_ms, needle) triples
    spread across one map's points — the same spread `_sample_previews` uses,
    but keeping each point's own chapter and stored audio position: the
    chapter names the affected range when a run of points turns out
    mismatched or out of book order (issue #595), and the audio position is
    checked against where the cached transcript actually puts that text
    (issue #586)."""
    ids = (await db.execute(
        select(SyncPoint.id)
        .where(SyncPoint.sync_map_id == sync_map_id)
        .order_by(SyncPoint.epub_chapter, SyncPoint.epub_sentence_index)
    )).scalars().all()

    chosen = _evenly_spaced(ids, min(len(ids), sample_size * 2))
    rows = (await db.execute(
        select(SyncPoint.epub_chapter, SyncPoint.audio_start_ms, SyncPoint.epub_text_preview)
        .where(SyncPoint.id.in_(chosen))
        .order_by(SyncPoint.epub_chapter, SyncPoint.epub_sentence_index)
    )).all()

    points = [
        (chapter, ms, needle)
        for chapter, ms, preview in rows if (needle := _needle(preview))
    ]
    return _evenly_spaced(points, min(len(points), sample_size))


@dataclass
class TimingCheckResult:
    """What `_match_timings` found, split into the two independent questions
    issue #595 asks the audit to tell apart: is the *transcript itself* (the
    audio, as narrated) in book order, and separately, does the *map's*
    stored timestamp agree with it. A reordered block explains the first; a
    displaced-but-correctly-ordered map explains the second without it.
    """
    checked: int = 0
    mismatches: int = 0
    #: Longest run of *consecutive* checked samples (book order) whose
    #: located transcript timestamp disagrees with the map's own stored one
    #: by more than `AUDIT_TIMING_MISMATCH_MS`.
    mismatch_run: int = 0
    #: (first, last) epub_chapter of that run, or None if there wasn't one.
    mismatch_chapters: Optional[Tuple[int, int]] = None
    #: Longest run of *consecutive* checked samples (book order) whose
    #: located transcript position is not consistent with an increasing walk
    #: through the transcript — see `_book_order_violations` below.
    book_order_violation_run: int = 0
    mismatch_rate: Optional[float] = None


def _book_order_violations(
    transcript_positions: List[int],
) -> Tuple[int, List[bool]]:
    """(longest contiguous violation run, per-point violation flags) for a
    list of transcript positions taken in book order.

    A naive "did position go backwards from the previous point" check is
    itself exactly the bug this issue is about at one remove: a single
    accidental mismatch (an ambiguous fuzzy hit that slipped through) would
    poison every comparison after it forever, since a plain running max never
    comes back down. Instead this finds the LONGEST NON-DECREASING
    SUBSEQUENCE of `transcript_positions` — the same methodology
    `services.alignment._diagnose_anchor_rejection` already uses for raw
    anchors — and treats whatever that subsequence does *not* include as
    violations. That makes a handful of scattered bad locates harmless (they
    just aren't part of the longest consistent run) while a genuinely
    reordered block still shows up as a real, contiguous run of exclusions.

    Non-decreasing, not strictly increasing: the spine-order check (issue
    #595) reuses this with *chapter* indices, where many points legitimately
    share the exact same value (several sampled points in one chapter) — a
    strict comparison would misread that ordinary tie as a violation.
    """
    n = len(transcript_positions)
    if n == 0:
        return 0, []
    lengths = [1] * n
    prev = [-1] * n
    for i in range(1, n):
        for j in range(i):
            if transcript_positions[j] <= transcript_positions[i] and lengths[j] + 1 > lengths[i]:
                lengths[i] = lengths[j] + 1
                prev[i] = j
    in_lis = [False] * n
    k = max(range(n), key=lambda idx: lengths[idx])
    while k != -1:
        in_lis[k] = True
        k = prev[k]

    violations = [not v for v in in_lis]
    best_run = cur_run = 0
    for v in violations:
        cur_run = cur_run + 1 if v else 0
        best_run = max(best_run, cur_run)
    return best_run, violations


def _longest_true_run(flags: List[bool]) -> Optional[Tuple[int, int]]:
    """(start_idx, end_idx) of the first longest run of `True` in `flags`, or
    None if there isn't one. Shared by the timing and spine-order checks to
    turn a violation-run into the index range a caller can map back to
    chapters."""
    best: Optional[Tuple[int, int]] = None
    best_len = cur_len = 0
    cur_start = 0
    for i, v in enumerate(flags):
        if v:
            if cur_len == 0:
                cur_start = i
            cur_len += 1
            if cur_len > best_len:
                best_len = cur_len
                best = (cur_start, i)
        else:
            cur_len = 0
    return best


# ---------------------------------------------------------------------------
# Spine-order check (issue #595)
#
# A map written before EPUB parsing was spine-indexed numbered `epub_chapter`
# by document-declaration order or a filename sort instead of the actual
# spine (`part1, part10, part11, part12, part2 ... part9` sorts lexically,
# not numerically) — migration 0004 re-based every map that existed when it
# ran, but a map imported or rebuilt outside that migration, from a book
# whose spine disagreed with whatever ordering produced the map, is not
# caught by it. This check is independent of the timing check above: it
# needs only the EPUB file on disk, not a cached transcript, and it is about
# the map's *chapter numbering*, not its timestamps.
# ---------------------------------------------------------------------------

#: A run of at least this many *consecutive* sampled points (in the map's own
#: stored chapter order) whose text is found in a spine chapter *earlier*
#: than the run's predecessor is what marks the chapter numbering itself as
#: disagreeing with the spine — mirrors `TIMING_MISMATCH_RUN_LENGTH`'s "a run,
#: not a scattered handful" reasoning.
SPINE_ORDER_VIOLATION_RUN_LENGTH = 3


def _spine_chapter_for_needle(needle: str, spine_chapters_norm: List[str]) -> Optional[int]:
    """Which spine chapter (index into `spine_chapters_norm`) contains
    `needle`, or None if it's in zero or more than one — an ambiguous hit
    carries no evidence, the same treatment every other locator in this
    module gives one."""
    hits = [i for i, text in enumerate(spine_chapters_norm) if needle in text]
    return hits[0] if len(hits) == 1 else None


def _spine_order_violations(
    points: List[Tuple[int, int, str]],
    spine_chapters: List[str],
) -> Tuple[int, int, Optional[Tuple[int, int]]]:
    """(checked, longest violation run, (first, last) *stored* chapter of
    that run) — whether a map's own stored `epub_chapter` order agrees with
    where its sampled points' text actually falls in today's EPUB spine.

    `points` is (epub_chapter, _ignored, needle) in the map's own stored book
    order — the same shape `_sample_timing_points` produces, reused here so
    this check costs no extra query. Locating each needle in
    `spine_chapters` (`epub_parser.extract_spine_chapter_texts`, one entry
    per spine item) gives its *true* chapter; running `_book_order_violations`
    on that sequence, in the map's stored order, finds whether the two
    orderings agree — the same longest-increasing-subsequence methodology
    the timing check uses, applied to chapter indices instead of transcript
    positions.
    """
    if not points or not spine_chapters:
        return 0, 0, None
    spine_norm = [normalize_for_search(t) for t in spine_chapters]
    located: List[Tuple[int, int]] = []  # (stored_chapter, true_spine_chapter)
    for stored_chapter, _ignored, needle in points:
        true_chapter = _spine_chapter_for_needle(needle, spine_norm)
        if true_chapter is None:
            continue
        located.append((stored_chapter, true_chapter))

    checked = len(located)
    if checked == 0:
        return 0, 0, None

    run, flags = _book_order_violations([tc for _sc, tc in located])
    if run < SPINE_ORDER_VIOLATION_RUN_LENGTH:
        return checked, run, None
    bounds = _longest_true_run(flags)
    stored_in_run = [located[i][0] for i in range(bounds[0], bounds[1] + 1)]
    return checked, run, (min(stored_in_run), max(stored_in_run))


def _match_timings(
    points: List[Tuple[int, int, str]],
    transcript_sentences: List[dict],
) -> TimingCheckResult:
    """For each (epub_chapter, audio_start_ms, needle), in book order, find
    the cached-transcript sentence whose text best matches the needle and
    compare its `start_ms` to the point's own — see `TimingCheckResult` for
    what's reported.

    A sample is skipped — not counted as checked — when no transcript segment
    scores above `TIMING_MATCH_SCORE_CUTOFF`, or when the best match doesn't
    clearly beat the second-best (`TIMING_MATCH_AMBIGUITY_MARGIN`): neither
    carries evidence. The haystack itself drops segments shorter than
    `MIN_TRANSCRIPT_SEGMENT_LEN` first — seeing real data was what exposed why
    this matters: `token_set_ratio` scores a one-word Whisper artifact a
    trivial 100 against nearly every needle, which produced a 95% false
    mismatch rate before this fix (see the module docstring above).

    CPU-bound (a rapidfuzz search of the transcript per sampled point) —
    always run via `asyncio.to_thread`, never awaited directly on the event
    loop (issue #586, same rule as every other blocking pass in this codebase).
    """
    from rapidfuzz import fuzz, process

    if not points or not transcript_sentences:
        return TimingCheckResult()

    haystack_all = [normalize_for_search(s.get("text") or "") for s in transcript_sentences]
    keep = [i for i, h in enumerate(haystack_all) if len(h) >= MIN_TRANSCRIPT_SEGMENT_LEN]
    haystack = [haystack_all[i] for i in keep]

    # located: one entry per successfully-matched sample, in book order —
    # `points` is already book order, and only skips (never reorders) here.
    located: List[Tuple[int, int, int]] = []  # (epub_chapter, transcript_pos, offset_ms)
    for chapter, audio_ms, needle in points:
        matches = process.extract(
            needle, haystack, scorer=fuzz.token_set_ratio, limit=2,
            score_cutoff=TIMING_MATCH_SCORE_CUTOFF,
        )
        if not matches:
            continue
        _, best_score, best_pos = matches[0]
        second_score = matches[1][1] if len(matches) > 1 else None
        if second_score is not None and (best_score - second_score) < TIMING_MATCH_AMBIGUITY_MARGIN:
            continue  # ambiguous: this text isn't uniquely locatable here
        transcript_ms = transcript_sentences[keep[best_pos]].get("start_ms")
        if transcript_ms is None:
            continue
        located.append((chapter, keep[best_pos], transcript_ms - audio_ms))

    checked = len(located)
    if checked == 0:
        return TimingCheckResult()

    mismatch_run = cur_run = 0
    mismatch_run_bounds: Optional[Tuple[int, int]] = None
    cur_run_start: Optional[int] = None
    mismatches = 0
    for chapter, _tpos, offset_ms in located:
        if abs(offset_ms) > AUDIT_TIMING_MISMATCH_MS:
            mismatches += 1
            if cur_run == 0:
                cur_run_start = chapter
            cur_run += 1
            if cur_run > mismatch_run:
                mismatch_run = cur_run
                mismatch_run_bounds = (cur_run_start, chapter)
        else:
            cur_run = 0

    book_order_violation_run, _flags = _book_order_violations([tpos for _c, tpos, _o in located])

    return TimingCheckResult(
        checked=checked,
        mismatches=mismatches,
        mismatch_run=mismatch_run,
        mismatch_chapters=mismatch_run_bounds,
        book_order_violation_run=book_order_violation_run,
        mismatch_rate=mismatches / checked,
    )


async def _current_hash(path: Optional[str]) -> Optional[str]:
    if not path or not os.path.exists(path):
        return None
    try:
        return await asyncio.to_thread(hash_file, path)
    except OSError as e:
        logger.warning("Could not hash ebook '%s': %s", path, e)
        return None


def _verdict(hash_status: str, hit_rate: Optional[float]) -> tuple[str, str]:
    """(status, reason) from the two signals, provenance first."""
    if hash_status == "file_missing":
        return "stale", "The ebook file is missing from disk."
    if hash_status == "mismatch":
        return "stale", (
            "The ebook file hash does not match the one recorded when this map "
            "was aligned — the map describes a different file."
        )
    if hit_rate is None:
        if hash_status == "match":
            return "healthy", "The ebook file is the one this map was aligned against."
        return "unknown", (
            "This map predates provenance tracking and its text was not sampled."
        )
    if hit_rate < STALE_HIT_RATE:
        return "stale", (
            f"Only {hit_rate:.0%} of sampled sentences occur in the ebook's text — "
            f"the map was aligned against a different parse or edition."
        )
    if hash_status == "match":
        return "healthy", (
            f"Hash matches and {hit_rate:.0%} of sampled sentences were found."
        )
    return "healthy", (
        f"No recorded provenance, but {hit_rate:.0%} of sampled sentences were "
        f"found in the ebook's text."
    )


async def audit_sync_maps(
    db: AsyncSession,
    *,
    sample_size: int = DEFAULT_SAMPLE_SIZE,
    pair_id: Optional[int] = None,
    limit: Optional[int] = None,
) -> List[dict]:
    """Audit every pair that has a sync map (or just `pair_id`).

    `sample_size=0` skips EPUB parsing entirely — a fast provenance-only pass
    over the whole library. Returns one dict per pair, in pair-id order; pairs
    without a map are not audited (there is nothing to drift).
    """
    query = (
        select(SyncMap, BookPair, EBook)
        .join(BookPair, BookPair.id == SyncMap.book_pair_id)
        .outerjoin(EBook, EBook.id == BookPair.ebook_id)
        .order_by(SyncMap.book_pair_id)
    )
    if pair_id is not None:
        query = query.where(SyncMap.book_pair_id == pair_id)
    if limit is not None:
        query = query.limit(limit)

    rows = (await db.execute(query)).all()
    if not rows:
        return []

    cached = set((await db.execute(
        select(AudioTranscript.pair_id).where(
            AudioTranscript.pair_id.in_([m.book_pair_id for m, _, _ in rows])
        )
    )).scalars().all())

    results = []
    for sync_map, pair, ebook in rows:
        results.append(await _audit_one(
            db, sync_map, pair, ebook,
            sample_size=sample_size,
            has_cached_transcript=sync_map.book_pair_id in cached,
        ))
    return results


async def _audit_one(
    db: AsyncSession,
    sync_map: SyncMap,
    pair: BookPair,
    ebook: Optional[EBook],
    *,
    sample_size: int,
    has_cached_transcript: bool,
) -> dict:
    path = ebook.file_path if ebook else None
    stored = sync_map.epub_file_hash
    current = await _current_hash(path)

    if current is None:
        hash_status = "file_missing"
    elif stored is None:
        hash_status = "unknown"
    elif stored == current:
        hash_status = "match"
    else:
        hash_status = "mismatch"

    # Parse the EPUB only when the answer isn't already settled. A mismatch or a
    # missing file is proof on its own, and reading the book to confirm it would
    # be the most expensive part of the audit spent on a decided case.
    needles: List[str] = []
    haystack = ""
    text_status = "skipped"
    spine_chapters: List[str] = []
    if sample_size > 0 and hash_status in ("match", "unknown"):
        needles = await _sample_previews(db, sync_map.id, sample_size)
        if needles:
            # Imported at call time: `epub_parser` pulls in ebooklib/nltk, and
            # this module is otherwise light enough for the router to import
            # unconditionally.
            from services import epub_parser
            try:
                # Also the spine-order check's own building block (issue
                # #595) — `extract_book_text` is `"\n".join` of exactly this,
                # so fetching it here instead of calling `extract_book_text`
                # separately gets both checks from one parse of the EPUB.
                spine_chapters = await asyncio.to_thread(
                    epub_parser.extract_spine_chapter_texts, path
                )
                haystack = normalize_for_search("\n".join(spine_chapters))
                text_status = "ok"
            except Exception as e:
                logger.warning("Could not read ebook text for pair %s (%s): %s",
                               sync_map.book_pair_id, path, e)
                needles = []
                spine_chapters = []
                text_status = "unreadable"

    hits = sum(1 for n in needles if n in haystack) if needles else 0
    hit_rate = (hits / len(needles)) if needles else None
    status, reason = _verdict(hash_status, hit_rate)

    # Sampled (chapter, ms, needle) points, book order — shared by the timing
    # check (issue #586) and the spine-order check (issue #595) below, so
    # fetching it costs one query no matter how many of those checks run.
    timing_points: List[Tuple[int, int, str]] = []
    if sample_size > 0:
        timing_points = await _sample_timing_points(db, sync_map.id, TIMING_SAMPLE_SIZE)

    # Timing check (issue #586): independent of the hash/text signals above —
    # it can flag a map that looks provenance-healthy but whose stored
    # timings disagree with the cached transcript. Bounded and read-only: a
    # sample (`TIMING_SAMPLE_SIZE`, not `sample_size` — see its docstring for
    # why), not the whole map, and the fuzzy search runs off the event loop.
    timing_result = TimingCheckResult()
    if sample_size <= 0:
        timing_status = "skipped"
    elif not has_cached_transcript:
        timing_status = "cannot_check"
    elif not timing_points:
        timing_status = "cannot_check"
    else:
        transcript_json = (await db.execute(
            select(AudioTranscript.sentences_json)
            .where(AudioTranscript.pair_id == sync_map.book_pair_id)
        )).scalar_one_or_none()
        if not transcript_json:
            timing_status = "cannot_check"
        else:
            try:
                transcript_sentences = json.loads(transcript_json)
            except (TypeError, ValueError) as e:
                logger.warning(
                    "Could not parse cached transcript for pair %s: %s",
                    sync_map.book_pair_id, e,
                )
                transcript_sentences = []
            timing_result = await asyncio.to_thread(
                _match_timings, timing_points, transcript_sentences
            )
            if timing_result.checked == 0:
                timing_status = "cannot_check"
            else:
                # Both signals, not just the share (see `TIMING_MISMATCH_RUN_LENGTH`
                # above): a reordered block is localized, and the run is what
                # actually separates it from scattered noise at this sample size.
                flagged = (
                    timing_result.mismatch_rate >= AUDIT_TIMING_MISMATCH_FRACTION
                    and timing_result.mismatch_run >= TIMING_MISMATCH_RUN_LENGTH
                )
                timing_status = "flagged" if flagged else "ok"

    # Spine-order check (issue #595): independent of the timing check above —
    # it only needs the EPUB file, not a cached transcript, and it is about
    # the map's *chapter numbering*, not its timestamps. A map written before
    # EPUB parsing was spine-indexed may have numbered chapters by
    # document-declaration or filename order instead (migration 0004
    # re-based every map that existed when it ran, not one built since from
    # a mis-ordering source it didn't anticipate).
    spine_checked = 0
    spine_violation_run = 0
    spine_order_chapters: Optional[Tuple[int, int]] = None
    if sample_size <= 0 or not spine_chapters or not timing_points:
        spine_order_status = "skipped" if sample_size <= 0 else "cannot_check"
    else:
        spine_checked, spine_violation_run, spine_order_chapters = await asyncio.to_thread(
            _spine_order_violations, timing_points, spine_chapters
        )
        if spine_checked == 0:
            spine_order_status = "cannot_check"
        else:
            spine_order_status = "mismatch" if spine_order_chapters is not None else "ok"

    if spine_order_status == "mismatch":
        logger.warning(
            "Sync-map spine-order check flagged pair %s: chapter order "
            "disagrees with the EPUB spine for a run of %d consecutive "
            "sampled points (stored chapters %s-%s)",
            sync_map.book_pair_id, spine_violation_run,
            spine_order_chapters[0], spine_order_chapters[1],
        )

    # issue #595: a flagged (or alignment-time degraded) map can mean two
    # different things, and they call for opposite fixes. If the *transcript*
    # is confirmed in book order (the timing check actually ran, and found no
    # run of `_book_order_violations` as long as a real reordered block would
    # produce), the audio is fine and the map's own stored timestamps are
    # what's wrong — re-aligning from the (good) cached transcript fixes it
    # without re-transcribing. Otherwise — the transcript itself is out of
    # order, or there simply isn't enough evidence to say (no cached
    # transcript, or the timing check didn't run) — this defaults to the
    # conservative, pre-#595 behaviour of pointing at the audio: re-aligning
    # from a transcript that's actually out of order would just reproduce the
    # same wrong map.
    transcript_confirmed_in_order = (
        timing_result.checked > 0
        and timing_result.book_order_violation_run < TIMING_MISMATCH_RUN_LENGTH
    )

    if timing_status == "flagged":
        logger.warning(
            "Sync-map timing check flagged pair %s: %d/%d sampled points "
            "differ from the cached transcript by more than %ds, including a "
            "run of %d consecutive mismatches (transcript confirmed in book "
            "order: %s)",
            sync_map.book_pair_id, timing_result.mismatches, timing_result.checked,
            AUDIT_TIMING_MISMATCH_MS // 1000, timing_result.mismatch_run,
            transcript_confirmed_in_order,
        )

    # A degraded map (flagged at alignment time, `sync_maps.degraded`) or a
    # timing-check flag both mean the sync map itself cannot be trusted.
    # Either promotes an otherwise-healthy verdict; neither overrides a
    # hash/text verdict that already caught a bigger problem (the wrong file
    # entirely).
    #
    # Issue #620: a clean, well-sampled timing check overrides the
    # alignment-time flag rather than compounding with it — see
    # `MIN_TIMING_CHECKED_FOR_CLEAN_OVERRIDE`'s docstring above. The raw
    # `sync_map.degraded`/`degraded_reason` are still surfaced on the response
    # regardless (below), so an operator can see the alignment-time flag was
    # overridden, not just that it's absent.
    alignment_flag_confirmed = sync_map.degraded and not (
        timing_status == "ok"
        and timing_result.checked >= MIN_TIMING_CHECKED_FOR_CLEAN_OVERRIDE
    )
    if status == "healthy" and (alignment_flag_confirmed or timing_status == "flagged"):
        status = "degraded"
        parts = []
        if alignment_flag_confirmed and sync_map.degraded_reason:
            parts.append(sync_map.degraded_reason)
        if timing_status == "flagged":
            chapters = timing_result.mismatch_chapters
            span = (
                f" (chapters {chapters[0]}-{chapters[1]})"
                if chapters and chapters[0] != chapters[1]
                else f" (chapter {chapters[0]})" if chapters else ""
            )
            parts.append(
                f"{timing_result.mismatches}/{timing_result.checked} sampled points "
                f"({timing_result.mismatch_rate:.0%}) have a timestamp more than "
                f"{AUDIT_TIMING_MISMATCH_MS // 60_000} min off from where that "
                f"text actually falls in the transcript, including a run of "
                f"{timing_result.mismatch_run} in a row{span}."
            )
        if transcript_confirmed_in_order:
            reason = (
                "The cached transcript is in book order, so the audio looks fine — "
                "the sync map itself is displaced. Re-align from the cached "
                "transcript. " + " ".join(parts)
            ).strip()
        else:
            reason = (
                "The audio's order differs from the ebook — check the audio "
                "file, then re-transcribe. " + " ".join(parts)
            ).strip()

    # Spine-order mismatch (issue #595) promotes an otherwise-healthy verdict
    # to "stale" — the map's chapter numbering doesn't match the book it
    # claims to describe, the same family of problem the hash/text checks
    # catch, just for the spine's chapter order instead of the whole file.
    # It never overrides a bigger problem the hash/text or degraded checks
    # already caught (both already ran first).
    if status == "healthy" and spine_order_status == "mismatch":
        status = "stale"
        reason = (
            f"The map's chapter numbering disagrees with the EPUB's spine for "
            f"a run of {spine_violation_run} consecutive sampled points "
            f"(stored chapters {spine_order_chapters[0]}-{spine_order_chapters[1]}) "
            f"— likely a map built before spine-order parsing. Re-align to "
            f"rebuild it with today's chapter numbering."
        )

    if status == "degraded":
        action = "realign" if transcript_confirmed_in_order else "check_audio_order"
    elif status != "stale":
        action = None
    elif hash_status == "file_missing":
        action = "restore_file"
    elif has_cached_transcript:
        action = "realign"
    else:
        # Re-alignment rebuilds from the cached transcript; without one, only a
        # full transcription can produce a map.
        action = "retranscribe"

    return {
        "pair_id": sync_map.book_pair_id,
        "title": ebook.title if ebook else None,
        # From the pair, not the joined row: an ebook_id pointing at a deleted
        # ebook is exactly the case worth seeing, and `ebook` is NULL there.
        "ebook_id": pair.ebook_id,
        "ebook_path": path,
        "sync_map_version": sync_map.version,
        "total_sentences": sync_map.total_sentences,
        "stored_epub_hash": stored,
        "current_epub_hash": current,
        "hash_status": hash_status,
        "text_status": text_status,
        "sampled": len(needles),
        "hits": hits,
        "hit_rate": hit_rate,
        "has_cached_transcript": has_cached_transcript,
        # Stamped at alignment time (`sync_engine.save_sync_map`, issue #586)
        # from `alignment.AlignmentDiagnostics` — surfaced here regardless of
        # whether it alone changed `status`, so an operator can see it even
        # when a bigger hash/text problem already took priority.
        "degraded": sync_map.degraded,
        "degraded_reason": sync_map.degraded_reason,
        "timing_status": timing_status,
        "timing_checked": timing_result.checked,
        "timing_mismatches": timing_result.mismatches,
        "timing_mismatch_rate": timing_result.mismatch_rate,
        "timing_mismatch_run": timing_result.mismatch_run,
        # (first, last) epub_chapter of the longest mismatched run, so an
        # operator (or the Troubleshoot page) can see which part of the book
        # is affected without re-running the check themselves (issue #595).
        "timing_mismatch_chapters": timing_result.mismatch_chapters,
        # Whether the *transcript itself* was confirmed in book order — the
        # signal that decides `suggested_action` between "realign" and
        # "check_audio_order" below (issue #595).
        "timing_transcript_confirmed_in_order": transcript_confirmed_in_order,
        # Spine-order check (issue #595): "ok" | "mismatch" | "cannot_check" |
        # "skipped". Independent of the timing check — needs only the EPUB
        # file, not a cached transcript.
        "spine_order_status": spine_order_status,
        "spine_order_checked": spine_checked,
        "spine_order_violation_run": spine_violation_run,
        "spine_order_chapters": spine_order_chapters,
        "status": status,
        "reason": reason,
        "suggested_action": action,
        # Named only when re-alignment is actually the right move: a pair with
        # no cached transcript has nothing for it to rebuild from, and pointing
        # an operator at an endpoint that will 404 is worse than saying nothing.
        "realign_path": (
            REALIGN_ENDPOINT.format(pair_id=sync_map.book_pair_id)
            if action == "realign" else None
        ),
    }
