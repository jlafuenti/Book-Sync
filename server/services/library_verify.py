"""
Library verification scan.

A background, cancelable, single-instance scan that runs the health checks over
the whole library and reports phase/progress. The expensive integrity checks
(audio full-decode, ebook parse) are persisted to `LibraryCheckResult` and
cached by (size, mtime) so re-scans only re-check new/changed files.

Cheap checks (missing files, zero-byte, unsupported formats, failed
transcriptions, failed ACSM imports) are computed live by the troubleshoot
router and don't need the scan — but the scan still ticks a "file checks" phase
for progress feedback.

Pair-plausibility re-check (issue #693). `pair_plausibility.check_pair_plausibility`
is recorded once, at pair creation (`record_pair_plausibility`, called from
`routers/library.py`'s manual-pair endpoint and `services/auto_match.py`) —
nothing else ever re-evaluates that stored verdict, so fixing a bug in the check
itself (like #693, where a passing word count used to be overruled by the
imprecise byte check) does not clear an old false positive already sitting on a
running server. This scan is the one place already walking the whole library on
an operator's request, so its last phase re-runs the check for pairs whose
stored row currently says `ok == False`, and only those: a passing pair's row is
left untouched, because re-checking it with a freshly computed word count could
newly flag an old pair that has synced fine for months, which is a behaviour
change nobody asked for. A stored row whose pair has since been deleted is
removed in the same pass, since it would otherwise sit there forever.
"""

import asyncio
import datetime
import logging
import os
from typing import Optional

from sqlalchemy import delete as sa_delete, select

from database import async_session
from models.book import AudioBook, BookPair, EBook
from models.library_issue import LibraryCheckResult
from services.audio_integrity import check_audio_integrity
from services.ebook_integrity import check_ebook_integrity
from services.pair_plausibility import (
    CHECK_TYPE as PAIR_PLAUSIBILITY_CHECK_TYPE,
    estimate_word_count,
    record_pair_plausibility,
)
from utils import utcnow

logger = logging.getLogger("library-verify")

# ---------------------------------------------------------------------------
# Scan state (module singleton)
# ---------------------------------------------------------------------------

_PHASES = [
    "Verifying files present",
    "Checking audio integrity",
    "Checking ebook integrity",
    "Re-checking flagged pairs",
]

_state = {
    "running": False,
    "phase_index": 0,
    "phase_count": len(_PHASES),
    "phase_label": "",
    "current": 0,
    "total": 0,
    "started_at": None,
    "finished_at": None,
    "cancel_requested": False,
    "last_error": None,
}
_task: Optional[asyncio.Task] = None


def get_progress() -> dict:
    return dict(_state)


def _set(**kwargs) -> None:
    _state.update(kwargs)


async def start_scan() -> bool:
    """Kick off a scan. Returns False if one is already running."""
    global _task
    if _state["running"]:
        return False
    _set(running=True, phase_index=0, phase_label=_PHASES[0], current=0, total=0,
         started_at=utcnow().isoformat(), finished_at=None,
         cancel_requested=False, last_error=None)
    _task = asyncio.create_task(_run_scan())
    return True


def request_cancel() -> None:
    if _state["running"]:
        _set(cancel_requested=True)


def _cancelled() -> bool:
    return _state["cancel_requested"]


async def _upsert_result(item_type: str, item_id: int, check_type: str,
                         file_path: str, size: Optional[int], mtime: Optional[float],
                         ok: bool, detail: str) -> None:
    async with async_session() as db:
        row = (await db.execute(
            select(LibraryCheckResult).where(
                LibraryCheckResult.item_type == item_type,
                LibraryCheckResult.item_id == item_id,
                LibraryCheckResult.check_type == check_type,
            )
        )).scalar_one_or_none()
        if row is None:
            row = LibraryCheckResult(item_type=item_type, item_id=item_id, check_type=check_type)
            db.add(row)
        row.file_path = file_path
        row.file_size = size
        row.file_mtime = mtime
        row.ok = ok
        row.detail = detail
        row.checked_at = utcnow()
        await db.commit()


async def _cached_ok(item_type: str, item_id: int, check_type: str,
                     size: Optional[int], mtime: Optional[float]):
    """Return the cached (ok, detail) if a result exists for the same size+mtime,
    else None (meaning the check must run)."""
    if size is None or mtime is None:
        return None
    async with async_session() as db:
        row = (await db.execute(
            select(LibraryCheckResult).where(
                LibraryCheckResult.item_type == item_type,
                LibraryCheckResult.item_id == item_id,
                LibraryCheckResult.check_type == check_type,
            )
        )).scalar_one_or_none()
    if row and row.file_size == size and row.file_mtime == mtime:
        return (row.ok, row.detail)
    return None


def _stat(path: str):
    try:
        st = os.stat(path)
        return st.st_size, st.st_mtime
    except OSError:
        return None, None


