"""
Bookmark/position sync router.
Handles reading and updating the user's current position in a book pair.
The sync engine automatically converts between ebook and audio positions.
"""

from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from database import get_db
from models.user import User
from models.book import BookPair
from models.bookmark import Bookmark, BookmarkLog, BookmarkSource
from models.sync_map import SyncMap, SyncPoint
from schemas import BookmarkUpdate, BookmarkResponse, BookmarkLogResponse
from routers.auth import get_current_user

router = APIRouter(prefix="/api/sync", tags=["sync"])


async def _convert_position(
    db: AsyncSession,
    book_pair_id: int,
    source: BookmarkSource,
    epub_chapter: int | None,
    epub_sentence_index: int | None,
    audio_position_ms: int | None,
) -> tuple[int | None, int | None, int | None]:
    """
    Given a position from one source, use the SyncMap to compute the
    corresponding position in the other format.

    Returns (epub_chapter, epub_sentence_index, audio_position_ms) with
    both sides filled in.
    """
    # Load the sync map
    result = await db.execute(
        select(SyncMap)
        .options(selectinload(SyncMap.sync_points))
        .where(SyncMap.book_pair_id == book_pair_id)
    )
    sync_map = result.scalar_one_or_none()

    if not sync_map or not sync_map.sync_points:
        # No sync map available yet — return as-is
        return epub_chapter, epub_sentence_index, audio_position_ms

    points = sync_map.sync_points  # Already ordered by chapter, sentence_index

    if source == BookmarkSource.EBOOK and epub_chapter is not None and epub_sentence_index is not None:
        # Find the matching sync point for this epub position
        for point in points:
            if point.epub_chapter == epub_chapter and point.epub_sentence_index == epub_sentence_index:
                return epub_chapter, epub_sentence_index, point.audio_start_ms

        # If exact match not found, find the closest preceding point
        best = None
        for point in points:
            if (point.epub_chapter < epub_chapter or
                (point.epub_chapter == epub_chapter and point.epub_sentence_index <= epub_sentence_index)):
                best = point

        if best:
            return epub_chapter, epub_sentence_index, best.audio_start_ms

    elif source == BookmarkSource.AUDIOBOOK and audio_position_ms is not None:
        # Binary search for the sync point covering this audio position
        best = None
        for point in points:
            if point.audio_start_ms <= audio_position_ms:
                best = point
            else:
                break

        if best:
            return best.epub_chapter, best.epub_sentence_index, audio_position_ms

    return epub_chapter, epub_sentence_index, audio_position_ms


@router.get("/bookmark/{pair_id}", response_model=BookmarkResponse)
async def get_bookmark(
    pair_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get the user's current bookmark for a book pair."""
    result = await db.execute(
        select(Bookmark).where(
            Bookmark.user_id == current_user.id,
            Bookmark.book_pair_id == pair_id,
        )
    )
    bookmark = result.scalar_one_or_none()

    if not bookmark:
        # Verify the book pair exists
        result = await db.execute(select(BookPair).where(BookPair.id == pair_id))
        if not result.scalar_one_or_none():
            raise HTTPException(status_code=404, detail="Book pair not found")

        # Create a new bookmark at the beginning
        bookmark = Bookmark(
            user_id=current_user.id,
            book_pair_id=pair_id,
            source=BookmarkSource.EBOOK,
            epub_chapter=0,
            epub_sentence_index=0,
            audio_position_ms=0,
        )
        db.add(bookmark)
        await db.flush()
        await db.refresh(bookmark)

    return bookmark


@router.put("/bookmark/{pair_id}", response_model=BookmarkResponse)
async def update_bookmark(
    pair_id: int,
    update: BookmarkUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Update the user's bookmark position for a book pair.
    Automatically computes the corresponding position in the other format
    using the SyncMap.
    """
    # Verify book pair exists
    result = await db.execute(select(BookPair).where(BookPair.id == pair_id))
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Book pair not found")

    # Get or create bookmark
    result = await db.execute(
        select(Bookmark).where(
            Bookmark.user_id == current_user.id,
            Bookmark.book_pair_id == pair_id,
        )
    )
    bookmark = result.scalar_one_or_none()

    # Convert position using sync map
    epub_ch, epub_si, audio_ms = await _convert_position(
        db, pair_id, update.source,
        update.epub_chapter, update.epub_sentence_index,
        update.audio_position_ms,
    )

    if not bookmark:
        bookmark = Bookmark(
            user_id=current_user.id,
            book_pair_id=pair_id,
            source=update.source,
            epub_chapter=epub_ch,
            epub_sentence_index=epub_si,
            audio_position_ms=audio_ms,
        )
        db.add(bookmark)
        await db.flush()
        await db.refresh(bookmark)
    else:
        # Log previous position
        log = BookmarkLog(
            bookmark_id=bookmark.id,
            source=update.source,
            prev_epub_chapter=bookmark.epub_chapter,
            prev_epub_sentence_index=bookmark.epub_sentence_index,
            prev_audio_position_ms=bookmark.audio_position_ms,
            new_epub_chapter=epub_ch,
            new_epub_sentence_index=epub_si,
            new_audio_position_ms=audio_ms,
        )
        db.add(log)

        # Update bookmark
        bookmark.source = update.source
        bookmark.epub_chapter = epub_ch
        bookmark.epub_sentence_index = epub_si
        bookmark.audio_position_ms = audio_ms
        bookmark.updated_at = datetime.utcnow()
        bookmark.synced_at = datetime.utcnow()

    return bookmark


@router.get("/bookmark/{pair_id}/log", response_model=List[BookmarkLogResponse])
async def get_bookmark_log(
    pair_id: int,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get the bookmark change history for a book pair."""
    result = await db.execute(
        select(Bookmark).where(
            Bookmark.user_id == current_user.id,
            Bookmark.book_pair_id == pair_id,
        )
    )
    bookmark = result.scalar_one_or_none()

    if not bookmark:
        return []

    result = await db.execute(
        select(BookmarkLog)
        .where(BookmarkLog.bookmark_id == bookmark.id)
        .order_by(BookmarkLog.changed_at.desc())
        .limit(limit)
    )
    return result.scalars().all()
