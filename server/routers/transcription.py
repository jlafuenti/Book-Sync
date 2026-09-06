"""
Transcription router: queue management and monitoring for transcription jobs.

Endpoints:
  POST /{pair_id}/start     — Add a book pair to the transcription queue
  GET  /{pair_id}/status    — Get transcription status for a pair
  POST /{pair_id}/cancel    — Cancel a transcription job
  PUT  /{pair_id}/text      — Update transcription text for sync points
  GET  /queue               — List the full transcription queue
  POST /queue/batch         — Add multiple pairs to the queue
  DELETE /queue/{item_id}   — Remove a queue item
  PUT  /queue/{item_id}/priority — Change priority of a queue item
  POST /queue/{item_id}/run-now  — Dispatch now, bypassing the off-hours window
  POST /queue/{item_id}/requeue  — Retry a failed/cancelled item (#247)
  GET  /offhours            — Current off-hours window state (#106)
"""

import asyncio
from typing import Dict, List

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from database import get_db, async_session
from models.user import User
from models.book import BookPair, PairStatus
from models.sync_map import SyncMap, SyncPoint
from models.transcription_queue import TranscriptionQueueItem
from schemas import (
    TranscriptionStatusResponse,
    SyncMapTextUpdate,
    OffHoursStatusResponse,
    QueueItemResponse,
    QueueAddRequest,
    QueuePriorityUpdate,
)
from rate_limit import search_reads
from routers.auth import (
    get_current_user,
    get_admin_user,
    get_editor_user,
    rate_limited,
)
from utils import utcnow

router = APIRouter(prefix="/api/transcription", tags=["transcription"])

# Ceiling on `GET /queue/history` (issue #208). Same number as the other capped
# read endpoints — no reason for a client to have to learn a different one.
QUEUE_HISTORY_MAX_LIMIT = 200


# ====================================================================
# Queue Management Endpoints
# ====================================================================

@router.post("/{pair_id}/start", response_model=QueueItemResponse)
async def start_transcription(
    pair_id: int,
    db: AsyncSession = Depends(get_db),
    # Editor floor (issue #207): starting a job spends the GPU for hours and
    # cancelling one discards work that may not be the canceller's.
    _: User = Depends(get_editor_user),
):
    """
    Add a book pair to the transcription queue.
    Replaces the old direct-start approach with queue-based processing.
    """
    # Verify book pair exists
    result = await db.execute(
        select(BookPair)
        .options(selectinload(BookPair.ebook), selectinload(BookPair.audiobook))
        .where(BookPair.id == pair_id)
    )
    pair = result.scalar_one_or_none()
    if not pair:
        raise HTTPException(status_code=404, detail="Book pair not found")

    if pair.status == PairStatus.SYNCED:
        raise HTTPException(
            status_code=409,
            detail="Already synced. Delete existing sync map first to re-transcribe.",
        )

    # Add to queue via the queue manager
    from services.queue_manager import add_to_queue
    created = await add_to_queue([pair_id])

    if not created:
        # Already in queue
        from services.queue_manager import get_queue_item_for_pair
        existing = await get_queue_item_for_pair(pair_id)
        if existing:
            return _queue_item_to_response(existing, pair)
        raise HTTPException(status_code=409, detail="Already in transcription queue")

    item = created[0]
    return _queue_item_to_response(item, pair)


