"""
Re-align applies the transcript-coverage floor the queue applies (issue #814).

Since #796 the queue refuses to sync a transcript that covers under half of
the audio file's stated length, and keeps it cached so the worker's hours
aren't lost. Re-align (and the bulk sync-map rebuild that goes through it)
rebuilt a map from that same cached transcript without asking, so one click
undid the refusal. It now refuses the same way, records the 50-90% flag the
queue records, and clears it when the transcript covers the file. Convert
still realigns regardless, as it does for a rejected transcript (#794),
because a converted ebook needs a map in its own coordinates.
"""

import json

import pytest
from sqlalchemy import select

from models.library_issue import LibraryCheckResult
from models.sync_map import SyncMap
from models.transcript import AudioTranscript
from services.audio_change import TRANSCRIPT_COVERAGE_CHECK_TYPE
from services.epub_parser import EpubSentence
from services.realign import (
    RealignError,
    TranscriptTooShort,
    realign_pair_from_cached_transcript,
)
from tests.factories import make_book_pair

# The transcript ends at 18 s; the tests vary the file's stated length.
TRANSCRIPT = [
    ("the quick brown fox jumps over the lazy dog", 0),
    ("pack my box with five dozen liquor jugs", 5_000),
    ("how vexingly quick daft zebras jump", 10_000),
    ("sphinx of black quartz judge my vow", 15_000),
]
EPUB = [
    EpubSentence(chapter=0, sentence_index=i, text=text)
    for i, (text, _ms) in enumerate(TRANSCRIPT)
]


@pytest.fixture(autouse=True)
def _epub(monkeypatch):
    monkeypatch.setattr("services.realign.extract_book_sentences", lambda _p: list(EPUB))


async def _pair(db, duration_seconds):
    pair = await make_book_pair(db, duration_seconds=duration_seconds)
    db.add(AudioTranscript(
        pair_id=pair.id, audiobook_path="/x/a.m4b", sentence_count=len(TRANSCRIPT),
        sentences_json=json.dumps([
            {"text": t, "start_ms": ms, "end_ms": ms + 3_000} for t, ms in TRANSCRIPT
        ]),
    ))
    await db.commit()
    return pair


async def _map(db, pair_id):
    return (await db.execute(
        select(SyncMap).where(SyncMap.book_pair_id == pair_id))).scalar_one_or_none()


async def _flag(db, pair_id):
    return (await db.execute(select(LibraryCheckResult).where(
        LibraryCheckResult.item_type == "pair",
        LibraryCheckResult.item_id == pair_id,
        LibraryCheckResult.check_type == TRANSCRIPT_COVERAGE_CHECK_TYPE,
    ).execution_options(populate_existing=True))).scalar_one_or_none()


async def test_a_transcript_far_short_of_the_file_is_refused(db):
    pair = await _pair(db, duration_seconds=450)  # 18 s of 450 s: 4%

    with pytest.raises(TranscriptTooShort) as exc_info:
        await realign_pair_from_cached_transcript(db, pair.id)

    assert isinstance(exc_info.value, RealignError)
    assert exc_info.value.status_code == 409
    assert "covers only 4%" in exc_info.value.detail
    assert "Re-transcribe from scratch" in exc_info.value.detail
    assert await _map(db, pair.id) is None


async def test_a_partial_transcript_realigns_and_is_flagged(db):
    pair = await _pair(db, duration_seconds=26)  # 18 s of 26 s: 69%

    await realign_pair_from_cached_transcript(db, pair.id)
    await db.commit()

    assert await _map(db, pair.id) is not None
    flag = await _flag(db, pair.id)
    assert flag is not None and flag.ok is False
    assert "covers only 69%" in flag.detail


async def test_a_full_transcript_clears_an_earlier_flag(db):
    pair = await _pair(db, duration_seconds=18)  # 100%
    db.add(LibraryCheckResult(item_type="pair", item_id=pair.id,
                              check_type=TRANSCRIPT_COVERAGE_CHECK_TYPE,
                              ok=False, detail="old flag"))
    await db.commit()

    await realign_pair_from_cached_transcript(db, pair.id)
    await db.commit()

    flag = await _flag(db, pair.id)
    assert flag.ok is True and flag.detail is None


async def test_a_full_transcript_never_flagged_writes_no_row(db):
    pair = await _pair(db, duration_seconds=18)

    await realign_pair_from_cached_transcript(db, pair.id)
    await db.commit()

    assert await _flag(db, pair.id) is None


async def test_an_unknown_length_is_not_judged(db):
    pair = await _pair(db, duration_seconds=None)

    await realign_pair_from_cached_transcript(db, pair.id)
    await db.commit()

    assert await _map(db, pair.id) is not None
    assert await _flag(db, pair.id) is None


async def test_convert_realigns_a_short_transcript_anyway(db):
    pair = await _pair(db, duration_seconds=450)

    await realign_pair_from_cached_transcript(db, pair.id, allow_rejected=True)
    await db.commit()

    assert await _map(db, pair.id) is not None


async def test_the_realign_endpoint_answers_409_with_the_reason(
    db, make_client, make_user, auth_header
):
    from routers import transcription

    editor = await make_user(role="editor")
    pair = await _pair(db, duration_seconds=450)

    async with make_client(transcription.router) as c:
        resp = await c.post(f"/api/transcription/{pair.id}/realign", headers=auth_header(editor))

    assert resp.status_code == 409
    assert "covers only 4%" in resp.json()["detail"]
