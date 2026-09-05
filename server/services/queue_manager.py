"""
Transcription Queue Manager

A singleton background service that processes transcription queue items
one at a time, using the configured transcription provider.

Started at application lifespan and runs as an asyncio background task.

Single process only (issue #252)
--------------------------------
"Singleton" here means one per *deployment*, not one per process, and nothing
in this module enforces that — it is a precondition:

* ``_process_next_item`` claims work with ``SELECT ... WHERE status='pending'
  LIMIT 1`` and then sets ``status='in_progress'`` in the same session. There is
  no ``FOR UPDATE SKIP LOCKED`` and no conditional ``UPDATE ... WHERE
  status='pending'``, so two processes claim the same row and transcribe the
  same audiobook twice.
* ``_cancel_requested``, ``_pause_requested``, ``_resuming_items``,
  ``_active_provider`` and ``_active_item_id`` are module-level globals. A
  cancel only reaches the process that happens to own the job.
* ``reset_stale_items()`` runs at every startup and flips *every* ``in_progress``
  row back to ``pending``, so a process starting up re-queues another process's
  running job.

Making this multi-process safe is a redesign (atomic claim, a
``cancel_requested`` column on the queue row, a ``worker_id``/heartbeat so
recovery only touches dead workers), tracked separately. Until then
``config.check_single_process()`` refuses to boot when ``WEB_CONCURRENCY`` and
friends ask for more than one worker, and ``server/entrypoint.sh`` runs one
uvicorn process with no ``--workers``.

Off-hours window (issue #106)
-----------------------------
Work is accepted around the clock but only *dispatched* while the configured
window is open — the transcription worker's GPU is shared with a
latency-sensitive voice pipeline the rest of the day. Two tasks cooperate:

* ``_queue_loop`` claims work, and refuses to claim anything outside the window
  unless the item carries a "Run now" override (``force_run``).
* ``_offhours_watcher`` ticks independently, because ``_queue_loop`` spends
  hours inside a single ``await`` and cannot notice the window closing under a
  running job. When it does, it asks the provider to pause at its next
  checkpoint; the item goes back to ``pending`` with its progress intact and
  resumes from that checkpoint when the window reopens.
"""

import asyncio
import logging
import datetime
import re
import time
import zipfile
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from database import async_session
from models.transcription_queue import TranscriptionQueueItem
from models.book import BookPair, PairStatus
from services import offhours
from utils import utcnow

logger = logging.getLogger("queue-manager")

# ---------------------------------------------------------------------------
# Retry ladder for a missing provider (issue #242)
#
# This used to be a flat 30 s x 5 — a ~2.5-minute budget spent entirely in the
# first minutes of the outage. A routine transcription-worker reboot plus a
# `medium` model load outruns that comfortably, so a book that the worker's
# checkpoint would have let us resume was instead marked permanently `failed`.
# Capped exponential backoff turns the same five retries into a ~15-minute
# window, and both knobs are settings so an operator with a flaky worker can
# widen it without a deploy. `routers.settings.DEFAULT_SETTINGS` mirrors the
# defaults and must match.
#
# Only ProviderUnavailableError takes this path; TranscriptionError is still a
# hard failure — a corrupt file does not get better on the seventh try.
# ---------------------------------------------------------------------------
RETRY_MAX_KEY = "transcription_retry_max"
RETRY_BASE_SECONDS_KEY = "transcription_retry_base_seconds"
DEFAULT_RETRY_MAX = 5
DEFAULT_RETRY_BASE_SECONDS = 30
# Ceiling on a single sleep. The backoff blocks the queue loop (see
# _process_next_item), which is fine while the provider is down but should not
# grow without bound.
RETRY_DELAY_CAP_SECONDS = 900

# Cancellation flag — set of queue item IDs that should be cancelled
_cancel_requested: set = set()

# Pause flag — queue item IDs the off-hours watcher has asked to stop. Only
# used to keep the watcher from re-asking every tick; the pause itself is
# enacted by the provider returning early.
_pause_requested: set = set()

# Item IDs claimed with a checkpoint already waiting on the worker. Lets the
# pipeline skip work that was already done on the first pass.
_resuming_items: set = set()

# The provider running the current job, and that job's item id. Pause and
# unload requests have to reach the provider actually in use.
_active_provider = None
_active_item_id: Optional[int] = None

# Reference to the running background tasks
_queue_task: Optional[asyncio.Task] = None
_offhours_task: Optional[asyncio.Task] = None