@router.get("/{pair_id}/status", response_model=TranscriptionStatusResponse)
async def get_transcription_status(
    pair_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Get the status of a transcription job (checks queue first, then DB)."""
    # Check queue for active/pending items
    from services.queue_manager import get_queue_item_for_pair
    queue_item = await get_queue_item_for_pair(pair_id)
    if queue_item:
        return TranscriptionStatusResponse(
            book_pair_id=pair_id,
            status=PairStatus.TRANSCRIBING if queue_item.status in ("pending", "in_progress") else PairStatus.ERROR,
            progress=queue_item.progress,
            message=queue_item.message,
        )

    # Fall back to database status
    result = await db.execute(select(BookPair).where(BookPair.id == pair_id))
    pair = result.scalar_one_or_none()
    if not pair:
        raise HTTPException(status_code=404, detail="Book pair not found")

    return TranscriptionStatusResponse(
        book_pair_id=pair_id,
        status=pair.status,
        progress=1.0 if pair.status == PairStatus.SYNCED else None,
        message="Sync complete" if pair.status == PairStatus.SYNCED else None,
    )


@router.post("/{pair_id}/cancel")
async def cancel_transcription(
    pair_id: int,
    db: AsyncSession = Depends(get_db),
    # Editor floor (issue #207): starting a job spends the GPU for hours and
    # cancelling one discards work that may not be the canceller's.
    _: User = Depends(get_editor_user),
):
    """Cancel an active or pending transcription job.

    The pair returns to what it was before the job — SYNCED for a cancelled
    re-transcription, otherwise AUTO_MATCHED / MANUAL_MATCHED as before.
    ERROR is reserved for a job that actually failed.
    """
    # The pair used to be flipped to ERROR here (issue #381), which made a job
    # queued by mistake look like a broken pair. The "status after cancel"
    # rule lives in `queue_manager.settle_pair_after_cancel`; `cancel_item`
    # applies it, and this endpoint only reaches for it directly when the pair
    # says a job is running but no queue row backs it.
    from services.queue_manager import cancel_item, settle_pair_after_cancel

    pair_result = await db.execute(select(BookPair).where(BookPair.id == pair_id))
    pair = pair_result.scalar_one_or_none()

    if not pair:
        raise HTTPException(status_code=404, detail="Book pair not found")

    was_transcribing = pair.status == PairStatus.TRANSCRIBING

    # Find the queue item for this pair
    result = await db.execute(
        select(TranscriptionQueueItem).where(
            TranscriptionQueueItem.book_pair_id == pair_id,
            TranscriptionQueueItem.status.in_(["pending", "in_progress"]),
        )
    )
    item = result.scalar_one_or_none()

    if item:
        await cancel_item(item.id)
    elif was_transcribing:
        # No row backs the running pair. Unstick it the same way a cancel
        # would, with nothing recorded to read from.
        await settle_pair_after_cancel(db, pair_id, None)
        await db.commit()
    else:
        raise HTTPException(status_code=404, detail="No active transcription found for this pair")

    return {"status": "cancelled"}


@router.get("/queue", response_model=List[QueueItemResponse])
async def get_queue(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Get the full transcription queue with positions."""
    from services.queue_manager import get_queue as get_queue_items
    items = await get_queue_items()

    # Enrich with book titles
    responses = []
    for i, item in enumerate(items):
        pair_result = await db.execute(
            select(BookPair)
            .options(selectinload(BookPair.ebook))
            .where(BookPair.id == item.book_pair_id)
        )
        pair = pair_result.scalar_one_or_none()
        title = pair.ebook.title if pair and pair.ebook else f"Pair #{item.book_pair_id}"

        responses.append(QueueItemResponse(
            id=item.id,
            book_pair_id=item.book_pair_id,
            book_title=title,
            status=item.status,
            priority=item.priority,
            position=i + 1,
            progress=item.progress,
            message=item.message,
            error_message=item.error_message,
            retry_count=item.retry_count or 0,
            created_at=item.created_at,
            started_at=item.started_at,
            completed_at=item.completed_at,
            force_run=bool(item.force_run),
            paused_at=item.paused_at,
        ))

    return responses


@router.post("/queue/batch", response_model=List[QueueItemResponse])
async def batch_add_to_queue(
    body: QueueAddRequest,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_admin_user),
):
    """Add multiple book pairs to the transcription queue at once."""
    from services.queue_manager import add_to_queue
    created = await add_to_queue(body.pair_ids)

    responses = []
    for item in created:
        pair_result = await db.execute(
            select(BookPair)
            .options(selectinload(BookPair.ebook))
            .where(BookPair.id == item.book_pair_id)
        )
        pair = pair_result.scalar_one_or_none()
        title = pair.ebook.title if pair and pair.ebook else f"Pair #{item.book_pair_id}"

        responses.append(QueueItemResponse(
            id=item.id,
            book_pair_id=item.book_pair_id,
            book_title=title,
            status=item.status,
            priority=item.priority,
            position=0,
            progress=item.progress,
            message=item.message,
            retry_count=item.retry_count or 0,
            created_at=item.created_at,
            force_run=bool(item.force_run),
            paused_at=item.paused_at,
        ))

    return responses


@router.delete("/queue/{item_id}")
async def remove_from_queue(
    item_id: int,
    _: User = Depends(get_admin_user),
):
    """Remove a queue item (only if not in_progress)."""
    from services.queue_manager import remove_item
    removed = await remove_item(item_id)
    if not removed:
        raise HTTPException(
            status_code=400,
            detail="Cannot remove this item. It may not exist or is currently in progress (cancel it first).",
        )
    return {"status": "removed"}


@router.put("/queue/{item_id}/priority")
async def update_queue_priority(
    item_id: int,
    body: QueuePriorityUpdate,
    _: User = Depends(get_admin_user),
):
    """Update the priority of a pending queue item."""
    from services.queue_manager import update_priority
    updated = await update_priority(item_id, body.priority)
    if not updated:
        raise HTTPException(
            status_code=400,
            detail="Cannot update priority. Item may not exist or is not in pending state.",
        )
    return {"status": "updated", "priority": body.priority}


