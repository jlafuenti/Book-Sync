"""
Splitter-version marker and the admin rebuild job (issue #774).

A change to how EPUB text is split into sentences shifts `sentence_index`, so
every sync map built under the old split keeps naming the old coordinates until
it is realigned from its cached transcript. Each map records which splitter
version built it (`sync_maps.splitter_version`), and
`services/sync_map_rebuild.py` rebuilds the outdated ones in the background,
with a dry-run mode, behind admin-only endpoints. Nothing rebuilds on its own.

Fixture text is invented; the extractor is patched, so no EPUB is ever opened.
"""

import asyncio
import json
import threading

import pytest
from sqlalchemy import select, update

from database import async_session
from models.book import BookPair, EBook, PairStatus
from models.bookmark import Bookmark, BookmarkSource
from models.sync_map import SyncMap, SyncPoint
from models.transcript import AudioTranscript
from models.transcription_queue import TranscriptionQueueItem
from services import sync_map_rebuild
from services.alignment import AlignedPoint
from services.epub_parser import SENTENCE_SPLITTER_VERSION, EpubSentence
from services.sync_engine import save_sync_map, save_sync_map_with_result
from utils import utcnow

from tests.factories import ensure_users, make_book_pair

# What the transcript says, in order.
TRANSCRIPT = [
    ("the quick brown fox jumps over the lazy dog", 0),
    ("pack my box with five dozen liquor jugs", 5_000),
    ("how vexingly quick daft zebras jump", 10_000),
    ("sphinx of black quartz judge my vow", 15_000),
]

# The old split had an extra leading sentence in each chapter, so every index
# in the old map sits one higher than the index the current split gives it.
OLD_POINTS = [
    ("a wizard job is to vex chumps quickly in fog", 0, 0, 0),
    ("the quick brown fox jumps over the lazy dog", 0, 1, 0),
    ("pack my box with five dozen liquor jugs", 0, 2, 5_000),
    ("bright vixens jump dozy fowl quack", 1, 0, 8_000),
    ("how vexingly quick daft zebras jump", 1, 1, 10_000),
    ("sphinx of black quartz judge my vow", 1, 2, 15_000),
]

NEW_EPUB = [
    EpubSentence(chapter=0, sentence_index=0, text=TRANSCRIPT[0][0]),
    EpubSentence(chapter=0, sentence_index=1, text=TRANSCRIPT[1][0]),
    EpubSentence(chapter=1, sentence_index=0, text=TRANSCRIPT[2][0]),
    EpubSentence(chapter=1, sentence_index=1, text=TRANSCRIPT[3][0]),
]


@pytest.fixture(autouse=True)
async def _clean_runner(db, monkeypatch):
    await ensure_users(db, 1, 2)
    sync_map_rebuild._reset()
    monkeypatch.setattr(sync_map_rebuild, "PAIR_PAUSE_SECONDS", 0)
    monkeypatch.setattr(
        "services.realign.extract_book_sentences", lambda _p: list(NEW_EPUB)
    )
    yield
    await sync_map_rebuild.wait()
    sync_map_rebuild._reset()


def _aligned(points):
    return [
        AlignedPoint(
            epub_chapter=ch, epub_sentence_index=si, epub_text_preview=text,
            audio_start_ms=ms, audio_end_ms=ms + 1_000, confidence=1.0,
        )
        for text, ch, si, ms in points
    ]


async def _seed(db, *, status=PairStatus.SYNCED, transcript=True, outdated=True,
                bookmark=False):
    """A pair carrying the OLD map (marked outdated unless told otherwise)."""
    pair = await make_book_pair(db, status=status)
    if transcript:
        db.add(AudioTranscript(
            pair_id=pair.id,
            audiobook_path="/x/a.m4b",
            sentence_count=len(TRANSCRIPT),
            sentences_json=json.dumps([
                {"text": t, "start_ms": ms, "end_ms": ms + 3_000}
                for t, ms in TRANSCRIPT
            ]),
        ))
    await save_sync_map(db, pair.id, _aligned(OLD_POINTS))
    if outdated:
        await db.execute(
            update(SyncMap).where(SyncMap.book_pair_id == pair.id)
            .values(splitter_version=1)
        )
    if bookmark:
        db.add(Bookmark(
            user_id=1, book_pair_id=pair.id, source=BookmarkSource.EBOOK,
            epub_chapter=0, epub_sentence_index=2,
            epub_text_preview="pack my box with five dozen liquor jugs",
            epub_progress_percent=42.0, audio_position_ms=5_000,
            anchor_revision=7, captured_at=utcnow(), sync_map_version=1,
        ))
    await db.commit()
    return pair.id


