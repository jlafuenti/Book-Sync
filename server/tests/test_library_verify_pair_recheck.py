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
and — issue #798 — evaluates pairs with no stored row at all (every pair made
before the check shipped, plus any path that never recorded one). A passing
pair's row is deliberately left alone, so re-running the check with a freshly
computed word count cannot newly flag an old pair that has synced fine for
months; a pair with no row has no such history to protect.

Tests call `_recheck_pair_plausibility` directly rather than the whole `_run_scan`
background task, which is already exercised (implicitly) by the router tests;
this file is only about the new phase's own DB effects.
"""

import pytest
from sqlalchemy import select

from models.book import AudioBook, BookPair, EBook, PairStatus
from models.library_issue import LibraryCheckResult
from services import library_verify
from services.pair_plausibility import (
    CHECK_TYPE,
    MEASURED_DETAIL_PREFIX,
    NOT_MEASURED_DETAIL_PREFIX,
    record_pair_plausibility,
)

# A stored pass that a real word count measured (issue #833). Only these are
# exempt from re-checking; a pass with no basis, or one not measured, is not.
MEASURED = f"{MEASURED_DETAIL_PREFIX} 120,000 words in 10.0 hours (~12,000 words/hour)"


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


async def _fresh_row(db, pair_id):
    """`_row`, after dropping this session's cached rows: the phase under test
    wrote through its own sessions. Take `pair.id` as the argument — it is read
    before the expire, which an expired ORM attribute could not be."""
    db.expire_all()
    return await _row(db, pair_id)


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

    result = await library_verify._recheck_pair_plausibility()

    assert result.cleared == 1
    assert result.orphans_removed == 0
    row = await _row(db, pair.id)
    assert row.ok is True
    assert row.detail.startswith(MEASURED_DETAIL_PREFIX)


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

    result = await library_verify._recheck_pair_plausibility()

    assert result.cleared == 0
    assert result.orphans_removed == 0
    row = await _row(db, pair.id)
    assert row.ok is False
    assert row.detail is not None


# ---------------------------------------------------------------------------
# A passing pair is never touched
# ---------------------------------------------------------------------------


async def test_a_passing_pair_is_not_recheck(db, monkeypatch):
    """A measured pass is not a candidate (issue #833 narrows #693's rule to
    passes a word count actually measured). Its pair must never even
    reach `estimate_word_count` — re-checking it with a freshly computed word
    count could newly flag an old pair that has synced fine for months."""
    passing_pair, passing_eb, passing_ab = await _pair(
        db, ebook_size=600_000, duration=10 * 3600, title="Passing Book")
    row = LibraryCheckResult(item_type="pair", item_id=passing_pair.id,
                             check_type=CHECK_TYPE, ok=True, detail=MEASURED)
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

    result = await library_verify._recheck_pair_plausibility()

    assert calls == [failing_eb.file_path]
    assert result.cleared == 1

    passing_row = await _row(db, passing_pair.id)
    assert passing_row.ok is True
    assert passing_row.detail == MEASURED


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

    result = await library_verify._recheck_pair_plausibility()

    assert result.cleared == 0
    assert result.orphans_removed == 1
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
    await library_verify._recheck_pair_plausibility()

    # The re-check wrote through its own session; drop this one's cached row.
    db.expire_all()
    assert (await _row(db, pair_id)).checked_at > stale


# ---------------------------------------------------------------------------
# Issue #798: a pair with no stored verdict is evaluated, not skipped
# ---------------------------------------------------------------------------


async def test_a_never_checked_implausible_pair_gets_a_failing_row(db, monkeypatch):
    """No row at all used to fall through both the failing-row query and the
    creation-time recording, so the pair could never reach Troubleshoot. The
    issue's own case: ~2 minutes of audio against a full-length ebook."""
    pair, eb, ab = await _pair(db, ebook_size=2_800_000, duration=132)
    eb_path = eb.file_path
    pair_id = pair.id
    assert await _fresh_row(db, pair_id) is None

    async def fake_estimate_word_count(path):
        assert path == eb_path
        return 120_000

    monkeypatch.setattr(library_verify, "estimate_word_count", fake_estimate_word_count)

    result = await library_verify._recheck_pair_plausibility()

    assert result.evaluated == 1
    assert result.failed == 1
    assert result.cleared == 0
    row = await _fresh_row(db, pair_id)
    assert row is not None
    assert row.ok is False
    assert row.detail