@router.post("/queue/{item_id}/run-now", response_model=QueueItemResponse)
async def run_queue_item_now(
    item_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_admin_user),
):
    """
    Dispatch a queued item immediately, ignoring the off-hours window (#106).

    The override is sticky: a job started this way also isn't paused when the
    window would otherwise have closed under it.
    """
    result = await db.execute(
        select(TranscriptionQueueItem).where(TranscriptionQueueItem.id == item_id)
    )
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="Queue item not found")
    if item.status not in ("pending", "in_progress"):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot run a {item.status} item — only queued items can be started.",
        )

    item.force_run = True
    if item.status == "pending":
        item.message = "Starting now (off-hours window bypassed)"
    await db.commit()
    await db.refresh(item)

    pair_result = await db.execute(
        select(BookPair)
        .options(selectinload(BookPair.ebook))
        .where(BookPair.id == item.book_pair_id)
    )
    return _queue_item_to_response(item, pair_result.scalar_one_or_none())


@router.post("/queue/{item_id}/requeue", response_model=QueueItemResponse)
async def requeue_queue_item(
    item_id: int,
    db: AsyncSession = Depends(get_db),
    # Editor floor, matching /start and /cancel (#207): a retry spends the GPU
    # for hours, so it sits at the same rung as starting a job for the first
    # time rather than at the admin-only queue-plumbing controls.
    _: User = Depends(get_editor_user),
):
    """
    Retry a finished-but-unsuccessful queue item (issue #247).

    Goes through `add_to_queue`, the path every other entry point uses, so the
    result is a *new* pending row. The failed row stays in History as the
    record of what went wrong — reviving it in place would erase that, and
    `reset_stale_items`/`run-now` both assume a freshly created row anyway.
    """
    result = await db.execute(
        select(TranscriptionQueueItem).where(TranscriptionQueueItem.id == item_id)
    )
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="Queue item not found")
    if item.status not in ("failed", "cancelled"):
        raise HTTPException(
            status_code=409,
            detail=f"Cannot retry a {item.status} item — only failed or cancelled jobs can be retried.",
        )

    pair_id = item.book_pair_id

    from services.queue_manager import add_to_queue, get_queue_item_for_pair
    created = await add_to_queue([pair_id])

    if created:
        new_item = created[0]
    else:
        # `add_to_queue` skips a pair that already has a pending/in_progress
        # row, which makes a double-click idempotent rather than an error.
        new_item = await get_queue_item_for_pair(pair_id)
        if not new_item:
            raise HTTPException(
                status_code=409,
                detail="Could not requeue this book — its pair no longer exists.",
            )

    pair_result = await db.execute(
        select(BookPair)
        .options(selectinload(BookPair.ebook))
        .where(BookPair.id == pair_id)
    )
    return _queue_item_to_response(new_item, pair_result.scalar_one_or_none())


