"""
Transcription queue router endpoints added for the off-hours window (#106):
the per-item "Run now" override and the window-state endpoint the queue page
uses for its banner.
"""

import datetime

import pytest

from database import async_session
from models.settings import SystemSetting
from models.transcription_queue import TranscriptionQueueItem
from routers import transcription as transcription_router
from services import offhours
from tests.factories import make_book_pair


async def _seed_item(db, pair_id, **kwargs):
    item = TranscriptionQueueItem(book_pair_id=pair_id, **kwargs)
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return item


async def _enable_window(db, start="01:00", end="07:00", tz="UTC"):
    for key, value in {
        offhours.ENABLED_KEY: "true",
        offhours.START_KEY: start,
        offhours.END_KEY: end,
        offhours.TIMEZONE_KEY: tz,
    }.items():
        db.add(SystemSetting(key=key, value=value))
    await db.commit()


def _freeze_clock(monkeypatch, hour):
    monkeypatch.setattr(
        offhours, "_now_utc",
        lambda: datetime.datetime(2026, 6, 1, hour, tzinfo=datetime.timezone.utc),
    )


# ---------------------------------------------------------------------------
# POST /queue/{id}/run-now
# ---------------------------------------------------------------------------

async def test_run_now_sets_the_force_flag(db, make_client, make_user, auth_header):
    admin = await make_user(username="admin1", role="admin")
    pair = await make_book_pair(db)
    item = await _seed_item(db, pair.id, status="pending", message="Waiting for off-hours window")

    async with make_client(transcription_router.router) as c:
        r = await c.post(
            f"/api/transcription/queue/{item.id}/run-now", headers=auth_header(admin)
        )

    assert r.status_code == 200
    assert r.json()["force_run"] is True

    async with async_session() as s:
        assert (await s.get(TranscriptionQueueItem, item.id)).force_run is True


async def test_run_now_requires_admin(db, make_client, make_user, auth_header):
    user = await make_user(username="u", role="user")
    pair = await make_book_pair(db)
    item = await _seed_item(db, pair.id, status="pending")

    async with make_client(transcription_router.router) as c:
        r = await c.post(
            f"/api/transcription/queue/{item.id}/run-now", headers=auth_header(user)
        )
    assert r.status_code == 403


async def test_run_now_404s_for_an_unknown_item(db, make_client, make_user, auth_header):
    admin = await make_user(username="admin1", role="admin")
    async with make_client(transcription_router.router) as c:
        r = await c.post("/api/transcription/queue/9999/run-now", headers=auth_header(admin))
    assert r.status_code == 404


@pytest.mark.parametrize("status", ["completed", "failed", "cancelled"])
async def test_run_now_rejects_finished_items(
    db, make_client, make_user, auth_header, status
):
    admin = await make_user(username="admin1", role="admin")
    pair = await make_book_pair(db)
    item = await _seed_item(db, pair.id, status=status)

    async with make_client(transcription_router.router) as c:
        r = await c.post(
            f"/api/transcription/queue/{item.id}/run-now", headers=auth_header(admin)
        )
    assert r.status_code == 400


async def test_queue_listing_exposes_pause_state(db, make_client, make_user, auth_header):
    """The UI needs to tell "paused, 40% banked" apart from "never started"."""
    user = await make_user(username="u", role="user")
    pair = await make_book_pair(db)
    paused_at = datetime.datetime(2026, 6, 1, 7, 0, 0)
    await _seed_item(db, pair.id, status="pending", progress=0.4, paused_at=paused_at)

    async with make_client(transcription_router.router) as c:
        r = await c.get("/api/transcription/queue", headers=auth_header(user))

    body = r.json()
    assert len(body) == 1
    assert body[0]["paused_at"] is not None
    assert body[0]["progress"] == 0.4
    assert body[0]["force_run"] is False


# ---------------------------------------------------------------------------
# Startup stale-pair reset
# ---------------------------------------------------------------------------

async def test_startup_does_not_error_a_pair_whose_job_is_paused(db):
    """A job paused for the off-hours window leaves its pair in `transcribing`
    for hours by design. A restart in the middle must not mark the book failed
    while its queue item is still waiting to resume."""
    from models.book import BookPair, PairStatus

    pair = await make_book_pair(db, status=PairStatus.TRANSCRIBING)
    await _seed_item(
        db, pair.id, status="pending", progress=0.4,
        paused_at=datetime.datetime(2026, 6, 1, 7, 0, 0),
    )

    await transcription_router.reset_stale_transcriptions()

    async with async_session() as s:
        assert (await s.get(BookPair, pair.id)).status == PairStatus.TRANSCRIBING


async def test_startup_still_errors_a_pair_with_no_queue_item(db):
    """The original purpose of the reset: a crash left the pair transcribing
    with nothing queued to ever finish it."""
    from models.book import BookPair, PairStatus

    pair = await make_book_pair(db, status=PairStatus.TRANSCRIBING)

    await transcription_router.reset_stale_transcriptions()

    async with async_session() as s:
        assert (await s.get(BookPair, pair.id)).status == PairStatus.ERROR