# Strong references to in-flight progress writes (issue #244). asyncio only
# holds a weak reference to a running task, so a bare `create_task(...)` whose
# handle is discarded can be collected mid-flight — "Task was destroyed but it
# is pending" in the log, and a lost update.
_inflight_updates: set = set()

# Poll cadence when there's nothing to do and the window is open.
IDLE_POLL_SECONDS = 5
# Cadence of the off-hours watchdog, and the cap on the idle sleep so a window
# opening is noticed within a minute.
OFFHOURS_TICK_SECONDS = 60

# Set once per closed window after the worker has been told to drop its model,
# so we ask once rather than every tick. Cleared when the window reopens.
_resources_released = False


async def _add_to_queue_in(
    db: AsyncSession, pair_ids: list[int]
) -> list[TranscriptionQueueItem]:
    """Create the queue rows in [db]. Flushes; never commits."""
    created = []
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

    # Flush so the new rows have ids, and so the dedup SELECT above sees them
    # on a later call within the same transaction.
    await db.flush()
    return created


async def add_to_queue(
    pair_ids: list[int], db: Optional[AsyncSession] = None
) -> list[TranscriptionQueueItem]:
    """
    Add one or more book pairs to the transcription queue.
    Skips pairs that are already queued.
    Returns the list of newly created queue items.

    Pass [db] when the pairs are not committed yet (issue #199). The library
    endpoints create a pair and queue it in the same request: with no session
    to share, this function opened its own, and on Postgres that session cannot
    see the caller's uncommitted row — the pair was "not found" and
    auto-transcribe silently queued nothing. When [db] is given the rows are
    written into that transaction and only flushed, so the queue item commits —
    or rolls back — atomically with the pair; the caller's `get_db` decides.
    Committing early in the router instead would split pair creation from the
    response into two transactions.

    With no [db], the old behaviour stands: own session, own commit. That is
    what the background loop and the transcription/troubleshoot routers rely
    on, and their pairs are already committed.
    """
    if db is not None:
        created = await _add_to_queue_in(db, pair_ids)
        logger.info(f"Added {len(created)} item(s) to transcription queue")
        return created

    async with async_session() as own_db:
        created = await _add_to_queue_in(own_db, pair_ids)
        await own_db.commit()
        # Refresh to get IDs
        for item in created:
            await own_db.refresh(item)

    logger.info(f"Added {len(created)} item(s) to transcription queue")
    return created


async def cancel_item(item_id: int) -> bool:
    """
    Request cancellation of a queue item.

    Works regardless of the off-hours window: a job waiting for the window to
    open, or paused mid-transcription, can always be cancelled.
    """
    was_paused = False
    was_running = False
    pair_id = None

    async with async_session() as db:
        result = await db.execute(
            select(TranscriptionQueueItem).where(TranscriptionQueueItem.id == item_id)
        )
        item = result.scalar_one_or_none()
        if not item:
            return False

        pair_id = item.book_pair_id

        if item.status == "in_progress":
            _cancel_requested.add(item_id)
            was_running = True
            item.status = "cancelled"
            item.message = "Cancellation requested"
            await db.commit()
        elif item.status == "pending":
            was_paused = item.paused_at is not None
            item.status = "cancelled"
            item.message = "Cancelled by user"
            item.completed_at = utcnow()
            item.paused_at = None
            await db.commit()
        else:
            return False

    # Tell the worker to stop (issue #196). Flipping the DB row used to be the
    # whole of "cancel": the worker kept the GPU for the remaining hours of the
    # book, starving the off-hours pipeline the window exists to protect.
    # `request_pause` is the stop we have — the worker halts at its next chunk
    # boundary and checkpoints — and the pipeline's TranscriptionPaused then
    # lands on the cancelled branch of `_process_next_item`, which discards
    # that checkpoint.
    #
    # Best effort by design: an unreachable worker must not turn a successful
    # cancel into an error. Its own 48h sweep collects whatever is left.
    if was_running and _active_item_id == item_id and _active_provider is not None:
        try:
            await _active_provider.request_pause()
        except Exception as e:
            logger.debug(f"Could not ask the worker to stop cancelled item {item_id}: {e}")

    # A paused item left a checkpoint — and the retained audio for it — on the
    # transcription worker. Nothing will ever resume it now, so reclaim that
    # disk rather than waiting out the worker's 48h sweep.
    if was_paused:
        await _discard_remote_checkpoint(pair_id)

    return True


