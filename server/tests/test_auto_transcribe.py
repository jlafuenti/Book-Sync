"""
Auto-transcribe queues the pair it just created (issue #199).

`add_to_queue` used to open its own `async_session()` and look the pair up by
id. Both library call sites hand it ids of pairs that exist only in the
*request* transaction — `get_db` commits after the endpoint returns — so on
Postgres the second session could never see them and the only trace was
`WARNING Pair N not found, skipping`. With "auto-transcribe new pairs" enabled,
neither a scan's auto-matches nor a manual pairing ever entered the queue.

The fix is a session parameter: when the caller supplies one, `add_to_queue`
writes into that transaction and flushes only, so the queue row commits (or
rolls back) atomically with the pair. Committing early in the router instead
would split pair creation from the response into two transactions.

Queue rows are read back through a *fresh* `async_session()` — the
`test_queue_manager.py::_get` pattern — so an assertion cannot be satisfied by
the writing session's identity map.
"""

import pytest
from sqlalchemy import select

from database import async_session
from models.book import AudioBook, BookPair, EBook
from models.settings import SystemSetting
from models.transcription_queue import TranscriptionQueueItem


async def _enable_auto_transcribe(db, value="true"):
    db.add(SystemSetting(key="auto_transcribe_enabled", value=value))
    await db.commit()


async def _queue_rows_in_a_fresh_session(pair_id):
    async with async_session() as s:
        return (await s.execute(
            select(TranscriptionQueueItem)
            .where(TranscriptionQueueItem.book_pair_id == pair_id)
        )).scalars().all()


@pytest.fixture
async def library_client(make_client, make_user, auth_header):
    from routers import library

    user = await make_user(role="admin")
    async with make_client(library.router) as c:
        yield c, auth_header(user)


# ---------------------------------------------------------------------------
# (1)/(2) POST /api/library/pairs
# ---------------------------------------------------------------------------

async def test_create_pair_queues_transcription_when_auto_transcribe_is_on(
    db, library_client
):
    c, headers = library_client
    from tests.factories import make_audiobook, make_ebook

    eb = await make_ebook(db)
    ab = await make_audiobook(db)
    await _enable_auto_transcribe(db)

    resp = await c.post(
        "/api/library/pairs",
        json={"ebook_id": eb.id, "audiobook_id": ab.id},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    pair_id = resp.json()["id"]

    rows = await _queue_rows_in_a_fresh_session(pair_id)
    assert len(rows) == 1
    assert rows[0].status == "pending"


async def test_create_pair_queues_nothing_when_the_setting_is_absent_or_false(
    db, library_client
):
    c, headers = library_client
    from tests.factories import make_audiobook, make_ebook

    eb = await make_ebook(db)
    ab = await make_audiobook(db)

    # Setting absent.
    resp = await c.post(
        "/api/library/pairs",
        json={"ebook_id": eb.id, "audiobook_id": ab.id},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    assert await _queue_rows_in_a_fresh_session(resp.json()["id"]) == []

    # Setting explicitly false.
    eb2 = await make_ebook(db, filename="e2.epub")
    ab2 = await make_audiobook(db, filename="a2.m4b")
    await _enable_auto_transcribe(db, "false")

    resp = await c.post(
        "/api/library/pairs",
        json={"ebook_id": eb2.id, "audiobook_id": ab2.id},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    assert await _queue_rows_in_a_fresh_session(resp.json()["id"]) == []


# ---------------------------------------------------------------------------
# (3) auto_match_books()
# ---------------------------------------------------------------------------

async def test_auto_match_queues_the_matched_pair_in_the_callers_session(db):
    from routers.library import auto_match_books

    db.add_all([
        EBook(title="The Long Way", author="Becky Chambers",
              filename="lw.epub", file_path="/x/lw.epub"),
        AudioBook(title="The Long Way", author="Becky Chambers",
                  filename="lw.m4b", file_path="/x/lw.m4b"),
    ])
    await _enable_auto_transcribe(db)

    assert await auto_match_books(db) == 1

    pair_id = (await db.execute(select(BookPair.id))).scalar_one()

    # Visible inside the caller's uncommitted transaction …
    rows = (await db.execute(
        select(TranscriptionQueueItem)
        .where(TranscriptionQueueItem.book_pair_id == pair_id)
    )).scalars().all()
    assert len(rows) == 1

    # … and still there once that transaction commits.
    await db.commit()
    assert len(await _queue_rows_in_a_fresh_session(pair_id)) == 1


# ---------------------------------------------------------------------------
# (4) atomicity
# ---------------------------------------------------------------------------

async def test_a_failure_after_queueing_rolls_back_the_pair_and_the_queue_row(db):
    """No orphan queue rows: the queue write shares the caller's transaction.

    A separate session would have committed the queue item independently, so
    the rollback would leave a `pending` job pointing at a pair that does not
    exist.
    """
    from routers.library import auto_match_books

    db.add_all([
        EBook(title="A Memory Called Empire", author="Arkady Martine",
              filename="amce.epub", file_path="/x/amce.epub"),
        AudioBook(title="A Memory Called Empire", author="Arkady Martine",
                  filename="amce.m4b", file_path="/x/amce.m4b"),
    ])
    await _enable_auto_transcribe(db)

    assert await auto_match_books(db) == 1
    assert (await db.execute(select(TranscriptionQueueItem.id))).scalars().all()

    await db.rollback()

    async with async_session() as s:
        assert (await s.execute(select(BookPair.id))).scalars().all() == []
        assert (await s.execute(
            select(TranscriptionQueueItem.id)
        )).scalars().all() == []
