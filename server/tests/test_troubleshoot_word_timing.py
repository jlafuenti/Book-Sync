"""
Admin endpoints to put the library through a fresh transcription so every
transcript gains word timing (issue #835), and the priority/rejection plumbing
they share with the per-pair re-transcribe.
"""

import pytest
from sqlalchemy import select

from models.library_issue import LibraryCheckResult
from models.transcription_queue import TranscriptionQueueItem
from models.transcript import AudioTranscript
from routers import troubleshoot
from services import queue_manager
from services.transcript_timing import REJECTED_CHECK_TYPE
from tests.factories import make_book_pair

URL = "/api/troubleshoot/word-timing"


@pytest.fixture
async def admin(make_user):
    return await make_user(username="adm", role="admin")


async def _pair(db, *, words):
    pair = await make_book_pair(db)
    db.add(AudioTranscript(
        pair_id=pair.id, audiobook_path="/x/a.m4b", sentence_count=1,
        sentences_json='[{"text": "a", "start_ms": 0, "end_ms": 1000}]',
        words_json='[[["a", 0, 900]]]' if words else None,
    ))
    await db.commit()
    return pair


async def _items(db, pair_id=None):
    stmt = select(TranscriptionQueueItem)
    if pair_id is not None:
        stmt = stmt.where(TranscriptionQueueItem.book_pair_id == pair_id)
    return (await db.execute(stmt.execution_options(populate_existing=True))).scalars().all()


async def test_counts(db, make_client, admin, auth_header):
    await _pair(db, words=True)
    no_words_queued = await _pair(db, words=False)
    await _pair(db, words=False)
    await make_book_pair(db)  # never transcribed: counted nowhere
    db.add(TranscriptionQueueItem(book_pair_id=no_words_queued.id, status="pending"))
    await db.commit()

    async with make_client(troubleshoot.router) as c:
        r = await c.get(URL, headers=auth_header(admin))

    assert r.status_code == 200
    assert r.json() == {"with_words": 1, "without_words": 2, "queued": 1}


async def test_queue_skips_pairs_with_words_and_pairs_already_queued(
    db, make_client, admin, auth_header
):
    with_words = await _pair(db, words=True)
    already = await _pair(db, words=False)
    todo = await _pair(db, words=False)
    db.add(TranscriptionQueueItem(book_pair_id=already.id, status="in_progress"))
    await db.commit()

    async with make_client(troubleshoot.router) as c:
        r = await c.post(f"{URL}/queue", headers=auth_header(admin))

    assert r.status_code == 200
    assert r.json() == {"queued": 1}
    assert await _items(db, with_words.id) == []
    assert len(await _items(db, already.id)) == 1
    (item,) = await _items(db, todo.id)
    assert item.status == "pending"


async def test_queued_rows_get_priority_200(db, make_client, admin, auth_header):
    pair = await _pair(db, words=False)

    async with make_client(troubleshoot.router) as c:
        await c.post(f"{URL}/queue", headers=auth_header(admin))

    (item,) = await _items(db, pair.id)
    assert item.priority == 200


async def test_queue_writes_a_rejection_row_so_the_old_transcript_is_not_reused(
    db, make_client, admin, auth_header
):
    pair = await _pair(db, words=False)

    async with make_client(troubleshoot.router) as c:
        await c.post(f"{URL}/queue", headers=auth_header(admin))

    row = (await db.execute(select(LibraryCheckResult).where(
        LibraryCheckResult.item_type == "pair",
        LibraryCheckResult.item_id == pair.id,
        LibraryCheckResult.check_type == REJECTED_CHECK_TYPE,
    ).execution_options(populate_existing=True))).scalar_one()
    assert row.ok is False


async def test_queue_twice_queues_nothing_the_second_time(db, make_client, admin, auth_header):
    await _pair(db, words=False)
    async with make_client(troubleshoot.router) as c:
        await c.post(f"{URL}/queue", headers=auth_header(admin))
        r = await c.post(f"{URL}/queue", headers=auth_header(admin))
    assert r.json() == {"queued": 0}


@pytest.mark.parametrize("method,path", [("GET", URL), ("POST", f"{URL}/queue")])
async def test_non_admin_is_forbidden(db, make_client, make_user, auth_header, method, path):
    editor = await make_user(username="ed", role="editor")
    async with make_client(troubleshoot.router) as c:
        r = await c.request(method, path, headers=auth_header(editor))
    assert r.status_code == 403


async def test_add_to_queue_defaults_to_priority_100(db):
    pair = await make_book_pair(db)
    (item,) = await queue_manager.add_to_queue([pair.id])
    assert item.priority == 100


async def test_add_to_queue_takes_a_priority(db):
    pair = await make_book_pair(db)
    (item,) = await queue_manager.add_to_queue([pair.id], priority=200)
    assert item.priority == 200


async def test_request_retranscription_flushes_without_committing(db):
    from services.retranscribe import request_retranscription

    pair = await make_book_pair(db)
    await request_retranscription(db, pair.id, detail="Because")
    await db.rollback()

    rows = (await db.execute(select(LibraryCheckResult).where(
        LibraryCheckResult.check_type == REJECTED_CHECK_TYPE))).scalars().all()
    assert rows == []
