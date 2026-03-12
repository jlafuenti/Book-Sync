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
"""

import asyncio
from typing import Dict, List

from fastapi import APIRouter, Depends, HTTPException
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
    QueueItemResponse,
    QueueAddRequest,
    QueuePriorityUpdate,
)
from routers.auth import get_current_user, get_admin_user

router = APIRouter(prefix="/api/transcription", tags=["transcription"])


# ====================================================================
# Queue Management Endpoints
# ====================================================================

@router.post("/{pair_id}/start", response_model=QueueItemResponse)
async def start_transcription(
    pair_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
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
    _: User = Depends(get_current_user),
):
    """Cancel an active or pending transcription job."""
    # Find the queue item for this pair
    result = await db.execute(
        select(TranscriptionQueueItem).where(
            TranscriptionQueueItem.book_pair_id == pair_id,
            TranscriptionQueueItem.status.in_(["pending", "in_progress"]),
        )
    )
    item = result.scalar_one_or_none()

    if not item:
        raise HTTPException(status_code=404, detail="No active transcription found for this pair")

    from services.queue_manager import cancel_item
    await cancel_item(item.id)

    # Update BookPair status
    pair_result = await db.execute(select(BookPair).where(BookPair.id == pair_id))
    pair = pair_result.scalar_one_or_none()
    if pair and pair.status == PairStatus.TRANSCRIBING:
        pair.status = PairStatus.ERROR

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
            created_at=item.created_at,
            started_at=item.started_at,
            completed_at=item.completed_at,
        ))

    return responses


@router.post("/queue/batch", response_model=List[QueueItemResponse])
async def batch_add_to_queue(
    body: QueueAddRequest,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
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
            created_at=item.created_at,
        ))

    return responses


@router.delete("/queue/{item_id}")
async def remove_from_queue(
    item_id: int,
    _: User = Depends(get_current_user),
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
    _: User = Depends(get_current_user),
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


# ====================================================================
# Sync Text Editing (unchanged from before)
# ====================================================================

@router.put("/{pair_id}/text")
async def update_transcription_text(
    pair_id: int,
    update_data: SyncMapTextUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
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
# Startup utility
# ====================================================================

async def reset_stale_transcriptions():
    """
    On startup, find any book pairs that are stuck in 'transcribing'
    state (likely due to a server crash/restart) and reset them.
    """
    from services.transcription import logger

    async with async_session() as db:
        result = await db.execute(
            select(BookPair).where(BookPair.status == PairStatus.TRANSCRIBING)
        )
        stale_pairs = result.scalars().all()

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
        created_at=item.created_at,
        started_at=item.started_at,
        completed_at=item.completed_at,
    )
