"""
Import scheduler — singleton asyncio background task.

Mirrors services/queue_manager.py in shape: started at app lifespan, stopped on
shutdown. Every minute it:
  - picks any import_sources rows where auto_sync_enabled=True AND
    (last_sync_at IS NULL OR last_sync_at + cadence_hours < now()),
  - runs source.sync() for each,
  - records an ImportJob row with the outcome,
  - updates the source's last_sync_at / last_status / last_message.

Also runs the ACSM watched-folder check every minute regardless of cadence —
the inbox is meant to feel "drop-and-go" rather than "wait 24 hours".
"""

import asyncio
import datetime
import json
import logging
from dataclasses import asdict
from typing import Optional

from sqlalchemy import select

from database import async_session
from models.import_source import ImportSource, ImportJob
from services.import_sources import get_source, list_sources
from utils import utcnow

logger = logging.getLogger("import-scheduler")

_task: Optional[asyncio.Task] = None
_stop = asyncio.Event()

POLL_INTERVAL_SECONDS = 60


async def _update_progress(source_key: str, current: int, total: int, title: str) -> None:
    """Push live progress for a running sync into the ImportSource row."""
    async with async_session() as db:
        result = await db.execute(
            select(ImportSource).where(ImportSource.source_key == source_key)
        )
        src_row = result.scalar_one_or_none()
        if src_row:
            src_row.progress_current = current
            src_row.progress_total = total
            src_row.progress_title = title[:500] if title else None
            await db.commit()


async def _run_sync(source_key: str, trigger: str) -> None:
    """Run one source.sync() and record an ImportJob."""
    source = get_source(source_key)
    started = utcnow()

    async with async_session() as db:
        job = ImportJob(
            source_key=source_key, status="running", trigger=trigger, started_at=started
        )
        db.add(job)
        # Mark source as running, clear stale progress
        result = await db.execute(
            select(ImportSource).where(ImportSource.source_key == source_key)
        )
        src_row = result.scalar_one_or_none()
        if src_row:
            src_row.last_status = "running"
            src_row.progress_current = 0
            src_row.progress_total = None
            src_row.progress_title = None
        await db.commit()
        await db.refresh(job)
        job_id = job.id

    progress_fn = lambda current, total, title: _update_progress(source_key, current, total, title)

    sync_result = None
    crash_error = None
    try:
        async with async_session() as db:
            sync_result = await source.sync(db, progress=progress_fn)
            await db.commit()
    except Exception as e:
        logger.exception(f"[import-scheduler] {source_key} sync crashed: {e}")
        crash_error = str(e)

    finished = utcnow()
    async with async_session() as db:
        job_q = await db.execute(select(ImportJob).where(ImportJob.id == job_id))
        job = job_q.scalar_one()
        job.finished_at = finished
        if sync_result is None:
            job.status = "failed"
            job.error_message = crash_error
            summary_msg = crash_error or "sync crashed"
        else:
            # The run "succeeded" if it didn't crash; per-item failures are
            # recorded but don't flip the whole job to "failed" unless nothing
            # was added at all.
            had_only_errors = (
                sync_result.fatal_error is not None
                or (sync_result.items_added == 0 and sync_result.errors)
            )
            job.status = "failed" if had_only_errors else "succeeded"
            job.items_added = sync_result.items_added
            job.items_skipped = sync_result.items_skipped
            job.error_message = sync_result.fatal_error
            # Detail is now structured JSON so the UI can render it cleanly.
            job.detail = json.dumps({
                "added_titles": sync_result.added_titles,
                "errors": [asdict(e) for e in sync_result.errors],
            })
            if sync_result.fatal_error:
                summary_msg = sync_result.fatal_error
            else:
                bits = [f"{sync_result.items_added} added"]
                if sync_result.items_skipped:
                    bits.append(f"{sync_result.items_skipped} already in library")
                if sync_result.errors:
                    bits.append(f"{len(sync_result.errors)} failed")
                summary_msg = ", ".join(bits)

        result_q = await db.execute(
            select(ImportSource).where(ImportSource.source_key == source_key)
        )
        src_row = result_q.scalar_one_or_none()
        if src_row:
            src_row.last_sync_at = finished
            src_row.last_status = job.status
            src_row.last_message = summary_msg
            src_row.progress_current = None
            src_row.progress_total = None
            src_row.progress_title = None
        await db.commit()

    # If anything was added, refresh the library so the new files get DB
    # rows, then stamp those rows with the import_source / external_id so
    # future syncs can dedup via ASIN instead of fuzzy title matching.
    # Targeted scan (just the imported paths) instead of walking the whole
    # library — for a 1-book sync we shouldn't be re-checking every other
    # audiobook on disk.
    if sync_result and sync_result.items_added > 0:
        try:
            from models.book import AudioBook, EBook
            from services.library_scan import scan_files_impl
            imported_paths = [item.file_path for item in sync_result.imported_items]
            async with async_session() as db:
                if imported_paths:
                    await scan_files_impl(db, imported_paths)
                for item in sync_result.imported_items:
                    # Audible imports → AudioBook; ACSM/ebook imports → EBook.
                    Model = AudioBook if item.source_key == "audible" else EBook
                    row_q = await db.execute(
                        select(Model).where(Model.file_path == item.file_path)
                    )
                    row = row_q.scalar_one_or_none()
                    if row is None:
                        logger.warning(
                            f"[import-scheduler] post-scan: no {Model.__name__} "
                            f"found at {item.file_path}, can't stamp provenance"
                        )
                        continue
                    row.import_source = item.source_key
                    row.external_id = item.external_id
                    if item.external_id and not row.asin and item.source_key == "audible":
                        row.asin = item.external_id
                    db.add(row)
                await db.commit()
        except Exception as e:
            logger.exception(f"[import-scheduler] post-sync scan/stamp failed: {e}")


