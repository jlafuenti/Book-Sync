"""
Transcription Queue Manager

A singleton background service that processes transcription queue items
one at a time, using the configured transcription provider.

Started at application lifespan and runs as an asyncio background task.
"""

import asyncio
import logging
import datetime
import zipfile
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.orm import selectinload

from database import async_session
from models.transcription_queue import TranscriptionQueueItem
from models.book import BookPair, PairStatus

logger = logging.getLogger("queue-manager")

# Cancellation flag — set of queue item IDs that should be cancelled
_cancel_requested: set = set()

# Reference to the running background task
_queue_task: Optional[asyncio.Task] = None


async def add_to_queue(pair_ids: list[int]) -> list[TranscriptionQueueItem]:
    """
    Add one or more book pairs to the transcription queue.
    Skips pairs that are already queued.
    Returns the list of newly created queue items.
    """
    created = []
    async with async_session() as db:
        for pair_id in pair_ids:
            # Check pair exists
            result = await db.execute(
                select(BookPair).where(BookPair.id == pair_id)
            )
            pair = result.scalar_one_or_none()
            if not pair:
                logger.warning(f"Pair {pair_id} not found, skipping")
                continue

            # Check not already queued
            existing = await db.execute(
                select(TranscriptionQueueItem).where(
                    TranscriptionQueueItem.book_pair_id == pair_id,
                    TranscriptionQueueItem.status.in_(["pending", "in_progress"]),
                )
            )
            if existing.scalar_one_or_none():
                logger.info(f"Pair {pair_id} already in queue, skipping")
                continue

            item = TranscriptionQueueItem(
                book_pair_id=pair_id,
                status="pending",
                priority=100,
                progress=0.0,
                message="Waiting in queue",
            )
            db.add(item)
            created.append(item)

        await db.commit()
        # Refresh to get IDs
        for item in created:
            await db.refresh(item)

    logger.info(f"Added {len(created)} item(s) to transcription queue")
    return created


async def cancel_item(item_id: int) -> bool:
    """Request cancellation of a queue item."""
    async with async_session() as db:
        result = await db.execute(
            select(TranscriptionQueueItem).where(TranscriptionQueueItem.id == item_id)
        )
        item = result.scalar_one_or_none()
        if not item:
            return False

        if item.status == "in_progress":
            _cancel_requested.add(item_id)
            item.status = "cancelled"
            item.message = "Cancellation requested"
            await db.commit()
            return True
        elif item.status == "pending":
            item.status = "cancelled"
            item.message = "Cancelled by user"
            item.completed_at = datetime.datetime.utcnow()
            await db.commit()
            return True

    return False


async def remove_item(item_id: int) -> bool:
    """Remove a queue item (only if pending or completed/failed/cancelled)."""
    async with async_session() as db:
        result = await db.execute(
            select(TranscriptionQueueItem).where(TranscriptionQueueItem.id == item_id)
        )
        item = result.scalar_one_or_none()
        if not item:
            return False
        if item.status == "in_progress":
            return False  # Can't delete active items — cancel first

        await db.delete(item)
        await db.commit()
        return True


async def update_priority(item_id: int, priority: int) -> bool:
    """Update priority of a pending queue item."""
    async with async_session() as db:
        result = await db.execute(
            select(TranscriptionQueueItem).where(
                TranscriptionQueueItem.id == item_id,
                TranscriptionQueueItem.status == "pending",
            )
        )
        item = result.scalar_one_or_none()
        if not item:
            return False

        item.priority = priority
        await db.commit()
        return True


async def get_queue() -> list[TranscriptionQueueItem]:
    """Get all active queue items (pending + in_progress), ordered by priority then created_at."""
    async with async_session() as db:
        result = await db.execute(
            select(TranscriptionQueueItem)
            .where(TranscriptionQueueItem.status.in_(["pending", "in_progress"]))
            .order_by(
                # in_progress first, then pending
                TranscriptionQueueItem.status.desc(),
                TranscriptionQueueItem.priority.asc(),
                TranscriptionQueueItem.created_at.asc(),
            )
        )
        items = result.scalars().all()

        # Assign display positions
        for i, item in enumerate(items):
            item.position = i + 1

        return items


