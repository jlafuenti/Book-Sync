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
import logging
import os
from typing import List, Optional

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

    if status != "stale":
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
