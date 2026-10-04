"""Troubleshoot lists pairs that synced from a transcript covering only part of
their audio (issue #796).

The queue records the verdict when it builds a sync map from a transcript
that ends between half and 90% of the way through the file
(`services.audio_change.TRANSCRIPT_COVERAGE_CHECK_TYPE`). Under half, the job
fails instead and the pair shows under failed transcriptions. A verdict older
than the pair's current transcript was about a transcript that has since been
replaced, so it is not listed.
"""

import datetime

import pytest

from models.library_issue import LibraryCheckResult
from models.transcript import AudioTranscript
from routers import troubleshoot
from services.audio_change import TRANSCRIPT_COVERAGE_CHECK_TYPE
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
        item_type="pair", item_id=pair.id, check_type=TRANSCRIPT_COVERAGE_CHECK_TYPE,
        ok=ok, detail="Transcript covers only 70% of the file's stated length",
        checked_at=checked_at,
    ))
    await db.commit()
    return pair


async def test_a_partly_covered_pair_is_listed_with_its_detail(db, make_client, editor, auth_header):
    pair = await _flagged_pair(db, transcribed_at=datetime.datetime(2026, 1, 1),
                               checked_at=datetime.datetime(2026, 2, 1))

    rows = (await _issues(make_client, editor, auth_header))["transcript_partial"]

    assert [r["pair_id"] for r in rows] == [pair.id]
    assert "70%" in rows[0]["detail"]
    assert rows[0]["audiobook_id"] == pair.audiobook_id


async def test_a_verdict_older_than_the_transcript_is_not_listed(db, make_client, editor, auth_header):
    await _flagged_pair(db, transcribed_at=datetime.datetime(2026, 3, 1),
                        checked_at=datetime.datetime(2026, 2, 1))

    rows = (await _issues(make_client, editor, auth_header))["transcript_partial"]

    assert rows == []


async def test_a_passing_verdict_is_not_listed(db, make_client, editor, auth_header):
    await _flagged_pair(db, transcribed_at=datetime.datetime(2026, 1, 1),
                        checked_at=datetime.datetime(2026, 2, 1), ok=True)

    rows = (await _issues(make_client, editor, auth_header))["transcript_partial"]

    assert rows == []


async def test_it_is_not_reported_as_out_of_step(db, make_client, editor, auth_header):
    """A short transcript is a different finding from a drifted one, and it
    must not make the queue discard the cached transcript."""
    await _flagged_pair(db, transcribed_at=datetime.datetime(2026, 1, 1),
                        checked_at=datetime.datetime(2026, 2, 1))

    rows = (await _issues(make_client, editor, auth_header))["transcript_out_of_step"]

    assert rows == []
