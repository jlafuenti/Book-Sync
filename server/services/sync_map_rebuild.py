"""
Bulk rebuild of outdated sync maps (issue #774).

`sync_maps.splitter_version` records which version of the EPUB sentence splitter
(`epub_parser.SENTENCE_SPLITTER_VERSION`) built a map. When the split changes in
a way that shifts `sentence_index`, every existing map keeps naming the old
coordinates until it is realigned from its cached transcript. This module walks
the outdated maps and does that, one pair at a time, through the same function
the per-pair "realign" button uses
(`services.realign.realign_pair_from_cached_transcript`) — so bookmarks are
re-mapped onto the new coordinates exactly as they are for a manual realign
(`docs/position-sync-contract.md`, "Re-transcription").

Nothing here runs by itself: not on deploy, not on startup. An admin starts it
(`POST /api/troubleshoot/sync-map-rebuild`), usually with `dry_run` first.

**Single process, by design** (CLAUDE.md, issue #252): the job's state lives in
module globals, exactly like the queue manager's cancel/pause flags, and a
restart loses a running job. That is acceptable because the marker is the
source of truth for what is left — after a restart the next run simply picks up
the maps still below the current version.

**Background-work rule** (docs/request-transactions.md): the runner opens its own
`async_session()` per pair and commits (or rolls back) itself, so a failure on
one pair cannot touch another and a crash loses at most the pair in flight.
"""

import asyncio
import logging
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import List, Optional, Tuple

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from database import async_session
from models.book import BookPair, PairStatus
from models.sync_map import SyncMap
from models.transcription_queue import TranscriptionQueueItem
from services.ebook_integrity import format_is_alignable
from services.epub_parser import SENTENCE_SPLITTER_VERSION
from services.realign import (
    NoCachedTranscript,
    RealignError,
    TranscriptRejected,
    realign_pair_from_cached_transcript,
)
from utils import utcnow

logger = logging.getLogger(__name__)

#: Breather between pairs so position sync, streaming and login get the event
#: loop. Realign itself runs its heavy steps in threads; this is for the rest.
PAIR_PAUSE_SECONDS = 0.5

#: Per-pair results kept for the status report; older ones fall off the front.
MAX_RESULTS = 500

#: Longest `detail` stored on a result.
_MAX_DETAIL = 200

#: A whitespace-delimited token that contains a path separator.
_PATHISH = re.compile(r"\S*[\\/]\S*")


class AlreadyRunning(RuntimeError):
    """A rebuild is in progress; only one runs at a time."""


@dataclass
class PairResult:
    pair_id: int
    #: "rebuilt", "dry_run", "failed" or "skipped".
    outcome: str
    detail: Optional[str] = None
    old_points: Optional[int] = None
    new_points: Optional[int] = None
    matched: Optional[int] = None
    old_multiline_points: Optional[int] = None
    bookmarks: Optional[int] = None
    bookmarks_remapped: Optional[int] = None


@dataclass
class _State:
    running: bool = False
    dry_run: bool = False
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    to_process: int = 0
    processed: int = 0
    succeeded: int = 0
    failed: int = 0
    skipped: int = 0
    cancelled: bool = False
    results: List[PairResult] = field(default_factory=list)
    task: Optional["asyncio.Task"] = None


_state = _State()


def _reset() -> None:
    """Forget everything. For tests; a live process never calls this."""
    global _state
    _state = _State()


def is_running() -> bool:
    return _state.running


async def wait() -> None:
    """Await the running job, if there is one."""
    task = _state.task
    if task is not None:
        await task


def cancel() -> bool:
    """Ask a running job to stop before its next pair. False when none runs."""
    if not _state.running:
        return False
    _state.cancelled = True
    return True


def status_snapshot() -> dict:
    s = _state
    return {
        "running": s.running,
        "dry_run": s.dry_run,
        "started_at": s.started_at,
        "finished_at": s.finished_at,
        "to_process": s.to_process,
        "processed": s.processed,
        "succeeded": s.succeeded,
        "failed": s.failed,
        "skipped": s.skipped,
        "cancelled": s.cancelled,
        "results": [asdict(r) for r in s.results],
    }


async def count_outdated(db: AsyncSession) -> Tuple[int, int]:
    """(outdated, total) over the sync maps of SYNCED pairs."""
    base = (
        select(func.count()).select_from(SyncMap)
        .join(BookPair, BookPair.id == SyncMap.book_pair_id)
        .where(BookPair.status == PairStatus.SYNCED)
    )
    total = (await db.execute(base)).scalar_one()
    outdated = (await db.execute(
        base.where(SyncMap.splitter_version < SENTENCE_SPLITTER_VERSION)
    )).scalar_one()
    return outdated, total


