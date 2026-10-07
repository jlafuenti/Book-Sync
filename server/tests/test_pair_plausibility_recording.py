"""Issue #458 — persisting the pair-plausibility verdict as a LibraryCheckResult.

The pure ratio logic is tested in `test_pair_plausibility.py`. This file covers
the part that touches the database, where the sharp edges are:

* `LibraryCheckResult` is unique on `(item_type, item_id, check_type)`. Re-pairing
  the same two books, or re-running the check after a file is replaced, must
  UPDATE the existing row — a second insert raises IntegrityError.
* A pair that *becomes* plausible has to clear. The troubleshoot page reads rows
  with `ok == False`, so leaving a stale failed row behind means the warning
  never goes away even after the file is fixed.
* The helper must not commit. Both call sites run inside `get_db`'s one
  request-long transaction, and `auto_match` deliberately shares it because its
  pairs are flushed rather than committed (issue #199). A commit in here would
  persist half a bulk match if a later pair raised.
"""

import os

import pytest
from sqlalchemy import select

from models.book import AudioBook, BookPair, EBook, PairStatus
from models.library_issue import LibraryCheckResult
from services.pair_plausibility import (
    CHECK_TYPE,
    MEASURED_DETAIL_PREFIX,
    NOT_MEASURED_DETAIL_PREFIX,
    is_measured_pass,
    record_pair_plausibility,
)


async def _pair(db, ebook_size, duration, is_abridged=False):
    eb = EBook(title="A Novel", author="Someone", filename="novel.epub",
               file_path="/books/novel.epub", format="epub", file_size=ebook_size)
    ab = AudioBook(title="A Novel", author="Someone", filename="novel.m4b",
                   file_path="/audio/novel.m4b", format="m4b", file_size=4096,
                   duration_seconds=duration, is_abridged=is_abridged)
    db.add(eb)
    db.add(ab)
    await db.flush()
    pair = BookPair(ebook_id=eb.id, audiobook_id=ab.id,
                    status=PairStatus.MANUAL_MATCHED)
    db.add(pair)
    await db.flush()
    return pair, eb, ab


async def _rows(db, pair_id):
    return (await db.execute(select(LibraryCheckResult).where(
        LibraryCheckResult.item_type == "pair",
        LibraryCheckResult.item_id == pair_id,
        LibraryCheckResult.check_type == CHECK_TYPE,
    ))).scalars().all()


async def test_an_implausible_pair_records_a_failed_row(db):
    """The reported case: a 1.2 MB novel against 132 seconds of audio."""
    pair, eb, ab = await _pair(db, ebook_size=1_200_000, duration=132)

    await record_pair_plausibility(db, pair, eb, ab)

    rows = await _rows(db, pair.id)
    assert len(rows) == 1
    assert rows[0].ok is False
    # The operator reads this string in Troubleshoot Library; it has to say what
    # disagrees, not merely that something does.
    assert "132 seconds" in rows[0].detail or "2 minutes" in rows[0].detail


async def test_a_plausible_pair_records_a_passing_row(db):
    pair, eb, ab = await _pair(db, ebook_size=600_000, duration=10 * 3600)

    await record_pair_plausibility(db, pair, eb, ab)

    rows = await _rows(db, pair.id)
    assert len(rows) == 1
    assert rows[0].ok is True
    assert rows[0].detail.startswith(NOT_MEASURED_DETAIL_PREFIX)


async def test_running_it_twice_updates_the_row_rather_than_duplicating(db):
    """The unique constraint makes a second insert an IntegrityError."""
    pair, eb, ab = await _pair(db, ebook_size=1_200_000, duration=132)

    await record_pair_plausibility(db, pair, eb, ab)
    await record_pair_plausibility(db, pair, eb, ab)

    assert len(await _rows(db, pair.id)) == 1


async def test_a_pair_that_becomes_plausible_clears_its_finding(db):
    """Replace the truncated file and the warning must actually go away.

    Troubleshoot reads `ok == False`, so a stale failed row would keep showing a
    problem that no longer exists — and an issue list that lies gets ignored.
    """
    pair, eb, ab = await _pair(db, ebook_size=1_200_000, duration=132)
    await record_pair_plausibility(db, pair, eb, ab)
    assert (await _rows(db, pair.id))[0].ok is False

    ab.duration_seconds = 11 * 3600          # the full recording arrives
    await record_pair_plausibility(db, pair, eb, ab)

    rows = await _rows(db, pair.id)
    assert len(rows) == 1
    assert rows[0].ok is True
    assert rows[0].detail.startswith(NOT_MEASURED_DETAIL_PREFIX)


