"""
Bookmark/position sync router.
Handles reading and updating the user's current position in a book pair.
The sync engine automatically converts between ebook and audio positions.
"""

from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from database import get_db
from models.user import User
from models.book import BookPair, EBook, AudioBook
from models.bookmark import Bookmark, BookmarkLog, BookmarkSource
from models.sync_map import SyncMap, SyncPoint
from models.progress import UserProgress, ProgressType
from schemas import (
    BookmarkUpdate, BookmarkResponse, BookmarkLogResponse,
    ProgressUpdate, ProgressResponse,
    TextMatchRequest, TextMatchResponse
)
from routers.auth import get_current_user

router = APIRouter(prefix="/api/sync", tags=["sync"])


async def _convert_position(
    db: AsyncSession,
    book_pair_id: int,
    source: BookmarkSource,
    epub_chapter: int | None,
    epub_sentence_index: int | None,
    audio_position_ms: int | None,
) -> tuple[int | None, int | None, int | None, str | None]:
    """
    Given a position from one source, use the SyncMap to compute the
    corresponding position in the other format.

    Returns (epub_chapter, epub_sentence_index, audio_position_ms, epub_text_preview)
    with both sides filled in.
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
        return epub_chapter, epub_sentence_index, audio_position_ms, None

    points = sync_map.sync_points  # Already ordered by chapter, sentence_index

    def _nearest_preview(chapter: int, sentence_index: int) -> str | None:
        """Find the epub_text_preview from the nearest sync point in the same chapter
        that has a non-null preview — fallback when the matched point has no preview."""
        candidates = [p for p in points if p.epub_chapter == chapter and p.epub_text_preview]
        if not candidates:
            return None
        return min(candidates, key=lambda p: abs(p.epub_sentence_index - sentence_index)).epub_text_preview

    if source == BookmarkSource.EBOOK and epub_chapter is not None and epub_sentence_index is not None:
        # Find the matching sync point for this epub position
        for point in points:
            if point.epub_chapter == epub_chapter and point.epub_sentence_index == epub_sentence_index:
                preview = point.epub_text_preview or _nearest_preview(epub_chapter, epub_sentence_index)
                return epub_chapter, epub_sentence_index, point.audio_start_ms, preview

        # If exact match not found, find the closest preceding point
        best = None
        for point in points:
            if (point.epub_chapter < epub_chapter or
                (point.epub_chapter == epub_chapter and point.epub_sentence_index <= epub_sentence_index)):
                best = point

        if best:
            preview = best.epub_text_preview or _nearest_preview(epub_chapter, epub_sentence_index)
            return epub_chapter, epub_sentence_index, best.audio_start_ms, preview

    elif source == BookmarkSource.AUDIOBOOK and audio_position_ms is not None:
        # Find the sync point covering this audio position
        best = None
        for point in points:
            if point.audio_start_ms <= audio_position_ms:
                best = point
            else:
                break

        if best:
            preview = best.epub_text_preview or _nearest_preview(best.epub_chapter, best.epub_sentence_index)
            return best.epub_chapter, best.epub_sentence_index, audio_position_ms, preview

    return epub_chapter, epub_sentence_index, audio_position_ms, None


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

    # Attach epub_text_preview by looking up nearest sync point with a preview
    _, _, _, text_preview = await _convert_position(
        db, pair_id, bookmark.source,
        bookmark.epub_chapter, bookmark.epub_sentence_index,
        bookmark.audio_position_ms,
    )
    response = BookmarkResponse.model_validate(bookmark)
    response.epub_text_preview = text_preview
    return response


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
    epub_ch, epub_si, audio_ms, text_preview = await _convert_position(
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
        # Only log if position actually changed
        position_changed = (
            bookmark.epub_chapter != epub_ch
            or bookmark.epub_sentence_index != epub_si
            or bookmark.audio_position_ms != audio_ms
        )
        if position_changed:
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

    await db.commit()
    await db.refresh(bookmark)
    # Attach epub_text_preview from the matched sync point (not stored on the model)
    response = BookmarkResponse.model_validate(bookmark)
    response.epub_text_preview = text_preview
    return response


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


# ============================================================
# User Progress Tracking (Individual Media)
# ============================================================

@router.get("/progress", response_model=List[ProgressResponse])
async def get_all_progress(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get all progress records for the current user."""
    result = await db.execute(
        select(UserProgress).where(UserProgress.user_id == current_user.id)
    )
    return result.scalars().all()


