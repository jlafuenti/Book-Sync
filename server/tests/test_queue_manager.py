"""
Transcription queue-manager tests (issue #46, Phase 2).

The queue manager uses its own async_session() internally (bound by conftest to
the SQLite test DB), so reads-back must use a *fresh* session — reusing the
seeding session returns stale identity-map objects. The transcription pipeline
is stubbed so retry/backoff and status transitions can be tested without any
real provider or audio.
"""

import datetime

import pytest
import pytest_asyncio
from sqlalchemy import select

from database import async_session
from models.book import AudioBook, BookPair, PairStatus
from models.settings import SystemSetting
from models.transcription_queue import TranscriptionQueueItem
from services import offhours, queue_manager
from services.transcription_providers.base import (
    ProviderUnavailableError,
    TranscriptionPaused,
)
from tests.factories import make_book_pair


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