# ---------------------------------------------------------------------------
# GET /offhours
# ---------------------------------------------------------------------------

async def test_offhours_reports_disabled_by_default(db, make_client, make_user, auth_header):
    user = await make_user(username="u", role="user")
    async with make_client(transcription_router.router) as c:
        r = await c.get("/api/transcription/offhours", headers=auth_header(user))

    body = r.json()
    assert body["enabled"] is False
    assert body["open"] is True, "a disabled window never blocks anything"
    assert body["opens_at"] is None and body["closes_at"] is None


async def test_offhours_reports_a_closed_window_and_when_it_opens(
    db, make_client, make_user, auth_header, monkeypatch
):
    user = await make_user(username="u", role="user")
    await _enable_window(db, start="01:00", end="07:00", tz="America/New_York")
    _freeze_clock(monkeypatch, 18)  # 14:00 EDT

    async with make_client(transcription_router.router) as c:
        r = await c.get("/api/transcription/offhours", headers=auth_header(user))

    body = r.json()
    assert body["enabled"] is True
    assert body["open"] is False
    assert body["start"] == "01:00" and body["end"] == "07:00"
    assert body["timezone"] == "America/New_York"
    assert body["opens_at"] is not None
    assert body["closes_at"] is None


async def test_offhours_reports_an_open_window_and_when_it_closes(
    db, make_client, make_user, auth_header, monkeypatch
):
    user = await make_user(username="u", role="user")
    await _enable_window(db)
    _freeze_clock(monkeypatch, 3)

    async with make_client(transcription_router.router) as c:
        r = await c.get("/api/transcription/offhours", headers=auth_header(user))

    body = r.json()
    assert body["open"] is True
    assert body["closes_at"] is not None
    assert body["opens_at"] is None


# --- Role gating on the write endpoints (issue #207) ------------------------
#
# Starting a job spends the GPU for hours; cancelling one throws away work that
# may not be the canceller's. Both were open to `user`, the lowest role, while
# the queue-management endpoints beside them already required admin and the
# text/realign editors already required editor. Read endpoints (status, queue,
# offhours, history) are deliberately left alone here — they are #208's subject,
# which pairs role with rate limiting.


async def test_start_transcription_requires_editor(db, make_client, make_user, auth_header):
    user = await make_user(username="plain", role="user")
    pair = await make_book_pair(db)

    async with make_client(transcription_router.router) as c:
        r = await c.post(
            f"/api/transcription/{pair.id}/start", headers=auth_header(user)
        )
    assert r.status_code == 403


async def test_cancel_transcription_requires_editor(db, make_client, make_user, auth_header):
    user = await make_user(username="plain", role="user")
    pair = await make_book_pair(db)

    async with make_client(transcription_router.router) as c:
        r = await c.post(
            f"/api/transcription/{pair.id}/cancel", headers=auth_header(user)
        )
    assert r.status_code == 403


async def test_start_transcription_allows_editor(db, make_client, make_user, auth_header):
    """The gate must not have been set too high — editor is the intended floor."""
    editor = await make_user(username="ed", role="editor")
    pair = await make_book_pair(db)

    async with make_client(transcription_router.router) as c:
        r = await c.post(
            f"/api/transcription/{pair.id}/start", headers=auth_header(editor)
        )
    # Whatever the queueing outcome, the role check let it through.
    assert r.status_code != 403


async def test_cancel_transcription_allows_editor(db, make_client, make_user, auth_header):
    editor = await make_user(username="ed", role="editor")
    pair = await make_book_pair(db)

    async with make_client(transcription_router.router) as c:
        r = await c.post(
            f"/api/transcription/{pair.id}/cancel", headers=auth_header(editor)
        )
    assert r.status_code != 403


# ---------------------------------------------------------------------------
# POST /queue/{id}/requeue  (issue #247)
#
# History showed a failed job's error and offered nothing to do about it. The
# retry goes through `add_to_queue`, the same path every other entry point
# uses: a *new* pending row, with the failed row left in History as the record
# of what went wrong.
# ---------------------------------------------------------------------------

async def test_requeue_creates_a_new_pending_item_for_a_failed_one(
    db, make_client, make_user, auth_header
):
    editor = await make_user(username="ed", role="editor")
    pair = await make_book_pair(db)
    failed = await _seed_item(
        db, pair.id, status="failed", error_message="whisper died",
        retry_count=3, progress=0.7,
    )

    async with make_client(transcription_router.router) as c:
        r = await c.post(
            f"/api/transcription/queue/{failed.id}/requeue", headers=auth_header(editor)
        )

    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "pending"
    assert body["book_pair_id"] == pair.id
    assert body["id"] != failed.id

    async with async_session() as s:
        # The failure record is untouched — History is the audit trail.
        old = await s.get(TranscriptionQueueItem, failed.id)
        assert old.status == "failed"
        assert old.error_message == "whisper died"
        assert old.retry_count == 3

        new = await s.get(TranscriptionQueueItem, body["id"])
        assert new.status == "pending"
        assert (new.retry_count or 0) == 0
        assert new.error_message is None


