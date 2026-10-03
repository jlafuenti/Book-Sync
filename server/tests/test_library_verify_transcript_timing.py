"""Library verify checks each cached transcript against its audio file.

`services.transcript_timing` decides whether a transcript is in step with the
file; this is the verify scan's phase that runs it for every pair with a
transcript and stores the verdict as a pair-level `LibraryCheckResult`.

The stored verdict is about one transcript *and* one file. It is re-checked
when the file changes (size/mtime, like the integrity checks) and also when
the transcript is newer than the verdict: re-transcribing a flagged pair
replaces the transcript without touching the file, and a cache keyed on the
file alone would keep reporting the old, fixed problem forever.
"""

import datetime
import threading

from sqlalchemy import select

from models.book import AudioBook, BookPair, EBook, PairStatus
from models.library_issue import LibraryCheckResult
from models.transcript import AudioTranscript
from services import library_verify
from services.transcript_timing import CHECK_TYPE, TimingVerdict


async def _pair_with_transcript(db, tmp_path, *, name="book", transcript=True):
    audio = tmp_path / f"{name}.m4b"
    audio.write_bytes(b"x" * 4096)
    eb = EBook(title=name, author="Someone", filename=f"{name}.epub",
               file_path=str(tmp_path / f"{name}.epub"), format="epub")
    ab = AudioBook(title=name, author="Someone", filename=audio.name,
                   file_path=str(audio), format="m4b", duration_seconds=36000)
    db.add_all([eb, ab])
    await db.flush()
    pair = BookPair(ebook_id=eb.id, audiobook_id=ab.id, status=PairStatus.SYNCED)
    db.add(pair)
    await db.flush()
    if transcript:
        db.add(AudioTranscript(
            pair_id=pair.id, audiobook_path=str(audio), sentence_count=1,
            sentences_json='[{"text": "a", "start_ms": 0, "end_ms": 1000}]',
            created_at=datetime.datetime(2026, 1, 1),
        ))
    await db.commit()
    return pair, ab


async def _row(pair_id):
    from database import async_session
    async with async_session() as s:
        return (await s.execute(select(LibraryCheckResult).where(
            LibraryCheckResult.item_type == "pair",
            LibraryCheckResult.item_id == pair_id,
            LibraryCheckResult.check_type == CHECK_TYPE,
        ))).scalar_one_or_none()


def _fake_check(verdict, calls):
    def check(audio_path, sentences_json, duration_s):
        calls.append((audio_path, threading.current_thread()))
        return verdict
    return check


async def test_an_out_of_step_transcript_is_stored_as_a_failed_pair_check(db, tmp_path, monkeypatch):
    pair, ab = await _pair_with_transcript(db, tmp_path)
    calls = []
    monkeypatch.setattr(library_verify, "check_transcript_timing", _fake_check(
        TimingVerdict(False, "Transcript runs about 4.9 s early", 4.9), calls))

    await library_verify._check_transcript_timings()

    assert [c[0] for c in calls] == [ab.file_path]
    assert calls[0][1] is not threading.main_thread(), "ffmpeg must not run on the event loop"
    row = await _row(pair.id)
    assert row.ok is False
    assert "4.9 s early" in row.detail


async def test_an_in_step_transcript_is_stored_as_passing(db, tmp_path, monkeypatch):
    pair, _ = await _pair_with_transcript(db, tmp_path)
    monkeypatch.setattr(library_verify, "check_transcript_timing",
                        _fake_check(TimingVerdict(True, "In step", 0.0), []))

    await library_verify._check_transcript_timings()

    assert (await _row(pair.id)).ok is True


async def test_cannot_judge_is_not_reported_as_a_problem(db, tmp_path, monkeypatch):
    pair, _ = await _pair_with_transcript(db, tmp_path)
    monkeypatch.setattr(library_verify, "check_transcript_timing",
                        _fake_check(TimingVerdict(None, "Not enough speech"), []))

    await library_verify._check_transcript_timings()

    row = await _row(pair.id)
    assert row.ok is True
    assert "Not enough speech" in row.detail


async def test_a_pair_without_a_transcript_is_skipped(db, tmp_path, monkeypatch):
    pair, _ = await _pair_with_transcript(db, tmp_path, transcript=False)
    calls = []
    monkeypatch.setattr(library_verify, "check_transcript_timing",
                        _fake_check(TimingVerdict(True, "In step", 0.0), calls))

    await library_verify._check_transcript_timings()

    assert calls == []
    assert await _row(pair.id) is None


async def test_an_unchanged_file_and_transcript_is_not_rechecked(db, tmp_path, monkeypatch):
    await _pair_with_transcript(db, tmp_path)
    calls = []
    monkeypatch.setattr(library_verify, "check_transcript_timing",
                        _fake_check(TimingVerdict(False, "early", 4.9), calls))

    await library_verify._check_transcript_timings()
    await library_verify._check_transcript_timings()

    assert len(calls) == 1


async def test_a_new_transcript_is_rechecked_even_though_the_file_is_unchanged(
    db, tmp_path, monkeypatch
):
    """The fix for a flagged pair is a re-transcription, which leaves the
    audio file alone. The verdict must follow the transcript, or the old
    problem would be reported against the new, correct transcript forever."""
    pair, _ = await _pair_with_transcript(db, tmp_path)
    calls = []
    monkeypatch.setattr(library_verify, "check_transcript_timing",
                        _fake_check(TimingVerdict(False, "early", 4.9), calls))
    await library_verify._check_transcript_timings()

    t = (await db.execute(
        select(AudioTranscript).where(AudioTranscript.pair_id == pair.id))).scalar_one()
    t.created_at = (await _row(pair.id)).checked_at + datetime.timedelta(seconds=1)
    await db.commit()
    monkeypatch.setattr(library_verify, "check_transcript_timing",
                        _fake_check(TimingVerdict(True, "In step", 0.0), calls))

    await library_verify._check_transcript_timings()

    assert len(calls) == 2
    assert (await _row(pair.id)).ok is True


async def test_the_scan_names_the_phase():
    assert "Checking transcript timing" in library_verify._PHASES