async def get_queue_item_for_pair(pair_id: int) -> Optional[TranscriptionQueueItem]:
    """Get the active queue item for a given book pair, if any."""
    async with async_session() as db:
        result = await db.execute(
            select(TranscriptionQueueItem).where(
                TranscriptionQueueItem.book_pair_id == pair_id,
                TranscriptionQueueItem.status.in_(["pending", "in_progress"]),
            )
        )
        return result.scalar_one_or_none()


# ---------------------------------------------------------------------------
# Background processing loop
# ---------------------------------------------------------------------------

async def _process_next_item():
    """
    Pull the next pending item and process it through the transcription pipeline.
    """
    async with async_session() as db:
        # Get the highest-priority pending item
        result = await db.execute(
            select(TranscriptionQueueItem)
            .where(TranscriptionQueueItem.status == "pending")
            .order_by(
                TranscriptionQueueItem.priority.asc(),
                TranscriptionQueueItem.created_at.asc(),
            )
            .limit(1)
        )
        item = result.scalar_one_or_none()
        if not item:
            return False  # Nothing to process

        item_id = item.id
        pair_id = item.book_pair_id

        # Mark as in_progress
        item.status = "in_progress"
        if item.started_at is None:
            item.started_at = datetime.datetime.utcnow()
        item.message = "Starting transcription..."
        # We don't overwrite progress to 0.0 either, to preserve it on restart
        if item.progress is None:
            item.progress = 0.0
        await db.commit()

    logger.info(f"Processing queue item {item_id} (pair {pair_id})")

    try:
        await _run_transcription_pipeline(item_id, pair_id)
    except Exception as e:
        # Check if the error is exactly about provider availability.
        from services.transcription_providers.base import ProviderUnavailableError
        if isinstance(e, ProviderUnavailableError):
            async with async_session() as db:
                result = await db.execute(
                    select(TranscriptionQueueItem).where(TranscriptionQueueItem.id == item_id)
                )
                item = result.scalar_one_or_none()
                if item:
                    item.retry_count = (item.retry_count or 0) + 1
                    MAX_RETRIES = 3
                    if item.retry_count >= MAX_RETRIES:
                        logger.error(
                            f"Queue item {item_id} permanently failed after {item.retry_count} retries: {e}"
                        )
                        item.status = "failed"
                        item.error_message = f"Failed after {item.retry_count} retries: {str(e)}"
                        item.message = f"Failed after {item.retry_count} retries"
                        item.completed_at = datetime.datetime.utcnow()
                        await db.commit()

                        # Also update the BookPair status
                        result2 = await db.execute(
                            select(BookPair).where(BookPair.id == pair_id)
                        )
                        pair = result2.scalar_one_or_none()
                        if pair:
                            pair.status = PairStatus.ERROR
                            await db.commit()
                    else:
                        logger.warning(
                            f"Queue item {item_id} paused (retry {item.retry_count}/{MAX_RETRIES}): "
                            f"Provider unavailable ({e}). Sleeping 30s before retry."
                        )
                        item.status = "pending"
                        item.message = f"Provider busy/offline. Retry {item.retry_count}/{MAX_RETRIES}..."
                        item.started_at = None
                        await db.commit()
            
            # Sleep a bit to prevent a tight loop querying a busy/offline server
            await asyncio.sleep(30)
        else:
            logger.error(f"Queue item {item_id} failed: {e}", exc_info=True)
            async with async_session() as db:
                result = await db.execute(
                    select(TranscriptionQueueItem).where(TranscriptionQueueItem.id == item_id)
                )
                item = result.scalar_one_or_none()
                if item:
                    item.status = "failed"
                    item.error_message = str(e)
                    item.message = f"Failed: {str(e)[:200]}"
                    item.completed_at = datetime.datetime.utcnow()
                    await db.commit()

                # Also update the BookPair status
                result = await db.execute(
                    select(BookPair).where(BookPair.id == pair_id)
                )
                pair = result.scalar_one_or_none()
                if pair:
                    pair.status = PairStatus.ERROR
                    await db.commit()

    finally:
        _cancel_requested.discard(item_id)

    return True  # We processed something