async def test_a_never_checked_plausible_pair_gets_a_passing_row(db, monkeypatch):
    pair, eb, ab = await _pair(db, ebook_size=600_000, duration=10 * 3600)
    pair_id = pair.id

    async def fake_estimate_word_count(path):
        return 100_000  # 10,000 words/hour

    monkeypatch.setattr(library_verify, "estimate_word_count", fake_estimate_word_count)

    result = await library_verify._recheck_pair_plausibility()

    assert result.evaluated == 1
    assert result.failed == 0
    row = await _fresh_row(db, pair_id)
    assert row is not None
    assert row.ok is True
    assert row.detail.startswith(MEASURED_DETAIL_PREFIX)


async def test_a_never_checked_pair_without_a_duration_skips_the_word_count(db, monkeypatch):
    """Same rule as at creation: the EPUB parse only happens when a duration
    makes the verdict possible. The row is still written (as a pass, since
    absent data is not a finding), so the pair counts as checked."""
    pair, eb, ab = await _pair(db, ebook_size=2_800_000, duration=None)
    pair_id = pair.id
    calls = []

    async def fake_estimate_word_count(path):
        calls.append(path)
        return 120_000

    monkeypatch.setattr(library_verify, "estimate_word_count", fake_estimate_word_count)

    result = await library_verify._recheck_pair_plausibility()

    assert calls == []
    assert result.evaluated == 1
    assert result.failed == 0
    assert (await _fresh_row(db, pair_id)).ok is True


async def test_a_stored_passing_row_is_never_reevaluated(db, monkeypatch):
    """The #693 rule, pinned from the other side: if the phase re-judged a
    stored pass, this pair (a 2-minute file against 120k words) would flip to
    failed. It must come out exactly as it went in."""
    pair, eb, ab = await _pair(db, ebook_size=2_800_000, duration=132)
    pair_id = pair.id
    db.add(LibraryCheckResult(item_type="pair", item_id=pair.id, check_type=CHECK_TYPE,
                              ok=True, detail=MEASURED))
    await db.commit()

    async def fake_estimate_word_count(path):
        raise AssertionError("a measured pass must not be re-evaluated")

    monkeypatch.setattr(library_verify, "estimate_word_count", fake_estimate_word_count)

    result = await library_verify._recheck_pair_plausibility()

    assert (result.evaluated, result.failed, result.cleared, result.rechecked) == (0, 0, 0, 0)
    row = await _fresh_row(db, pair_id)
    assert row.ok is True
    assert row.detail == MEASURED


async def test_both_kinds_are_handled_in_one_run(db, monkeypatch):
    """A stored failure, a stored pass, a never-checked pair and an orphan, all
    at once: each gets its own treatment and the progress total counts the work
    the phase does (the failing and orphaned rows, plus the unchecked pair)."""
    flagged, flagged_eb, _ = await _pair(
        db, ebook_size=99_844_954, duration=14_940, title="Flagged Book")
    await _record_failed_row(db, flagged)
    flagged_path = flagged_eb.file_path
    passing, _, _ = await _pair(db, ebook_size=600_000, duration=10 * 3600, title="Passing Book")
    db.add(LibraryCheckResult(item_type="pair", item_id=passing.id, check_type=CHECK_TYPE,
                              ok=True, detail=MEASURED))
    unchecked, unchecked_eb, _ = await _pair(
        db, ebook_size=2_800_000, duration=132, title="Unchecked Book")
    gone, _, _ = await _pair(db, ebook_size=1_200_000, duration=132, title="Gone Book")
    await _record_failed_row(db, gone)
    flagged_id, passing_id, unchecked_id = flagged.id, passing.id, unchecked.id
    await db.delete(gone)
    await db.commit()

    async def fake_estimate_word_count(path):
        return 37_734 if path == flagged_path else 120_000

    monkeypatch.setattr(library_verify, "estimate_word_count", fake_estimate_word_count)

    result = await library_verify._recheck_pair_plausibility()

    assert result.cleared == 1
    assert result.orphans_removed == 1
    assert result.evaluated == 1
    assert result.failed == 1
    assert (await _fresh_row(db, flagged_id)).ok is True
    assert (await _fresh_row(db, passing_id)).ok is True
    assert (await _fresh_row(db, unchecked_id)).ok is False
    assert library_verify.get_progress()["total"] == 3


