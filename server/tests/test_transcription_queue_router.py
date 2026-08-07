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
