"""
Bookmark/position sync router.
Handles reading and updating the user's current position in a book pair.
The sync engine automatically converts between ebook and audio positions.
"""

from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy import select, delete, or_
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
    PositionScope, PositionUpdate, PositionResponse, PositionHintPayload,
    TextMatchRequest, TextMatchResponse
)
from models.bookmark import HintKind
from services.position_service import (
    PositionScopeError, apply_position, latest_progress_row, read_position,
    resolve_scope, to_response_dict,
)
from routers.auth import get_current_user

router = APIRouter(prefix="/api/sync", tags=["sync"])


# The staleness rule (issue #54) now lives in position_service.is_stale, so
# every write path — the canonical endpoint and both legacy adapters — is
# judged by one implementation. Keeping a second copy here is how the two
# endpoints came to disagree in the first place.


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

        # Synthesise a start-of-book response for legacy clients that expect a
        # 200 here — but do NOT persist it. A stored chapter-0 row is
        # indistinguishable from a real position at the start of a book, which
        # makes "has this user read any of this?" unanswerable and gives a
        # failed restore something to overwrite.
        bookmark = Bookmark(
            id=0,
            user_id=current_user.id,
            book_pair_id=pair_id,
            source=BookmarkSource.EBOOK,
            epub_chapter=0,
            epub_sentence_index=0,
            audio_position_ms=0,
            anchor_revision=1,
            is_completed=False,
            updated_at=datetime.utcnow(),
        )

    # Attach epub_text_preview by looking up nearest sync point with a preview
    _, _, _, text_preview = await _convert_position(
        db, pair_id, bookmark.source,
        bookmark.epub_chapter, bookmark.epub_sentence_index,
        bookmark.audio_position_ms,
    )
    response = BookmarkResponse.model_validate(bookmark)
    response.epub_text_preview = text_preview
    return response


