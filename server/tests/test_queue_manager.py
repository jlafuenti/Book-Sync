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
from models.book import EBook, AudioBook, BookPair, PairStatus
from models.transcription_queue import TranscriptionQueueItem
from services import queue_manager
from services.transcription_providers.base import ProviderUnavailableError


@pytest_asyncio.fixture(autouse=True)
async def _clear_cancel_flags():
    queue_manager._cancel_requested.clear()
    yield
    queue_manager._cancel_requested.clear()


async def _get(model, id_):
    """Read a row through a fresh session to avoid identity-map staleness."""
    async with async_session() as s:
        return (await s.execute(select(model).where(model.id == id_))).scalar_one()


async def _make_pair(db, status=PairStatus.SYNCED):
    eb = EBook(title="E", filename="e.epub", file_path="/x/e.epub")
    ab = AudioBook(title="A", filename="a.m4b", file_path="/x/a.m4b")
    db.add_all([eb, ab])
    await db.flush()
    pair = BookPair(ebook_id=eb.id, audiobook_id=ab.id, status=status)
    db.add(pair)
    await db.commit()
    await db.refresh(pair)
    return pair


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
    pair = await _make_pair(db)
    created = await queue_manager.add_to_queue([pair.id])
    assert len(created) == 1
    assert created[0].status == "pending"


async def test_add_to_queue_dedups_active_pair(db):
    pair = await _make_pair(db)
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


# ---------------------------------------------------------------------------
# cancel / remove / priority
# ---------------------------------------------------------------------------

async def test_cancel_pending_item(db):
    pair = await _make_pair(db)
    item = await _seed_item(db, pair.id, status="pending")
    assert await queue_manager.cancel_item(item.id) is True
    assert (await _get(TranscriptionQueueItem, item.id)).status == "cancelled"


async def test_cancel_in_progress_item_sets_flag(db):
    pair = await _make_pair(db)
    item = await _seed_item(db, pair.id, status="in_progress")
    assert await queue_manager.cancel_item(item.id) is True
    assert item.id in queue_manager._cancel_requested
    assert (await _get(TranscriptionQueueItem, item.id)).status == "cancelled"


async def test_remove_pending_but_not_in_progress(db):
    pair = await _make_pair(db)
    pending = await _seed_item(db, pair.id, status="pending")
    assert await queue_manager.remove_item(pending.id) is True
    # queue_manager deleted the row via its own session; drop our cached copy so
    # SQLite reusing the rowid doesn't trip an identity-map warning.
    db.expunge_all()

    pair2 = await _make_pair(db)
    active = await _seed_item(db, pair2.id, status="in_progress")
    assert await queue_manager.remove_item(active.id) is False


async def test_update_priority_pending_only(db):
    pair = await _make_pair(db)
    pending = await _seed_item(db, pair.id, status="pending", priority=100)
    assert await queue_manager.update_priority(pending.id, 10) is True

    pair2 = await _make_pair(db)
    active = await _seed_item(db, pair2.id, status="in_progress", priority=100)
    assert await queue_manager.update_priority(active.id, 10) is False


# ---------------------------------------------------------------------------
# get_queue / reset_stale_items
# ---------------------------------------------------------------------------

async def test_get_queue_orders_by_priority_and_assigns_positions(db):
    base = datetime.datetime(2026, 1, 1, 12, 0, 0)
    p1 = await _make_pair(db)
    p2 = await _make_pair(db)
    p3 = await _make_pair(db)
    # Lower priority number = higher priority; ties broken by created_at asc.
    await _seed_item(db, p1.id, status="pending", priority=100, created_at=base)
    await _seed_item(db, p2.id, status="pending", priority=50, created_at=base + datetime.timedelta(minutes=1))
    await _seed_item(db, p3.id, status="pending", priority=100, created_at=base + datetime.timedelta(minutes=2))

    queue = await queue_manager.get_queue()
    assert [q.book_pair_id for q in queue] == [p2.id, p1.id, p3.id]
    assert [q.position for q in queue] == [1, 2, 3]


async def test_reset_stale_items_requeues_in_progress(db):
    pair = await _make_pair(db)
    item = await _seed_item(db, pair.id, status="in_progress", progress=0.4)
    await queue_manager.reset_stale_items()
    refreshed = await _get(TranscriptionQueueItem, item.id)
    assert refreshed.status == "pending"
    assert refreshed.started_at is None


# ---------------------------------------------------------------------------
# _process_next_item — retry / backoff / failure transitions
# ---------------------------------------------------------------------------

async def test_provider_unavailable_requeues_with_incremented_retry(db, monkeypatch):
    pair = await _make_pair(db)
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
    pair = await _make_pair(db, status=PairStatus.TRANSCRIBING)
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
    pair = await _make_pair(db)
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
    pair = await _make_pair(db)
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
