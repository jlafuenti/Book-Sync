"""
Transcription queue-manager tests (issue #46, Phase 2).

The queue manager uses its own async_session() internally (bound by conftest to
the SQLite test DB), so reads-back must use a *fresh* session — reusing the
seeding session returns stale identity-map objects.

Two layers of test live here. The claiming/retry/off-hours tests replace
`_run_transcription_pipeline` wholesale, because what they are about is what
`_process_next_item` does *around* it. The pipeline tests at the bottom
(issue #254) do the opposite: they fake only the hardware and parsing boundary
— provider, EPUB extraction, alignment, integrity gates — and let the transcript
cache, the sync map, the pair status and the bookmark re-map run for real.
"""

import asyncio
import datetime
import logging

import pytest
import pytest_asyncio
from sqlalchemy import select

from database import async_session
from models.book import AudioBook, BookPair, PairStatus
from models.bookmark import Bookmark, BookmarkSource
from models.settings import SystemSetting
from models.sync_map import SyncMap, SyncPoint
from models.transcript import AudioTranscript
from models.transcription_queue import TranscriptionQueueItem
from services import offhours, queue_manager
from services.transcription_providers.base import (
    ProviderUnavailableError,
    TranscriptionPaused,
)
from tests.factories import ensure_users, make_book_pair, make_sync_map
from utils import utcnow


@pytest_asyncio.fixture(autouse=True)
async def _clear_cancel_flags():
    queue_manager._cancel_requested.clear()
    queue_manager._pause_requested.clear()
    queue_manager._resuming_items.clear()
    queue_manager._active_item_id = None
    queue_manager._active_provider = None
    queue_manager._resources_released = False
    yield
    queue_manager._cancel_requested.clear()
    queue_manager._pause_requested.clear()
    queue_manager._resuming_items.clear()
    queue_manager._active_item_id = None
    queue_manager._active_provider = None
    queue_manager._resources_released = False


# ---------------------------------------------------------------------------
# Off-hours helpers (issue #106)
# ---------------------------------------------------------------------------

async def _enable_window(db, start="01:00", end="07:00", tz="UTC"):
    """Turn the off-hours window on in system_settings."""
    for key, value in {
        offhours.ENABLED_KEY: "true",
        offhours.START_KEY: start,
        offhours.END_KEY: end,
        offhours.TIMEZONE_KEY: tz,
    }.items():
        db.add(SystemSetting(key=key, value=value))
    await db.commit()


def _freeze_clock(monkeypatch, hour, minute=0):
    """Pin the wall clock the window is evaluated against (UTC)."""
    fixed = datetime.datetime(2026, 6, 1, hour, minute, tzinfo=datetime.timezone.utc)
    monkeypatch.setattr(offhours, "_now_utc", lambda: fixed)
    return fixed


class _FakeProvider:
    """Records the off-hours control calls the queue makes on a provider."""

    def __init__(self, can_pause=True):
        self.can_pause = can_pause
        self.pause_calls = 0
        self.release_calls = 0
        self.discarded = []

    def name(self):
        return "Fake"

    async def request_pause(self):
        self.pause_calls += 1
        return self.can_pause

    async def release_resources(self):
        self.release_calls += 1

    async def discard_checkpoint(self, audio_path):
        self.discarded.append(audio_path)


async def _get(model, id_):
    """Read a row through a fresh session to avoid identity-map staleness."""
    async with async_session() as s:
        return (await s.execute(select(model).where(model.id == id_))).scalar_one()


async def _seed_item(db, pair_id, **kwargs):
    item = TranscriptionQueueItem(book_pair_id=pair_id, **kwargs)
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return item


async def _noop_sleep(*args, **kwargs):
    return None


# ---------------------------------------------------------------------------
# add_to_queue
# ---------------------------------------------------------------------------

async def test_add_to_queue_creates_item(db):
    pair = await make_book_pair(db)
    created = await queue_manager.add_to_queue([pair.id])
    assert len(created) == 1
    assert created[0].status == "pending"


async def test_add_to_queue_dedups_active_pair(db):
    pair = await make_book_pair(db)
    await queue_manager.add_to_queue([pair.id])
    second = await queue_manager.add_to_queue([pair.id])
    assert second == []
    async with async_session() as s:
        rows = (await s.execute(
            select(TranscriptionQueueItem).where(TranscriptionQueueItem.book_pair_id == pair.id)
        )).scalars().all()
    assert len(rows) == 1


async def test_add_to_queue_skips_nonexistent_pair(db):
    created = await queue_manager.add_to_queue([99999])
    assert created == []


async def test_add_to_queue_without_a_session_still_commits_on_its_own(db):
    """The background/router callers pass no session and must keep committing.

    `add_to_queue` grew an optional `db` parameter (issue #199) so the library
    endpoints can queue inside their own request transaction. Callers that pass
    nothing — `routers/transcription.py`, `routers/troubleshoot.py`, the queue
    loop — operate on already-committed pairs and rely on the old behaviour:
    open a session, write, commit. Nothing else commits for them.
    """
    pair = await make_book_pair(db)

    created = await queue_manager.add_to_queue([pair.id])
    assert len(created) == 1

    # Durable without any commit from this test's session.
    async with async_session() as s:
        rows = (await s.execute(
            select(TranscriptionQueueItem)
            .where(TranscriptionQueueItem.book_pair_id == pair.id)
        )).scalars().all()
    assert len(rows) == 1
    assert rows[0].status == "pending"


# ---------------------------------------------------------------------------
# cancel / remove / priority
# ---------------------------------------------------------------------------

async def test_cancel_pending_item(db):
    pair = await make_book_pair(db)
    item = await _seed_item(db, pair.id, status="pending")
    assert await queue_manager.cancel_item(item.id) is True
    assert (await _get(TranscriptionQueueItem, item.id)).status == "cancelled"


async def test_cancel_in_progress_item_sets_flag(db):
    pair = await make_book_pair(db)
    item = await _seed_item(db, pair.id, status="in_progress")
    assert await queue_manager.cancel_item(item.id) is True
    assert item.id in queue_manager._cancel_requested
    assert (await _get(TranscriptionQueueItem, item.id)).status == "cancelled"


async def test_remove_pending_but_not_in_progress(db):
    pair = await make_book_pair(db)
    pending = await _seed_item(db, pair.id, status="pending")
    assert await queue_manager.remove_item(pending.id) is True
    # queue_manager deleted the row via its own session; drop our cached copy so
    # SQLite reusing the rowid doesn't trip an identity-map warning.
    db.expunge_all()

    pair2 = await make_book_pair(db)
    active = await _seed_item(db, pair2.id, status="in_progress")
    assert await queue_manager.remove_item(active.id) is False


async def test_update_priority_pending_only(db):
    pair = await make_book_pair(db)
    pending = await _seed_item(db, pair.id, status="pending", priority=100)
    assert await queue_manager.update_priority(pending.id, 10) is True

    pair2 = await make_book_pair(db)
    active = await _seed_item(db, pair2.id, status="in_progress", priority=100)
    assert await queue_manager.update_priority(active.id, 10) is False


# ---------------------------------------------------------------------------
# get_queue / reset_stale_items
# ---------------------------------------------------------------------------

async def test_get_queue_orders_by_priority_and_assigns_positions(db):
    base = datetime.datetime(2026, 1, 1, 12, 0, 0)
    p1 = await make_book_pair(db)
    p2 = await make_book_pair(db)
    p3 = await make_book_pair(db)
    # Lower priority number = higher priority; ties broken by created_at asc.
    await _seed_item(db, p1.id, status="pending", priority=100, created_at=base)
    await _seed_item(db, p2.id, status="pending", priority=50, created_at=base + datetime.timedelta(minutes=1))
    await _seed_item(db, p3.id, status="pending", priority=100, created_at=base + datetime.timedelta(minutes=2))

    queue = await queue_manager.get_queue()
    assert [q.book_pair_id for q in queue] == [p2.id, p1.id, p3.id]
    assert [q.position for q in queue] == [1, 2, 3]


async def test_reset_stale_items_requeues_in_progress(db):
    pair = await make_book_pair(db)
    item = await _seed_item(db, pair.id, status="in_progress", progress=0.4)
    await queue_manager.reset_stale_items()
    refreshed = await _get(TranscriptionQueueItem, item.id)
    assert refreshed.status == "pending"
    assert refreshed.started_at is None


# ---------------------------------------------------------------------------
# _process_next_item — retry / backoff / failure transitions
# ---------------------------------------------------------------------------

async def test_provider_unavailable_requeues_with_incremented_retry(db, monkeypatch):
    pair = await make_book_pair(db)
    item = await _seed_item(db, pair.id, status="pending", retry_count=0)

    async def _raise(item_id, pair_id):
        raise ProviderUnavailableError("remote offline")

    monkeypatch.setattr(queue_manager, "_run_transcription_pipeline", _raise)
    monkeypatch.setattr("asyncio.sleep", _noop_sleep)

    assert await queue_manager._process_next_item() is True

    refreshed = await _get(TranscriptionQueueItem, item.id)
    assert refreshed.status == "pending"
    assert refreshed.retry_count == 1
    assert refreshed.started_at is None