async def _update_queue_item(item_id: int, **kwargs):
    """Helper to update a queue item's fields."""
    async with async_session() as db:
        result = await db.execute(
            select(TranscriptionQueueItem).where(TranscriptionQueueItem.id == item_id)
        )
        item = result.scalar_one_or_none()
        if item:
            for key, value in kwargs.items():
                setattr(item, key, value)
            await db.commit()


async def _run_transcription_pipeline(item_id: int, pair_id: int):
    """
    The actual transcription + alignment pipeline, adapted from the old
    _run_transcription function but using the queue DB for status tracking.
    """
    import asyncio as _asyncio

    async with async_session() as db:
        # Load book pair
        result = await db.execute(
            select(BookPair)
            .options(selectinload(BookPair.ebook), selectinload(BookPair.audiobook))
            .where(BookPair.id == pair_id)
        )
        pair = result.scalar_one_or_none()
        if not pair:
            raise Exception(f"Book pair {pair_id} not found")

        # Mark pair as transcribing
        pair.status = PairStatus.TRANSCRIBING
        await db.commit()

        ebook_path = pair.ebook.file_path
        audiobook_path = pair.audiobook.file_path

    # Step 1: Transcription (or load from cache)
    # Transcript is persisted immediately after completion, linked to the audio file.
    # EPUB issues cannot cause transcript data to be lost.
    import json
    from models.transcript import AudioTranscript
    from services.transcription import TranscribedSentence as _TranscribedSentence

    async with async_session() as db:
        cached_result = await db.execute(
            select(AudioTranscript).where(AudioTranscript.pair_id == pair_id)
        )
        cached_transcript = cached_result.scalar_one_or_none()

    if cached_transcript and cached_transcript.audiobook_path == audiobook_path:
        logger.info(f"Pair {pair_id}: loading transcript from cache ({cached_transcript.sentence_count} sentences)")
        raw = json.loads(cached_transcript.sentences_json)
        whisper_sentences = [_TranscribedSentence(**s) for s in raw]
        await _update_queue_item(
            item_id,
            progress=0.50,
            message=f"Using cached transcript ({len(whisper_sentences)} sentences). Extracting EPUB text...",
        )
    else:
        await _update_queue_item(item_id, message="Selecting transcription provider...", progress=0.02)

        # Check cancellation
        if item_id in _cancel_requested:
            await _update_queue_item(item_id, status="cancelled", message="Cancelled by user",
                                      completed_at=datetime.datetime.utcnow())
            return

        from services.transcription import _format_duration
        from services.transcription_providers import get_transcription_provider

        provider = await get_transcription_provider()
        await _update_queue_item(item_id, message=f"Transcribing via {provider.name()}...")

        # Capture event loop reference for thread-safe progress updates
        _loop = asyncio.get_running_loop()

        def on_whisper_progress(fraction: float, total_duration_sec: float, message: str = None):
            """Called by transcription provider with real-time progress."""
            mapped_progress = 0.05 + (fraction * 0.45)

            if message:
                # If the provider (like Jetson) supplies a detailed message, just use it
                msg = f"{provider.name()} - {message}"
            elif total_duration_sec and total_duration_sec > 0:
                elapsed_sec = fraction * total_duration_sec
                elapsed_str = _format_duration(elapsed_sec)
                total_str = _format_duration(total_duration_sec)
                pct = int(fraction * 100)
                msg = f"Transcribing ({provider.name()}): {elapsed_str} / {total_str} ({pct}%)"
            else:
                pct = int(fraction * 100)
                msg = f"Transcribing via {provider.name()}... ({pct}%)"

            # Schedule the DB update on the event loop (thread-safe)
            _loop.call_soon_threadsafe(
                _loop.create_task,
                _update_queue_item(item_id, progress=round(mapped_progress, 3), message=msg)
            )

        whisper_sentences = await provider.transcribe(
            audiobook_path,
            progress_callback=on_whisper_progress,
        )

        # Check cancellation
        if item_id in _cancel_requested:
            await _update_queue_item(item_id, status="cancelled", message="Cancelled by user",
                                      completed_at=datetime.datetime.utcnow())
            return

        # Persist transcript immediately — before any EPUB work — so it is never lost
        sentences_data = [
            {"text": s.text, "start_ms": s.start_ms, "end_ms": s.end_ms}
            for s in whisper_sentences
        ]
        async with async_session() as db:
            if cached_transcript:
                # Stale cache (different audio file) — replace it
                stale = await db.get(AudioTranscript, cached_transcript.id)
                if stale:
                    await db.delete(stale)
            db.add(AudioTranscript(
                pair_id=pair_id,
                audiobook_path=audiobook_path,
                sentence_count=len(whisper_sentences),
                sentences_json=json.dumps(sentences_data),
            ))
            await db.commit()
        logger.info(f"Pair {pair_id}: transcript saved ({len(whisper_sentences)} sentences)")

        await _update_queue_item(
            item_id,
            progress=0.50,
            message=f"Transcription complete ({len(whisper_sentences)} sentences). Extracting EPUB text...",
        )

    # Step 2: Extract EPUB text
    from services.epub_parser import extract_epub_sentences
    from services.transcription_providers.base import TranscriptionError
    try:
        epub_sentences = await _asyncio.to_thread(extract_epub_sentences, ebook_path)
    except zipfile.BadZipFile as e:
        raise TranscriptionError(
            f"EPUB file appears corrupted (bad zip): {ebook_path}. "
            f"Please re-add the ebook file. Original error: {e}"
        ) from e

    await _update_queue_item(
        item_id,
        progress=0.60,
        message=f"EPUB extracted ({len(epub_sentences)} sentences). Aligning text to audio...",
    )

    # Check cancellation
    if item_id in _cancel_requested:
        await _update_queue_item(item_id, status="cancelled", message="Cancelled by user",
                                  completed_at=datetime.datetime.utcnow())
        return

    # Step 3: Align texts
    from services.alignment import align_texts
    sync_points_data = await _asyncio.to_thread(align_texts, epub_sentences, whisper_sentences)

    await _update_queue_item(item_id, progress=0.90, message="Saving sync map...")

    # Step 4: Save sync map
    async with async_session() as db:
        from services.sync_engine import save_sync_map
        await save_sync_map(db, pair_id, sync_points_data)

        # Update pair status
        result = await db.execute(select(BookPair).where(BookPair.id == pair_id))
        pair = result.scalar_one_or_none()
        if pair:
            pair.status = PairStatus.SYNCED
            pair.synced_at = datetime.datetime.utcnow()
        await db.commit()

    # Mark queue item complete
    await _update_queue_item(
        item_id,
        status="completed",
        progress=1.0,
        message="Sync complete!",
        completed_at=datetime.datetime.utcnow(),
    )
    logger.info(f"Queue item {item_id} (pair {pair_id}) completed successfully")