async def test_the_summary_is_exposed_in_progress(db, monkeypatch):
    pair, eb, ab = await _pair(db, ebook_size=2_800_000, duration=132)
    pair_id = pair.id

    async def fake_estimate_word_count(path):
        return 120_000

    monkeypatch.setattr(library_verify, "estimate_word_count", fake_estimate_word_count)

    await library_verify._recheck_pair_plausibility()

    assert library_verify.get_progress()["plausibility_recheck"] == {
        "cleared": 0, "orphans_removed": 0, "evaluated": 1, "failed": 1,
        "rechecked": 0, "newly_failed": 0,
    }


async def test_a_second_run_does_not_evaluate_the_same_pair_as_new(db, monkeypatch):
    """The first run gives the pair a row, so the second sees a stored verdict
    and never counts it as newly evaluated."""
    pair, eb, ab = await _pair(db, ebook_size=2_800_000, duration=132)
    pair_id = pair.id

    async def fake_estimate_word_count(path):
        return 120_000

    monkeypatch.setattr(library_verify, "estimate_word_count", fake_estimate_word_count)

    first = await library_verify._recheck_pair_plausibility()
    second = await library_verify._recheck_pair_plausibility()

    assert first.evaluated == 1
    assert second.evaluated == 0


# ---------------------------------------------------------------------------
# Passes that were never measured are re-checked (issue #833)
# ---------------------------------------------------------------------------


async def _created_without_evidence(db, *, ebook_size, duration, title):
    """A pair recorded the way pair creation records it, with nothing to judge
    on: no duration yet (#127) or no word count, so the stored row is a pass."""
    pair, eb, ab = await _pair(db, ebook_size=ebook_size, duration=duration, title=title)
    await record_pair_plausibility(db, pair, eb, ab, word_count=None,
                                   real_duration_seconds=None)
    await db.commit()
    row = await _row(db, pair.id)
    assert row.ok is True
    assert row.detail.startswith(NOT_MEASURED_DETAIL_PREFIX)
    return pair, eb, ab


async def test_an_abridged_edition_recorded_without_a_length_is_caught(db, monkeypatch):
    """The issue's first case: created before the audiobook had a length, so
    the stored pass judged nothing. Once the length is known, ~220,000 words
    against 6.4 hours is ~34,000 words/hour, an abridgement, and verify must
    list it."""
    pair, eb, ab = await _created_without_evidence(
        db, ebook_size=1_500_000, duration=None, title="Abridged Audio")
    pair_id = pair.id
    ab.duration_seconds = int(6.4 * 3600)
    await db.commit()

    async def fake_estimate_word_count(path):
        return 220_000

    monkeypatch.setattr(library_verify, "estimate_word_count", fake_estimate_word_count)

    result = await library_verify._recheck_pair_plausibility()

    assert (result.rechecked, result.newly_failed) == (1, 1)
    row = await _fresh_row(db, pair_id)
    assert row.ok is False
    assert "words/hour" in row.detail