async def _recheck_flagged_pairs() -> tuple[int, int]:
    """Re-run `pair_plausibility` for pairs whose stored verdict is `ok == False`.

    Passing rows are never touched — see the module docstring for why. Opens
    its own session per row and commits itself (this is background work, not a
    helper: `docs/request-transactions.md`), the same pattern `_upsert_result`
    above already uses, so a crash partway through leaves the rows already
    re-checked persisted rather than losing the whole pass.

    Returns `(cleared, orphans_removed)` — pairs that flipped from failing to
    passing, and stored rows deleted because their pair no longer exists.
    """
    async with async_session() as db:
        failing_rows = (await db.execute(
            select(LibraryCheckResult).where(
                LibraryCheckResult.item_type == "pair",
                LibraryCheckResult.check_type == PAIR_PLAUSIBILITY_CHECK_TYPE,
                LibraryCheckResult.ok == False,  # noqa: E712
            )
        )).scalars().all()
        # Snapshot the (row id, pair id) pairs now — each row below opens its
        # own session, so the ORM objects here would be stale by the time it
        # runs.
        targets = [(row.id, row.item_id) for row in failing_rows]

    _set(phase_index=3, phase_label=_PHASES[3], current=0, total=len(targets))
    cleared = 0
    orphans_removed = 0
    for i, (row_id, pair_id) in enumerate(targets, start=1):
        if _cancelled():
            break
        async with async_session() as db:
            pair = (await db.execute(
                select(BookPair).where(BookPair.id == pair_id)
            )).scalar_one_or_none()
            if pair is None:
                # The pair was deleted since this row was recorded; the row is
                # already invisible to Troubleshoot (it joins against live
                # pairs), this just stops it accumulating forever.
                await db.execute(sa_delete(LibraryCheckResult).where(
                    LibraryCheckResult.id == row_id))
                await db.commit()
                orphans_removed += 1
                _set(current=i)
                continue

            ebook = (await db.execute(
                select(EBook).where(EBook.id == pair.ebook_id)
            )).scalar_one_or_none()
            audiobook = (await db.execute(
                select(AudioBook).where(AudioBook.id == pair.audiobook_id)
            )).scalar_one_or_none()
            if ebook is None or audiobook is None:
                # One side of the pair is gone but the pair row itself
                # survives somehow; nothing to re-check it against.
                _set(current=i)
                continue

            word_count = None
            if audiobook.duration_seconds:
                # Real file I/O + CPU work; estimate_word_count always runs it
                # via asyncio.to_thread internally, never directly on this
                # event loop.
                word_count = await estimate_word_count(ebook.file_path)
            ok = await record_pair_plausibility(
                db, pair, ebook, audiobook, word_count=word_count)
            await db.commit()
            if ok:
                cleared += 1
        _set(current=i)

    return cleared, orphans_removed


async def _run_scan() -> None:
    try:
        # ── Phase 1: file presence (cheap; for progress feedback) ──
        async with async_session() as db:
            ebooks = (await db.execute(select(EBook))).scalars().all()
            audiobooks = (await db.execute(select(AudioBook))).scalars().all()
        _set(phase_index=0, phase_label=_PHASES[0], current=0, total=len(ebooks) + len(audiobooks))
        n = 0
        for item in [*ebooks, *audiobooks]:
            if _cancelled():
                return
            os.path.isfile(item.file_path or "")  # touch the fs; live check reads it later
            n += 1
            _set(current=n)

        # ── Phase 2: audio integrity (expensive; cached + persisted) ──
        _set(phase_index=1, phase_label=_PHASES[1], current=0, total=len(audiobooks))
        for i, ab in enumerate(audiobooks, start=1):
            if _cancelled():
                return
            size, mtime = _stat(ab.file_path or "")
            if size:  # only check files that exist & are non-empty
                cached = await _cached_ok("audiobook", ab.id, "audio_integrity", size, mtime)
                if cached is None:
                    ok, detail = await asyncio.to_thread(check_audio_integrity, ab.file_path)
                    await _upsert_result("audiobook", ab.id, "audio_integrity",
                                         ab.file_path, size, mtime, ok, detail)
            _set(current=i)

        # ── Phase 3: ebook integrity (expensive; cached + persisted) ──
        _set(phase_index=2, phase_label=_PHASES[2], current=0, total=len(ebooks))
        for i, eb in enumerate(ebooks, start=1):
            if _cancelled():
                return
            size, mtime = _stat(eb.file_path or "")
            if size:
                cached = await _cached_ok("ebook", eb.id, "ebook_integrity", size, mtime)
                if cached is None:
                    ok, detail = await asyncio.to_thread(check_ebook_integrity, eb.file_path)
                    await _upsert_result("ebook", eb.id, "ebook_integrity",
                                         eb.file_path, size, mtime, ok, detail)
            _set(current=i)

        # ── Phase 4: re-check previously flagged pairs (issue #693) ──
        if _cancelled():
            return
        cleared, orphans_removed = await _recheck_flagged_pairs()
        if cleared or orphans_removed:
            logger.info(
                "[verify] pair_plausibility re-check: cleared %d, removed %d orphan row(s)",
                cleared, orphans_removed,
            )

    except Exception as e:
        logger.exception(f"Library verification scan failed: {e}")
        _set(last_error=str(e))
    finally:
        _set(running=False, finished_at=utcnow().isoformat())