async def _map(pair_id):
    async with async_session() as s:
        return (await s.execute(
            select(SyncMap).where(SyncMap.book_pair_id == pair_id)
        )).scalar_one()


async def _bookmark(pair_id):
    async with async_session() as s:
        return (await s.execute(
            select(Bookmark).where(Bookmark.book_pair_id == pair_id)
        )).scalar_one()


async def _run(**kwargs):
    kwargs.setdefault("dry_run", False)
    kwargs.setdefault("pair_ids", None)
    kwargs.setdefault("limit", None)
    await sync_map_rebuild.start(**kwargs)
    await sync_map_rebuild.wait()
    return sync_map_rebuild.status_snapshot()


# ---------------------------------------------------------------------------
# The marker
# ---------------------------------------------------------------------------

async def test_save_sync_map_stamps_the_current_splitter_version(db):
    pair = await make_book_pair(db)
    sm = await save_sync_map(db, pair.id, _aligned(OLD_POINTS))
    await db.commit()
    assert sm.splitter_version == SENTENCE_SPLITTER_VERSION
    assert SENTENCE_SPLITTER_VERSION >= 2


async def test_a_row_inserted_without_the_field_is_version_one(db):
    pair = await make_book_pair(db)
    db.add(SyncMap(book_pair_id=pair.id, version=1, total_sentences=1,
                   total_chapters=1))
    await db.commit()
    assert (await _map(pair.id)).splitter_version == 1


async def test_save_with_result_reports_the_remap(db):
    pair_id = await _seed(db, bookmark=True, outdated=False)
    # Re-saving over the old map with the current split.
    _sm, result = await save_sync_map_with_result(
        db, pair_id,
        _aligned([(t, 0 if i < 2 else 1, i % 2, ms)
                  for i, (t, ms) in enumerate(TRANSCRIPT)]),
    )
    await db.commit()
    assert result.version == 2
    assert result.bookmarks == 1
    assert result.bookmarks_remapped == 1


async def test_first_ever_map_counts_no_bookmarks(db):
    pair = await make_book_pair(db)
    _sm, result = await save_sync_map_with_result(
        db, pair.id, _aligned(OLD_POINTS)
    )
    assert (result.version, result.bookmarks, result.bookmarks_remapped) == (1, 0, 0)


async def test_count_outdated_counts_only_synced_pairs_below_current(db):
    await _seed(db)                                    # outdated, synced
    await _seed(db)                                    # outdated, synced
    await _seed(db, outdated=False)                    # current
    await _seed(db, status=PairStatus.ERROR)           # outdated but not synced
    assert await sync_map_rebuild.count_outdated(db) == (2, 3)


# ---------------------------------------------------------------------------
# A real run
# ---------------------------------------------------------------------------

async def test_real_run_rebuilds_outdated_maps_and_remaps_bookmarks(db):
    a = await _seed(db, bookmark=True)
    b = await _seed(db)
    current = await _seed(db, outdated=False)
    current_version = (await _map(current)).version

    snap = await _run()

    assert [r["outcome"] for r in snap["results"]] == ["rebuilt", "rebuilt"]
    assert [r["pair_id"] for r in snap["results"]] == [a, b]
    assert snap["to_process"] == 2 and snap["processed"] == 2
    assert snap["running"] is False and snap["finished_at"] is not None

    for pid in (a, b):
        sm = await _map(pid)
        assert sm.splitter_version == SENTENCE_SPLITTER_VERSION
        assert sm.version == 2
        assert sm.total_sentences == 4
    # The up-to-date pair was left alone.
    assert (await _map(current)).version == current_version

    first = snap["results"][0]
    assert first["old_points"] == 6 and first["new_points"] == 4
    assert first["bookmarks"] == 1 and first["bookmarks_remapped"] == 1
    # The bookmark followed its sentence from index 2 to index 1.
    bm = await _bookmark(a)
    assert (bm.epub_chapter, bm.epub_sentence_index) == (0, 1)

    async with async_session() as s:
        assert await sync_map_rebuild.count_outdated(s) == (0, 3)