async def test_a_file_size_pass_is_measured_once_a_word_count_exists(db, monkeypatch):
    """The issue's second case in shape: a byte-band pass (no word count at
    creation) hid ~3,200 words/hour, far more audio than the text explains."""
    pair, eb, ab = await _created_without_evidence(
        db, ebook_size=1_500_000, duration=int(55.9 * 3600), title="Too Much Audio")
    pair_id = pair.id

    async def fake_estimate_word_count(path):
        return 179_000

    monkeypatch.setattr(library_verify, "estimate_word_count", fake_estimate_word_count)

    result = await library_verify._recheck_pair_plausibility()

    assert (result.rechecked, result.newly_failed) == (1, 1)
    assert (await _fresh_row(db, pair_id)).ok is False


async def test_a_plausible_unmeasured_pass_becomes_measured(db, monkeypatch):
    pair, eb, ab = await _created_without_evidence(
        db, ebook_size=600_000, duration=10 * 3600, title="Fine Book")
    pair_id = pair.id

    async def fake_estimate_word_count(path):
        return 100_000

    monkeypatch.setattr(library_verify, "estimate_word_count", fake_estimate_word_count)

    result = await library_verify._recheck_pair_plausibility()

    assert (result.rechecked, result.newly_failed) == (1, 0)
    row = await _fresh_row(db, pair_id)
    assert row.ok is True
    assert row.detail.startswith(MEASURED_DETAIL_PREFIX)


async def test_a_legacy_pass_is_rechecked_once(db, monkeypatch):
    """Rows from before #833 say nothing about their basis. Judge them once;
    the measured verdict that replaces them is then left alone."""
    pair, eb, ab = await _pair(db, ebook_size=600_000, duration=10 * 3600)
    pair_id = pair.id
    db.add(LibraryCheckResult(item_type="pair", item_id=pair.id, check_type=CHECK_TYPE,
                              ok=True, detail=None))
    await db.commit()
    calls = []

    async def fake_estimate_word_count(path):
        calls.append(path)
        return 100_000

    monkeypatch.setattr(library_verify, "estimate_word_count", fake_estimate_word_count)

    first = await library_verify._recheck_pair_plausibility()
    second = await library_verify._recheck_pair_plausibility()

    assert (first.rechecked, second.rechecked) == (1, 0)
    assert len(calls) == 1
    assert (await _fresh_row(db, pair_id)).detail.startswith(MEASURED_DETAIL_PREFIX)


async def test_an_unparseable_ebook_is_tried_again_next_run(db, monkeypatch):
    """No word count is a gap in the evidence, not a fact about the book: the
    row stays unmeasured and the next verify tries again."""
    pair, eb, ab = await _created_without_evidence(
        db, ebook_size=600_000, duration=10 * 3600, title="Broken EPUB")
    pair_id = pair.id
    calls = []

    async def fake_estimate_word_count(path):
        calls.append(path)
        return None

    monkeypatch.setattr(library_verify, "estimate_word_count", fake_estimate_word_count)

    await library_verify._recheck_pair_plausibility()
    await library_verify._recheck_pair_plausibility()

    assert len(calls) == 2
    row = await _fresh_row(db, pair_id)
    assert row.ok is True
    assert row.detail.startswith(NOT_MEASURED_DETAIL_PREFIX)


async def test_an_abridged_pass_is_reconfirmed_without_a_parse(db, monkeypatch):
    pair, eb, ab = await _pair(db, ebook_size=600_000, duration=3 * 3600, title="Abridged")
    ab.is_abridged = True
    await db.commit()
    await record_pair_plausibility(db, pair, eb, ab, real_duration_seconds=None)
    await db.commit()
    pair_id = pair.id

    async def fake_estimate_word_count(path):
        raise AssertionError("an abridged audiobook is not judged, so no parse")

    monkeypatch.setattr(library_verify, "estimate_word_count", fake_estimate_word_count)

    result = await library_verify._recheck_pair_plausibility()

    assert (result.rechecked, result.newly_failed) == (1, 0)
    row = await _fresh_row(db, pair_id)
    assert row.ok is True
    assert row.detail.startswith(NOT_MEASURED_DETAIL_PREFIX)