async def test_provider_unavailable_fails_at_max_retries(db, monkeypatch):
    pair = await make_book_pair(db, status=PairStatus.TRANSCRIBING)
    item = await _seed_item(db, pair.id, status="pending", retry_count=4)  # 4 -> 5 == MAX

    async def _raise(item_id, pair_id):
        raise ProviderUnavailableError("still offline")

    monkeypatch.setattr(queue_manager, "_run_transcription_pipeline", _raise)
    monkeypatch.setattr("asyncio.sleep", _noop_sleep)

    await queue_manager._process_next_item()

    refreshed = await _get(TranscriptionQueueItem, item.id)
    assert refreshed.status == "failed"
    assert refreshed.retry_count == 5
    assert (await _get(BookPair, pair.id)).status == PairStatus.ERROR


async def test_generic_error_fails_immediately(db, monkeypatch):
    pair = await make_book_pair(db)
    item = await _seed_item(db, pair.id, status="pending")

    async def _boom(item_id, pair_id):
        raise ValueError("corrupt input")

    monkeypatch.setattr(queue_manager, "_run_transcription_pipeline", _boom)

    await queue_manager._process_next_item()

    refreshed = await _get(TranscriptionQueueItem, item.id)
    assert refreshed.status == "failed"
    assert "corrupt input" in (refreshed.error_message or "")
    assert (await _get(BookPair, pair.id)).status == PairStatus.ERROR


async def test_happy_path_marks_completed(db, monkeypatch):
    pair = await make_book_pair(db)
    item = await _seed_item(db, pair.id, status="pending")

    async def _ok(item_id, pair_id):
        await queue_manager._update_queue_item(item_id, status="completed", progress=1.0)

    monkeypatch.setattr(queue_manager, "_run_transcription_pipeline", _ok)

    assert await queue_manager._process_next_item() is True

    refreshed = await _get(TranscriptionQueueItem, item.id)
    assert refreshed.status == "completed"
    assert refreshed.progress == 1.0


async def test_process_next_item_noop_when_empty(db):
    assert await queue_manager._process_next_item() is False


# ---------------------------------------------------------------------------
# Off-hours dispatch gate (issue #106)
# ---------------------------------------------------------------------------

async def _stub_pipeline(monkeypatch, ran: list):
    async def _ok(item_id, pair_id):
        ran.append(item_id)
        await queue_manager._update_queue_item(item_id, status="completed", progress=1.0)

    monkeypatch.setattr(queue_manager, "_run_transcription_pipeline", _ok)


async def test_pending_item_not_claimed_outside_window(db, monkeypatch):
    pair = await make_book_pair(db)
    item = await _seed_item(db, pair.id, status="pending")
    await _enable_window(db)
    _freeze_clock(monkeypatch, 14)  # window is 01:00–07:00

    ran = []
    await _stub_pipeline(monkeypatch, ran)

    assert await queue_manager._process_next_item() is False
    assert ran == []

    refreshed = await _get(TranscriptionQueueItem, item.id)
    assert refreshed.status == "pending"
    assert "off-hours" in refreshed.message.lower()
    assert "01:00" in refreshed.message


async def test_pending_item_claimed_inside_window(db, monkeypatch):
    pair = await make_book_pair(db)
    item = await _seed_item(db, pair.id, status="pending")
    await _enable_window(db)
    _freeze_clock(monkeypatch, 2)

    ran = []
    await _stub_pipeline(monkeypatch, ran)

    assert await queue_manager._process_next_item() is True
    assert ran == [item.id]


async def test_midnight_crossing_window_dispatches_after_midnight(db, monkeypatch):
    pair = await make_book_pair(db)
    item = await _seed_item(db, pair.id, status="pending")
    await _enable_window(db, start="22:00", end="06:00")
    _freeze_clock(monkeypatch, 1)  # 01:00 is inside 22:00–06:00

    ran = []
    await _stub_pipeline(monkeypatch, ran)

    assert await queue_manager._process_next_item() is True
    assert ran == [item.id]


async def test_disabled_window_dispatches_at_any_hour(db, monkeypatch):
    """The default. Behaviour must be identical to before #106."""
    pair = await make_book_pair(db)
    item = await _seed_item(db, pair.id, status="pending")
    _freeze_clock(monkeypatch, 14)

    ran = []
    await _stub_pipeline(monkeypatch, ran)

    assert await queue_manager._process_next_item() is True
    assert ran == [item.id]


async def test_force_run_bypasses_a_closed_window(db, monkeypatch):
    pair = await make_book_pair(db)
    item = await _seed_item(db, pair.id, status="pending", force_run=True)
    await _enable_window(db)
    _freeze_clock(monkeypatch, 14)

    ran = []
    await _stub_pipeline(monkeypatch, ran)

    assert await queue_manager._process_next_item() is True
    assert ran == [item.id]


async def test_force_run_item_is_reachable_past_blocked_ones(db, monkeypatch):
    """A forced item buried behind higher-priority waiting items must still run
    — the gate can't just look at the head of the queue."""
    base = datetime.datetime(2026, 1, 1, 12, 0, 0)
    blocked_pair = await make_book_pair(db)
    forced_pair = await make_book_pair(db)
    await _seed_item(db, blocked_pair.id, status="pending", priority=1, created_at=base)
    forced = await _seed_item(
        db, forced_pair.id, status="pending", priority=100,
        created_at=base + datetime.timedelta(minutes=1), force_run=True,
    )
    await _enable_window(db)
    _freeze_clock(monkeypatch, 14)

    ran = []
    await _stub_pipeline(monkeypatch, ran)

    assert await queue_manager._process_next_item() is True
    assert ran == [forced.id]


async def test_waiting_message_is_written_once_then_left_alone(db):
    """The gate runs every minute for hours — an unchanged message must not
    produce a write per item per tick."""
    pair = await make_book_pair(db)
    await _seed_item(db, pair.id, status="pending", message="Waiting in queue")

    commits = 0
    real_commit = db.commit

    async def _counting_commit():
        nonlocal commits
        commits += 1
        await real_commit()

    db.commit = _counting_commit
    reason = "Waiting for off-hours window (opens 01:00 UTC)"

    await queue_manager._mark_pending_items_waiting(db, reason)
    assert commits == 1

    await queue_manager._mark_pending_items_waiting(db, reason)
    assert commits == 1


async def test_waiting_message_skips_forced_items(db):
    """A "Run now" item isn't waiting for anything — don't tell the user it is."""
    pair = await make_book_pair(db)
    forced = await _seed_item(
        db, pair.id, status="pending", force_run=True, message="Waiting in queue"
    )
    await queue_manager._mark_pending_items_waiting(db, "Waiting for off-hours window")
    assert (await _get(TranscriptionQueueItem, forced.id)).message == "Waiting in queue"


async def test_provider_unavailable_retry_cannot_dispatch_outside_window(db, monkeypatch):
    """The 30s retry backoff must not become a hole in the gate: an item
    re-pended by a provider failure has to wait for the window like the rest."""
    pair = await make_book_pair(db)
    item = await _seed_item(db, pair.id, status="pending", retry_count=0)
    await _enable_window(db)
    _freeze_clock(monkeypatch, 6, 59)  # inside the window, one minute from close

    attempts = []

    async def _raise(item_id, pair_id):
        attempts.append(item_id)
        raise ProviderUnavailableError("remote offline")

    monkeypatch.setattr(queue_manager, "_run_transcription_pipeline", _raise)
    monkeypatch.setattr("asyncio.sleep", _noop_sleep)

    assert await queue_manager._process_next_item() is True
    assert attempts == [item.id]

    # The window closed while we were sleeping off the retry.
    _freeze_clock(monkeypatch, 7, 1)
    assert await queue_manager._process_next_item() is False
    assert attempts == [item.id], "retry must not re-dispatch outside the window"


# ---------------------------------------------------------------------------
# Pause / resume (issue #106)
# ---------------------------------------------------------------------------

async def test_pause_repends_item_preserving_progress_and_retries(db, monkeypatch):
    pair = await make_book_pair(db, status=PairStatus.TRANSCRIBING)
    started = datetime.datetime(2026, 6, 1, 2, 0, 0)
    item = await _seed_item(
        db, pair.id, status="pending", progress=0.31, retry_count=2, started_at=started
    )
    await _enable_window(db)
    _freeze_clock(monkeypatch, 2)

    async def _pause(item_id, pair_id):
        raise TranscriptionPaused("window closed", completed_through_sec=1800, progress=0.25)

    monkeypatch.setattr(queue_manager, "_run_transcription_pipeline", _pause)

    assert await queue_manager._process_next_item() is True

    refreshed = await _get(TranscriptionQueueItem, item.id)
    assert refreshed.status == "pending"
    assert refreshed.paused_at is not None
    assert refreshed.progress == 0.31, "banked progress must survive the pause"
    assert refreshed.retry_count == 2, "a pause must not burn a provider retry"
    assert refreshed.started_at == started
    assert "paused" in refreshed.message.lower()
    # The book is still mid-transcription, not errored.
    assert (await _get(BookPair, pair.id)).status == PairStatus.TRANSCRIBING


async def test_pause_asks_the_active_provider_to_release_its_model(db, monkeypatch):
    pair = await make_book_pair(db)
    await _seed_item(db, pair.id, status="pending")
    await _enable_window(db)
    _freeze_clock(monkeypatch, 2)

    provider = _FakeProvider()

    async def _pause(item_id, pair_id):
        queue_manager._active_provider = provider
        raise TranscriptionPaused("window closed", completed_through_sec=900, progress=0.1)

    monkeypatch.setattr(queue_manager, "_run_transcription_pipeline", _pause)
    await queue_manager._process_next_item()

    assert provider.release_calls == 1