async def test_rows_carry_multiline_counts(db):
    pair_id = await _seed(db)
    await db.execute(
        update(SyncPoint).where(
            SyncPoint.epub_text_preview.like("pack my box%")
        ).values(epub_text_preview="pack my box\nwith five dozen liquor jugs")
    )
    await db.commit()
    snap = await _run()
    assert snap["results"][0]["pair_id"] == pair_id
    assert snap["results"][0]["old_multiline_points"] == 1


# ---------------------------------------------------------------------------
# Dry run
# ---------------------------------------------------------------------------

async def test_dry_run_reports_and_changes_nothing(db):
    pair_id = await _seed(db, bookmark=True)
    before_map = await _map(pair_id)
    before_bm = await _bookmark(pair_id)
    async with async_session() as s:
        before_points = (await s.execute(
            select(SyncPoint.epub_chapter, SyncPoint.epub_sentence_index)
            .where(SyncPoint.sync_map_id == before_map.id)
            .order_by(SyncPoint.id)
        )).all()

    snap = await _run(dry_run=True)

    assert snap["dry_run"] is True
    (row,) = snap["results"]
    assert row["outcome"] == "dry_run"
    assert row["old_points"] == 6 and row["new_points"] == 4
    assert row["bookmarks"] == 1 and row["bookmarks_remapped"] == 1
    assert (snap["succeeded"], snap["failed"], snap["skipped"]) == (1, 0, 0)

    after_map = await _map(pair_id)
    after_bm = await _bookmark(pair_id)
    assert after_map.id == before_map.id
    assert after_map.version == before_map.version
    assert after_map.splitter_version == 1
    async with async_session() as s:
        after_points = (await s.execute(
            select(SyncPoint.epub_chapter, SyncPoint.epub_sentence_index)
            .where(SyncPoint.sync_map_id == after_map.id)
            .order_by(SyncPoint.id)
        )).all()
    assert after_points == before_points
    assert (after_bm.epub_chapter, after_bm.epub_sentence_index,
            after_bm.audio_position_ms, after_bm.sync_map_version) == (
        before_bm.epub_chapter, before_bm.epub_sentence_index,
        before_bm.audio_position_ms, before_bm.sync_map_version)


# ---------------------------------------------------------------------------
# Targets
# ---------------------------------------------------------------------------

async def test_pair_ids_target_an_up_to_date_map_too(db):
    current = await _seed(db, outdated=False)
    outdated = await _seed(db)
    snap = await _run(pair_ids=[current])
    assert [r["pair_id"] for r in snap["results"]] == [current]
    assert snap["results"][0]["outcome"] == "rebuilt"
    # The explicit list replaces the "outdated" selection.
    assert (await _map(outdated)).splitter_version == 1


async def test_limit_truncates_the_outdated_list_in_pair_order(db):
    ids = [await _seed(db) for _ in range(3)]
    snap = await _run(limit=2)
    assert [r["pair_id"] for r in snap["results"]] == ids[:2]
    assert snap["to_process"] == 2
    assert (await _map(ids[2])).splitter_version == 1


# ---------------------------------------------------------------------------
# Skips and failures
# ---------------------------------------------------------------------------

async def test_queued_non_synced_and_untranscribed_pairs_are_skipped(db):
    queued = await _seed(db)
    db.add(TranscriptionQueueItem(book_pair_id=queued, status="pending",
                                  priority=100, progress=0.0, message="x"))
    unsynced = await _seed(db)
    await db.execute(
        update(BookPair).where(BookPair.id == unsynced)
        .values(status=PairStatus.ERROR)
    )
    untranscribed = await _seed(db, transcript=False)
    await db.commit()

    # `unsynced` is not selected by the outdated query, so name it explicitly.
    snap = await _run(pair_ids=[queued, unsynced, untranscribed])

    by_pair = {r["pair_id"]: r for r in snap["results"]}
    assert {p: r["outcome"] for p, r in by_pair.items()} == {
        queued: "skipped", unsynced: "skipped", untranscribed: "skipped",
    }
    assert all(r["detail"] for r in by_pair.values())
    assert snap["skipped"] == 3
    # Nothing changed.
    for pid in (queued, unsynced, untranscribed):
        assert (await _map(pid)).splitter_version == 1