@router.get("/progress/{media_type}/{media_id}", response_model=ProgressResponse)
async def get_progress(
    media_type: ProgressType,
    media_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get the user's progress for a specific piece of media."""
    query = select(UserProgress).where(
        UserProgress.user_id == current_user.id,
        UserProgress.media_type == media_type
    )
    
    if media_type == ProgressType.EBOOK:
        query = query.where(UserProgress.ebook_id == media_id)
    else:
        query = query.where(UserProgress.audiobook_id == media_id)
        
    result = await db.execute(query)
    progress = result.scalar_one_or_none()
    
    if not progress:
        # Create an empty progress record to return
        progress_data = {
            "user_id": current_user.id,
            "media_type": media_type,
            "is_completed": False
        }
        if media_type == ProgressType.EBOOK:
            progress_data["ebook_id"] = media_id
        else:
            progress_data["audiobook_id"] = media_id
            
        progress = UserProgress(**progress_data)
        db.add(progress)
        await db.flush()
        await db.refresh(progress)

    return progress


@router.put("/progress/{media_type}/{media_id}", response_model=ProgressResponse)
async def update_progress(
    media_type: ProgressType,
    media_id: int,
    update_data: ProgressUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Update progress for a specific piece of media."""
    
    # 1. Verify existence of media
    if media_type == ProgressType.EBOOK:
        result = await db.execute(select(EBook).where(EBook.id == media_id))
        if not result.scalar_one_or_none():
            raise HTTPException(status_code=404, detail=f"Ebook {media_id} not found")
    else:
        result = await db.execute(select(AudioBook).where(AudioBook.id == media_id))
        if not result.scalar_one_or_none():
            raise HTTPException(status_code=404, detail=f"Audiobook {media_id} not found")
        
    query = select(UserProgress).where(
        UserProgress.user_id == current_user.id,
        UserProgress.media_type == media_type
    )
    
    if media_type == ProgressType.EBOOK:
        query = query.where(UserProgress.ebook_id == media_id)
    else:
        query = query.where(UserProgress.audiobook_id == media_id)
        
    result = await db.execute(query)
    progress = result.scalar_one_or_none()
    
    if not progress:
        progress_data = {
            "user_id": current_user.id,
            "media_type": media_type,
            "book_pair_id": update_data.book_pair_id
        }
        if media_type == ProgressType.EBOOK:
            progress_data["ebook_id"] = media_id
        else:
            progress_data["audiobook_id"] = media_id
            
        progress = UserProgress(**progress_data)
        db.add(progress)
    
    # Update fields
    if update_data.book_pair_id is not None:
        progress.book_pair_id = update_data.book_pair_id
        
    if media_type == ProgressType.EBOOK:
        if update_data.epub_cfi is not None:
            progress.epub_cfi = update_data.epub_cfi
        if update_data.epub_chapter is not None:
            progress.epub_chapter = update_data.epub_chapter
        if update_data.epub_progress_percent is not None:
            progress.epub_progress_percent = update_data.epub_progress_percent
    elif media_type == ProgressType.AUDIOBOOK:
        if update_data.audio_position_ms is not None:
            progress.audio_position_ms = update_data.audio_position_ms
            
    if update_data.is_completed is not None:
        progress.is_completed = update_data.is_completed
        
    if update_data.device_id is not None:
        progress.device_id = update_data.device_id
        
    progress.updated_at = datetime.utcnow()

    await db.commit()
    await db.refresh(progress)

    return progress


@router.delete("/progress/pair/{pair_id}")
async def reset_pair_progress(
    pair_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Delete ALL progress records for a book pair (catches corrupted records too)."""
    # Verify the pair exists
    result = await db.execute(select(BookPair).where(BookPair.id == pair_id))
    pair = result.scalar_one_or_none()
    if not pair:
        raise HTTPException(status_code=404, detail="Book pair not found")

    # Delete all progress records linked to this pair for this user
    await db.execute(
        delete(UserProgress).where(
            UserProgress.user_id == current_user.id,
            UserProgress.book_pair_id == pair_id,
        )
    )

    # Also delete any progress records by media ID that belong to this pair
    # (in case book_pair_id wasn't set on some records)
    if pair.ebook_id:
        await db.execute(
            delete(UserProgress).where(
                UserProgress.user_id == current_user.id,
                UserProgress.media_type == ProgressType.EBOOK,
                UserProgress.ebook_id == pair.ebook_id,
            )
        )
    if pair.audiobook_id:
        await db.execute(
            delete(UserProgress).where(
                UserProgress.user_id == current_user.id,
                UserProgress.media_type == ProgressType.AUDIOBOOK,
                UserProgress.audiobook_id == pair.audiobook_id,
            )
        )

    await db.commit()
    return {"status": "ok"}


import re
import unicodedata


def _normalize_for_search(text: str) -> str:
    """Normalize text for substring matching — same algorithm as Android normalizeForSearch."""
    t = text.lower()
    # Replace all whitespace variants with regular space
    for ch in "\n\r\t\u00a0\u2002\u2003\u2009\u200b\u202f":
        t = t.replace(ch, " ")
    # Keep only a-z, 0-9, and space
    t = re.sub(r"[^a-z0-9 ]", "", t)
    # Collapse multiple spaces
    t = re.sub(r" +", " ", t)
    return t.strip()


def _match_text_to_sync_points(
    sync_points: list[SyncPoint],
    epub_text: str,
    chapter_hint: int,
) -> SyncPoint | None:
    """
    Find the sync point matching extracted EPUB text.
    Same algorithm as Android getSyncPointForEpubText:
    1. Normalize epub text
    2. For each chapter (hint first, then ±10):
       - Build transcript by concatenating normalized sync point previews
       - Progressive substring search (200→150→100→60→30 chars)
       - Try from start, then skip 30 chars
    """
    normalized_epub = _normalize_for_search(epub_text)
    if len(normalized_epub) < 10:
        return None

    # Group sync points by chapter
    chapters: dict[int, list[SyncPoint]] = {}
    for p in sync_points:
        chapters.setdefault(p.epub_chapter, []).append(p)
    for ch in chapters:
        chapters[ch].sort(key=lambda p: p.epub_sentence_index)

    # Try chapters in order: hint first, then expanding outward ±10
    chapters_to_try = [chapter_hint]
    for d in range(1, 11):
        chapters_to_try.extend([chapter_hint - d, chapter_hint + d])

    search_lengths = sorted(set(
        min(len(normalized_epub), l) for l in [200, 150, 100, 60, 30]
        if min(len(normalized_epub), l) > 10
    ), reverse=True)

    for target_chapter in chapters_to_try:
        points = chapters.get(target_chapter)
        if not points:
            continue

        # Build concatenated transcript with sentence boundary tracking
        transcript_parts = []
        boundaries = []  # (start_char_index, point_index)
        pos = 0
        for idx, point in enumerate(points):
            preview = point.epub_text_preview
            if not preview:
                continue
            normalized = _normalize_for_search(preview)
            if not normalized:
                continue
            boundaries.append((pos, idx))
            transcript_parts.append(normalized)
            pos += len(normalized) + 1  # +1 for space separator

        transcript = " ".join(transcript_parts)
        if not transcript:
            continue

        for search_len in search_lengths:
            search_text = normalized_epub[:search_len]
            match_index = transcript.find(search_text)

            # Also try skipping first 30 chars (handles chapter headings)
            if match_index < 0 and len(normalized_epub) > search_len + 30:
                offset_text = normalized_epub[30 : 30 + search_len]
                match_index = transcript.find(offset_text)

            if match_index >= 0:
                # Map character offset to sentence index
                matched_idx = 0
                for start_pos, idx in boundaries:
                    if start_pos <= match_index:
                        matched_idx = idx
                    else:
                        break
                return points[matched_idx]

    return None


@router.post("/match-text/{pair_id}", response_model=TextMatchResponse)
async def match_text_to_audio(
    pair_id: int,
    body: TextMatchRequest,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Match extracted EPUB text to an audio position using the sync map."""
    result = await db.execute(
        select(SyncMap)
        .options(selectinload(SyncMap.sync_points))
        .where(SyncMap.book_pair_id == pair_id)
    )
    sync_map = result.scalar_one_or_none()

    if not sync_map or not sync_map.sync_points:
        raise HTTPException(status_code=404, detail="No sync map found for this pair")

    matched_point = _match_text_to_sync_points(
        sync_map.sync_points,
        body.epub_text,
        body.chapter_hint,
    )

    if matched_point is None:
        raise HTTPException(status_code=404, detail="No matching audio position found")

    return TextMatchResponse(
        audio_position_ms=matched_point.audio_start_ms,
        epub_chapter=matched_point.epub_chapter,
        epub_sentence_index=matched_point.epub_sentence_index,
        preview=matched_point.epub_text_preview,
    )