async def test_paused_item_is_dispatched_before_a_fresh_one(db, monkeypatch):
    """Reopening the window should finish the half-done book, not start a new
    one — its partial work is banked on the worker."""
    base = datetime.datetime(2026, 1, 1, 12, 0, 0)
    fresh_pair = await make_book_pair(db)
    paused_pair = await make_book_pair(db)
    await _seed_item(db, fresh_pair.id, status="pending", priority=1, created_at=base)
    paused = await _seed_item(
        db, paused_pair.id, status="pending", priority=100,
        created_at=base + datetime.timedelta(minutes=1),
        paused_at=base + datetime.timedelta(hours=1), progress=0.4,
    )
    _freeze_clock(monkeypatch, 2)

    ran = []
    await _stub_pipeline(monkeypatch, ran)

    assert await queue_manager._process_next_item() is True
    assert ran == [paused.id]


async def test_resuming_item_clears_its_paused_marker_and_flags_the_pipeline(db, monkeypatch):
    pair = await make_book_pair(db)
    item = await _seed_item(
        db, pair.id, status="pending", progress=0.4,
        paused_at=datetime.datetime(2026, 6, 1, 7, 0, 0),
    )
    _freeze_clock(monkeypatch, 2)

    seen_resuming = []

    async def _check(item_id, pair_id):
        seen_resuming.append(item_id in queue_manager._resuming_items)
        await queue_manager._update_queue_item(item_id, status="completed")

    monkeypatch.setattr(queue_manager, "_run_transcription_pipeline", _check)

    await queue_manager._process_next_item()

    assert seen_resuming == [True]
    assert (await _get(TranscriptionQueueItem, item.id)).paused_at is None


async def test_integrity_gates_skipped_when_resuming(monkeypatch):
    """Re-decoding a multi-GB audiobook on every window reopen is pure waste —
    the same bytes already passed on the first pass."""
    def _boom(path):
        raise AssertionError(f"integrity check must not run on resume: {path}")

    monkeypatch.setattr("services.audio_integrity.check_audio_integrity", _boom)
    monkeypatch.setattr("services.ebook_integrity.check_ebook_integrity", _boom)

    await queue_manager._run_integrity_gates(1, "/audio.m4b", "/book.epub", resuming=True)


async def test_integrity_gates_run_on_a_fresh_start(db, monkeypatch):
    pair = await make_book_pair(db)
    item = await _seed_item(db, pair.id, status="pending")
    calls = []

    monkeypatch.setattr(
        "services.audio_integrity.check_audio_integrity",
        lambda p: (calls.append(p), (True, "ok"))[1],
    )
    monkeypatch.setattr(
        "services.ebook_integrity.check_ebook_integrity",
        lambda p: (calls.append(p), (True, "ok"))[1],
    )

    await queue_manager._run_integrity_gates(
        item.id, "/audio.m4b", "/book.epub", resuming=False
    )
    assert calls == ["/audio.m4b", "/book.epub"]


# ---------------------------------------------------------------------------
# Off-hours watchdog (issue #106)
# ---------------------------------------------------------------------------

async def test_watcher_pauses_a_running_job_when_the_window_closes(db, monkeypatch):
    pair = await make_book_pair(db)
    item = await _seed_item(db, pair.id, status="in_progress", progress=0.4)
    await _enable_window(db)
    _freeze_clock(monkeypatch, 8)  # window shut

    provider = _FakeProvider()
    queue_manager._active_provider = provider
    queue_manager._active_item_id = item.id

    await queue_manager._offhours_tick()

    assert provider.pause_calls == 1
    assert item.id in queue_manager._pause_requested

    # A second tick must not nag the worker again.
    await queue_manager._offhours_tick()
    assert provider.pause_calls == 1


async def test_watcher_leaves_a_forced_job_alone(db, monkeypatch):
    """"Run now" means run now — including past the window's edge."""
    pair = await make_book_pair(db)
    item = await _seed_item(db, pair.id, status="in_progress", force_run=True)
    await _enable_window(db)
    _freeze_clock(monkeypatch, 8)

    provider = _FakeProvider()
    queue_manager._active_provider = provider
    queue_manager._active_item_id = item.id

    await queue_manager._offhours_tick()
    assert provider.pause_calls == 0


async def test_watcher_does_nothing_while_the_window_is_open(db, monkeypatch):
    pair = await make_book_pair(db)
    item = await _seed_item(db, pair.id, status="in_progress")
    await _enable_window(db)
    _freeze_clock(monkeypatch, 3)

    provider = _FakeProvider()
    queue_manager._active_provider = provider
    queue_manager._active_item_id = item.id

    await queue_manager._offhours_tick()
    assert provider.pause_calls == 0
    assert provider.release_calls == 0


async def test_watcher_is_inert_when_the_window_is_disabled(db, monkeypatch):
    pair = await make_book_pair(db)
    item = await _seed_item(db, pair.id, status="in_progress")
    _freeze_clock(monkeypatch, 14)

    provider = _FakeProvider()
    queue_manager._active_provider = provider
    queue_manager._active_item_id = item.id

    await queue_manager._offhours_tick()
    assert provider.pause_calls == 0


async def test_watcher_releases_the_model_once_per_closed_window(db, monkeypatch):
    await _enable_window(db)
    _freeze_clock(monkeypatch, 8)

    provider = _FakeProvider()

    async def _fake_get_provider():
        return provider

    monkeypatch.setattr(
        "services.transcription_providers.get_transcription_provider", _fake_get_provider
    )

    await queue_manager._offhours_tick()
    await queue_manager._offhours_tick()
    assert provider.release_calls == 1, "unload should be asked for once, not every tick"

    # Reopening and closing again arms it afresh.
    _freeze_clock(monkeypatch, 3)
    await queue_manager._offhours_tick()
    _freeze_clock(monkeypatch, 8)
    await queue_manager._offhours_tick()
    assert provider.release_calls == 2


async def test_pause_active_job_lets_an_unpausable_provider_finish(db):
    """Local Whisper can't stop mid-job. It also isn't using the remote
    worker's GPU, so finishing is the right outcome — and we must not re-ask
    on every tick."""
    provider = _FakeProvider(can_pause=False)
    queue_manager._active_provider = provider
    queue_manager._active_item_id = 7

    assert await queue_manager.pause_active_job() is False
    assert provider.pause_calls == 1

    await queue_manager.pause_active_job()
    assert provider.pause_calls == 1


async def test_pause_active_job_noop_without_a_running_job():
    assert await queue_manager.pause_active_job() is False


# ---------------------------------------------------------------------------
# Restart + cancellation interactions with the window (issue #106)
# ---------------------------------------------------------------------------

async def test_reset_stale_items_preserves_progress(db):
    """The worker's checkpoint outlives a server restart, so the job really
    does resume from here — reporting 0% would be a lie."""
    pair = await make_book_pair(db)
    item = await _seed_item(db, pair.id, status="in_progress", progress=0.62)
    await queue_manager.reset_stale_items()
    refreshed = await _get(TranscriptionQueueItem, item.id)
    assert refreshed.status == "pending"
    assert refreshed.progress == 0.62


async def test_cancel_works_while_the_window_is_closed(db, monkeypatch):
    pair = await make_book_pair(db)
    item = await _seed_item(db, pair.id, status="pending")
    await _enable_window(db)
    _freeze_clock(monkeypatch, 14)

    assert await queue_manager.cancel_item(item.id) is True
    assert (await _get(TranscriptionQueueItem, item.id)).status == "cancelled"


async def test_cancelling_a_paused_item_discards_the_remote_checkpoint(db, monkeypatch):
    pair = await make_book_pair(db)
    audio_path = (await db.execute(
        select(AudioBook).where(AudioBook.id == pair.audiobook_id)
    )).scalar_one().file_path
    item = await _seed_item(
        db, pair.id, status="pending", progress=0.4,
        paused_at=datetime.datetime(2026, 6, 1, 7, 0, 0),
    )

    provider = _FakeProvider()

    async def _fake_get_provider():
        return provider

    monkeypatch.setattr(
        "services.transcription_providers.get_transcription_provider", _fake_get_provider
    )

    assert await queue_manager.cancel_item(item.id) is True
    assert provider.discarded == [audio_path]


async def test_cancelling_an_unpaused_item_leaves_the_worker_alone(db, monkeypatch):
    pair = await make_book_pair(db)
    item = await _seed_item(db, pair.id, status="pending")

    provider = _FakeProvider()

    async def _fake_get_provider():
        return provider

    monkeypatch.setattr(
        "services.transcription_providers.get_transcription_provider", _fake_get_provider
    )

    await queue_manager.cancel_item(item.id)
    assert provider.discarded == []


async def test_cancel_returns_false_for_an_already_finished_item(db):
    pair = await make_book_pair(db)
    item = await _seed_item(db, pair.id, status="completed")
    assert await queue_manager.cancel_item(item.id) is False


# ---------------------------------------------------------------------------
# Cancelling an in-progress job reaches the worker (issue #196)
#
# "Cancel" used to flip the DB row and nothing else: the worker kept the GPU
# for the remaining hours of the book — starving the off-hours pipeline the
# window exists to protect — and when it finally finished, the server saw the
# cancel flag and returned before persisting, throwing the transcript away.
# ---------------------------------------------------------------------------

async def test_cancelling_an_in_progress_item_asks_the_worker_to_stop(db):
    pair = await make_book_pair(db)
    item = await _seed_item(db, pair.id, status="in_progress")
    provider = _FakeProvider()
    queue_manager._active_item_id = item.id
    queue_manager._active_provider = provider

    assert await queue_manager.cancel_item(item.id) is True

    assert provider.pause_calls == 1, "the worker must be told to stop at its next chunk"
    assert (await _get(TranscriptionQueueItem, item.id)).status == "cancelled"