async def test_a_missing_pair_is_skipped(db):
    snap = await _run(pair_ids=[987_654])
    assert snap["results"][0]["outcome"] == "skipped"


async def test_a_non_epub_pair_is_skipped(db):
    pair_id = await _seed(db)
    pair = await db.get(BookPair, pair_id)
    ebook = await db.get(EBook, pair.ebook_id)
    ebook.file_path = "/x/book.mobi"
    await db.commit()
    snap = await _run(pair_ids=[pair_id])
    assert snap["results"][0]["outcome"] == "skipped"


async def test_a_failing_pair_is_recorded_and_does_not_stop_the_run(db, monkeypatch):
    bad = await _seed(db)
    good = await _seed(db)
    calls = {"n": 0}

    def _extract(_p):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom with detail that must not leak")
        return list(NEW_EPUB)

    monkeypatch.setattr("services.realign.extract_book_sentences", _extract)
    before = await _map(bad)

    snap = await _run()

    assert [r["outcome"] for r in snap["results"]] == ["failed", "rebuilt"]
    assert snap["failed"] == 1 and snap["succeeded"] == 1
    # Unexpected exceptions report their class name only.
    assert snap["results"][0]["detail"] == "RuntimeError"
    after = await _map(bad)
    assert (after.id, after.version, after.splitter_version) == (
        before.id, before.version, 1)
    assert (await _map(good)).splitter_version == SENTENCE_SPLITTER_VERSION


async def test_a_file_error_detail_contains_no_path(db, monkeypatch):
    await _seed(db)

    def _extract(_p):
        raise FileNotFoundError("/some/dir/book.epub")

    monkeypatch.setattr("services.realign.extract_book_sentences", _extract)
    snap = await _run()

    (row,) = snap["results"]
    assert row["outcome"] == "failed"
    assert row["detail"]
    assert "/" not in row["detail"] and "\\" not in row["detail"]
    assert "book.epub" not in row["detail"]


# ---------------------------------------------------------------------------
# Cancel and exclusion
# ---------------------------------------------------------------------------

async def test_cancel_stops_before_the_next_pair(db, monkeypatch):
    ids = [await _seed(db) for _ in range(3)]

    def _extract(_p):
        sync_map_rebuild.cancel()
        return list(NEW_EPUB)

    monkeypatch.setattr("services.realign.extract_book_sentences", _extract)
    snap = await _run()

    assert snap["cancelled"] is True
    assert snap["to_process"] == 3 and snap["processed"] == 1
    assert [r["pair_id"] for r in snap["results"]] == [ids[0]]
    assert (await _map(ids[1])).splitter_version == 1


def test_cancel_when_idle_returns_false():
    assert sync_map_rebuild.cancel() is False


async def _hold_a_run_open(db, monkeypatch):
    """Start a run whose extractor blocks until the returned event is set."""
    entered, release = threading.Event(), threading.Event()

    def _extract(_p):
        entered.set()
        assert release.wait(10)
        return list(NEW_EPUB)

    monkeypatch.setattr("services.realign.extract_book_sentences", _extract)
    pair_id = await _seed(db)
    await sync_map_rebuild.start(dry_run=False, pair_ids=None, limit=None)
    for _ in range(500):
        if entered.is_set():
            break
        await asyncio.sleep(0.01)
    assert entered.is_set()
    return pair_id, release


async def test_a_second_start_while_running_raises(db, monkeypatch):
    _pid, release = await _hold_a_run_open(db, monkeypatch)
    try:
        assert sync_map_rebuild.is_running() is True
        with pytest.raises(sync_map_rebuild.AlreadyRunning):
            await sync_map_rebuild.start(dry_run=False, pair_ids=None, limit=None)
    finally:
        release.set()
        await sync_map_rebuild.wait()
    assert sync_map_rebuild.is_running() is False


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

STATUS_KEYS = {
    "current_version", "outdated", "total", "running", "dry_run", "started_at",
    "finished_at", "to_process", "processed", "succeeded", "failed", "skipped",
    "cancelled", "results",
}
RESULT_KEYS = {
    "pair_id", "outcome", "detail", "old_points", "new_points", "matched",
    "old_multiline_points", "bookmarks", "bookmarks_remapped",
}