async def test_an_abridged_audiobook_records_no_finding(db):
    pair, eb, ab = await _pair(db, ebook_size=1_200_000, duration=132,
                               is_abridged=True)

    await record_pair_plausibility(db, pair, eb, ab)

    rows = await _rows(db, pair.id)
    assert len(rows) == 1
    assert rows[0].ok is True


async def test_it_does_not_commit(db):
    """Both call sites own the transaction; this helper only flushes.

    `auto_match` creates pairs in a loop and shares one transaction on purpose
    (issue #199). If this committed, a failure on the fifth pair would leave the
    first four permanently half-written.
    """
    pair, eb, ab = await _pair(db, ebook_size=1_200_000, duration=132)

    await record_pair_plausibility(db, pair, eb, ab)
    await db.rollback()

    # Everything above was rolled back together, the check row included.
    assert await _rows(db, pair.id) == []


async def test_rewriting_an_existing_row_advances_checked_at(db):
    """Issue #699: `checked_at` has to say when the verdict was last computed.

    Library verify re-checks flagged pairs (issue #693) and rewrites their rows.
    A row that kept its creation time looked as though the re-check never ran.
    """
    import datetime

    pair, eb, ab = await _pair(db, ebook_size=1_200_000, duration=132)
    await record_pair_plausibility(db, pair, eb, ab)
    row = (await _rows(db, pair.id))[0]
    stale = datetime.datetime(2020, 1, 1)
    row.checked_at = stale
    await db.flush()

    await record_pair_plausibility(db, pair, eb, ab)

    row = (await _rows(db, pair.id))[0]
    assert row.checked_at > stale


# ---------------------------------------------------------------------------
# Issue #798: the EPUB-conversion relink re-points a pair at a different ebook,
# so the verdict has to be taken for the ebook the pair now has.
# ---------------------------------------------------------------------------


async def test_relinking_a_pair_to_a_converted_epub_records_a_verdict(db):
    """A pair re-pointed onto a converted EPUB used to keep whatever verdict
    (or none at all) it had for the source file. The pair that had no row must
    get one, and it describes the new ebook, not the old one."""
    from routers.library import _relink_or_cleanup_pairs
    from tests.factories import make_audiobook, make_ebook

    source = await make_ebook(db, filename="book.mobi", format="mobi", file_size=50_000)
    epub = await make_ebook(db, filename="book.epub", file_size=2_800_000)
    audio = await make_audiobook(db, duration_seconds=132)
    pair = BookPair(ebook_id=source.id, audiobook_id=audio.id, status=PairStatus.SYNCED)
    db.add(pair)
    await db.commit()
    await db.refresh(pair)
    assert await _rows(db, pair.id) == []

    await _relink_or_cleanup_pairs(source.id, epub, db)
    await db.commit()

    rows = await _rows(db, pair.id)
    assert len(rows) == 1
    assert rows[0].ok is False  # 2.8 MB of EPUB against 132 s of audio


async def test_relinking_replaces_a_stale_verdict_for_the_old_file(db):
    from routers.library import _relink_or_cleanup_pairs
    from tests.factories import make_audiobook, make_ebook

    source = await make_ebook(db, filename="book.mobi", format="mobi", file_size=2_800_000)
    epub = await make_ebook(db, filename="book.epub", file_size=600_000)
    audio = await make_audiobook(db, duration_seconds=10 * 3600)
    pair = BookPair(ebook_id=source.id, audiobook_id=audio.id, status=PairStatus.SYNCED)
    db.add(pair)
    await db.flush()
    db.add(LibraryCheckResult(item_type="pair", item_id=pair.id, check_type=CHECK_TYPE,
                              ok=False, detail="about the source file"))
    await db.commit()

    await _relink_or_cleanup_pairs(source.id, epub, db)
    await db.commit()

    rows = await _rows(db, pair.id)
    assert len(rows) == 1
    assert rows[0].ok is True
    assert rows[0].detail.startswith(NOT_MEASURED_DETAIL_PREFIX)


# ---------------------------------------------------------------------------
# Padded audio (issue #796)
# ---------------------------------------------------------------------------


def _real_length(monkeypatch, seconds, calls=None):
    """Stand in for the ffprobe sample count (blocking; run via to_thread)."""
    from services import audio_integrity

    def probe(path):
        if calls is not None:
            calls.append(path)
        return seconds

    monkeypatch.setattr(audio_integrity, "probe_real_audio_seconds", probe)