async def test_requeue_works_for_a_cancelled_item(db, make_client, make_user, auth_header):
    editor = await make_user(username="ed", role="editor")
    pair = await make_book_pair(db)
    item = await _seed_item(db, pair.id, status="cancelled")

    async with make_client(transcription_router.router) as c:
        r = await c.post(
            f"/api/transcription/queue/{item.id}/requeue", headers=auth_header(editor)
        )

    assert r.status_code == 200
    assert r.json()["status"] == "pending"


@pytest.mark.parametrize("status", ["pending", "in_progress", "completed"])
async def test_requeue_rejects_items_that_are_not_failed_or_cancelled(
    db, make_client, make_user, auth_header, status
):
    editor = await make_user(username="ed", role="editor")
    pair = await make_book_pair(db)
    item = await _seed_item(db, pair.id, status=status)

    async with make_client(transcription_router.router) as c:
        r = await c.post(
            f"/api/transcription/queue/{item.id}/requeue", headers=auth_header(editor)
        )
    assert r.status_code == 409


async def test_requeue_returns_the_active_item_when_the_pair_is_queued_again(
    db, make_client, make_user, auth_header
):
    """`add_to_queue` dedups on an active row, so retrying twice is idempotent."""
    editor = await make_user(username="ed", role="editor")
    pair = await make_book_pair(db)
    failed = await _seed_item(db, pair.id, status="failed")
    active = await _seed_item(db, pair.id, status="pending")

    async with make_client(transcription_router.router) as c:
        r = await c.post(
            f"/api/transcription/queue/{failed.id}/requeue", headers=auth_header(editor)
        )

    assert r.status_code == 200
    assert r.json()["id"] == active.id


async def test_requeue_requires_editor(db, make_client, make_user, auth_header):
    user = await make_user(username="u", role="user")
    pair = await make_book_pair(db)
    item = await _seed_item(db, pair.id, status="failed")

    async with make_client(transcription_router.router) as c:
        r = await c.post(
            f"/api/transcription/queue/{item.id}/requeue", headers=auth_header(user)
        )
    assert r.status_code == 403


async def test_requeue_404s_for_an_unknown_item(db, make_client, make_user, auth_header):
    editor = await make_user(username="ed", role="editor")
    async with make_client(transcription_router.router) as c:
        r = await c.post("/api/transcription/queue/9999/requeue", headers=auth_header(editor))
    assert r.status_code == 404


async def test_requeue_409s_when_the_pair_vanished_under_it(
    db, make_client, make_user, auth_header, monkeypatch
):
    """Defensive path: `add_to_queue` skips a pair it cannot find, so a pair
    deleted between the history row and this call must not 200 with nothing
    queued."""
    from services import queue_manager

    editor = await make_user(username="ed", role="editor")
    pair = await make_book_pair(db)
    item = await _seed_item(db, pair.id, status="failed")

    async def _nothing_created(pair_ids, db=None):
        return []

    async def _no_active_item(pair_id):
        return None

    monkeypatch.setattr(queue_manager, "add_to_queue", _nothing_created)
    monkeypatch.setattr(queue_manager, "get_queue_item_for_pair", _no_active_item)

    async with make_client(transcription_router.router) as c:
        r = await c.post(
            f"/api/transcription/queue/{item.id}/requeue", headers=auth_header(editor)
        )

    assert r.status_code == 409
    assert "no longer exists" in r.json()["detail"]


# ---------------------------------------------------------------------------
# GET /api/transcription/queue/history — bounded (issue #208)
#
# `limit: int = 50` was a plain int with no `Query(...)` bounds, so
# `?limit=10000000` was a valid request that loaded that many rows and then ran
# a per-row pair lookup on each. Every browse listing in the API is capped at
# 500; this one was capped at nothing.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "query,expected",
    [
        ("limit=10000", 422),   # over the ceiling
        ("limit=0", 422),       # a page of nothing is a client bug
        ("limit=-1", 422),
        ("offset=-1", 422),     # negative offsets are not a paging strategy
        ("limit=200", 200),     # the ceiling itself is allowed
        ("limit=1&offset=0", 200),
    ],
)
async def test_queue_history_limit_and_offset_are_bounded(
    db, make_client, make_user, auth_header, query, expected
):
    user = await make_user(username=f"hist{abs(hash(query)) % 10000}")

    async with make_client(transcription_router.router) as c:
        r = await c.get(
            f"/api/transcription/queue/history?{query}", headers=auth_header(user)
        )

    assert r.status_code == expected


async def test_queue_history_is_rate_limited(
    db, make_client, make_user, auth_header, monkeypatch
):
    from config import settings

    user = await make_user(username="histrl")
    monkeypatch.setattr(settings, "search_read_limit", 2)

    async with make_client(transcription_router.router) as c:
        codes = [
            (
                await c.get(
                    "/api/transcription/queue/history", headers=auth_header(user)
                )
            ).status_code
            for _ in range(3)
        ]
        over = await c.get(
            "/api/transcription/queue/history", headers=auth_header(user)
        )

    assert codes == [200, 200, 429]
    assert int(over.headers["Retry-After"]) > 0
