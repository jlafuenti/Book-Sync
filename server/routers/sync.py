"""
Bookmark/position sync router.
Handles reading and updating the user's current position in a book pair.
The sync engine automatically converts between ebook and audio positions.
"""

from typing import List

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy import select, delete, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from database import get_db
from models.user import User
from models.book import BookPair
from models.bookmark import Bookmark, BookmarkLog
from models.sync_map import SyncMap
from models.progress import UserProgress, ProgressType
from schemas import (
    BookmarkLogResponse, ProgressResponse,
    PositionScope, PositionUpdate, PositionResponse,
    TextMatchRequest, TextMatchResponse
)
from services.position_service import (
    PositionScopeError, apply_position, latest_progress_row, read_position,
    resolve_scope, to_response_dict,
)
from routers.auth import get_current_user

router = APIRouter(prefix="/api/sync", tags=["sync"])


# The staleness rule (issue #54) lives in position_service.is_stale. There is
# exactly one write path — `PUT /position/{scope}/{ident}` — so there is
# nothing left to disagree with it. The legacy `PUT /bookmark/{pair}` and
# `PUT /progress/{type}/{id}` adapters are gone (issue #102).


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


@router.get(
    "/progress/{media_type}/{media_id}",
    response_model=ProgressResponse,
    responses={204: {"description": "No progress recorded for this media"}},
)
async def get_progress(
    media_type: ProgressType,
    media_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """The user's progress for a piece of media, or 204 when there is none.

    Never creates a row — same contract as the canonical `GET /position`. This
    endpoint used to INSERT on a miss, so two clients opening the same book at
    once each left a row behind and every subsequent read of that media raised
    `MultipleResultsFound` (issue #64).

    Read via `latest_progress_row` rather than `scalar_one_or_none()` so a
    database that hit that race *before* the unique index landed still answers
    instead of 500ing; migration 0006 dedupes it.
    """
    progress = await latest_progress_row(db, current_user.id, media_type, media_id)

    if progress is None:
        return Response(status_code=204)

    return progress


async def _reset_position(db: AsyncSession, user_id: int, ref) -> None:
    """Delete the canonical record(s) a scope addresses, plus their projection.

    `user_progress` is only a projection of the canonical `Bookmark` (see
    `services/position_service.py`). Deleting just the projection used to leave
    the bookmark in place, so the next write re-seeded `user_progress` straight
    back from it and "reset" silently un-reset itself (issue #6).

    A **pair** reset also sweeps the standalone ebook/audiobook rows for the
    same underlying media: a client can reach those scopes independently of the
    pair, and a survivor would resurrect the position. A **standalone** reset is
    deliberately not symmetric — it must not wipe the pair's record.
    """
    bookmark_scopes = []
    if ref.scope == PositionScope.PAIR:
        bookmark_scopes.append(Bookmark.book_pair_id == ref.book_pair_id)
        if ref.ebook_id:
            bookmark_scopes.append(
                (Bookmark.book_pair_id.is_(None)) & (Bookmark.ebook_id == ref.ebook_id)
            )
        if ref.audiobook_id:
            bookmark_scopes.append(
                (Bookmark.book_pair_id.is_(None))
                & (Bookmark.audiobook_id == ref.audiobook_id)
            )
    elif ref.scope == PositionScope.EBOOK:
        bookmark_scopes.append(
            (Bookmark.book_pair_id.is_(None)) & (Bookmark.ebook_id == ref.ebook_id)
        )
    else:
        bookmark_scopes.append(
            (Bookmark.book_pair_id.is_(None))
            & (Bookmark.audiobook_id == ref.audiobook_id)
        )

    # Load (not bulk-delete) the bookmark rows: `Bookmark.hints` cascades via
    # `cascade="all, delete-orphan"`, which only fires on ORM instance deletion
    # — a Core `delete()` statement would bypass it and orphan `position_hints`.
    rows = (await db.execute(
        select(Bookmark)
        .options(selectinload(Bookmark.hints))
        .where(Bookmark.user_id == user_id, or_(*bookmark_scopes))
    )).scalars().all()
    for bookmark in rows:
        await db.delete(bookmark)
    await db.flush()

    media = []
    if ref.ebook_id:
        media.append((ProgressType.EBOOK, UserProgress.ebook_id, ref.ebook_id))
    if ref.audiobook_id:
        media.append((ProgressType.AUDIOBOOK, UserProgress.audiobook_id, ref.audiobook_id))

    for media_type, id_col, media_id in media:
        # `user_progress` is keyed by media, not by scope — the pair-scoped and
        # standalone-scoped records project into the *same* row. So a standalone
        # reset may only drop it once nothing else still writes it, or resetting
        # an unpaired view would blank the pair's projection too.
        bookmark_col = (Bookmark.ebook_id if media_type == ProgressType.EBOOK
                        else Bookmark.audiobook_id)
        still_covered = (await db.execute(
            select(Bookmark.id).where(
                Bookmark.user_id == user_id,
                or_(bookmark_col == media_id,
                    Bookmark.book_pair_id.in_(
                        select(BookPair.id).where(
                            (BookPair.ebook_id == media_id)
                            if media_type == ProgressType.EBOOK
                            else (BookPair.audiobook_id == media_id)
                        )
                    )),
            ).limit(1)
        )).scalar_one_or_none()
        if still_covered is not None:
            continue
        await db.execute(
            delete(UserProgress).where(
                UserProgress.user_id == user_id,
                UserProgress.media_type == media_type,
                id_col == media_id,
            )
        )

    if ref.scope == PositionScope.PAIR:
        # Catches rows whose `book_pair_id` was set but whose media ids weren't
        # (older writes), which the media sweep above would miss.
        await db.execute(
            delete(UserProgress).where(
                UserProgress.user_id == user_id,
                UserProgress.book_pair_id == ref.book_pair_id,
            )
        )

    await db.commit()


@router.delete("/position/{scope}/{ident}")
async def reset_position(
    scope: PositionScope,
    ident: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Clear the user's position for a scope — the canonical reset.

    Standalone media had no reset endpoint at all; the web client faked one by
    PUTting zeros through the legacy progress adapter, which left the canonical
    bookmark behind for the next write to resurrect.
    """
    try:
        ref = await resolve_scope(db, scope, ident)
    except PositionScopeError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    await _reset_position(db, current_user.id, ref)
    return {"status": "ok"}


@router.delete("/progress/pair/{pair_id}")
async def reset_pair_progress(
    pair_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Alias for `DELETE /position/pair/{id}`, kept so existing clients don't
    have to change their reset call in the same release."""
    try:
        ref = await resolve_scope(db, PositionScope.PAIR, pair_id)
    except PositionScopeError:
        raise HTTPException(status_code=404, detail="Book pair not found")

    await _reset_position(db, current_user.id, ref)
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
# One record, one staleness verdict, one transaction. This is the only write
# path; the `/progress` routes above are read-only projections of what lands
# here, and the reset routes delete the same record these write.
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