@router.get("/offhours", response_model=OffHoursStatusResponse)
async def get_offhours_status(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Current off-hours window state, for the queue page's banner."""
    from services import offhours

    config = await offhours.load_config(db)
    is_open = offhours.is_open(config)
    return OffHoursStatusResponse(
        enabled=config.enabled,
        open=is_open,
        start=offhours.format_hhmm(config.start),
        end=offhours.format_hhmm(config.end),
        timezone=config.tz.key,
        opens_at=offhours.next_open(config) if config.enabled and not is_open else None,
        closes_at=offhours.next_close(config) if config.enabled and is_open else None,
    )


@router.get("/queue/history", response_model=List[QueueItemResponse])
async def get_queue_history(
    # Bounded since issue #208: this was a bare `int`, so `?limit=10000000` was
    # a valid request that loaded that many rows and then ran a per-row pair
    # lookup on each of them. 200 matches the other capped read endpoints.
    limit: int = Query(50, ge=1, le=QUEUE_HISTORY_MAX_LIMIT, description="Page size"),
    offset: int = Query(0, ge=0, description="Rows to skip"),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(rate_limited(search_reads)),
):
    """Get all historical queue items (completed, failed, cancelled), newest first."""
    result = await db.execute(
        select(TranscriptionQueueItem)
        .where(
            TranscriptionQueueItem.status.in_(["completed", "failed", "cancelled"])
        )
        .order_by(TranscriptionQueueItem.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    items = result.scalars().all()

    responses = []
    for item in items:
        pair_result = await db.execute(
            select(BookPair)
            .options(selectinload(BookPair.ebook))
            .where(BookPair.id == item.book_pair_id)
        )
        pair = pair_result.scalar_one_or_none()
        title = pair.ebook.title if pair and pair.ebook else f"Pair #{item.book_pair_id}"

        responses.append(QueueItemResponse(
            id=item.id,
            book_pair_id=item.book_pair_id,
            book_title=title,
            status=item.status,
            priority=item.priority,
            position=0,
            progress=item.progress,
            message=item.message,
            error_message=item.error_message,
            retry_count=item.retry_count or 0,
            created_at=item.created_at,
            started_at=item.started_at,
            completed_at=item.completed_at,
            force_run=bool(item.force_run),
            paused_at=item.paused_at,
        ))

    return responses


# ====================================================================
# Sync Text Editing (unchanged from before)
# ====================================================================

@router.put("/{pair_id}/text")
async def update_transcription_text(
    pair_id: int,
    update_data: SyncMapTextUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_editor_user),
):
    """
    Update transcription text for specific sync points without altering timestamps.
    Useful for correcting Whisper transcription errors from the frontend.
    """
    result = await db.execute(
        select(SyncMap).where(SyncMap.book_pair_id == pair_id)
    )
    sync_map = result.scalar_one_or_none()

    if not sync_map:
        raise HTTPException(status_code=404, detail="Sync map not found")

    point_ids = [p.id for p in update_data.points]
    if not point_ids:
        return {"status": "success", "updated": 0}

    points_result = await db.execute(
        select(SyncPoint).where(
            SyncPoint.sync_map_id == sync_map.id,
            SyncPoint.id.in_(point_ids)
        )
    )
    existing_points = {p.id: p for p in points_result.scalars().all()}

    updated_count = 0
    for update_pt in update_data.points:
        db_pt = existing_points.get(update_pt.id)
        if db_pt:
            db_pt.audio_text = update_pt.audio_text
            updated_count += 1

    await db.commit()

    return {"status": "success", "updated": updated_count}


# ====================================================================
# Re-alignment (uses cached transcript — no re-transcription needed)
# ====================================================================

@router.post("/{pair_id}/realign")
async def realign_pair(
    pair_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_editor_user),
):
    """
    Regenerate the sync map for an already-transcribed pair using the cached
    AudioTranscript. Cheap (CPU-only, no whisper). Replaces the existing
    SyncMap and bumps its version.

    The work lives in `services.realign` because the Convert flow
    (`routers/library.py`) has to do exactly the same thing after it re-points a
    pair at its converted EPUB — issue #101.
    """
    from services.realign import RealignError, realign_pair_from_cached_transcript

    try:
        result = await realign_pair_from_cached_transcript(db, pair_id)
    except RealignError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)

    await db.commit()
    return {
        "status": "ok",
        "points": result.points,
        "matched": result.matched,
        "interpolated": result.interpolated,
    }


# ====================================================================
# Startup utility
# ====================================================================

async def reset_stale_transcriptions():
    """
    On startup, find any book pairs that are stuck in 'transcribing'
    state (likely due to a server crash/restart) and reset them.

    A pair whose queue item is still live (pending or in_progress) is *not*
    stale — the queue manager requeues those itself. This matters most for a
    job paused for the off-hours window (#106), which legitimately sits in
    `transcribing` for hours: erroring it here would contradict a queue item
    that is about to resume from its checkpoint.
    """
    from services.transcription import logger

    async with async_session() as db:
        live_pairs = await db.execute(
            select(TranscriptionQueueItem.book_pair_id).where(
                TranscriptionQueueItem.status.in_(["pending", "in_progress"])
            )
        )
        queued_pair_ids = set(live_pairs.scalars().all())

        result = await db.execute(
            select(BookPair).where(BookPair.status == PairStatus.TRANSCRIBING)
        )
        stale_pairs = [p for p in result.scalars().all() if p.id not in queued_pair_ids]

        if stale_pairs:
            logger.warning(
                f"Found {len(stale_pairs)} stale transcription job(s). Resetting..."
            )
            for pair in stale_pairs:
                pair.status = PairStatus.ERROR

            await db.commit()


# ====================================================================
# Helpers
# ====================================================================

def _queue_item_to_response(
    item: TranscriptionQueueItem,
    pair: BookPair = None,
) -> QueueItemResponse:
    """Convert a queue item model to a response schema."""
    title = None
    if pair and pair.ebook:
        title = pair.ebook.title

    return QueueItemResponse(
        id=item.id,
        book_pair_id=item.book_pair_id,
        book_title=title,
        status=item.status,
        priority=item.priority,
        position=item.position or 0,
        progress=item.progress,
        message=item.message,
        error_message=item.error_message,
        retry_count=item.retry_count or 0,
        created_at=item.created_at,
        started_at=item.started_at,
        completed_at=item.completed_at,
        force_run=bool(item.force_run),
        paused_at=item.paused_at,
    )