async def test_a_padded_file_is_recorded_on_its_real_length(db, monkeypatch):
    """Stated 10.3 h, real 2.7 h: 100k words is plausible on the first and not
    on the second."""
    pair, eb, ab = await _pair(db, ebook_size=600_000, duration=int(10.3 * 3600))
    calls = []
    _real_length(monkeypatch, 2.7 * 3600, calls)

    ok = await record_pair_plausibility(db, pair, eb, ab, word_count=100_000)

    assert ok is False
    assert calls == ["/audio/novel.m4b"]
    row = (await _rows(db, pair.id))[0]
    assert row.ok is False
    assert "2.7 hours" in row.detail


async def test_an_unpadded_file_is_recorded_on_its_stated_length(db, monkeypatch):
    pair, eb, ab = await _pair(db, ebook_size=600_000, duration=int(10.3 * 3600))
    _real_length(monkeypatch, 10.3 * 3600 * 0.99)

    ok = await record_pair_plausibility(db, pair, eb, ab, word_count=100_000)

    assert ok is True


async def test_an_unknown_real_length_is_recorded_on_the_stated_length(db, monkeypatch):
    pair, eb, ab = await _pair(db, ebook_size=600_000, duration=int(10.3 * 3600))
    _real_length(monkeypatch, None)

    ok = await record_pair_plausibility(db, pair, eb, ab, word_count=100_000)

    assert ok is True


async def test_no_probe_when_there_is_no_stated_length(db, monkeypatch):
    """Nothing to judge, so no reason to spawn ffprobe."""
    pair, eb, ab = await _pair(db, ebook_size=600_000, duration=None)
    calls = []
    _real_length(monkeypatch, 100.0, calls)

    await record_pair_plausibility(db, pair, eb, ab)

    assert calls == []


# ---------------------------------------------------------------------------
# What a stored pass was based on (issue #833)
# ---------------------------------------------------------------------------
#
# Library verify re-checks a stored pass only when it was not measured by a
# word count, so the row has to say which kind it is. Before #833 every pass
# was `detail=None`, and a byte-band pass or a "cannot judge" looked exactly
# like a real words-per-hour measurement.


async def test_a_word_count_pass_is_recorded_as_measured(db):
    pair, eb, ab = await _pair(db, ebook_size=600_000, duration=10 * 3600)

    await record_pair_plausibility(db, pair, eb, ab, word_count=100_000)

    row = (await _rows(db, pair.id))[0]
    assert row.ok is True
    assert row.detail.startswith(MEASURED_DETAIL_PREFIX)
    assert "100,000 words" in row.detail
    assert "10.0 hours" in row.detail


async def test_a_file_size_pass_is_recorded_as_not_measured(db):
    pair, eb, ab = await _pair(db, ebook_size=600_000, duration=10 * 3600)

    await record_pair_plausibility(db, pair, eb, ab)

    row = (await _rows(db, pair.id))[0]
    assert row.ok is True
    assert row.detail.startswith(NOT_MEASURED_DETAIL_PREFIX)
    assert "file size" in row.detail


async def test_a_pass_without_a_length_is_recorded_as_not_measured(db):
    """No duration yet (#127): the check cannot judge, which is not a pass."""
    pair, eb, ab = await _pair(db, ebook_size=600_000, duration=None)

    await record_pair_plausibility(db, pair, eb, ab, word_count=220_000)

    row = (await _rows(db, pair.id))[0]
    assert row.ok is True
    assert row.detail.startswith(NOT_MEASURED_DETAIL_PREFIX)


async def test_an_abridged_pass_is_recorded_as_not_measured(db):
    pair, eb, ab = await _pair(db, ebook_size=600_000, duration=3 * 3600,
                               is_abridged=True)

    await record_pair_plausibility(db, pair, eb, ab, word_count=220_000)

    row = (await _rows(db, pair.id))[0]
    assert row.ok is True
    assert row.detail.startswith(NOT_MEASURED_DETAIL_PREFIX)


async def test_a_failure_keeps_its_explanation(db):
    """Failing rows read as before: the operator sees them in Troubleshoot."""
    pair, eb, ab = await _pair(db, ebook_size=600_000, duration=int(6.4 * 3600))

    await record_pair_plausibility(db, pair, eb, ab, word_count=220_000)

    row = (await _rows(db, pair.id))[0]
    assert row.ok is False
    assert not row.detail.startswith((MEASURED_DETAIL_PREFIX, NOT_MEASURED_DETAIL_PREFIX))
    assert "words/hour" in row.detail


@pytest.mark.parametrize("ok, detail, expected", [
    (True, "Measured: 100,000 words in 10.0 hours (~10,000 words/hour)", True),
    (True, "Not measured: judged on file size alone", False),
    (True, None, False),            # a row from before #833: basis unknown
    (False, "Measured: looks like one but failed", False),
])
def test_is_measured_pass(ok, detail, expected):
    assert is_measured_pass(ok, detail) is expected