async def _discard_remote_checkpoint(pair_id: int) -> None:
    """Best-effort cleanup of a cancelled job's partial progress on the worker."""
    try:
        async with async_session() as db:
            result = await db.execute(
                select(BookPair)
                .options(selectinload(BookPair.audiobook))
                .where(BookPair.id == pair_id)
            )
            pair = result.scalar_one_or_none()
            if not pair or not pair.audiobook:
                return
            audio_path = pair.audiobook.file_path

        from services.transcription_providers import get_transcription_provider

        provider = await get_transcription_provider()
        await provider.discard_checkpoint(audio_path)
    except Exception as e:
        logger.debug(f"Could not discard remote checkpoint for pair {pair_id}: {e}")


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

async def _mark_pending_items_waiting(db, reason: str) -> None:
    """
    Explain the wait on every queue item the closed window is holding back.

    Only writes rows whose message actually changes — this runs on every idle
    tick, and a no-op UPDATE per item per minute is pure noise.
    """
    result = await db.execute(
        select(TranscriptionQueueItem).where(
            TranscriptionQueueItem.status == "pending",
            TranscriptionQueueItem.force_run.is_(False),
        )
    )
    changed = False
    for item in result.scalars().all():
        if item.message != reason:
            item.message = reason
            changed = True
    if changed:
        await db.commit()


# ---------------------------------------------------------------------------
# Progress-write throttling (issue #244)
# ---------------------------------------------------------------------------

# Progress must move at least this much to earn a write of its own...
PROGRESS_MIN_DELTA = 0.005
# ...and a write happens at least this often regardless, so the row stays
# visibly alive and any message change lands promptly.
PROGRESS_MAX_INTERVAL_SEC = 5.0

# Digits are stripped before comparing two progress messages. The remote
# provider's message is "Transcribing: 1:02:03 / 20:00:00 (5%)", which changes
# on literally every 2 Hz poll — comparing it verbatim would force a write
# every time and the throttle would do nothing. What we actually want to catch
# is a change of *kind*: "Loading model...", "Paused at 1:02:03",
# "Waiting: 'other.m4b' in progress...". Those survive the substitution.
_DIGITS_RE = re.compile(r"\d+")


def _message_shape(message: Optional[str]) -> Optional[str]:
    """The part of a progress message that isn't just the numbers moving."""
    if message is None:
        return None
    return _DIGITS_RE.sub("#", message)


class ProgressThrottle:
    """Decides whether a progress update is worth an UPDATE + commit.

    Remote polling calls the progress callback twice a second for the whole
    run, and each call used to open its own session and commit a row — roughly
    144 000 writes for a 20-hour book, for a value the UI reads every few
    seconds at most.
    """

    def __init__(
        self,
        min_delta: float = PROGRESS_MIN_DELTA,
        max_interval: float = PROGRESS_MAX_INTERVAL_SEC,
    ):
        self.min_delta = min_delta
        self.max_interval = max_interval
        self._last_progress: Optional[float] = None
        self._last_shape: Optional[str] = None
        self._last_write: Optional[float] = None

    def should_write(self, progress: float, message: Optional[str], now: float) -> bool:
        """True when this update should be persisted. Records the decision, so
        a True return also counts as "written" — call it once per update."""
        shape = _message_shape(message)
        if (
            self._last_write is None
            or shape != self._last_shape
            or abs(progress - self._last_progress) >= self.min_delta
            or (now - self._last_write) >= self.max_interval
        ):
            self._last_progress = progress
            self._last_shape = shape
            self._last_write = now
            return True
        return False


def _spawn_tracked(coro) -> asyncio.Task:
    """Schedule `coro` on the running loop, holding a strong reference to the
    task until it finishes. Must be called from the loop thread."""
    task = asyncio.ensure_future(coro)
    _inflight_updates.add(task)
    task.add_done_callback(_inflight_updates.discard)
    return task