async def start(
    *,
    dry_run: bool,
    pair_ids: Optional[List[int]],
    limit: Optional[int],
) -> None:
    """Begin a background rebuild; raises `AlreadyRunning` if one is active.

    `pair_ids` names exactly the pairs to process, whatever their marker says —
    so one pair can be re-run or dry-run on purpose. Without it, the targets are
    every outdated map on a SYNCED pair, by pair id, cut to `limit` when given.
    """
    global _state
    if _state.running:
        raise AlreadyRunning("A sync map rebuild is already running")
    # Claim the slot before the first await: two requests arriving together must
    # not both pass the check above.
    state = _State(running=True, dry_run=dry_run, started_at=utcnow())
    _state = state
    try:
        if pair_ids is not None:
            targets = list(dict.fromkeys(pair_ids))
        else:
            async with async_session() as db:
                query = (
                    select(SyncMap.book_pair_id)
                    .join(BookPair, BookPair.id == SyncMap.book_pair_id)
                    .where(
                        BookPair.status == PairStatus.SYNCED,
                        SyncMap.splitter_version < SENTENCE_SPLITTER_VERSION,
                    )
                    .order_by(SyncMap.book_pair_id)
                )
                if limit is not None:
                    query = query.limit(limit)
                targets = list((await db.execute(query)).scalars().all())
    except BaseException:
        state.running = False
        state.finished_at = utcnow()
        raise
    state.to_process = len(targets)
    state.task = asyncio.create_task(_run(state, targets, dry_run))


def _clean_detail(detail: str) -> str:
    """A `RealignError` message with anything path-like removed.

    `realign` quotes the underlying OS error for an unreadable file, and that
    carries the file's path; this report is shown in the web UI and must never
    echo one.
    """
    if detail.startswith("Could not read ebook file"):
        return "Could not read ebook file"
    return _PATHISH.sub("<path>", detail)[:_MAX_DETAIL]


async def _process_one(pair_id: int, dry_run: bool) -> PairResult:
    async with async_session() as db:
        try:
            pair = (await db.execute(
                select(BookPair)
                .options(selectinload(BookPair.ebook))
                .where(BookPair.id == pair_id)
            )).scalar_one_or_none()
            if pair is None:
                return PairResult(pair_id, "skipped", "Pair no longer exists")
            if pair.status != PairStatus.SYNCED:
                return PairResult(pair_id, "skipped", "Pair is not synced")
            queued = (await db.execute(
                select(TranscriptionQueueItem.id).where(
                    TranscriptionQueueItem.book_pair_id == pair_id,
                    TranscriptionQueueItem.status.in_(["pending", "in_progress"]),
                ).limit(1)
            )).scalar_one_or_none()
            if queued is not None:
                return PairResult(pair_id, "skipped", "Pair is in the transcription queue")
            if pair.ebook is None or not format_is_alignable(pair.ebook.file_path)[0]:
                return PairResult(pair_id, "skipped", "Ebook is not an EPUB")

            result = await realign_pair_from_cached_transcript(db, pair_id)
            if dry_run:
                # The dry run does the whole job, bookmark re-map included, so
                # its counts are what a real run would produce — and then
                # throws it away.
                await db.rollback()
                outcome = "dry_run"
            else:
                # Background work owns its transaction (docs/request-transactions.md).
                await db.commit()
                outcome = "rebuilt"
            return PairResult(
                pair_id, outcome,
                old_points=result.old_points,
                new_points=result.points,
                matched=result.matched,
                old_multiline_points=result.old_multiline_points,
                bookmarks=result.bookmarks,
                bookmarks_remapped=result.bookmarks_remapped,
            )
        except NoCachedTranscript:
            await db.rollback()
            return PairResult(pair_id, "skipped", "No cached transcript")
        except TranscriptRejected:
            # Issue #794: rebuilding would only repeat the transcript's error.
            await db.rollback()
            return PairResult(pair_id, "skipped",
                              "Transcript is out of step with the audio; re-queue to re-transcribe")
        except RealignError as e:
            await db.rollback()
            return PairResult(pair_id, "failed", _clean_detail(e.detail))
        except Exception as e:
            await db.rollback()
            logger.exception("[sync-map-rebuild] pair %s failed", pair_id)
            return PairResult(pair_id, "failed", type(e).__name__)


def _record(state: _State, result: PairResult) -> None:
    state.processed += 1
    if result.outcome in ("rebuilt", "dry_run"):
        state.succeeded += 1
    elif result.outcome == "failed":
        state.failed += 1
    else:
        state.skipped += 1
    state.results.append(result)
    if len(state.results) > MAX_RESULTS:
        del state.results[: len(state.results) - MAX_RESULTS]


async def _run(state: _State, targets: List[int], dry_run: bool) -> None:
    try:
        for index, pair_id in enumerate(targets):
            if state.cancelled:
                break
            t0 = time.perf_counter()
            result = await _process_one(pair_id, dry_run)
            _record(state, result)
            logger.info(
                "[sync-map-rebuild] pair %s: %s in %.1fs",
                pair_id, result.outcome, time.perf_counter() - t0,
            )
            if index < len(targets) - 1:
                await asyncio.sleep(PAIR_PAUSE_SECONDS)
    except Exception:
        logger.exception("[sync-map-rebuild] run aborted")
    finally:
        state.running = False
        state.finished_at = utcnow()