async def test_cancelling_a_different_item_does_not_touch_the_running_job(db):
    """Only the *running* item's provider gets the pause request."""
    pair = await make_book_pair(db)
    running = await _seed_item(db, pair.id, status="in_progress")
    pair2 = await make_book_pair(db)
    other = await _seed_item(db, pair2.id, status="pending")

    provider = _FakeProvider()
    queue_manager._active_item_id = running.id
    queue_manager._active_provider = provider

    await queue_manager.cancel_item(other.id)

    assert provider.pause_calls == 0


async def test_cancel_survives_a_worker_that_cannot_be_reached(db):
    """Asking the worker is best effort — a dead worker must not fail the cancel."""
    pair = await make_book_pair(db)
    item = await _seed_item(db, pair.id, status="in_progress")

    class _BrokenProvider(_FakeProvider):
        async def request_pause(self):
            raise RuntimeError("worker unreachable")

    queue_manager._active_item_id = item.id
    queue_manager._active_provider = _BrokenProvider()

    assert await queue_manager.cancel_item(item.id) is True
    assert (await _get(TranscriptionQueueItem, item.id)).status == "cancelled"


async def test_a_cancelled_job_that_then_pauses_stays_cancelled_and_discards_the_checkpoint(
    db, monkeypatch
):
    """The pause we asked for comes back as TranscriptionPaused.

    `_mark_item_paused` sets the row back to `pending` unconditionally, which
    would silently un-cancel the job and re-run it in the next window.
    """
    pair = await make_book_pair(db)
    audio_path = (await db.execute(
        select(AudioBook).where(AudioBook.id == pair.audiobook_id)
    )).scalar_one().file_path
    item = await _seed_item(db, pair.id, status="pending")

    provider = _FakeProvider()

    async def _fake_get_provider():
        return provider

    monkeypatch.setattr(
        "services.transcription_providers.get_transcription_provider", _fake_get_provider
    )

    async def _pipeline(item_id, pair_id):
        queue_manager._cancel_requested.add(item_id)
        raise TranscriptionPaused("paused", completed_through_sec=900, progress=0.5)

    monkeypatch.setattr(queue_manager, "_run_transcription_pipeline", _pipeline)

    assert await queue_manager._process_next_item() is True

    refreshed = await _get(TranscriptionQueueItem, item.id)
    assert refreshed.status == "cancelled"
    assert refreshed.paused_at is None, "a cancelled item must not look resumable"
    assert refreshed.completed_at is not None
    assert provider.discarded == [audio_path], (
        "the worker's checkpoint and retained audio are dead weight now"
    )


async def test_a_cancelled_job_is_not_re_pended_when_the_provider_goes_away(db, monkeypatch):
    pair = await make_book_pair(db)
    item = await _seed_item(db, pair.id, status="pending")

    async def _pipeline(item_id, pair_id):
        queue_manager._cancel_requested.add(item_id)
        raise ProviderUnavailableError("worker rebooted")

    monkeypatch.setattr(queue_manager, "_run_transcription_pipeline", _pipeline)
    monkeypatch.setattr(queue_manager.asyncio, "sleep", _noop_sleep)

    await queue_manager._process_next_item()

    refreshed = await _get(TranscriptionQueueItem, item.id)
    assert refreshed.status == "cancelled"
    assert (refreshed.retry_count or 0) == 0, "a cancelled job must not burn retries"


async def test_a_cancelled_job_is_not_marked_failed_when_the_pipeline_errors(db, monkeypatch):
    pair = await make_book_pair(db, status=PairStatus.AUTO_MATCHED)
    item = await _seed_item(db, pair.id, status="pending")

    async def _pipeline(item_id, pair_id):
        queue_manager._cancel_requested.add(item_id)
        raise RuntimeError("torn down mid-job")

    monkeypatch.setattr(queue_manager, "_run_transcription_pipeline", _pipeline)

    await queue_manager._process_next_item()

    assert (await _get(TranscriptionQueueItem, item.id)).status == "cancelled"
    assert (await _get(BookPair, pair.id)).status != PairStatus.ERROR, (
        "a job the user cancelled is not an error the operator has to clear"
    )


async def test_discarding_a_checkpoint_survives_a_broken_provider(db, monkeypatch):
    """Cleanup is best effort — an unreachable worker must not turn a
    successful cancel into an error."""
    pair = await make_book_pair(db)
    item = await _seed_item(
        db, pair.id, status="pending",
        paused_at=datetime.datetime(2026, 6, 1, 7, 0, 0),
    )

    async def _explode():
        raise RuntimeError("worker unreachable")

    monkeypatch.setattr(
        "services.transcription_providers.get_transcription_provider", _explode
    )

    assert await queue_manager.cancel_item(item.id) is True
    assert (await _get(TranscriptionQueueItem, item.id)).status == "cancelled"


async def test_release_worker_resources_survives_a_broken_provider(monkeypatch):
    async def _explode():
        raise RuntimeError("worker unreachable")

    monkeypatch.setattr(
        "services.transcription_providers.get_transcription_provider", _explode
    )
    await queue_manager._release_worker_resources()  # should not raise


# ---------------------------------------------------------------------------
# Idle cadence + task lifecycle
# ---------------------------------------------------------------------------

async def test_idle_sleep_is_short_while_the_window_is_open(db, monkeypatch):
    await _enable_window(db)
    _freeze_clock(monkeypatch, 3)
    assert await queue_manager._idle_sleep_seconds() == queue_manager.IDLE_POLL_SECONDS


async def test_idle_sleep_backs_off_while_the_window_is_shut(db, monkeypatch):
    """Polling the DB every 5s for the 18 hours the window is closed is waste —
    but never sleep past the opening either."""
    await _enable_window(db)
    _freeze_clock(monkeypatch, 14)
    assert await queue_manager._idle_sleep_seconds() == queue_manager.OFFHOURS_TICK_SECONDS


async def test_idle_sleep_never_overshoots_the_window_opening(db, monkeypatch):
    await _enable_window(db)
    _freeze_clock(monkeypatch, 0, 59)  # 60s before 01:00
    assert await queue_manager._idle_sleep_seconds() == 60


async def test_start_and_stop_manage_both_background_tasks(db):
    await queue_manager.start_queue_manager()
    try:
        assert queue_manager._queue_task is not None
        assert queue_manager._offhours_task is not None
    finally:
        await queue_manager.stop_queue_manager()

    assert queue_manager._queue_task is None
    assert queue_manager._offhours_task is None


# ---------------------------------------------------------------------------
# Integrity gates
# ---------------------------------------------------------------------------

async def test_integrity_gate_rejects_undecodable_audio(db, monkeypatch):
    from services.transcription_providers.base import TranscriptionError

    pair = await make_book_pair(db)
    item = await _seed_item(db, pair.id, status="pending")
    monkeypatch.setattr(
        "services.audio_integrity.check_audio_integrity", lambda p: (False, "truncated")
    )

    with pytest.raises(TranscriptionError, match="re-import required"):
        await queue_manager._run_integrity_gates(
            item.id, "/audio.m4b", "/book.epub", resuming=False
        )


async def test_integrity_gate_rejects_an_unreadable_ebook(db, monkeypatch):
    from services.transcription_providers.base import TranscriptionError

    pair = await make_book_pair(db)
    item = await _seed_item(db, pair.id, status="pending")
    monkeypatch.setattr(
        "services.audio_integrity.check_audio_integrity", lambda p: (True, "ok")
    )
    monkeypatch.setattr(
        "services.ebook_integrity.check_ebook_integrity", lambda p: (False, "DRM-encrypted")
    )

    with pytest.raises(TranscriptionError, match="DRM-encrypted"):
        await queue_manager._run_integrity_gates(
            item.id, "/audio.m4b", "/book.epub", resuming=False
        )


async def test_unverified_integrity_result_does_not_fail_the_item(db, monkeypatch, caplog):
    """A gate that could not check the file is not a verdict of corruption.

    Missing ffmpeg and a decode that outran the timeout both pass with an
    unverified detail (issue #245). The job proceeds — a genuinely corrupt
    file still fails at the worker's own decode — but the reason is logged at
    WARNING and left on the queue item so it is visible in the UI.
    """
    pair = await make_book_pair(db)
    item = await _seed_item(db, pair.id, status="pending")
    monkeypatch.setattr(
        "services.audio_integrity.check_audio_integrity",
        lambda p: (True, "full decode did not finish within 1800s — could not verify"),
    )
    monkeypatch.setattr(
        "services.ebook_integrity.check_ebook_integrity", lambda p: (True, "ok")
    )

    with caplog.at_level(logging.WARNING, logger="services.queue_manager"):
        await queue_manager._run_integrity_gates(
            item.id, "/audio.m4b", "/book.epub", resuming=False
        )

    assert "could not verify" in caplog.text
    assert "could not verify" in (await _get(TranscriptionQueueItem, item.id)).message


# ---------------------------------------------------------------------------
# The real transcription pipeline (issue #254)
#
# Every test above replaces `_run_transcription_pipeline` with a stub, which
# left all of it — transcript cache, sync map, pair status, the bookmark re-map
# — untested. These drive the real thing with only the hardware boundary faked:
# a provider that returns a canned transcript, EPUB extraction and alignment
# stubbed to fixed sentences, and the integrity gates (which shell out to
# ffmpeg) skipped. Everything else runs for real against the SQLite `db`.
# ---------------------------------------------------------------------------

