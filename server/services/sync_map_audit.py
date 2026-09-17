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
) -> List[Tuple[int, str]]:
    """Up to `sample_size` (audio_start_ms, needle) pairs spread across one
    map's points — the same spread `_sample_previews` uses, but keeping each
    point's own stored audio position so it can be checked against where the
    cached transcript actually puts that text (issue #586)."""
    ids = (await db.execute(
        select(SyncPoint.id)
        .where(SyncPoint.sync_map_id == sync_map_id)
        .order_by(SyncPoint.epub_chapter, SyncPoint.epub_sentence_index)
    )).scalars().all()

    chosen = _evenly_spaced(ids, min(len(ids), sample_size * 2))
    rows = (await db.execute(
        select(SyncPoint.audio_start_ms, SyncPoint.epub_text_preview)
        .where(SyncPoint.id.in_(chosen))
        .order_by(SyncPoint.epub_chapter, SyncPoint.epub_sentence_index)
    )).all()

    points = [(ms, needle) for ms, preview in rows if (needle := _needle(preview))]
    return _evenly_spaced(points, min(len(points), sample_size))


def _match_timings(
    points: List[Tuple[int, str]],
    transcript_sentences: List[dict],
) -> Tuple[int, int, int]:
    """(checked, mismatches, longest_mismatch_run): for each (audio_start_ms,
    needle), in book order, find the cached-transcript sentence whose text
    best matches the needle and compare its `start_ms` to the point's own.

    A sample is skipped — not counted as checked, and not counted as a
    mismatch — when no transcript segment scores above
    `TIMING_MATCH_SCORE_CUTOFF`, or when the best match doesn't clearly beat
    the second-best (`TIMING_MATCH_AMBIGUITY_MARGIN`): neither carries
    evidence. The haystack itself drops segments shorter than
    `MIN_TRANSCRIPT_SEGMENT_LEN` first — seeing real data was what exposed why
    this matters: `token_set_ratio` scores a one-word Whisper artifact a
    trivial 100 against nearly every needle, which produced a 95% false
    mismatch rate before this fix (see the module docstring above).

    `longest_mismatch_run` is the longest run of *consecutive* checked samples
    (in the book-order `points` were given in) that all mismatch — the signal
    `_audit_one` actually flags on, since a reordered block is localized and a
    run is a much cleaner signature of that than the overall share is.

    CPU-bound (a rapidfuzz search of the transcript per sampled point) —
    always run via `asyncio.to_thread`, never awaited directly on the event
    loop (issue #586, same rule as every other blocking pass in this codebase).
    """
    from rapidfuzz import fuzz, process

    if not points or not transcript_sentences:
        return 0, 0, 0

    haystack_all = [normalize_for_search(s.get("text") or "") for s in transcript_sentences]
    keep = [i for i, h in enumerate(haystack_all) if len(h) >= MIN_TRANSCRIPT_SEGMENT_LEN]
    haystack = [haystack_all[i] for i in keep]

    checked = mismatches = 0
    run = best_run = 0
    for audio_ms, needle in points:
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
        checked += 1
        if abs(transcript_ms - audio_ms) > AUDIT_TIMING_MISMATCH_MS:
            mismatches += 1
            run += 1
            best_run = max(best_run, run)
        else:
            run = 0
    return checked, mismatches, best_run


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
    if sample_size > 0 and hash_status in ("match", "unknown"):
        needles = await _sample_previews(db, sync_map.id, sample_size)
        if needles:
            # Imported at call time: `epub_parser` pulls in ebooklib/nltk, and
            # this module is otherwise light enough for the router to import
            # unconditionally.
            from services import epub_parser
            try:
                book_text = await asyncio.to_thread(
                    epub_parser.extract_book_text, path
                )
                haystack = normalize_for_search(book_text)
                text_status = "ok"
            except Exception as e:
                logger.warning("Could not read ebook text for pair %s (%s): %s",
                               sync_map.book_pair_id, path, e)
                needles = []
                text_status = "unreadable"

    hits = sum(1 for n in needles if n in haystack) if needles else 0
    hit_rate = (hits / len(needles)) if needles else None
    status, reason = _verdict(hash_status, hit_rate)

    # Timing check (issue #586): independent of the hash/text signals above —
    # it can flag a map that looks provenance-healthy but whose audio content
    # is reordered relative to the ebook. Bounded and read-only: a sample
    # (`TIMING_SAMPLE_SIZE`, not `sample_size` — see its docstring for why),
    # not the whole map, and the fuzzy search runs off the event loop.
    timing_checked = 0
    timing_mismatches = 0
    timing_mismatch_run = 0
    timing_mismatch_rate: Optional[float] = None
    if sample_size <= 0:
        timing_status = "skipped"
    elif not has_cached_transcript:
        timing_status = "cannot_check"
    else:
        transcript_json = (await db.execute(
            select(AudioTranscript.sentences_json)
            .where(AudioTranscript.pair_id == sync_map.book_pair_id)
        )).scalar_one_or_none()
        timing_points = await _sample_timing_points(db, sync_map.id, TIMING_SAMPLE_SIZE)
        if not transcript_json or not timing_points:
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
            timing_checked, timing_mismatches, timing_mismatch_run = await asyncio.to_thread(
                _match_timings, timing_points, transcript_sentences
            )
            if timing_checked == 0:
                timing_status = "cannot_check"
            else:
                timing_mismatch_rate = timing_mismatches / timing_checked
                # Both signals, not just the share (see `TIMING_MISMATCH_RUN_LENGTH`
                # above): a reordered block is localized, and the run is what
                # actually separates it from scattered noise at this sample size.
                flagged = (
                    timing_mismatch_rate >= AUDIT_TIMING_MISMATCH_FRACTION
                    and timing_mismatch_run >= TIMING_MISMATCH_RUN_LENGTH
                )
                timing_status = "flagged" if flagged else "ok"

    if timing_status == "flagged":
        logger.warning(
            "Sync-map timing check flagged pair %s: %d/%d sampled points "
            "differ from the cached transcript by more than %ds, including a "
            "run of %d consecutive mismatches",
            sync_map.book_pair_id, timing_mismatches, timing_checked,
            AUDIT_TIMING_MISMATCH_MS // 1000, timing_mismatch_run,
        )

    # A degraded map (flagged at alignment time, `sync_maps.degraded`) or a
    # timing-check flag both mean the same underlying problem — the audio's
    # order doesn't match the ebook's. Either promotes an otherwise-healthy
    # verdict; neither overrides a hash/text verdict that already caught a
    # bigger problem (the wrong file entirely).
    if status == "healthy" and (sync_map.degraded or timing_status == "flagged"):
        status = "degraded"
        parts = []
        if sync_map.degraded and sync_map.degraded_reason:
            parts.append(sync_map.degraded_reason)
        if timing_status == "flagged":
            parts.append(
                f"{timing_mismatches}/{timing_checked} sampled points "
                f"({timing_mismatch_rate:.0%}) have a timestamp more than "
                f"{AUDIT_TIMING_MISMATCH_MS // 60_000} min off from where that "
                f"text actually falls in the transcript, including a run of "
                f"{timing_mismatch_run} in a row."
            )
        reason = (
            "The audio's order differs from the ebook — check the audio "
            "file, then re-transcribe. " + " ".join(parts)
        ).strip()

    if status == "degraded":
        action = "check_audio_order"
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
        "timing_checked": timing_checked,
        "timing_mismatches": timing_mismatches,
        "timing_mismatch_rate": timing_mismatch_rate,
        "timing_mismatch_run": timing_mismatch_run,
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