def _make_progress_callback(
    item_id: int,
    provider_name: str,
    loop: asyncio.AbstractEventLoop,
    throttle: Optional[ProgressThrottle] = None,
    now=None,
):
    """Build the `progress_callback` handed to a transcription provider.

    Providers call this from a worker thread, so the DB write is bounced onto
    the event loop; the throttle decides whether there is a write at all.
    """
    from services.transcription import _format_duration

    throttle = throttle or ProgressThrottle()
    now = now or time.monotonic

    def on_whisper_progress(fraction: float, total_duration_sec: float, message: str = None):
        """Called by the transcription provider with real-time progress."""
        mapped_progress = 0.05 + (fraction * 0.45)

        if message:
            # If the provider (like Jetson) supplies a detailed message, just use it
            msg = f"{provider_name} - {message}"
        elif total_duration_sec and total_duration_sec > 0:
            elapsed_sec = fraction * total_duration_sec
            elapsed_str = _format_duration(elapsed_sec)
            total_str = _format_duration(total_duration_sec)
            pct = int(fraction * 100)
            msg = f"Transcribing ({provider_name}): {elapsed_str} / {total_str} ({pct}%)"
        else:
            pct = int(fraction * 100)
            msg = f"Transcribing via {provider_name}... ({pct}%)"

        if not throttle.should_write(mapped_progress, msg, now()):
            return

        # Schedule the DB update on the event loop (thread-safe)
        loop.call_soon_threadsafe(
            _spawn_tracked,
            _update_queue_item(item_id, progress=round(mapped_progress, 3), message=msg),
        )

    return on_whisper_progress


def retry_delay_seconds(
    retry_count: int,
    base_seconds: int = DEFAULT_RETRY_BASE_SECONDS,
    cap_seconds: int = RETRY_DELAY_CAP_SECONDS,
) -> int:
    """Seconds to wait before the ``retry_count``-th retry (1-based).

    Capped exponential backoff: base, 2x base, 4x base, ... up to
    ``cap_seconds``.
    """
    exponent = max(retry_count, 1) - 1
    return min(base_seconds * (2 ** exponent), cap_seconds)


async def load_retry_policy() -> tuple:
    """Read ``(max_retries, base_delay_seconds)`` from ``system_settings``.

    Never raises and never returns a nonsense value: a typo in a settings row
    degrades to the defaults rather than wedging the queue loop, the same
    contract :func:`services.offhours.load_config` keeps.
    """
    values = {}
    try:
        from models.settings import SystemSetting

        async with async_session() as db:
            result = await db.execute(
                select(SystemSetting).where(
                    SystemSetting.key.in_((RETRY_MAX_KEY, RETRY_BASE_SECONDS_KEY))
                )
            )
            values = {s.key: s.value for s in result.scalars().all()}
    except Exception as e:  # pragma: no cover — DB down; the caller must keep going
        logger.warning(f"Could not load retry settings ({e}) — using defaults")

    def _positive_int(key: str, default: int) -> int:
        raw = values.get(key, default)
        try:
            parsed = int(raw)
        except (TypeError, ValueError):
            logger.warning(f"Setting {key}={raw!r} is not a number — using {default}")
            return default
        if parsed < 1:
            logger.warning(f"Setting {key}={raw!r} must be >= 1 — using {default}")
            return default
        return parsed

    return (
        _positive_int(RETRY_MAX_KEY, DEFAULT_RETRY_MAX),
        _positive_int(RETRY_BASE_SECONDS_KEY, DEFAULT_RETRY_BASE_SECONDS),
    )