TRANSCRIPT = [
    ("the quick brown fox jumps over the lazy dog", 0, 4_000),
    ("pack my box with five dozen liquor jugs", 4_000, 9_000),
    ("how vexingly quick daft zebras jump", 9_000, 14_000),
]

# The map a first run produces, and the shifted map a re-run produces: the same
# two sentences, one new sentence ahead of them, so every index moves by one.
FIRST_MAP = [
    ("the quick brown fox jumps over the lazy dog", 0, 0, 0),
    ("pack my box with five dozen liquor jugs", 0, 1, 4_000),
    ("how vexingly quick daft zebras jump", 1, 0, 9_000),
]
SHIFTED_MAP = [
    ("a wizard job is to vex chumps quickly in fog", 0, 0, 0),
    ("the quick brown fox jumps over the lazy dog", 0, 1, 1_500),
    ("pack my box with five dozen liquor jugs", 0, 2, 5_000),
    ("how vexingly quick daft zebras jump", 1, 0, 10_000),
]


def _transcribed(rows):
    from services.transcription import TranscribedSentence

    return [TranscribedSentence(text=t, start_ms=s, end_ms=e) for t, s, e in rows]


def _aligned(rows):
    from services.alignment import AlignedPoint

    return [
        AlignedPoint(epub_chapter=ch, epub_sentence_index=si,
                     epub_text_preview=text, audio_start_ms=ms,
                     audio_end_ms=ms + 1_000, confidence=1.0)
        for text, ch, si, ms in rows
    ]


def _epub_sentences(rows):
    from services.epub_parser import EpubSentence

    return [EpubSentence(chapter=ch, sentence_index=si, text=text)
            for text, ch, si, _ in rows]


async def _settle_progress_updates(before):
    """Let the progress writes this callback scheduled finish before returning.

    `on_whisper_progress` hands `_update_queue_item` to
    `call_soon_threadsafe(create_task, ...)`, so the task does not exist until
    the loop turns once and then does its own DB round trip. A real provider
    calls the callback seconds to minutes before `transcribe()` returns, so
    that write has long landed by the time the pipeline reaches its next
    cancellation checkpoint. A double that returns the instant it fires the
    callback does not give it that room, and the late write then lands on top
    of the `message="Cancelled by user"` the checkpoint just wrote — a race in
    the double, not in the code under test, and one that showed up only under
    CI's scheduling.

    Only tasks that appeared *since* `before` are awaited, so a long-lived task
    belonging to the harness can never be waited on. Finding nothing to await
    is a valid outcome: a throttle (issue #244) may legitimately drop the
    update instead of scheduling it.
    """
    me = asyncio.current_task()
    for _ in range(10):
        await asyncio.sleep(0)
        pending = {t for t in asyncio.all_tasks()
                   if t is not me and t not in before and not t.done()}
        pending |= {t for t in getattr(queue_manager, "_inflight_updates", ())
                    if not t.done()}
        if not pending:
            return
        await asyncio.gather(*pending, return_exceptions=True)


class _PipelineProvider:
    """A transcription provider that returns a canned transcript.

    `on_transcribe` runs inside `transcribe()`, which is how a test drops a
    cancellation in at that exact point in the pipeline.
    """

    def __init__(self, rows=TRANSCRIPT, on_transcribe=None):
        self.rows = rows
        self.on_transcribe = on_transcribe
        self.transcribe_calls = 0
        self.paths = []

    def name(self):
        return "FakePipeline"

    async def transcribe(self, audio_path, progress_callback=None):
        self.transcribe_calls += 1
        self.paths.append(audio_path)
        if progress_callback:
            before = asyncio.all_tasks()
            progress_callback(0.5, 100.0, None)
            await _settle_progress_updates(before)
        if self.on_transcribe:
            self.on_transcribe()
        return _transcribed(self.rows)

    async def request_pause(self):
        return True

    async def release_resources(self):
        pass


def _install_pipeline(monkeypatch, provider, *, map_rows=FIRST_MAP,
                      on_extract=None):
    """Fake only the hardware/parsing boundary; the rest of the pipeline is real."""

    async def _no_gates(item_id, audiobook_path, ebook_path, resuming):
        return None

    async def _get_provider():
        return provider

    def _extract(path):
        if on_extract:
            on_extract()
        return _epub_sentences(map_rows)

    def _align(epub_sentences, whisper_sentences):
        return _aligned(map_rows)

    monkeypatch.setattr(queue_manager, "_run_integrity_gates", _no_gates)
    monkeypatch.setattr(
        "services.transcription_providers.get_transcription_provider", _get_provider
    )
    monkeypatch.setattr("services.epub_parser.extract_book_sentences", _extract)
    monkeypatch.setattr("services.alignment.align_texts", _align)


async def _sync_map_for(pair_id):
    async with async_session() as s:
        return (await s.execute(
            select(SyncMap).where(SyncMap.book_pair_id == pair_id)
        )).scalar_one_or_none()


async def _transcripts_for(pair_id):
    async with async_session() as s:
        return (await s.execute(
            select(AudioTranscript).where(AudioTranscript.pair_id == pair_id)
        )).scalars().all()


async def test_full_pipeline_run_completes_item_pair_transcript_and_map(db, monkeypatch):
    pair = await make_book_pair(db, status=PairStatus.AUTO_MATCHED)
    item = await _seed_item(db, pair.id, status="pending")
    provider = _PipelineProvider()
    _install_pipeline(monkeypatch, provider)

    assert await queue_manager._process_next_item() is True

    refreshed = await _get(TranscriptionQueueItem, item.id)
    assert refreshed.status == "completed"
    assert refreshed.progress == 1.0
    assert refreshed.completed_at is not None

    refreshed_pair = await _get(BookPair, pair.id)
    assert refreshed_pair.status == PairStatus.SYNCED
    assert refreshed_pair.synced_at is not None

    transcripts = await _transcripts_for(pair.id)
    assert len(transcripts) == 1
    assert transcripts[0].sentence_count == len(TRANSCRIPT)
    assert transcripts[0].audiobook_path == provider.paths[0]

    sync_map = await _sync_map_for(pair.id)
    assert sync_map.version == 1
    assert sync_map.total_sentences == len(FIRST_MAP)
    async with async_session() as s:
        points = (await s.execute(
            select(SyncPoint).where(SyncPoint.sync_map_id == sync_map.id)
        )).scalars().all()
    assert len(points) == len(FIRST_MAP)


async def test_a_second_run_on_the_same_audio_reuses_the_cached_transcript(db, monkeypatch):
    pair = await make_book_pair(db, status=PairStatus.AUTO_MATCHED)
    await _seed_item(db, pair.id, status="pending")
    provider = _PipelineProvider()
    _install_pipeline(monkeypatch, provider)

    await queue_manager._process_next_item()
    await _seed_item(db, pair.id, status="pending")
    await queue_manager._process_next_item()

    # The audio file did not change, so the second run must not re-transcribe.
    assert provider.transcribe_calls == 1
    assert len(await _transcripts_for(pair.id)) == 1


async def test_rerun_with_a_different_audio_path_replaces_the_cached_transcript(db, monkeypatch):
    """Issue #193: the stale row is updated in place, not deleted and re-added.

    Delete-then-add in one flush sent the INSERT first (SQLAlchemy orders every
    INSERT for a mapper ahead of its DELETEs), the new row hit the `pair_id`
    unique index, and the whole run was lost to an IntegrityError — leaving the
    pair permanently unable to re-transcribe. Pinned as xfail by #254 until the
    fix landed.
    """
    pair = await make_book_pair(db, status=PairStatus.AUTO_MATCHED)
    await _seed_item(db, pair.id, status="pending")
    provider = _PipelineProvider()
    _install_pipeline(monkeypatch, provider)

    await queue_manager._process_next_item()
    first_path = provider.paths[0]

    # The audiobook was replaced on disk with a different file.
    async with async_session() as s:
        audiobook = (await s.execute(
            select(AudioBook).join(BookPair, BookPair.audiobook_id == AudioBook.id)
            .where(BookPair.id == pair.id)
        )).scalar_one()
        audiobook.file_path = "/x/replaced/new-audio.m4b"
        await s.commit()

    await _seed_item(db, pair.id, status="pending")
    await queue_manager._process_next_item()

    assert provider.transcribe_calls == 2
    transcripts = await _transcripts_for(pair.id)
    assert len(transcripts) == 1, "the stale cache row must be replaced, not added to"
    assert transcripts[0].audiobook_path == "/x/replaced/new-audio.m4b"
    assert transcripts[0].audiobook_path != first_path


