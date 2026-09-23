"""Issue #693 — the "Library verify" scan re-checks previously flagged pairs.

`pair_plausibility.check_pair_plausibility` is only ever recorded at pair
creation (`record_pair_plausibility`, called from the manual-pair endpoint and
`auto_match.py`). Nothing else re-evaluates a stored verdict, so fixing a bug
in the check itself — #693's "a passing word count was overruled by the
imprecise byte check" — did not clear a false positive already sitting on a
running server.

This adds a fourth phase to the existing "Library verify" scan
(`services/library_verify.py`, `POST /api/troubleshoot/scan`) that re-runs
`pair_plausibility` for pairs whose *stored* row currently says `ok == False`,
and only those — a passing pair's row is deliberately left alone, so re-running
the check with a freshly computed word count cannot newly flag an old pair that
has synced fine for months.

Tests call `_recheck_flagged_pairs` directly rather than the whole `_run_scan`
background task, which is already exercised (implicitly) by the router tests;
this file is only about the new phase's own DB effects.
"""

import pytest
from sqlalchemy import select

from models.book import AudioBook, BookPair, EBook, PairStatus
from models.library_issue import LibraryCheckResult
from services import library_verify
from services.pair_plausibility import CHECK_TYPE


async def _pair(db, *, ebook_size, duration, title="A Book"):
    eb = EBook(title=title, author="Someone", filename="book.epub",
               file_path=f"/books/{title}.epub", format="epub", file_size=ebook_size)
    ab = AudioBook(title=title, author="Someone", filename="book.m4b",
                   file_path=f"/audio/{title}.m4b", format="m4b", file_size=4096,
                   duration_seconds=duration)
    db.add(eb)
    db.add(ab)
    await db.flush()
    pair = BookPair(ebook_id=eb.id, audiobook_id=ab.id, status=PairStatus.MANUAL_MATCHED)
    db.add(pair)
    await db.commit()
    await db.refresh(pair)
    return pair, eb, ab


async def _record_failed_row(db, pair, *, detail="stale finding"):
    """Seed a stored `ok=False` row directly, the way an old server would
    already have one sitting from before the #693 fix — bypassing
    `record_pair_plausibility` so the test controls the stored detail/ok
    independent of the (already-fixed) check logic."""
    row = LibraryCheckResult(item_type="pair", item_id=pair.id, check_type=CHECK_TYPE,
                             ok=False, detail=detail)
    db.add(row)
    await db.commit()


async def _row(db, pair_id):
    return (await db.execute(select(LibraryCheckResult).where(
        LibraryCheckResult.item_type == "pair",
        LibraryCheckResult.item_id == pair_id,
        LibraryCheckResult.check_type == CHECK_TYPE,
    ))).scalar_one_or_none()


# ---------------------------------------------------------------------------
# The issue's own numbers: a stored false positive clears
# ---------------------------------------------------------------------------


async def test_a_stale_false_positive_is_cleared(db, monkeypatch):
    """99,844,954 bytes / 14,940 s is ~24 MB/hour — over MAX_BYTES_PER_HOUR,
    which is exactly the shape of the stale flag #693 reports. A word count of
    37,734 is ~9,100 words/hour, squarely plausible, and once the scan can
    compute it, the row must flip to ok=True."""
    pair, eb, ab = await _pair(db, ebook_size=99_844_954, duration=14_940)
    await _record_failed_row(db, pair)

    async def fake_estimate_word_count(path):
        assert path == eb.file_path
        return 37_734

    monkeypatch.setattr(library_verify, "estimate_word_count", fake_estimate_word_count)

    cleared, orphans = await library_verify._recheck_flagged_pairs()

    assert cleared == 1
    assert orphans == 0
    row = await _row(db, pair.id)
    assert row.ok is True
    assert row.detail is None


# ---------------------------------------------------------------------------
# A genuine positive stays flagged
# ---------------------------------------------------------------------------


