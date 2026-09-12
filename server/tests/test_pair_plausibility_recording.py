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
from services.pair_plausibility import CHECK_TYPE, record_pair_plausibility


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
    assert rows[0].detail is None


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
    assert rows[0].detail is None


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