async def test_rerun_bumps_the_sync_map_version_and_remaps_bookmarks(db, monkeypatch):
    """The re-map of issue #55, exercised through its real caller.

    `test_sync_remap.py` drives `save_sync_map` directly; this pins that a
    re-transcription actually reaches it, so a bookmark follows its text onto
    the new map's coordinates instead of being left on a stale index.
    """
    await ensure_users(db, 1)
    pair = await make_book_pair(db, status=PairStatus.AUTO_MATCHED)
    await _seed_item(db, pair.id, status="pending")
    provider = _PipelineProvider()
    _install_pipeline(monkeypatch, provider, map_rows=FIRST_MAP)

    await queue_manager._process_next_item()

    bookmark = Bookmark(
        user_id=1, book_pair_id=pair.id, source=BookmarkSource.EBOOK,
        epub_chapter=0, epub_sentence_index=1,
        epub_text_preview="pack my box with five dozen liquor jugs",
        audio_position_ms=4_000, anchor_revision=3, captured_at=utcnow(),
        sync_map_version=1,
    )
    db.add(bookmark)
    await db.commit()

    # Re-run: the same audio (cached transcript) but a re-segmented ebook, so
    # every sentence in chapter 0 shifts by one.
    _install_pipeline(monkeypatch, provider, map_rows=SHIFTED_MAP)
    await _seed_item(db, pair.id, status="pending")
    await queue_manager._process_next_item()

    sync_map = await _sync_map_for(pair.id)
    assert sync_map.version == 2

    await db.refresh(bookmark)
    assert bookmark.epub_chapter == 0
    assert bookmark.epub_sentence_index == 2, "the bookmark must follow its text"
    assert bookmark.audio_position_ms == 5_000
    assert bookmark.sync_map_version == 2


# --- cancellation ----------------------------------------------------------

CHECKPOINTS = ["before_provider", "after_transcribe", "after_epub_extract"]


def _install_pipeline_cancelling_at(monkeypatch, checkpoint, item_id, provider_box):
    """Wire the pipeline so a cancellation lands at exactly one checkpoint."""
    def _cancel():
        queue_manager._cancel_requested.add(item_id)

    provider = _PipelineProvider(
        on_transcribe=_cancel if checkpoint == "after_transcribe" else None
    )
    provider_box.append(provider)
    _install_pipeline(
        monkeypatch, provider,
        on_extract=_cancel if checkpoint == "after_epub_extract" else None,
    )
    if checkpoint == "before_provider":
        _cancel()


@pytest.mark.parametrize("checkpoint", CHECKPOINTS)
async def test_cancel_at_each_checkpoint_leaves_the_item_cancelled(db, monkeypatch, checkpoint):
    pair = await make_book_pair(db, status=PairStatus.AUTO_MATCHED)
    item = await _seed_item(db, pair.id, status="pending")
    box = []
    _install_pipeline_cancelling_at(monkeypatch, checkpoint, item.id, box)

    assert await queue_manager._process_next_item() is True

    refreshed = await _get(TranscriptionQueueItem, item.id)
    assert refreshed.status == "cancelled"
    assert refreshed.message == "Cancelled by user"
    assert refreshed.completed_at is not None

    # The pipeline stopped at that checkpoint rather than running on.
    assert box[0].transcribe_calls == (0 if checkpoint == "before_provider" else 1)
    assert await _sync_map_for(pair.id) is None
    # A transcript reached the cache whenever the provider actually ran: since
    # issue #196 the persist happens before the post-transcribe cancel check,
    # so cancelling the *sync* never throws away hours of *transcription*.
    expected_transcripts = 0 if checkpoint == "before_provider" else 1
    assert len(await _transcripts_for(pair.id)) == expected_transcripts


async def test_a_completed_transcript_is_persisted_even_when_the_item_was_cancelled(
    db, monkeypatch
):
    """Cancelling the sync must not discard a transcription that already finished.

    The worker had already spent the hours; a re-queue should pick the
    transcript up from the cache rather than starting from scratch.
    """
    pair = await make_book_pair(db, status=PairStatus.AUTO_MATCHED)
    item = await _seed_item(db, pair.id, status="pending")
    _install_pipeline_cancelling_at(monkeypatch, "after_transcribe", item.id, [])

    await queue_manager._process_next_item()

    transcripts = await _transcripts_for(pair.id)
    assert len(transcripts) == 1
    assert transcripts[0].sentence_count == len(TRANSCRIPT)
    assert (await _get(TranscriptionQueueItem, item.id)).status == "cancelled"


@pytest.mark.parametrize("checkpoint", CHECKPOINTS)
async def test_cancel_at_each_checkpoint_does_not_leave_the_pair_transcribing(
    db, monkeypatch, checkpoint
):
    pair = await make_book_pair(db, status=PairStatus.AUTO_MATCHED)
    item = await _seed_item(db, pair.id, status="pending")
    _install_pipeline_cancelling_at(monkeypatch, checkpoint, item.id, [])

    await queue_manager._process_next_item()

    assert (await _get(BookPair, pair.id)).status != PairStatus.TRANSCRIBING


async def test_cancel_after_the_last_checkpoint_is_not_overwritten_by_completed(
    db, monkeypatch
):
    """A user who cancels while the sync map is being saved stays cancelled (#254).

    The cancellation is injected at the "Saving sync map..." update — past the
    last checkpoint the pipeline consults, and the window in which
    `cancel_item` writes `cancelled` only for the pipeline to write
    `completed` over it. The map itself is already saved by then and is kept:
    the pair is genuinely SYNCED, and only the queue row records the cancel.
    """
    pair = await make_book_pair(db, status=PairStatus.AUTO_MATCHED)
    item = await _seed_item(db, pair.id, status="pending")
    _install_pipeline(monkeypatch, _PipelineProvider())

    real_update = queue_manager._update_queue_item

    async def _update(item_id, **kwargs):
        if kwargs.get("message") == "Saving sync map...":
            assert await queue_manager.cancel_item(item_id) is True
        await real_update(item_id, **kwargs)

    monkeypatch.setattr(queue_manager, "_update_queue_item", _update)

    await queue_manager._process_next_item()

    refreshed = await _get(TranscriptionQueueItem, item.id)
    assert refreshed.status == "cancelled"
    assert refreshed.message == "Cancelled by user"
    assert refreshed.completed_at is not None
    assert (await _sync_map_for(pair.id)) is not None, "the finished map is kept"
    assert (await _get(BookPair, pair.id)).status == PairStatus.SYNCED


# --- cancel returns the pair to its pre-job status (issue #381) -------------
#
# The pipeline flips the pair to TRANSCRIBING on entry and remembers what it
# was on the queue row. Every path that settles a cancelled item — the API
# cancel, the pipeline's own checkpoints, the raise path — hands the pair back
# through the one helper. ERROR is reserved for a job that actually failed.


async def test_cancelling_an_in_progress_item_returns_the_pair_to_its_pre_job_status(db):
    pair = await make_book_pair(db, status=PairStatus.TRANSCRIBING)
    item = await _seed_item(
        db, pair.id, status="in_progress", pair_status_before="auto_matched"
    )

    assert await queue_manager.cancel_item(item.id) is True

    assert (await _get(BookPair, pair.id)).status == PairStatus.AUTO_MATCHED


async def test_cancelling_a_paused_item_returns_the_pair_to_its_pre_job_status(db, monkeypatch):
    """A paused job is a pending row, but its pair is still TRANSCRIBING."""
    pair = await make_book_pair(db, status=PairStatus.TRANSCRIBING)
    item = await _seed_item(
        db, pair.id, status="pending", paused_at=utcnow(),
        pair_status_before="manual_matched",
    )

    async def _fake_get_provider():
        return _FakeProvider()

    monkeypatch.setattr(
        "services.transcription_providers.get_transcription_provider", _fake_get_provider
    )

    assert await queue_manager.cancel_item(item.id) is True

    assert (await _get(BookPair, pair.id)).status == PairStatus.MANUAL_MATCHED


async def test_cancelling_a_pending_item_leaves_an_untouched_pair_alone(db):
    """Nothing claimed the pair yet, so there is nothing to hand back."""
    pair = await make_book_pair(db, status=PairStatus.AUTO_MATCHED)
    item = await _seed_item(db, pair.id, status="pending")

    assert await queue_manager.cancel_item(item.id) is True

    assert (await _get(BookPair, pair.id)).status == PairStatus.AUTO_MATCHED


@pytest.mark.parametrize("checkpoint", CHECKPOINTS)
async def test_cancelling_a_re_transcription_leaves_the_pair_synced(db, monkeypatch, checkpoint):
    """The pipeline records SYNCED on claim and the checkpoint hands it back."""
    pair = await make_book_pair(db, status=PairStatus.SYNCED)
    await make_sync_map(db, pair.id)
    item = await _seed_item(db, pair.id, status="pending")
    _install_pipeline_cancelling_at(monkeypatch, checkpoint, item.id, [])

    await queue_manager._process_next_item()

    refreshed = await _get(TranscriptionQueueItem, item.id)
    assert refreshed.status == "cancelled"
    assert refreshed.pair_status_before == "synced"
    assert (await _get(BookPair, pair.id)).status == PairStatus.SYNCED


@pytest.mark.parametrize("checkpoint", CHECKPOINTS)
async def test_cancelling_at_a_checkpoint_returns_an_auto_matched_pair(db, monkeypatch, checkpoint):
    pair = await make_book_pair(db, status=PairStatus.AUTO_MATCHED)
    item = await _seed_item(db, pair.id, status="pending")
    _install_pipeline_cancelling_at(monkeypatch, checkpoint, item.id, [])

    await queue_manager._process_next_item()

    assert (await _get(TranscriptionQueueItem, item.id)).pair_status_before == "auto_matched"
    assert (await _get(BookPair, pair.id)).status == PairStatus.AUTO_MATCHED


async def test_cancelling_at_a_checkpoint_returns_a_manual_matched_pair(db, monkeypatch):
    """The status is remembered, not guessed: a MANUAL_MATCHED pair has no map,
    so deriving it would answer AUTO_MATCHED."""
    pair = await make_book_pair(db, status=PairStatus.MANUAL_MATCHED)
    item = await _seed_item(db, pair.id, status="pending")
    _install_pipeline_cancelling_at(monkeypatch, "after_epub_extract", item.id, [])

    await queue_manager._process_next_item()

    assert (await _get(TranscriptionQueueItem, item.id)).pair_status_before == "manual_matched"
    assert (await _get(BookPair, pair.id)).status == PairStatus.MANUAL_MATCHED


