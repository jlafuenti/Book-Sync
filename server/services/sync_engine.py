"""
Sync Engine Service

Handles saving sync maps and converting between ebook positions
and audio positions — the bridge that makes cross-mode sync work.
"""

import asyncio
import logging
import os
from dataclasses import dataclass
from typing import List, Optional, Tuple

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from models.book import BookPair, EBook
from models.bookmark import Bookmark, BookmarkSource
from models.sync_map import SyncMap, SyncPoint
from schemas import PositionScope
from services.alignment import AlignedPoint
from services.file_hash import hash_file
from services.position_service import ScopeRef, _sync_derived_progress
from services.sync_matcher import match_text_to_sync_points
from config import settings
from utils import utcnow

logger = logging.getLogger(__name__)

#: Shortest text anchor worth matching when re-mapping a bookmark. Below this the
#: text matches too much — the same threshold the restore ladder's text rung uses
#: (`position_resolver.MIN_SEARCHABLE_PREVIEW`).
MIN_REMAPPABLE_PREVIEW = 10


@dataclass(frozen=True)
class OldPoint:
    """One point of the map being replaced — just enough to translate a
    bookmark's coordinate back into the text it named."""
    epub_chapter: int
    epub_sentence_index: int
    epub_text_preview: Optional[str]


async def _ebook_file_hash(db: AsyncSession, book_pair_id: int) -> Optional[str]:
    """Composite hash of the ebook file this pair's map is being built from.

    Returns None — "no provenance recorded" — for a pair with no ebook row, no
    path, or a file that can't be read. A map is still worth saving without its
    provenance; refusing to save one would trade a usable map for a stamp.
    """
    path = (await db.execute(
        select(EBook.file_path)
        .join(BookPair, BookPair.ebook_id == EBook.id)
        .where(BookPair.id == book_pair_id)
    )).scalar_one_or_none()
    if not path or not os.path.exists(path):
        return None
    try:
        return await asyncio.to_thread(hash_file, path)
    except OSError as e:
        logger.warning("Could not hash ebook '%s' for pair %s: %s",
                       path, book_pair_id, e)
        return None


async def save_sync_map(
    db: AsyncSession,
    book_pair_id: int,
    aligned_points: List[AlignedPoint],
) -> SyncMap:
    """
    Save alignment results as a SyncMap with SyncPoints.

    If a SyncMap already exists for this pair, it is replaced — and the
    bookmarks that referenced it are re-mapped onto the new coordinates
    (issue #55). Flushes but does not commit; the caller owns the transaction.

    The ebook file's hash is stamped on the map as its provenance (issue #295).
    Every producer of a sync map — transcription, re-alignment, the Convert
    flow — funnels through here, so stamping at this one point is what keeps the
    drift audit's question ("was this map built from the file now on disk?")
    answerable for all of them.
    """
    # Delete existing sync map for this pair
    result = await db.execute(
        select(SyncMap).where(SyncMap.book_pair_id == book_pair_id)
    )
    existing = result.scalar_one_or_none()
    new_version = 1
    old_points: List[OldPoint] = []
    if existing:
        new_version = (existing.version or 1) + 1
        # Snapshot the outgoing points *before* the delete: they are the only
        # way to turn a bookmark's (chapter, sentence) coordinate back into the
        # text it meant, which is what the re-map translates from. Read as plain
        # columns, not ORM rows — a book's map runs to thousands of points and
        # none of them need to be session-tracked just to be read once.
        old_points = [
            OldPoint(*row) for row in (await db.execute(
                select(SyncPoint.epub_chapter, SyncPoint.epub_sentence_index,
                       SyncPoint.epub_text_preview)
                .where(SyncPoint.sync_map_id == existing.id)
            )).all()
        ]
        # Core deletes on both tables, then detach: `SyncMap.sync_points` is
        # `delete-orphan`, so an ORM `db.delete(existing)` would load every child
        # row and issue a DELETE per point on top of the bulk statement above —
        # which is what the "expected to delete N row(s); 0 were matched"
        # SAWarning was reporting.
        await db.execute(
            delete(SyncPoint).where(SyncPoint.sync_map_id == existing.id)
        )
        await db.execute(delete(SyncMap).where(SyncMap.id == existing.id))
        db.expunge(existing)
        await db.flush()

    # Count unique chapters
    chapters = set(p.epub_chapter for p in aligned_points)

    # Create new sync map
    sync_map = SyncMap(
        book_pair_id=book_pair_id,
        version=new_version,
        total_sentences=len(aligned_points),
        total_chapters=len(chapters),
        epub_file_hash=await _ebook_file_hash(db, book_pair_id),
    )
    db.add(sync_map)
    await db.flush()

    # Batch insert sync points
    for point in aligned_points:
        sp = SyncPoint(
            sync_map_id=sync_map.id,
            epub_chapter=point.epub_chapter,
            epub_sentence_index=point.epub_sentence_index,
            epub_text_preview=point.epub_text_preview,
            audio_start_ms=point.audio_start_ms,
            audio_end_ms=point.audio_end_ms,
            confidence=point.confidence,
        )
        db.add(sp)

    await db.flush()
    logger.info(
        f"Saved SyncMap with {len(aligned_points)} points "
        f"for pair {book_pair_id}"
    )

    # Only a *replacement* invalidates anything. A first-ever map has no old
    # coordinates to translate from, and its points are what the next write will
    # establish coordinates against anyway.
    if old_points:
        await remap_bookmarks_for_pair(
            db, book_pair_id, aligned_points, old_points, new_version
        )

    return sync_map