async def _queue_loop():
    """
    Main background loop: continuously checks for pending items and processes them.
    Sleeps between checks to avoid busy-waiting.
    """
    logger.info("Transcription queue manager started")

    while True:
        try:
            processed = await _process_next_item()
            if not processed:
                # No items to process — sleep before checking again
                await asyncio.sleep(5)
        except asyncio.CancelledError:
            logger.info("Queue manager shutting down")
            break
        except Exception as e:
            logger.error(f"Queue manager error: {e}", exc_info=True)
            await asyncio.sleep(10)  # Back off on errors


async def reset_stale_items():
    """
    On startup, reset any in_progress items back to pending
    (they were interrupted by a server restart).
    """
    async with async_session() as db:
        result = await db.execute(
            select(TranscriptionQueueItem).where(
                TranscriptionQueueItem.status == "in_progress"
            )
        )
        stale = result.scalars().all()
        if stale:
            logger.warning(f"Resetting {len(stale)} stale in_progress queue item(s) to pending")
            for item in stale:
                item.status = "pending"
                item.progress = 0.0
                item.message = "Requeued after server restart"
                item.started_at = None
            await db.commit()


async def start_queue_manager():
    """Start the background queue processing loop."""
    global _queue_task

    await reset_stale_items()

    _queue_task = asyncio.create_task(_queue_loop())
    logger.info("Queue manager background task created")


async def stop_queue_manager():
    """Stop the background queue processing loop."""
    global _queue_task
    if _queue_task:
        _queue_task.cancel()
        try:
            await _queue_task
        except asyncio.CancelledError:
            pass
        _queue_task = None
        logger.info("Queue manager stopped")