async def _process_next_item():
    """
    Pull the next pending item and process it through the transcription pipeline.
    """
    global _active_item_id, _active_provider

    config = await offhours.load_config()
    window_open, wait_reason = offhours.dispatch_allowed(config)

    async with async_session() as db:
        # Get the highest-priority pending item. Items holding a checkpoint
        # (paused_at set) sort first so a half-transcribed book finishes before
        # a fresh one starts — its partial work is sitting on the worker and is
        # what the next window should be spent on.
        stmt = select(TranscriptionQueueItem).where(
            TranscriptionQueueItem.status == "pending"
        )
        if not window_open:
            # Outside the window only "Run now" items are eligible.
            stmt = stmt.where(TranscriptionQueueItem.force_run.is_(True))

        result = await db.execute(
            stmt.order_by(
                TranscriptionQueueItem.paused_at.is_(None).asc(),
                TranscriptionQueueItem.priority.asc(),
                TranscriptionQueueItem.created_at.asc(),
            ).limit(1)
        )
        item = result.scalar_one_or_none()
        if not item:
            if not window_open:
                await _mark_pending_items_waiting(db, wait_reason)
            return False  # Nothing to process

        item_id = item.id
        pair_id = item.book_pair_id
        resuming = item.paused_at is not None

        # Mark as in_progress
        item.status = "in_progress"
        item.paused_at = None
        if item.started_at is None:
            item.started_at = utcnow()
        item.message = "Resuming transcription..." if resuming else "Starting transcription..."
        # We don't overwrite progress to 0.0 either, to preserve it on restart
        if item.progress is None:
            item.progress = 0.0
        await db.commit()

    logger.info(
        f"{'Resuming' if resuming else 'Processing'} queue item {item_id} (pair {pair_id})"
    )
    _active_item_id = item_id
    if resuming:
        _resuming_items.add(item_id)

    try:
        await _run_transcription_pipeline(item_id, pair_id)
    except Exception as e:
        # Check if the error is exactly about provider availability.
        from services.transcription_providers.base import (
            ProviderUnavailableError,
            TranscriptionPaused,
        )
        if item_id in _cancel_requested:
            # The user cancelled, and this exception is the job unwinding —
            # most often the pause we asked the worker for (issue #196).
            # Whatever it is, a cancelled item must not be re-pended by
            # `_mark_item_paused`, re-queued by the retry ladder, or flipped to
            # `failed` with the pair in ERROR. It stays cancelled.
            logger.info(f"Queue item {item_id} was cancelled; unwound with: {e}")
            await _finalize_cancelled(item_id, pair_id)
        elif isinstance(e, TranscriptionPaused):
            # Not a failure — the provider stopped at a checkpoint because the
            # window closed. Re-pend with progress intact and no retry burned.
            await _mark_item_paused(item_id, e, config)
        elif isinstance(e, ProviderUnavailableError):
            max_retries, base_delay = await load_retry_policy()
            # Base delay unless we re-pend, in which case it becomes the
            # backoff for this attempt. A permanently-failed item has nothing
            # to back off for; the short sleep only stops the loop spinning
            # straight onto the next item and the same dead provider.
            delay = base_delay
            async with async_session() as db:
                result = await db.execute(
                    select(TranscriptionQueueItem).where(TranscriptionQueueItem.id == item_id)
                )
                item = result.scalar_one_or_none()
                if item:
                    item.retry_count = (item.retry_count or 0) + 1
                    MAX_RETRIES = max_retries
                    if item.retry_count >= MAX_RETRIES:
                        logger.error(
                            f"Queue item {item_id} permanently failed after {item.retry_count} retries: {e}"
                        )
                        item.status = "failed"
                        item.error_message = f"Failed after {item.retry_count} retries: {str(e)}"
                        item.message = f"Failed after {item.retry_count} retries"
                        item.completed_at = utcnow()
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
                        delay = retry_delay_seconds(
                            item.retry_count, base_seconds=base_delay
                        )
                        logger.warning(
                            f"Queue item {item_id} paused (retry {item.retry_count}/{MAX_RETRIES}): "
                            f"Provider unavailable ({e}). Sleeping {delay}s before retry."
                        )
                        item.status = "pending"
                        item.message = f"Provider busy/offline. Retry {item.retry_count}/{MAX_RETRIES}..."
                        item.started_at = None
                        await db.commit()

            # Sleep a bit to prevent a tight loop querying a busy/offline server
            await asyncio.sleep(delay)
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
                    item.completed_at = utcnow()
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
        _pause_requested.discard(item_id)
        _resuming_items.discard(item_id)
        _active_item_id = None
        _active_provider = None

    return True  # We processed something


async def _finalize_cancelled(item_id: int, pair_id: int) -> None:
    """Settle a cancelled item and reclaim what it left on the worker (#196).

    Called when a job the user cancelled unwinds by raising — usually the pause
    the cancel asked for. Clears ``paused_at`` so nothing treats the row as
    resumable, and drops the worker's checkpoint plus the audio parked beside
    it, which nothing will ever resume now.
    """
    async with async_session() as db:
        result = await db.execute(
            select(TranscriptionQueueItem).where(TranscriptionQueueItem.id == item_id)
        )
        item = result.scalar_one_or_none()
        if item:
            item.status = "cancelled"
            item.message = "Cancelled by user"
            item.paused_at = None
            item.completed_at = utcnow()
            await db.commit()

    await _discard_remote_checkpoint(pair_id)


