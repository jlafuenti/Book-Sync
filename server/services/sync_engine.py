"""
Sync Engine Service

Handles saving sync maps and converting between ebook positions
and audio positions — the bridge that makes cross-mode sync work.
"""

import logging
from typing import List, Optional, Tuple

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from models.sync_map import SyncMap, SyncPoint
from services.alignment import AlignedPoint
from config import settings

logger = logging.getLogger(__name__)


async def save_sync_map(
    db: AsyncSession,
    book_pair_id: int,
    aligned_points: List[AlignedPoint],
) -> SyncMap:
    """
    Save alignment results as a SyncMap with SyncPoints.

    If a SyncMap already exists for this pair, it is replaced.
    """
    # Delete existing sync map for this pair
    result = await db.execute(
        select(SyncMap).where(SyncMap.book_pair_id == book_pair_id)
    )
    existing = result.scalar_one_or_none()
    if existing:
        await db.execute(
            delete(SyncPoint).where(SyncPoint.sync_map_id == existing.id)
        )
        await db.delete(existing)
        await db.flush()

    # Count unique chapters
    chapters = set(p.epub_chapter for p in aligned_points)

    # Create new sync map
    sync_map = SyncMap(
        book_pair_id=book_pair_id,
        version=1,
        total_sentences=len(aligned_points),
        total_chapters=len(chapters),
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
        )
        db.add(sp)

    await db.flush()
    logger.info(
        f"Saved SyncMap with {len(aligned_points)} points "
        f"for pair {book_pair_id}"
    )

    return sync_map


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