@router.put(
    "/bookmark/{pair_id}",
    response_model=BookmarkResponse,
    responses={409: {"model": BookmarkResponse}},
)
async def update_bookmark(
    pair_id: int,
    update: BookmarkUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Legacy bookmark write — an adapter over the canonical position service.

    Kept so app builds that predate `PUT /position/{scope}/{id}` keep working.
    Because both go through one write path, an old phone and a new one
    converge on the same record instead of maintaining two that drift apart.

    Conflict resolution (issue #54) is unchanged: a write whose `captured_at`
    is older than the stored one is a stale replay and is rejected with 409
    carrying the current state. Clients that omit `captured_at` keep
    last-write-wins.
    """
    try:
        ref = await resolve_scope(db, PositionScope.PAIR, pair_id)
    except PositionScopeError:
        raise HTTPException(status_code=404, detail="Book pair not found")

    # The sync map still supplies the cross-format position and the searchable
    # preview; it no longer decides what the client's anchor was.
    epub_ch, epub_si, audio_ms, text_preview = await _convert_position(
        db, pair_id, update.source,
        update.epub_chapter, update.epub_sentence_index,
        update.audio_position_ms,
    )

    hint = None
    if update.epub_locator is not None:
        hint = PositionHintPayload(
            kind=HintKind.READIUM_LOCATOR,
            value=update.epub_locator,
            audio_position_ms=update.locator_audio_ms,
        )

    position = PositionUpdate(
        source=update.source,
        epub_chapter=epub_ch,
        epub_sentence_index=epub_si,
        epub_text_preview=text_preview,
        audio_position_ms=audio_ms,
        hint=hint,
        append_to_log=update.append_to_log,
        captured_at=update.captured_at,
        device_id=update.device_id,
        device_name=update.device_name,
    )

    record, accepted = await apply_position(db, current_user.id, ref, position)

    response = BookmarkResponse.model_validate(record)
    response.epub_text_preview = text_preview
    if not accepted:
        return JSONResponse(status_code=409, content=jsonable_encoder(response))
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


@router.put(
    "/progress/{media_type}/{media_id}",
    response_model=ProgressResponse,
    responses={409: {"model": ProgressResponse}},
)
async def update_progress(
    media_type: ProgressType,
    media_id: int,
    update_data: ProgressUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Legacy progress write — an adapter over the canonical position service.

    `user_progress` is now a projection of the canonical record rather than an
    independently written table. Routing this endpoint through the same service
    is what stops a client's two writes being adjudicated separately and
    leaving the two rows describing different positions.

    A write scoped to a paired book updates the pair's canonical record; an
    unpaired one gets its own standalone record.

    Conflict resolution (issue #54) is unchanged: a stale `captured_at` is
    rejected with 409 carrying the current state.
    """
    if media_type == ProgressType.EBOOK:
        exists = (await db.execute(select(EBook.id).where(EBook.id == media_id))).scalar_one_or_none()
        if not exists:
            raise HTTPException(status_code=404, detail=f"Ebook {media_id} not found")
        scope = PositionScope.EBOOK
    else:
        exists = (await db.execute(select(AudioBook.id).where(AudioBook.id == media_id))).scalar_one_or_none()
        if not exists:
            raise HTTPException(status_code=404, detail=f"Audiobook {media_id} not found")
        scope = PositionScope.AUDIOBOOK

    # Prefer the pair scope when this media is half of one, so the reader and
    # the player share a single record for the book.
    ref = None
    pair_id = update_data.book_pair_id
    if pair_id is None:
        col = BookPair.ebook_id if media_type == ProgressType.EBOOK else BookPair.audiobook_id
        pair_id = (await db.execute(select(BookPair.id).where(col == media_id))).scalar_one_or_none()
    if pair_id is not None:
        try:
            ref = await resolve_scope(db, PositionScope.PAIR, pair_id)
        except PositionScopeError:
            ref = None
    if ref is None:
        ref = await resolve_scope(db, scope, media_id)

    hint = None
    if update_data.epub_cfi is not None:
        hint = PositionHintPayload(kind=HintKind.EPUBJS_CFI, value=update_data.epub_cfi)

    position = PositionUpdate(
        # This endpoint's request body has no `source` field at all, so every
        # write through it used to synthesize one from the URL's media_type —
        # a background audiobook-player save through
        # `/progress/audiobook/{id}` re-stamped `source=audiobook` even
        # mid-read. Passing None lets apply_position's merge rule keep
        # whatever source is already stored.
        source=None,
        # A progress write's epub_chapter is a spine index (that is what both
        # readers send here), which is the axis the canonical record stores.
        epub_chapter=update_data.epub_chapter,
        epub_progress_percent=update_data.epub_progress_percent,
        audio_position_ms=update_data.audio_position_ms,
        is_completed=update_data.is_completed,
        hint=hint,
        captured_at=update_data.captured_at,
        device_id=update_data.device_id,
        device_name=update_data.device_name,
    )

    _, accepted = await apply_position(db, current_user.id, ref, position)

    # Return the projected row the caller asked about.
    # Tolerates the duplicate rows an old flush-without-commit race left behind
    # (see latest_progress_row) — those books would otherwise 500 on every write.
    row = await latest_progress_row(db, current_user.id, media_type, media_id)
    if row is None:
        raise HTTPException(status_code=500, detail="progress projection missing")

    payload = ProgressResponse.model_validate(row)
    if not accepted:
        return JSONResponse(status_code=409, content=jsonable_encoder(payload))
    return payload


@router.delete("/progress/pair/{pair_id}")
async def reset_pair_progress(
    pair_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Delete ALL progress records for a book pair (catches corrupted records too).

    `user_progress` is only a projection of the canonical `Bookmark` now (see
    `services/position_service.py`). Deleting just the projection used to leave
    the bookmark in place, so the next write re-seeded `user_progress` from it
    and "reset" silently un-reset itself (issue #6). So this also deletes the
    canonical bookmark row(s) for this pair — the pair-scoped row and any
    standalone ebook/audiobook rows for the same underlying media, since a
    client can reach those independently of the pair.
    """
    # Verify the pair exists
    result = await db.execute(select(BookPair).where(BookPair.id == pair_id))
    pair = result.scalar_one_or_none()
    if not pair:
        raise HTTPException(status_code=404, detail="Book pair not found")

    # Load (not bulk-delete) the bookmark rows: `Bookmark.hints` cascades via
    # `cascade="all, delete-orphan"`, which only fires on ORM instance deletion
    # — a Core `delete()` statement would bypass it and orphan `position_hints`.
    bookmark_scopes = [Bookmark.book_pair_id == pair_id]
    if pair.ebook_id:
        bookmark_scopes.append(
            (Bookmark.book_pair_id.is_(None)) & (Bookmark.ebook_id == pair.ebook_id)
        )
    if pair.audiobook_id:
        bookmark_scopes.append(
            (Bookmark.book_pair_id.is_(None)) & (Bookmark.audiobook_id == pair.audiobook_id)
        )

    result = await db.execute(
        select(Bookmark)
        .options(selectinload(Bookmark.hints))
        .where(Bookmark.user_id == current_user.id, or_(*bookmark_scopes))
    )
    for bookmark in result.scalars().all():
        await db.delete(bookmark)

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


# The matching algorithm itself lives in services/sync_matcher.py -- it is shared,
# vector-for-vector, with the Android client (issue #41). Aliased here because
# existing call sites and tests import these private names from this module.
from services.sync_matcher import (  # noqa: E402
    match_text_to_sync_points as _match_text_to_sync_points,
    normalize_for_search as _normalize_for_search,
)



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


# ====================================================================
# Canonical position endpoints
#
# One record, one staleness verdict, one transaction. The bookmark and
# progress endpoints above are adapters over the same service, so an old app
# build and a new one converge on this row instead of writing two that drift.
# ====================================================================

@router.get("/position/{scope}/{ident}")
async def get_position(
    scope: PositionScope,
    ident: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """The canonical position, or 204 when the user has none.

    Never creates a row. A GET that manufactures a chapter-0 position (which
    the legacy bookmark GET does) makes "has this user read any of this?"
    unanswerable, and a fabricated chapter 0 is indistinguishable from a real
    position at the start of a book.
    """
    try:
        ref = await resolve_scope(db, scope, ident)
    except PositionScopeError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    record = await read_position(db, current_user.id, ref)
    if record is None:
        return Response(status_code=204)
    return PositionResponse.model_validate(to_response_dict(record, ref))


@router.put(
    "/position/{scope}/{ident}",
    response_model=PositionResponse,
    responses={409: {"model": PositionResponse}},
)
async def put_position(
    scope: PositionScope,
    ident: int,
    update: PositionUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Apply a whole position atomically.

    A stale write returns 409 with the authoritative state and changes
    nothing — not the record, not the hints, not the derived progress rows.
    """
    try:
        ref = await resolve_scope(db, scope, ident)
    except PositionScopeError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    record, accepted = await apply_position(db, current_user.id, ref, update)
    payload = PositionResponse.model_validate(to_response_dict(record, ref))
    if not accepted:
        return JSONResponse(status_code=409, content=jsonable_encoder(payload))
    return payload