async def _mark_item_paused(item_id: int, exc, config) -> None:
    """
    Re-pend a job the provider stopped at a checkpoint.

    Deliberately leaves ``progress``, ``started_at`` and ``retry_count`` alone:
    the work is banked on the worker, this isn't a restart, and a pause must
    never eat into the provider-unavailable retry budget.
    """
    opens = offhours.format_hhmm(config.start) if config.enabled else "the next window"
    async with async_session() as db:
        result = await db.execute(
            select(TranscriptionQueueItem).where(TranscriptionQueueItem.id == item_id)
        )
        item = result.scalar_one_or_none()
        if item:
            item.status = "pending"
            item.paused_at = utcnow()
            item.message = f"Paused for off-hours — resumes at {opens}"
            await db.commit()

    logger.info(
        f"Queue item {item_id} paused at {exc.completed_through_sec}s of audio; "
        f"will resume from the worker's checkpoint at {opens}"
    )

    # The window is closed and nothing else will start, so let the worker drop
    # its model weights now rather than waiting out its idle timer.
    if _active_provider is not None:
        try:
            await _active_provider.release_resources()
        except Exception as e:  # pragma: no cover — best-effort housekeeping
            logger.debug(f"Could not release provider resources after pause: {e}")


async def pause_active_job() -> bool:
    """
    Ask the running job's provider to stop at its next safe point.

    Returns False when there's nothing running, when the provider has already
    been asked, or when the backend can't pause at all (local Whisper) — in
    that last case the job simply runs to completion, which is fine because it
    isn't competing for the remote worker's GPU.
    """
    item_id = _active_item_id
    provider = _active_provider
    if item_id is None or provider is None:
        return False
    if item_id in _pause_requested:
        return True  # already asked; don't nag the worker every tick

    if not await provider.request_pause():
        logger.info(
            f"Provider {provider.name()} cannot pause — letting queue item "
            f"{item_id} run to completion despite the closed window."
        )
        _pause_requested.add(item_id)  # don't retry every tick either
        return False

    _pause_requested.add(item_id)
    logger.info(f"Requested pause of queue item {item_id} (off-hours window closed)")
    await _update_queue_item(
        item_id, message="Off-hours window closed — pausing at the next checkpoint..."
    )
    return True


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