# ---------------------------------------------------------------------------
# Re-mapping bookmarks across a sync-map regeneration (issue #55)
# ---------------------------------------------------------------------------

def _old_text_for(old_points: List[OldPoint], chapter, sentence_index) -> Optional[str]:
    """The text the old map put at (chapter, sentence_index).

    Falls back to the nearest earlier sentence in that chapter that has a
    preview, mirroring Android's `epubTextForSentence` — a point whose preview
    is NULL should resolve to the last text we actually know about rather than
    to nothing.
    """
    if chapter is None:
        return None
    in_chapter = sorted(
        (p for p in old_points if p.epub_chapter == chapter),
        key=lambda p: p.epub_sentence_index,
    )
    target = sentence_index or 0
    earlier = [p for p in in_chapter
               if p.epub_sentence_index <= target and p.epub_text_preview]
    if earlier:
        return earlier[-1].epub_text_preview
    return None


def _anchor_text(bookmark: Bookmark, old_points: List[OldPoint]) -> Optional[str]:
    """The text this bookmark sits on, best source first.

    The bookmark's own preview is preferred: it is the axis-independent anchor
    the whole restore ladder is built on, and it survives re-parsing. The old
    map is the fallback for rows written before previews were stored.
    """
    preview = (bookmark.epub_text_preview or "").strip()
    if len(preview) >= MIN_REMAPPABLE_PREVIEW:
        return preview
    fallback = (_old_text_for(
        old_points, bookmark.epub_chapter, bookmark.epub_sentence_index) or "").strip()
    return fallback if len(fallback) >= MIN_REMAPPABLE_PREVIEW else None


def resolve_on_map(
    source,
    audio_position_ms: Optional[int],
    anchor_text: Optional[str],
    chapter: Optional[int],
    points,
    points_by_audio,
) -> Optional[Tuple[int, int, Optional[int]]]:
    """Express one position in a map's coordinates, best evidence first.

    The one rule both the re-map and a version-mismatched client write share
    (issue #116):

    * **audiobook**-sourced with an audio position → `audio_to_epub`; the audio
      file didn't change, so that field is the truth. Returns `audio_ms=None`
      because it must not be rewritten.
    * otherwise → the text anchor through the shared matcher (unchanged — the
      Kotlin mirror and the parity vectors are untouched by this). Returns the
      matched point's audio start, since an ebook-sourced audio position *is*
      derived from the map.

    `points` may be `AlignedPoint`s or `SyncPoint` rows; `points_by_audio` is
    the same list sorted by `audio_start_ms` (`audio_to_epub` stops early).
    Returns None when there is nothing usable to resolve from.
    """
    if source == BookmarkSource.AUDIOBOOK and audio_position_ms is not None:
        ch, sentence = audio_to_epub(points_by_audio, audio_position_ms)
        return (ch, sentence, None)
    text = (anchor_text or "").strip()
    if len(text) < MIN_REMAPPABLE_PREVIEW:
        return None
    match = match_text_to_sync_points(points, text, chapter or 0)
    if match is None:
        return None
    return (match.epub_chapter, match.epub_sentence_index, match.audio_start_ms)


