"""Troubleshoot lists pairs whose transcript is out of step with their audio.

Library verify stores the verdict (`services.transcript_timing`,
`test_library_verify_transcript_timing.py`); the issues endpoint reports a
failed one as a pair row. A verdict older than the pair's current transcript
is about a transcript that no longer exists, since re-transcribing replaced it,
so it is not reported, even before the next verify re-checks it.
"""

import datetime

import pytest

from models.library_issue import LibraryCheckResult
from models.transcript import AudioTranscript
from routers import troubleshoot
from services.transcript_timing import CHECK_TYPE
from tests.factories import make_book_pair


async def _issues(make_client, user, auth_header):
    async with make_client(troubleshoot.router) as c:
        resp = await c.get("/api/troubleshoot/issues", headers=auth_header(user))
    assert resp.status_code == 200, resp.text
    return resp.json()["categories"]


@pytest.fixture
async def editor(make_user):
    return await make_user(role="editor")


async def _flagged_pair(db, *, transcribed_at, checked_at, ok=False):
    pair = await make_book_pair(db)
    db.add(AudioTranscript(
        pair_id=pair.id, audiobook_path="/audio/book.m4b", sentence_count=1,
        sentences_json="[]", created_at=transcribed_at,
    ))
    db.add(LibraryCheckResult(
        item_type="pair", item_id=pair.id, check_type=CHECK_TYPE, ok=ok,
        detail="Transcript runs about 4.9 s early against this audio file",
        checked_at=checked_at,
    ))
    await db.commit()
    return pair


async def test_an_out_of_step_pair_is_listed_with_its_detail(db, make_client, editor, auth_header):
    pair = await _flagged_pair(db, transcribed_at=datetime.datetime(2026, 1, 1),
                               checked_at=datetime.datetime(2026, 2, 1))

    rows = (await _issues(make_client, editor, auth_header))["transcript_out_of_step"]

    assert [r["pair_id"] for r in rows] == [pair.id]
    assert "4.9 s early" in rows[0]["detail"]
    assert rows[0]["audiobook_id"] == pair.audiobook_id


async def test_a_verdict_older_than_the_transcript_is_not_listed(db, make_client, editor, auth_header):
    await _flagged_pair(db, transcribed_at=datetime.datetime(2026, 3, 1),
                        checked_at=datetime.datetime(2026, 2, 1))

    rows = (await _issues(make_client, editor, auth_header))["transcript_out_of_step"]

    assert rows == []


async def test_a_passing_verdict_is_not_listed(db, make_client, editor, auth_header):
    await _flagged_pair(db, transcribed_at=datetime.datetime(2026, 1, 1),
                        checked_at=datetime.datetime(2026, 2, 1), ok=True)

    rows = (await _issues(make_client, editor, auth_header))["transcript_out_of_step"]

    assert rows == []