async def test_a_genuine_positive_stays_flagged(db, monkeypatch):
    """A word count wildly out of the plausible band (an abridgement-shaped
    pair) must still fail after the re-check — the fix only stops a *passing*
    word rate from being overruled, it doesn't loosen anything else."""
    pair, eb, ab = await _pair(db, ebook_size=600_000, duration=int(2.99 * 3600))
    await _record_failed_row(db, pair)

    async def fake_estimate_word_count(path):
        return 87_398  # ~29.3k words/h — well outside the band

    monkeypatch.setattr(library_verify, "estimate_word_count", fake_estimate_word_count)

    cleared, orphans = await library_verify._recheck_flagged_pairs()

    assert cleared == 0
    assert orphans == 0
    row = await _row(db, pair.id)
    assert row.ok is False
    assert row.detail is not None


# ---------------------------------------------------------------------------
# A passing pair is never touched
# ---------------------------------------------------------------------------


async def test_a_passing_pair_is_not_recheck(db, monkeypatch):
    """Only ok=False rows are candidates. A passing row's pair must never even
    reach `estimate_word_count` — re-checking it with a freshly computed word
    count could newly flag an old pair that has synced fine for months."""
    passing_pair, passing_eb, passing_ab = await _pair(
        db, ebook_size=600_000, duration=10 * 3600, title="Passing Book")
    row = LibraryCheckResult(item_type="pair", item_id=passing_pair.id,
                             check_type=CHECK_TYPE, ok=True, detail=None)
    db.add(row)
    await db.commit()

    failing_pair, failing_eb, failing_ab = await _pair(
        db, ebook_size=99_844_954, duration=14_940, title="Failing Book")
    await _record_failed_row(db, failing_pair)

    calls = []

    async def fake_estimate_word_count(path):
        calls.append(path)
        return 37_734

    monkeypatch.setattr(library_verify, "estimate_word_count", fake_estimate_word_count)

    cleared, orphans = await library_verify._recheck_flagged_pairs()

    assert calls == [failing_eb.file_path]
    assert cleared == 1

    passing_row = await _row(db, passing_pair.id)
    assert passing_row.ok is True
    assert passing_row.detail is None


# ---------------------------------------------------------------------------
# An orphaned row (its pair no longer exists) is removed
# ---------------------------------------------------------------------------


async def test_an_orphaned_row_is_removed(db):
    """A stored row for a pair that was later deleted (unpaired, or the ebook/
    audiobook removed) would otherwise sit there forever — Troubleshoot never
    surfaces it (it joins against live pairs), but it still accumulates."""
    pair, eb, ab = await _pair(db, ebook_size=1_200_000, duration=132)
    await _record_failed_row(db, pair)

    # The pair itself is gone, but its check-result row survives (this mirrors
    # what a manual delete leaves behind if nothing sweeps LibraryCheckResult).
    await db.delete(pair)
    await db.commit()

    cleared, orphans = await library_verify._recheck_flagged_pairs()

    assert cleared == 0
    assert orphans == 1
    assert await _row(db, pair.id) is None


# ---------------------------------------------------------------------------
# Issue #699: a re-checked row records when it was re-checked
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("word_count", [37_734, 87_398], ids=["cleared", "still-flagged"])
async def test_the_recheck_advances_checked_at(db, monkeypatch, word_count):
    """Whether the re-check clears the pair or keeps it flagged, the row's
    `checked_at` must move: a cleared pair that still showed its six-day-old
    creation time read as though the re-check never ran."""
    import datetime

    pair, eb, ab = await _pair(db, ebook_size=99_844_954, duration=14_940)
    await _record_failed_row(db, pair)
    stale = datetime.datetime(2020, 1, 1)
    row = await _row(db, pair.id)
    row.checked_at = stale
    await db.commit()

    async def fake_estimate_word_count(path):
        return word_count

    monkeypatch.setattr(library_verify, "estimate_word_count", fake_estimate_word_count)

    pair_id = pair.id
    await library_verify._recheck_flagged_pairs()

    # The re-check wrote through its own session; drop this one's cached row.
    db.expire_all()
    assert (await _row(db, pair_id)).checked_at > stale
