"""
Transcription router: trigger and monitor Whisper transcription jobs.
"""

import asyncio
from typing import Dict

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from database import get_db, async_session
from models.user import User
from models.book import BookPair, PairStatus
from schemas import TranscriptionStatusResponse
from routers.auth import get_current_user, get_admin_user

router = APIRouter(prefix="/api/transcription", tags=["transcription"])

# In-memory tracking of transcription jobs
# In production, this could be moved to Redis or the database
_transcription_jobs: Dict[int, dict] = {}


@router.post("/{pair_id}/start", response_model=TranscriptionStatusResponse)
async def start_transcription(
    pair_id: int,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """
    Start the transcription and alignment pipeline for a book pair.
    This runs Whisper on the audiobook, extracts text from the EPUB,
    aligns them, and generates a SyncMap.
    """
    # Verify book pair exists and is in a valid state
    result = await db.execute(
        select(BookPair)
        .options(selectinload(BookPair.ebook), selectinload(BookPair.audiobook))
        .where(BookPair.id == pair_id)
    )
    pair = result.scalar_one_or_none()
    if not pair:
        raise HTTPException(status_code=404, detail="Book pair not found")

    if pair.status == PairStatus.TRANSCRIBING:
        raise HTTPException(status_code=409, detail="Transcription already in progress")

    if pair.status == PairStatus.SYNCED:
        raise HTTPException(
            status_code=409,
            detail="Already synced. Delete existing sync map first to re-transcribe.",
        )

    # Mark as transcribing
    pair.status = PairStatus.TRANSCRIBING
    _transcription_jobs[pair_id] = {
        "status": PairStatus.TRANSCRIBING,
        "progress": 0.0,
        "message": "Starting transcription...",
    }

    # Launch background transcription
    background_tasks.add_task(_run_transcription, pair_id)

    return TranscriptionStatusResponse(
        book_pair_id=pair_id,
        status=PairStatus.TRANSCRIBING,
        progress=0.0,
        message="Transcription started",
    )


@router.get("/{pair_id}/status", response_model=TranscriptionStatusResponse)
async def get_transcription_status(
    pair_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Get the status of a transcription job."""
    # Check in-memory job status first
    if pair_id in _transcription_jobs:
        job = _transcription_jobs[pair_id]
        return TranscriptionStatusResponse(
            book_pair_id=pair_id,
            status=job["status"],
            progress=job.get("progress"),
            message=job.get("message"),
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


async def _run_transcription(pair_id: int):
    """
    Background task: run the full transcription + alignment pipeline.

    Steps:
    1. Transcribe audiobook with Whisper → timestamped sentences
    2. Extract text from EPUB → sentence list
    3. Align the two sentence lists → SyncMap
    """
    try:
        _transcription_jobs[pair_id]["message"] = "Loading audiobook for transcription..."
        _transcription_jobs[pair_id]["progress"] = 0.02

        async with async_session() as db:
            # Load book pair with related data
            result = await db.execute(
                select(BookPair)
                .options(selectinload(BookPair.ebook), selectinload(BookPair.audiobook))
                .where(BookPair.id == pair_id)
            )
            pair = result.scalar_one_or_none()
            if not pair:
                raise Exception(f"Book pair {pair_id} not found")

            # Step 1: Transcribe audiobook with real-time progress
            _transcription_jobs[pair_id]["message"] = "Downloading & loading Whisper model (this may take a minute)..."
            _transcription_jobs[pair_id]["progress"] = 0.02

            from services.transcription import transcribe_audiobook, _format_duration

            def on_whisper_progress(fraction: float, total_duration_sec: float):
                """Called by Whisper progress tracker with real-time progress."""
                # Map whisper's 0-100% onto our 5-50% range
                mapped_progress = 0.05 + (fraction * 0.45)
                _transcription_jobs[pair_id]["progress"] = round(mapped_progress, 3)
                
                # Build a descriptive message with time info
                if total_duration_sec and total_duration_sec > 0:
                    elapsed_sec = fraction * total_duration_sec
                    elapsed_str = _format_duration(elapsed_sec)
                    total_str = _format_duration(total_duration_sec)
                    pct = int(fraction * 100)
                    _transcription_jobs[pair_id]["message"] = (
                        f"Transcribing: {elapsed_str} / {total_str} ({pct}%)"
                    )
                else:
                    pct = int(fraction * 100)
                    _transcription_jobs[pair_id]["message"] = (
                        f"Transcribing audiobook... ({pct}%)"
                    )

            whisper_sentences = await asyncio.to_thread(
                transcribe_audiobook,
                pair.audiobook.file_path,
                progress_callback=on_whisper_progress,
            )

            _transcription_jobs[pair_id]["progress"] = 0.50
            _transcription_jobs[pair_id]["message"] = (
                f"Transcription complete ({len(whisper_sentences)} sentences). "
                "Extracting EPUB text..."
            )

            # Step 2: Extract EPUB text
            from services.epub_parser import extract_epub_sentences
            epub_sentences = await asyncio.to_thread(
                extract_epub_sentences, pair.ebook.file_path
            )

            _transcription_jobs[pair_id]["progress"] = 0.6
            _transcription_jobs[pair_id]["message"] = (
                f"EPUB extracted ({len(epub_sentences)} sentences). "
                "Aligning text to audio..."
            )

            # Step 3: Align texts
            from services.alignment import align_texts
            sync_points_data = await asyncio.to_thread(
                align_texts, epub_sentences, whisper_sentences
            )

            _transcription_jobs[pair_id]["progress"] = 0.9
            _transcription_jobs[pair_id]["message"] = "Saving sync map..."

            # Step 4: Save sync map
            from services.sync_engine import save_sync_map
            await save_sync_map(db, pair_id, sync_points_data)

            # Update pair status
            from datetime import datetime
            pair.status = PairStatus.SYNCED
            pair.synced_at = datetime.utcnow()
            await db.commit()

            _transcription_jobs[pair_id]["status"] = PairStatus.SYNCED
            _transcription_jobs[pair_id]["progress"] = 1.0
            _transcription_jobs[pair_id]["message"] = "Sync complete!"

    except Exception as e:
        _transcription_jobs[pair_id]["status"] = PairStatus.ERROR
        _transcription_jobs[pair_id]["progress"] = None
        _transcription_jobs[pair_id]["message"] = f"Error: {str(e)}"

        # Update database status
        try:
            async with async_session() as db:
                result = await db.execute(
                    select(BookPair).where(BookPair.id == pair_id)
                )
                pair = result.scalar_one_or_none()
                if pair:
                    pair.status = PairStatus.ERROR
                    await db.commit()
        except Exception:
            pass  # Best effort


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
                # We could set to UNMATCHED, but ERROR is more informative to the user
                # that something went wrong.
            
            await db.commit()


@router.post("/{pair_id}/cancel")
async def cancel_transcription(
    pair_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Cancel a running transcription job.
    """
    # 1. Update in-memory job status to act as a signal (if implemented)
    # Since we can't easily kill an asyncio thread running synchronously,
    # we mostly just update the DB status so the UI reflects it.
    # A true "cancellation" of the running thread is complex without celery/rq.
    # For now, we update state so the user isn't blocked.
    
    if pair_id in _transcription_jobs:
        _transcription_jobs[pair_id]["status"] = PairStatus.ERROR
        _transcription_jobs[pair_id]["message"] = "Cancelled by user"

    # 2. Update DB
    result = await db.execute(select(BookPair).where(BookPair.id == pair_id))
    pair = result.scalar_one_or_none()
    
    if not pair:
        raise HTTPException(status_code=404, detail="Book pair not found")
        
    if pair.status == PairStatus.TRANSCRIBING:
        pair.status = PairStatus.ERROR
        await db.commit()
        
    return {"status": "cancelled"}