async def remap_bookmarks_for_pair(
    db: AsyncSession,
    book_pair_id: int,
    new_points: List[AlignedPoint],
    old_points: List[OldPoint],
    new_version: int,
) -> int:
    """Translate every bookmark on this pair onto the freshly saved map.

    Re-transcription re-segments sentences, so `epub_sentence_index` — a
    sync-map coordinate — can end up naming different text. The position itself
    hasn't moved; only the coordinate system has. This re-expresses each
    bookmark in the new system:

    * **audiobook**-sourced rows are re-derived from `audio_position_ms`. The
      audio file didn't change, so that field is the truth and is never
      rewritten; the epub side is what was derived from the old map.
    * **ebook**-sourced rows are re-derived by matching their text anchor with
      the shared matcher (`services/sync_matcher.py`, unchanged — the Kotlin
      mirror and the parity vectors are untouched by this). Their
      `audio_position_ms` *is* derived from the map, so it is refreshed to the
      matched point's start.

    `anchor_revision` bumps **only** when the chapter moves. A sentence-index
    shift within the same spine item is the same page, and the device's Readium
    locator / epub.js CFI still describes it — marking every hint stale would
    drop each reader to text-search restore for a page that never moved. A
    chapter change is a genuine relocation, so there the hints must go stale.

    `captured_at` is deliberately untouched: this is a server-side translation,
    not a device capture, and stamping it would let the remap beat a genuinely
    newer write in `position_service.is_stale`. No `BookmarkLog` row is written
    either — that log is the history of moves the *user* made.

    A row with no usable anchor keeps its coordinates and its old
    `sync_map_version`, so the drift stays visible instead of being papered over.

    Returns the number of bookmarks re-mapped.
    """
    bookmarks = (await db.execute(
        select(Bookmark).where(Bookmark.book_pair_id == book_pair_id)
    )).scalars().all()
    if not bookmarks:
        return 0

    pair = (await db.execute(
        select(BookPair).where(BookPair.id == book_pair_id)
    )).scalar_one_or_none()
    ref = ScopeRef(
        PositionScope.PAIR, book_pair_id=book_pair_id,
        ebook_id=pair.ebook_id if pair else None,
        audiobook_id=pair.audiobook_id if pair else None,
    )

    # `audio_to_epub` walks the list in audio order and stops early, so give it
    # one. The alignment output is in reading order, which is usually the same,
    # but "usually" is not a contract worth relying on here.
    by_audio = sorted(new_points, key=lambda p: p.audio_start_ms)

    remapped = 0
    for bookmark in bookmarks:
        resolved = resolve_on_map(
            bookmark.source, bookmark.audio_position_ms,
            _anchor_text(bookmark, old_points), bookmark.epub_chapter,
            new_points, by_audio,
        )

        if resolved is None:
            logger.warning(
                "Sync map for pair %s regenerated to v%s, but bookmark %s "
                "(user %s) has no usable anchor — its coordinates (ch=%s, s=%s) "
                "still describe v%s and may be stale.",
                book_pair_id, new_version, bookmark.id, bookmark.user_id,
                bookmark.epub_chapter, bookmark.epub_sentence_index,
                bookmark.sync_map_version,
            )
            continue

        chapter, sentence, audio_ms = resolved
        chapter_moved = bookmark.epub_chapter != chapter
        bookmark.epub_chapter = chapter
        bookmark.epub_sentence_index = sentence
        if audio_ms is not None:
            bookmark.audio_position_ms = audio_ms
        bookmark.sync_map_version = new_version
        bookmark.updated_at = utcnow()
        if chapter_moved:
            bookmark.anchor_revision = (bookmark.anchor_revision or 0) + 1
        remapped += 1

        await db.flush()
        await _sync_derived_progress(db, bookmark.user_id, ref, bookmark)

    logger.info(
        "Re-mapped %s/%s bookmark(s) for pair %s onto sync map v%s",
        remapped, len(bookmarks), book_pair_id, new_version,
    )
    return remapped


def epub_to_audio(
    sync_points: List[SyncPoint],
    epub_chapter: int,
    epub_sentence_index: int,
    rewind_seconds: Optional[int] = None,
) -> int:
    """
    Convert an EPUB position to an audio position (milliseconds).

    Args:
        sync_points: List of SyncPoint objects (ordered by chapter, sentence)
        epub_chapter: Current chapter index
        epub_sentence_index: Current sentence index within the chapter
        rewind_seconds: How many seconds to rewind (default from settings)

    Returns:
        Audio position in milliseconds, with rewind applied.
    """
    if rewind_seconds is None:
        rewind_seconds = settings.default_rewind_seconds

    # Find exact match
    for point in sync_points:
        if point.epub_chapter == epub_chapter and point.epub_sentence_index == epub_sentence_index:
            audio_ms = max(0, point.audio_start_ms - (rewind_seconds * 1000))
            return audio_ms

    # Find closest preceding point
    best = None
    for point in sync_points:
        if (point.epub_chapter < epub_chapter or
            (point.epub_chapter == epub_chapter and point.epub_sentence_index <= epub_sentence_index)):
            best = point

    if best:
        audio_ms = max(0, best.audio_start_ms - (rewind_seconds * 1000))
        return audio_ms

    return 0  # Beginning of audiobook as fallback


def audio_to_epub(
    sync_points: List[SyncPoint],
    audio_position_ms: int,
) -> Tuple[int, int]:
    """
    Convert an audio position (milliseconds) to an EPUB position.

    Args:
        sync_points: List of SyncPoint objects (ordered by chapter, sentence)
        audio_position_ms: Current audio position in milliseconds

    Returns:
        Tuple of (epub_chapter, epub_sentence_index)
    """
    best = None
    for point in sync_points:
        if point.audio_start_ms <= audio_position_ms:
            best = point
        else:
            break  # Points are ordered, so we can stop early

    if best:
        return best.epub_chapter, best.epub_sentence_index

    return 0, 0  # Beginning of ebook as fallback