async def trigger_now(source_key: str) -> None:
    """Run a sync immediately, off the scheduler thread (used by manual triggers)."""
    asyncio.create_task(_run_sync(source_key, trigger="manual"))


async def _due_sources() -> list[str]:
    """Return source_keys whose auto-sync is due."""
    now = utcnow()
    due: list[str] = []
    async with async_session() as db:
        result = await db.execute(
            select(ImportSource).where(ImportSource.auto_sync_enabled == True)  # noqa: E712
        )
        for row in result.scalars().all():
            if row.last_status == "running":
                continue
            if row.last_sync_at is None:
                due.append(row.source_key)
            else:
                next_due = row.last_sync_at + datetime.timedelta(hours=row.cadence_hours)
                if next_due <= now:
                    due.append(row.source_key)
    return due


async def _maybe_run_watched_folders() -> None:
    """Run sources that have inbox-style intake every poll regardless of cadence."""
    # Only ACSM has a watched folder right now.
    try:
        from services.import_sources.acsm import _inbox_dir
        inbox = _inbox_dir()
        if inbox.exists() and any(inbox.iterdir()):
            await _run_sync("acsm", trigger="scheduled")
    except Exception as e:
        logger.warning(f"[import-scheduler] watched-folder check failed: {e}")


async def _run_loop() -> None:
    logger.info("[import-scheduler] started")
    while not _stop.is_set():
        try:
            await _maybe_run_watched_folders()
            for key in await _due_sources():
                await _run_sync(key, trigger="scheduled")
        except Exception as e:
            logger.exception(f"[import-scheduler] loop error: {e}")

        try:
            await asyncio.wait_for(_stop.wait(), timeout=POLL_INTERVAL_SECONDS)
        except asyncio.TimeoutError:
            pass

    logger.info("[import-scheduler] stopped")


async def reset_stale_running_sources() -> None:
    """
    Clear any import_sources row stuck at last_status='running' from a
    previous server lifetime — e.g. a crash mid-sync, or a docker restart
    while a download was in flight. Called once at lifespan startup so
    the UI doesn't show a phantom "Syncing" state forever.
    """
    async with async_session() as db:
        result = await db.execute(
            select(ImportSource).where(ImportSource.last_status == "running")
        )
        rows = result.scalars().all()
        if not rows:
            return
        for row in rows:
            row.last_status = "failed"
            row.last_message = "Previous sync was interrupted (server restart)."
            row.progress_current = None
            row.progress_total = None
            row.progress_title = None
        await db.commit()
        logger.info(f"[import-scheduler] cleared {len(rows)} stale running source(s)")

        # Also mark any matching ImportJob rows still 'running' as 'failed'.
        result = await db.execute(
            select(ImportJob).where(ImportJob.status == "running")
        )
        for j in result.scalars().all():
            j.status = "failed"
            j.error_message = j.error_message or "Interrupted by server restart"
            j.finished_at = utcnow()
        await db.commit()


async def start() -> None:
    global _task
    _stop.clear()
    await reset_stale_running_sources()
    if _task is None or _task.done():
        _task = asyncio.create_task(_run_loop())


async def stop() -> None:
    _stop.set()
    if _task is not None:
        try:
            await asyncio.wait_for(_task, timeout=5)
        except asyncio.TimeoutError:
            _task.cancel()