@pytest.mark.parametrize("has_map, expected", [
    (True, PairStatus.SYNCED),
    (False, PairStatus.AUTO_MATCHED),
])
async def test_cancelling_a_retry_of_an_errored_pair_derives_a_fresh_status(db, monkeypatch, has_map, expected):
    """A retry records ERROR as the pre-job status, because that is what the
    pair honestly was. Cancelling that retry must not hand ERROR back: the
    job did not fail, the user stopped it. Derive instead, as for a row with
    no record at all (owner's call, 2026-09-06)."""
    pair = await make_book_pair(db, status=PairStatus.ERROR)
    if has_map:
        db.add(SyncMap(book_pair_id=pair.id, version=1))
        await db.commit()
    item = await _seed_item(db, pair.id, status="pending")
    _install_pipeline_cancelling_at(monkeypatch, "after_epub_extract", item.id, [])

    await queue_manager._process_next_item()

    assert (await _get(TranscriptionQueueItem, item.id)).pair_status_before == "error"
    assert (await _get(BookPair, pair.id)).status == expected


async def test_a_resumed_job_does_not_overwrite_the_recorded_pre_job_status(db, monkeypatch):
    """Re-entering the pipeline finds the pair already TRANSCRIBING.

    A resume after an off-hours pause, a provider-unavailable retry and a
    restart all come back through the same entry point. TRANSCRIBING is not a
    status worth returning to, so the first record stands.
    """
    pair = await make_book_pair(db, status=PairStatus.TRANSCRIBING)
    item = await _seed_item(
        db, pair.id, status="pending", paused_at=utcnow(), progress=0.4,
        pair_status_before="manual_matched",
    )
    _install_pipeline_cancelling_at(monkeypatch, "before_provider", item.id, [])

    await queue_manager._process_next_item()

    assert (await _get(TranscriptionQueueItem, item.id)).pair_status_before == "manual_matched"
    assert (await _get(BookPair, pair.id)).status == PairStatus.MANUAL_MATCHED


@pytest.mark.parametrize("has_map, expected", [
    (True, PairStatus.SYNCED),
    (False, PairStatus.AUTO_MATCHED),
])
async def test_cancel_without_a_recorded_status_derives_it(db, has_map, expected):
    """A row that predates the column (a job running across the deploy)."""
    pair = await make_book_pair(db, status=PairStatus.TRANSCRIBING)
    if has_map:
        await make_sync_map(db, pair.id)
    item = await _seed_item(db, pair.id, status="in_progress")

    assert await queue_manager.cancel_item(item.id) is True

    assert (await _get(BookPair, pair.id)).status == expected


async def test_the_raise_path_returns_the_pair_to_its_pre_job_status(db, monkeypatch):
    """`_finalize_cancelled` — a cancelled job that unwinds by raising."""
    pair = await make_book_pair(db, status=PairStatus.TRANSCRIBING)
    item = await _seed_item(db, pair.id, status="pending", pair_status_before="auto_matched")

    async def _pipeline(item_id, pair_id):
        queue_manager._cancel_requested.add(item_id)
        raise TranscriptionPaused("paused", completed_through_sec=900, progress=0.5)

    monkeypatch.setattr(queue_manager, "_run_transcription_pipeline", _pipeline)

    async def _fake_get_provider():
        return _FakeProvider()

    monkeypatch.setattr(
        "services.transcription_providers.get_transcription_provider", _fake_get_provider
    )

    await queue_manager._process_next_item()

    assert (await _get(TranscriptionQueueItem, item.id)).status == "cancelled"
    assert (await _get(BookPair, pair.id)).status == PairStatus.AUTO_MATCHED


async def test_a_job_that_actually_fails_still_marks_the_pair_error(db, monkeypatch):
    """The recorded pre-job status is for cancels only, never for failures."""
    pair = await make_book_pair(db, status=PairStatus.AUTO_MATCHED)
    item = await _seed_item(db, pair.id, status="pending")

    def _boom():
        raise RuntimeError("worker fell over")

    _install_pipeline(monkeypatch, _PipelineProvider(on_transcribe=_boom))

    await queue_manager._process_next_item()

    refreshed = await _get(TranscriptionQueueItem, item.id)
    assert refreshed.status == "failed"
    assert refreshed.pair_status_before == "auto_matched"
    assert (await _get(BookPair, pair.id)).status == PairStatus.ERROR


# ---------------------------------------------------------------------------
# Retry ladder for a missing provider (issue #242)
#
# The old ladder was a flat 30 s x 5 — a ~2.5-minute budget, which a Jetson
# reboot plus a model load outruns, so the book died for an outage it would
# have survived.
# ---------------------------------------------------------------------------

def test_retry_delay_backs_off_exponentially_and_is_capped():
    delays = [
        queue_manager.retry_delay_seconds(n, base_seconds=30, cap_seconds=900)
        for n in range(1, 9)
    ]
    assert delays[:5] == [30, 60, 120, 240, 480]
    assert delays[5:] == [900, 900, 900]


def test_retry_delay_is_sane_for_a_degenerate_retry_count():
    # retry_count is 1-based; anything lower must still produce the base delay
    # rather than a fractional or negative sleep.
    assert queue_manager.retry_delay_seconds(0, base_seconds=30, cap_seconds=900) == 30
    assert queue_manager.retry_delay_seconds(-3, base_seconds=30, cap_seconds=900) == 30


async def test_retry_policy_defaults_when_settings_are_absent(db):
    assert await queue_manager.load_retry_policy() == (
        queue_manager.DEFAULT_RETRY_MAX,
        queue_manager.DEFAULT_RETRY_BASE_SECONDS,
    )


async def test_retry_policy_ignores_nonsense_settings(db):
    """A typo in a settings row must never wedge the queue — same contract the
    off-hours loader keeps."""
    db.add(SystemSetting(key="transcription_retry_max", value="not a number"))
    db.add(SystemSetting(key="transcription_retry_base_seconds", value="0"))
    await db.commit()

    assert await queue_manager.load_retry_policy() == (
        queue_manager.DEFAULT_RETRY_MAX,
        queue_manager.DEFAULT_RETRY_BASE_SECONDS,
    )


async def test_provider_unavailable_sleeps_with_exponential_backoff(db, monkeypatch):
    pair = await make_book_pair(db)
    item = await _seed_item(db, pair.id, status="pending", retry_count=2)
    slept = []

    async def _record_sleep(seconds):
        slept.append(seconds)

    async def _raise(item_id, pair_id):
        raise ProviderUnavailableError("remote offline")

    monkeypatch.setattr(queue_manager, "_run_transcription_pipeline", _raise)
    monkeypatch.setattr("asyncio.sleep", _record_sleep)

    assert await queue_manager._process_next_item() is True

    refreshed = await _get(TranscriptionQueueItem, item.id)
    assert refreshed.retry_count == 3
    # Third retry: 30 * 2**2.
    assert slept == [120]


async def test_retry_ceiling_and_delay_come_from_settings(db, monkeypatch):
    """An operator with a flaky worker can widen the window without a deploy."""
    pair = await make_book_pair(db, status=PairStatus.TRANSCRIBING)
    item = await _seed_item(db, pair.id, status="pending", retry_count=4)
    db.add(SystemSetting(key="transcription_retry_max", value="8"))
    db.add(SystemSetting(key="transcription_retry_base_seconds", value="10"))
    await db.commit()
    slept = []

    async def _record_sleep(seconds):
        slept.append(seconds)

    async def _raise(item_id, pair_id):
        raise ProviderUnavailableError("still offline")

    monkeypatch.setattr(queue_manager, "_run_transcription_pipeline", _raise)
    monkeypatch.setattr("asyncio.sleep", _record_sleep)

    await queue_manager._process_next_item()

    refreshed = await _get(TranscriptionQueueItem, item.id)
    # Would have been a permanent failure under the hard-coded ceiling of 5.
    assert refreshed.status == "pending"
    assert refreshed.retry_count == 5
    assert "5/8" in (refreshed.message or "")
    assert slept == [160]  # 10 * 2**4
    assert (await _get(BookPair, pair.id)).status == PairStatus.TRANSCRIBING


async def test_permanent_failure_does_not_sleep_out_the_full_backoff(db, monkeypatch):
    """Once the item is dead there is nothing to back off for — only the short
    guard against spinning on the next item."""
    pair = await make_book_pair(db, status=PairStatus.TRANSCRIBING)
    item = await _seed_item(db, pair.id, status="pending", retry_count=4)
    slept = []

    async def _record_sleep(seconds):
        slept.append(seconds)

    async def _raise(item_id, pair_id):
        raise ProviderUnavailableError("still offline")

    monkeypatch.setattr(queue_manager, "_run_transcription_pipeline", _raise)
    monkeypatch.setattr("asyncio.sleep", _record_sleep)

    await queue_manager._process_next_item()

    assert (await _get(TranscriptionQueueItem, item.id)).status == "failed"
    assert slept == [queue_manager.DEFAULT_RETRY_BASE_SECONDS]


# ---------------------------------------------------------------------------
# Progress-write throttling (issue #244)
#
# Remote polling fires the progress callback twice a second for the whole run,
# and each call used to be its own session + UPDATE + commit — ~144k writes for
# a 20-hour book, for a value the UI reads every few seconds.
# ---------------------------------------------------------------------------