async def test_status_endpoint_shape_and_counts(db, make_client, make_user, auth_header):
    from routers import troubleshoot
    await _seed(db)
    await _seed(db, outdated=False)
    admin = await make_user(username="adm", role="admin")
    async with make_client(troubleshoot.router) as c:
        r = await c.get("/api/troubleshoot/sync-map-rebuild", headers=auth_header(admin))
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == STATUS_KEYS
    assert body["current_version"] == SENTENCE_SPLITTER_VERSION
    assert (body["outdated"], body["total"]) == (1, 2)
    assert body["running"] is False and body["results"] == []


async def test_start_endpoint_runs_the_job(db, make_client, make_user, auth_header):
    from routers import troubleshoot
    pair_id = await _seed(db)
    admin = await make_user(username="adm", role="admin")
    async with make_client(troubleshoot.router) as c:
        r = await c.post(
            "/api/troubleshoot/sync-map-rebuild",
            json={"dry_run": False, "limit": 5}, headers=auth_header(admin),
        )
        assert r.status_code == 202, r.text
        assert set(r.json()) == STATUS_KEYS
        await sync_map_rebuild.wait()
        r = await c.get("/api/troubleshoot/sync-map-rebuild", headers=auth_header(admin))
    body = r.json()
    assert body["succeeded"] == 1 and body["outdated"] == 0
    assert set(body["results"][0]) == RESULT_KEYS
    assert body["results"][0]["pair_id"] == pair_id
    assert (await _map(pair_id)).splitter_version == SENTENCE_SPLITTER_VERSION


async def test_start_endpoint_rejects_a_zero_limit(make_client, make_user, auth_header):
    from routers import troubleshoot
    admin = await make_user(username="adm", role="admin")
    async with make_client(troubleshoot.router) as c:
        r = await c.post("/api/troubleshoot/sync-map-rebuild",
                         json={"limit": 0}, headers=auth_header(admin))
    assert r.status_code == 422


async def test_an_editor_is_refused_on_all_three_endpoints(
    make_client, make_user, auth_header
):
    from routers import troubleshoot
    editor = await make_user(username="ed", role="editor")
    h = auth_header(editor)
    async with make_client(troubleshoot.router) as c:
        assert (await c.get("/api/troubleshoot/sync-map-rebuild", headers=h)).status_code == 403
        assert (await c.post("/api/troubleshoot/sync-map-rebuild", json={}, headers=h)).status_code == 403
        assert (await c.post("/api/troubleshoot/sync-map-rebuild/cancel", headers=h)).status_code == 403
    assert sync_map_rebuild.is_running() is False


async def test_start_endpoint_409_while_running_and_realign_blocked(
    db, monkeypatch, make_client, make_user, auth_header
):
    from routers import transcription, troubleshoot
    pair_id, release = await _hold_a_run_open(db, monkeypatch)
    admin = await make_user(username="adm", role="admin")
    h = auth_header(admin)
    try:
        async with make_client(troubleshoot.router, transcription.router) as c:
            r = await c.post("/api/troubleshoot/sync-map-rebuild", json={}, headers=h)
            assert r.status_code == 409
            r = await c.post(f"/api/transcription/{pair_id}/realign", headers=h)
            assert r.status_code == 409
            r = await c.get("/api/troubleshoot/sync-map-rebuild", headers=h)
            assert r.json()["running"] is True
    finally:
        release.set()
        await sync_map_rebuild.wait()


async def test_realign_works_again_once_the_run_is_over(
    db, make_client, make_user, auth_header
):
    from routers import transcription
    pair_id = await _seed(db)
    await _run()
    admin = await make_user(username="adm", role="admin")
    async with make_client(transcription.router) as c:
        r = await c.post(f"/api/transcription/{pair_id}/realign",
                         headers=auth_header(admin))
    assert r.status_code == 200, r.text


async def test_cancel_endpoint_returns_status(db, make_client, make_user, auth_header):
    from routers import troubleshoot
    admin = await make_user(username="adm", role="admin")
    async with make_client(troubleshoot.router) as c:
        r = await c.post("/api/troubleshoot/sync-map-rebuild/cancel",
                         headers=auth_header(admin))
    assert r.status_code == 200
    assert set(r.json()) == STATUS_KEYS
