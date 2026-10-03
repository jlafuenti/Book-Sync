"""A cached transcript known to be wrong is never built from again (issue #794).

Two things mark a pair's cached transcript as unusable:

* Library verify's timing check failed it (`transcript_timing.CHECK_TYPE`,
  issue #797): its timestamps are out of step with the audio file.
* An admin asked for a fresh transcription (`REJECTED_CHECK_TYPE`), for a
  transcript the check cannot see is wrong, such as one decoded with #795's
  drift, which only stretches part of each chunk.

Either way the verdict counts only while it is at least as new as the
transcript; re-transcribing replaces the transcript and retires it. While it
counts, realign and the sync-map rebuild refuse to build from that transcript,
because they would only rebuild the same error. Convert still realigns: a
converted ebook needs a map in its own coordinates, offset or not.
"""

import datetime

import pytest
from sqlalchemy import select

from models.library_issue import LibraryCheckResult
from models.transcript import AudioTranscript
from models.transcription_queue import TranscriptionQueueItem
from routers import transcription
from services import transcript_timing
from services.realign import TranscriptRejected, realign_pair_from_cached_transcript
from tests.factories import make_book_pair

T0 = datetime.datetime(2026, 1, 1)


async def _pair_with_transcript(db, *, transcribed_at=T0):
    pair = await make_book_pair(db)
    db.add(AudioTranscript(
        pair_id=pair.id, audiobook_path="/x/a.m4b", sentence_count=1,
        sentences_json='[{"text": "a", "start_ms": 0, "end_ms": 1000}]',
        created_at=transcribed_at,
    ))
    await db.commit()
    return pair


async def _verdict(db, pair_id, *, check_type=transcript_timing.CHECK_TYPE, ok=False,
                   checked_at=T0 + datetime.timedelta(days=1), detail="Transcript runs about 4.8 s early"):
    db.add(LibraryCheckResult(item_type="pair", item_id=pair_id, check_type=check_type,
                              ok=ok, detail=detail, checked_at=checked_at))
    await db.commit()


async def _transcript(db, pair_id):
    return (await db.execute(
        select(AudioTranscript).where(AudioTranscript.pair_id == pair_id))).scalar_one()


# ---------------------------------------------------------------------------
# The rule
# ---------------------------------------------------------------------------


async def test_a_failed_timing_verdict_rejects_the_transcript(db):
    pair = await _pair_with_transcript(db)
    await _verdict(db, pair.id)
    reason = await transcript_timing.current_rejection(db, pair.id, await _transcript(db, pair.id))
    assert "4.8 s early" in reason


async def test_a_retranscription_request_rejects_the_transcript(db):
    pair = await _pair_with_transcript(db)
    await _verdict(db, pair.id, check_type=transcript_timing.REJECTED_CHECK_TYPE,
                   detail="Re-transcription requested")
    assert await transcript_timing.current_rejection(db, pair.id, await _transcript(db, pair.id))


async def test_a_verdict_older_than_the_transcript_does_not_count(db):
    pair = await _pair_with_transcript(db, transcribed_at=T0 + datetime.timedelta(days=2))
    await _verdict(db, pair.id)
    assert await transcript_timing.current_rejection(db, pair.id, await _transcript(db, pair.id)) is None


async def test_a_passing_verdict_does_not_reject(db):
    pair = await _pair_with_transcript(db)
    await _verdict(db, pair.id, ok=True, detail="In step")
    assert await transcript_timing.current_rejection(db, pair.id, await _transcript(db, pair.id)) is None


# ---------------------------------------------------------------------------
# Realign
# ---------------------------------------------------------------------------


async def test_realign_refuses_a_rejected_transcript(db, make_client, make_user, auth_header):
    editor = await make_user(role="editor")
    pair = await _pair_with_transcript(db)
    await _verdict(db, pair.id)

    async with make_client(transcription.router) as c:
        resp = await c.post(f"/api/transcription/{pair.id}/realign", headers=auth_header(editor))

    assert resp.status_code == 409, resp.text
    detail = resp.json()["detail"]
    assert "4.8 s early" in detail
    assert "re-queue" in detail.lower()


async def test_convert_still_realigns_a_rejected_transcript(db, monkeypatch):
    """The Convert flow passes `allow_rejected`: it gets past the rejection
    gate and fails later, on the (absent) ebook file, not on the verdict."""
    pair = await _pair_with_transcript(db)
    await _verdict(db, pair.id)

    def _unreadable(path):
        raise OSError("no such file")

    monkeypatch.setattr("services.realign.extract_book_sentences", _unreadable)
    with pytest.raises(Exception) as exc_info:
        await realign_pair_from_cached_transcript(db, pair.id, allow_rejected=True)
    assert not isinstance(exc_info.value, TranscriptRejected)
    assert "no such file" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Re-transcribe from scratch
# ---------------------------------------------------------------------------


async def test_retranscribe_is_admin_only(db, make_client, make_user, auth_header):
    editor = await make_user(role="editor")
    pair = await _pair_with_transcript(db)

    async with make_client(transcription.router) as c:
        resp = await c.post(f"/api/transcription/{pair.id}/retranscribe", headers=auth_header(editor))

    assert resp.status_code == 403


async def test_retranscribe_rejects_the_transcript_and_queues_the_pair(
    db, make_client, make_user, auth_header
):
    admin = await make_user(role="admin")
    pair = await _pair_with_transcript(db)

    async with make_client(transcription.router) as c:
        resp = await c.post(f"/api/transcription/{pair.id}/retranscribe", headers=auth_header(admin))

    assert resp.status_code == 200, resp.text
    assert resp.json()["pair_id"] == pair.id
    assert await transcript_timing.current_rejection(db, pair.id, await _transcript(db, pair.id))
    queued = (await db.execute(select(TranscriptionQueueItem).where(
        TranscriptionQueueItem.book_pair_id == pair.id,
        TranscriptionQueueItem.status == "pending"))).scalars().all()
    assert len(queued) == 1
    # The old transcript stays until the new one replaces it, so a failed or
    # cancelled job leaves the pair no worse off.
    assert (await _transcript(db, pair.id)).sentence_count == 1


async def test_retranscribe_an_unknown_pair_is_404(db, make_client, make_user, auth_header):
    admin = await make_user(role="admin")
    async with make_client(transcription.router) as c:
        resp = await c.post("/api/transcription/987654/retranscribe", headers=auth_header(admin))
    assert resp.status_code == 404