def test_progress_throttle_suppresses_unchanged_and_sub_threshold_updates():
    throttle = queue_manager.ProgressThrottle(min_delta=0.005, max_interval=5.0)
    writes = 0
    # 100 polls at 2 Hz — 50 s of a long book, progress creeping by 0.0001.
    for i in range(100):
        if throttle.should_write(0.10 + i * 0.0001, "Transcribing: 1:00 / 20:00 (5%)", 1000.0 + i * 0.5):
            writes += 1
    # One at the start, then one per 5 s window. The creeping progress never
    # crosses the 0.005 threshold within a window.
    assert writes == 10


def test_progress_throttle_writes_when_progress_moves_past_the_threshold():
    throttle = queue_manager.ProgressThrottle(min_delta=0.005, max_interval=5.0)
    assert throttle.should_write(0.10, "Transcribing", 1000.0) is True
    assert throttle.should_write(0.104, "Transcribing", 1000.5) is False
    assert throttle.should_write(0.106, "Transcribing", 1001.0) is True


def test_progress_throttle_forces_a_write_when_the_message_changes():
    """A different *kind* of message (paused, waiting, loading) must reach the
    UI promptly — but the digits inside a progress message churn on every poll
    and must not count as a change."""
    throttle = queue_manager.ProgressThrottle(min_delta=0.005, max_interval=5.0)
    assert throttle.should_write(0.10, "Transcribing: 1:00 / 20:00 (5%)", 1000.0) is True
    assert throttle.should_write(0.1001, "Transcribing: 1:07 / 20:00 (5%)", 1000.5) is False
    assert throttle.should_write(0.1002, "Loading model...", 1001.0) is True


async def test_scheduled_progress_tasks_are_retained_until_done():
    """Fire-and-forget tasks can be garbage-collected mid-flight; hold a
    reference until they finish."""
    queue_manager._inflight_updates.clear()
    release = asyncio.Event()

    async def _work():
        await release.wait()

    task = queue_manager._spawn_tracked(_work())
    assert task in queue_manager._inflight_updates

    release.set()
    await task
    await asyncio.sleep(0)  # let the done-callback run
    assert task not in queue_manager._inflight_updates


async def test_progress_callback_writes_are_bounded(monkeypatch):
    """The end-to-end wiring: 100 provider callbacks must not become 100 rows
    written."""
    writes = []

    async def _fake_update(item_id, **kwargs):
        writes.append(kwargs)

    monkeypatch.setattr(queue_manager, "_update_queue_item", _fake_update)

    clock = {"t": 1000.0}
    callback = queue_manager._make_progress_callback(
        item_id=1,
        provider_name="Fake",
        loop=asyncio.get_running_loop(),
        now=lambda: clock["t"],
    )

    for i in range(100):
        callback(i * 0.0001, 72000.0, None)
        clock["t"] += 0.5

    for _ in range(5):
        await asyncio.sleep(0)

    assert 0 < len(writes) <= 12, f"expected a bounded number of writes, got {len(writes)}"
    # Writes land on the 5 s heartbeat, so the newest one carries the value
    # from the start of the current window, not the very last poll.
    assert writes[-1]["progress"] == pytest.approx(0.05 + 0.0090 * 0.45, abs=1e-3)


async def test_progress_callback_uses_the_provider_message_when_supplied(monkeypatch):
    writes = []

    async def _fake_update(item_id, **kwargs):
        writes.append(kwargs)

    monkeypatch.setattr(queue_manager, "_update_queue_item", _fake_update)

    callback = queue_manager._make_progress_callback(
        item_id=7, provider_name="Jetson", loop=asyncio.get_running_loop()
    )
    callback(0.5, None, "Transcribing: 1:00 / 2:00 (50%)")
    for _ in range(5):
        await asyncio.sleep(0)

    assert writes[0]["message"] == "Jetson - Transcribing: 1:00 / 2:00 (50%)"
    assert writes[0]["progress"] == pytest.approx(0.275, abs=1e-3)


async def test_progress_callback_falls_back_to_a_bare_percentage(monkeypatch):
    """No provider message and no known duration — a percentage is all there
    is to say."""
    writes = []

    async def _fake_update(item_id, **kwargs):
        writes.append(kwargs)

    monkeypatch.setattr(queue_manager, "_update_queue_item", _fake_update)

    callback = queue_manager._make_progress_callback(
        item_id=3, provider_name="Local Whisper", loop=asyncio.get_running_loop()
    )
    callback(0.25, None, None)
    for _ in range(5):
        await asyncio.sleep(0)

    assert writes[0]["message"] == "Transcribing via Local Whisper... (25%)"


# ---------------------------------------------------------------------------
# An empty result is a failure, not a sync (issue #194)
#
# A silence-only file, a worker answering `{"sentences": []}`, or an EPUB that
# extracts no text all made `align_texts` return `[]`. The pipeline saved that
# as a new map with zero points, set the pair SYNCED and said "Sync complete!"
# — while `save_sync_map` had already deleted the working map that was there.
# ---------------------------------------------------------------------------

async def test_an_empty_alignment_does_not_replace_an_existing_sync_map(db, monkeypatch):
    pair = await make_book_pair(db, status=PairStatus.AUTO_MATCHED)
    await _seed_item(db, pair.id, status="pending")
    _install_pipeline(monkeypatch, _PipelineProvider(), map_rows=FIRST_MAP)
    await queue_manager._process_next_item()

    good_map = await _sync_map_for(pair.id)
    assert good_map.version == 1 and good_map.total_sentences == len(FIRST_MAP)

    # Second run: the ebook now extracts no text, so alignment yields nothing.
    item = await _seed_item(db, pair.id, status="pending")
    _install_pipeline(monkeypatch, _PipelineProvider(), map_rows=FIRST_MAP)
    monkeypatch.setattr("services.epub_parser.extract_book_sentences", lambda p: [])
    monkeypatch.setattr("services.alignment.align_texts", lambda e, w: [])

    await queue_manager._process_next_item()

    still = await _sync_map_for(pair.id)
    assert still.version == 1, "the working map must survive an empty alignment"
    async with async_session() as s:
        points = (await s.execute(
            select(SyncPoint).where(SyncPoint.sync_map_id == still.id)
        )).scalars().all()
    assert len(points) == len(FIRST_MAP)

    refreshed = await _get(TranscriptionQueueItem, item.id)
    assert refreshed.status == "failed"
    assert "no points" in (refreshed.error_message or "").lower()
    assert (await _get(BookPair, pair.id)).status == PairStatus.ERROR


async def test_an_empty_alignment_on_a_first_run_fails_instead_of_saying_synced(db, monkeypatch):
    pair = await make_book_pair(db, status=PairStatus.AUTO_MATCHED)
    item = await _seed_item(db, pair.id, status="pending")
    _install_pipeline(monkeypatch, _PipelineProvider(), map_rows=FIRST_MAP)
    monkeypatch.setattr("services.alignment.align_texts", lambda e, w: [])

    await queue_manager._process_next_item()

    assert await _sync_map_for(pair.id) is None, "no map beats an empty map"
    refreshed = await _get(TranscriptionQueueItem, item.id)
    assert refreshed.status == "failed"
    assert refreshed.message != "Sync complete!"
    # The reason has to be something the UI can put in front of a user.
    assert "no points" in (refreshed.error_message or "").lower()
    assert (await _get(BookPair, pair.id)).status == PairStatus.ERROR


async def test_an_empty_transcription_does_not_overwrite_the_cached_transcript(db, monkeypatch):
    pair = await make_book_pair(db, status=PairStatus.AUTO_MATCHED)
    await _seed_item(db, pair.id, status="pending")
    _install_pipeline(monkeypatch, _PipelineProvider(), map_rows=FIRST_MAP)
    await queue_manager._process_next_item()

    good = (await _transcripts_for(pair.id))[0]
    assert good.sentence_count == len(TRANSCRIPT)

    # The audio file was replaced, so the cache is stale and the provider runs
    # again — and this time answers with nothing.
    async with async_session() as s:
        audiobook = (await s.execute(
            select(AudioBook).join(BookPair, BookPair.audiobook_id == AudioBook.id)
            .where(BookPair.id == pair.id)
        )).scalar_one()
        audiobook.file_path = "/x/replaced/silent.m4b"
        await s.commit()

    item = await _seed_item(db, pair.id, status="pending")
    _install_pipeline(monkeypatch, _PipelineProvider(rows=[]), map_rows=FIRST_MAP)

    await queue_manager._process_next_item()

    transcripts = await _transcripts_for(pair.id)
    assert len(transcripts) == 1
    assert transcripts[0].sentence_count == len(TRANSCRIPT),         "a good cached transcript must not be replaced by an empty one"
    refreshed = await _get(TranscriptionQueueItem, item.id)
    assert refreshed.status == "failed"
    assert "no sentences" in (refreshed.error_message or "").lower()


async def test_an_empty_transcription_on_a_first_run_writes_no_transcript(db, monkeypatch):
    pair = await make_book_pair(db, status=PairStatus.AUTO_MATCHED)
    item = await _seed_item(db, pair.id, status="pending")
    _install_pipeline(monkeypatch, _PipelineProvider(rows=[]), map_rows=FIRST_MAP)

    await queue_manager._process_next_item()

    assert await _transcripts_for(pair.id) == []
    assert (await _get(TranscriptionQueueItem, item.id)).status == "failed"
    assert (await _get(BookPair, pair.id)).status == PairStatus.ERROR