async def _run_integrity_gates(
    item_id: int, audiobook_path: str, ebook_path: str, resuming: bool
) -> None:
    """
    Validate the source audio and ebook are readable before doing any work.

    A corrupt/incomplete import can never be transcribed, so fail fast with a
    clear, non-retriable error rather than uploading ~GBs to the remote worker
    and burning retries on it. The ebook is checked here too because extraction
    otherwise happens only *after* transcription, so a DRM-encrypted ebook
    would waste a multi-hour job before failing.

    Skipped when resuming a paused job: the very same bytes passed these gates
    when the job first started, and the audio check decodes the whole file —
    minutes of CPU on a multi-GB audiobook, every time the window reopens.
    """
    import asyncio as _asyncio

    from services.audio_integrity import check_audio_integrity, is_unverified
    from services.ebook_integrity import check_ebook_integrity
    from services.transcription_providers.base import TranscriptionError

    if resuming:
        logger.info(
            f"Queue item {item_id}: resuming — skipping integrity gates "
            f"(already passed before the pause)"
        )
        return

    await _update_queue_item(item_id, progress=0.01, message="Checking audio integrity...")
    ok, detail = await _asyncio.to_thread(check_audio_integrity, audiobook_path)
    if not ok:
        raise TranscriptionError(
            f"Audio failed integrity check — corrupt or incomplete source file, "
            f"re-import required. {audiobook_path}: {detail}"
        )

    await _update_queue_item(item_id, progress=0.015, message="Checking ebook integrity...")
    ok_e, detail_e = await _asyncio.to_thread(check_ebook_integrity, ebook_path)
    if not ok_e:
        raise TranscriptionError(
            f"Ebook failed integrity check — {detail_e}. {ebook_path}"
        )

    # The audio gate can pass without having checked anything (no ffmpeg, or a
    # decode that outran its timeout — issue #245). That is not a verdict of
    # health, so say so rather than letting it read as a clean gate. Written
    # last so the note survives both gate steps; a truly corrupt file still
    # fails at the worker's own decode.
    if is_unverified(detail):
        logger.warning(
            f"Queue item {item_id}: audio integrity unverified — {detail} "
            f"({audiobook_path})"
        )
        await _update_queue_item(
            item_id, message=f"Audio integrity unverified: {detail}"
        )


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

    # Step 0: integrity gates (skipped when resuming — see the helper).
    await _run_integrity_gates(
        item_id, audiobook_path, ebook_path, resuming=item_id in _resuming_items
    )

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
                                      completed_at=utcnow())
            return

        from services.transcription_providers import get_transcription_provider

        provider = await get_transcription_provider()
        # Publish the provider so the off-hours watcher can reach *this* one
        # with a pause/unload request while the job is running.
        global _active_provider
        _active_provider = provider
        await _update_queue_item(item_id, message=f"Transcribing via {provider.name()}...")

        # Capture event loop reference for thread-safe progress updates
        _loop = asyncio.get_running_loop()

        on_whisper_progress = _make_progress_callback(
            item_id, provider.name(), _loop
        )

        whisper_sentences = await provider.transcribe(
            audiobook_path,
            progress_callback=on_whisper_progress,
        )

        # An empty result is a failure, not a transcript (issue #194). Checked
        # here, *before* the persist below, because that persist overwrites a
        # good cached transcript in place — so accepting `[]` would turn a
        # silence-only pass, or a worker answering `{"sentences": []}`, into a
        # loss that only a full re-transcription could undo.
        if not whisper_sentences:
            from services.transcription_providers.base import (
                TranscriptionError as _TranscriptionError,
            )
            raise _TranscriptionError(
                "Transcription produced no sentences — check that the audio "
                "file actually contains speech."
            )

        # Persist the transcript before anything else — including before the
        # cancellation check below (issue #196). The worker has already spent
        # the hours; cancelling the *sync* must not throw away the
        # *transcription*, or a re-queue starts the whole book again.
        sentences_data = [
            {"text": s.text, "start_ms": s.start_ms, "end_ms": s.end_ms}
            for s in whisper_sentences
        ]
        async with async_session() as db:
            # Stale cache (the audio file changed) — update the existing row in
            # place rather than delete-then-add (issue #193).
            #
            # `audio_transcripts.pair_id` is UNIQUE, and SQLAlchemy's unit of
            # work emits every INSERT for a mapper before any DELETE for it. So
            # `db.delete(stale)` followed by `db.add(new)` in one flush sent the
            # INSERT first, hit the unique index, and raised IntegrityError; the
            # session rolled back, so the new transcript was never saved, the
            # item was marked `failed` and the pair `ERROR`. Re-queueing took
            # the identical path — the stale row with the old path was still
            # there — so the pair stayed wedged until someone deleted the row by
            # hand. An UPDATE has no ordering hazard at all, and it also keeps
            # the primary key (and any future FK to it) stable.
            existing = (
                await db.get(AudioTranscript, cached_transcript.id)
                if cached_transcript
                else None
            )
            if existing is not None:
                existing.audiobook_path = audiobook_path
                existing.sentence_count = len(whisper_sentences)
                existing.sentences_json = json.dumps(sentences_data)
                # The column records when this transcription was produced, not
                # when the pair was first transcribed.
                existing.created_at = utcnow()
            else:
                db.add(AudioTranscript(
                    pair_id=pair_id,
                    audiobook_path=audiobook_path,
                    sentence_count=len(whisper_sentences),
                    sentences_json=json.dumps(sentences_data),
                ))
            await db.commit()
        logger.info(f"Pair {pair_id}: transcript saved ({len(whisper_sentences)} sentences)")

        # Check cancellation — now that the transcript is banked.
        if item_id in _cancel_requested:
            await _update_queue_item(item_id, status="cancelled", message="Cancelled by user",
                                      completed_at=utcnow())
            return

        await _update_queue_item(
            item_id,
            progress=0.50,
            message=f"Transcription complete ({len(whisper_sentences)} sentences). Extracting ebook text...",
        )

    # Step 2: Extract ebook text
    from services.epub_parser import extract_book_sentences
    from services.transcription_providers.base import TranscriptionError
    try:
        epub_sentences = await _asyncio.to_thread(extract_book_sentences, ebook_path)
    except zipfile.BadZipFile as e:
        raise TranscriptionError(
            f"EPUB file appears corrupted (bad zip): {ebook_path}. "
            f"Please re-add the ebook file. Original error: {e}"
        ) from e

    await _update_queue_item(
        item_id,
        progress=0.60,
        message=f"Ebook extracted ({len(epub_sentences)} sentences). Aligning text to audio...",
    )

    # Check cancellation
    if item_id in _cancel_requested:
        await _update_queue_item(item_id, status="cancelled", message="Cancelled by user",
                                  completed_at=utcnow())
        return

    # Step 3: Align texts
    from services.alignment import align_texts
    sync_points_data = await _asyncio.to_thread(align_texts, epub_sentences, whisper_sentences)

    # Zero points is not a sync (issue #194). `save_sync_map` deletes the
    # existing map before inserting the new one, so letting an empty result
    # through would replace a working map with nothing while the pair went on
    # reporting SYNCED and the queue said "Sync complete!". Failing here leaves
    # the old map, its version and every bookmark exactly as they were
    # (docs/position-sync-contract.md, "Re-transcription") and puts a reason in
    # front of the user instead. The manual re-align path has always guarded
    # this (`services/realign.py`); the main pipeline did not.
    if not sync_points_data:
        raise TranscriptionError(
            "Alignment produced no points (empty transcript or ebook text) — "
            "the existing sync map, if any, was left untouched."
        )

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
            pair.synced_at = utcnow()
        await db.commit()

    # Mark queue item complete
    await _update_queue_item(
        item_id,
        status="completed",
        progress=1.0,
        message="Sync complete!",
        completed_at=utcnow(),
    )
    logger.info(f"Queue item {item_id} (pair {pair_id}) completed successfully")


