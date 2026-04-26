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
import logging
from typing import Optional

from sqlalchemy import select

from database import async_session
from models.import_source import ImportSource, ImportJob
from services.import_sources import get_source, list_sources

logger = logging.getLogger("import-scheduler")

_task: Optional[asyncio.Task] = None
_stop = asyncio.Event()

POLL_INTERVAL_SECONDS = 60


async def _run_sync(source_key: str, trigger: str) -> None:
    """Run one source.sync() and record an ImportJob."""
    source = get_source(source_key)
    started = datetime.datetime.utcnow()

    async with async_session() as db:
        job = ImportJob(
            source_key=source_key, status="running", trigger=trigger, started_at=started
        )
        db.add(job)
        # Mark source as running
        result = await db.execute(
            select(ImportSource).where(ImportSource.source_key == source_key)
        )
        src_row = result.scalar_one_or_none()
        if src_row:
            src_row.last_status = "running"
        await db.commit()
        await db.refresh(job)
        job_id = job.id

    try:
        async with async_session() as db:
            result = await source.sync(db)
            await db.commit()
    except Exception as e:
        logger.exception(f"[import-scheduler] {source_key} sync crashed: {e}")
        result = None
        error = str(e)
    else:
        error = result.error

    finished = datetime.datetime.utcnow()
    async with async_session() as db:
        job_q = await db.execute(select(ImportJob).where(ImportJob.id == job_id))
        job = job_q.scalar_one()
        job.finished_at = finished
        if result is None:
            job.status = "failed"
            job.error_message = error
        else:
            job.status = "succeeded" if result.succeeded else "failed"
            job.items_added = result.items_added
            job.items_skipped = result.items_skipped
            job.detail = result.detail
            job.error_message = result.error

        result_q = await db.execute(
            select(ImportSource).where(ImportSource.source_key == source_key)
        )
        src_row = result_q.scalar_one_or_none()
        if src_row:
            src_row.last_sync_at = finished
            src_row.last_status = job.status
            src_row.last_message = (
                job.error_message
                if job.status == "failed"
                else f"{job.items_added} added, {job.items_skipped} skipped"
            )
        await db.commit()

    # If anything was added, refresh the library so the new files get DB rows.
    if result and result.items_added > 0:
        try:
            from routers.library import scan_library_impl
            async with async_session() as db:
                await scan_library_impl(db)
                await db.commit()
        except Exception as e:
            logger.exception(f"[import-scheduler] post-sync scan failed: {e}")


async def trigger_now(source_key: str) -> None:
    """Run a sync immediately, off the scheduler thread (used by manual triggers)."""
    asyncio.create_task(_run_sync(source_key, trigger="manual"))


async def _due_sources() -> list[str]:
    """Return source_keys whose auto-sync is due."""
    now = datetime.datetime.utcnow()
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


async def start() -> None:
    global _task
    _stop.clear()
    if _task is None or _task.done():
        _task = asyncio.create_task(_run_loop())


async def stop() -> None:
    _stop.set()
    if _task is not None:
        try:
            await asyncio.wait_for(_task, timeout=5)
        except asyncio.TimeoutError:
            _task.cancel()