async def _idle_sleep_seconds() -> float:
    """
    How long to wait before looking for work again.

    Outside the off-hours window there is nothing to find, so back off to the
    watchdog cadence instead of hammering the DB every 5s for hours — but never
    sleep past the window opening.
    """
    config = await offhours.load_config()
    wait = offhours.seconds_until_open(config)
    if wait <= 0:
        return IDLE_POLL_SECONDS
    return min(OFFHOURS_TICK_SECONDS, wait)


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
                await asyncio.sleep(await _idle_sleep_seconds())
        except asyncio.CancelledError:
            logger.info("Queue manager shutting down")
            break
        except Exception as e:
            logger.error(f"Queue manager error: {e}", exc_info=True)
            await asyncio.sleep(10)  # Back off on errors


async def _offhours_tick() -> None:
    """
    One pass of the off-hours watchdog.

    ``_queue_loop`` can sit inside a single multi-hour ``await``, so it is
    structurally unable to notice the window closing under a running job. This
    runs on its own task to do exactly that.
    """
    global _resources_released

    config = await offhours.load_config()
    if not config.enabled or offhours.is_open(config):
        _resources_released = False
        return

    if _active_item_id is not None:
        # A "Run now" item is exempt: the user asked for it explicitly, so it
        # keeps running until it finishes.
        if await _item_is_forced(_active_item_id):
            return
        await pause_active_job()
        return

    # Nothing running and the window is shut — have the worker drop its model
    # so it isn't holding GPU memory hostage all day. Once per closed window.
    if not _resources_released:
        _resources_released = True
        await _release_worker_resources()


async def _item_is_forced(item_id: int) -> bool:
    async with async_session() as db:
        result = await db.execute(
            select(TranscriptionQueueItem.force_run).where(
                TranscriptionQueueItem.id == item_id
            )
        )
        return bool(result.scalar_one_or_none())


async def _release_worker_resources() -> None:
    """Ask the configured provider to free its model weights. Best effort."""
    try:
        from services.transcription_providers import get_transcription_provider

        provider = await get_transcription_provider()
        await provider.release_resources()
        logger.info("Off-hours window closed — asked the transcription worker to unload")
    except Exception as e:
        logger.debug(f"Could not release transcription worker resources: {e}")


async def _offhours_watcher():  # pragma: no cover — asyncio task plumbing
    """Background task wrapper around :func:`_offhours_tick`."""
    while True:
        try:
            await asyncio.sleep(OFFHOURS_TICK_SECONDS)
            await _offhours_tick()
        except asyncio.CancelledError:
            logger.info("Off-hours watcher shutting down")
            break
        except Exception as e:
            logger.error(f"Off-hours watcher error: {e}", exc_info=True)


async def reset_stale_items():
    """
    On startup, reset any in_progress items back to pending
    (they were interrupted by a server restart).

    ``progress`` is deliberately preserved: the transcription worker keeps its
    own on-disk checkpoint, so the next attempt resumes from where this one got
    to. Zeroing the bar would report a restart that isn't going to happen.
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
                item.message = "Requeued after server restart"
                item.started_at = None
            await db.commit()


async def start_queue_manager():
    """Start the background queue processing loop."""
    global _queue_task, _offhours_task

    await reset_stale_items()

    _queue_task = asyncio.create_task(_queue_loop())
    _offhours_task = asyncio.create_task(_offhours_watcher())
    logger.info("Queue manager background task created")


async def stop_queue_manager():
    """Stop the background queue processing loop."""
    global _queue_task, _offhours_task
    for name, task in (("queue", _queue_task), ("off-hours watcher", _offhours_task)):
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            logger.info(f"Queue manager {name} task stopped")
    _queue_task = None
    _offhours_task = None
