"""
Golden vectors for the automatic ebook/audiobook pairing rules (issue #253).

`auto_match_books` runs on every library scan and *permanently* pairs an ebook
with an audiobook. A wrong pair produces a sync map between two different books.
These vectors pin the rules exactly, so that any later change is a deliberate,
reviewed edit to this fixture rather than a silent change in scan results.

Fixture: `tests/fixtures/auto_match_cases.json`
`[{ "name", "why", "ebook": {…columns}, "audiobook": {…columns},
   "expected_score": number|null }]`

- `ebook` / `audiobook` are column subsets fed straight to the ORM models;
  anything omitted is `None`.
- `expected_score` is what `_score_candidate` returns: `null` means "rejected,
  never pair these" and a number is the winning score (title score plus the
  author-agreement boost, capped at 100). Compared numerically, so the int/float
  split the implementation happens to produce does not matter.
- `why` says what the case defends; keep it filled in.
- Treat these as golden vectors: change one only when the *intended* rule
  changes, and say so in the commit. Scores come from `rapidfuzz==3.10.0`
  (pinned in `requirements.txt`), so a bump to that pin can legitimately move
  them — re-derive rather than deleting the case.

Four of the rules the first pass recorded as questionable were tightened in
issue #253, and their vectors were flipped in the same commit: series indexes
compare as floats (1 is not 1.5), a missing author on either side raises the
title bar to 90 instead of skipping the author gate, the unpair exclusion is
keyed by row id as well as file hash, and an empty normalized title never
pairs. Greedy scan-order assignment was left alone then and replaced in issue
#803 by a global best-pair-first assignment that falls through to the next
candidate when plausibility rejects one — see the "Issue #803" section below
and `test_the_best_scoring_pair_wins_across_ebooks_not_the_first_ebook_scanned`.
"""

import json
import os

import pytest
from sqlalchemy import select

from models.book import AudioBook, BookPair, EBook, PairStatus
from routers import library
from services.auto_match import _score_candidate, auto_match_books
from tests.factories import make_audiobook, make_ebook

_FIXTURE = os.path.join(
    os.path.dirname(__file__), "fixtures", "auto_match_cases.json"
)

with open(_FIXTURE, encoding="utf-8") as _f:
    CASES = json.load(_f)


# ---------------------------------------------------------------------------
# Pure scoring rules — no DB
# ---------------------------------------------------------------------------

def test_the_fixture_covers_both_verdicts():
    """A fixture that drifted to all-matches or all-rejects proves nothing."""
    verdicts = {case["expected_score"] is None for case in CASES}
    assert verdicts == {True, False}
    assert all(case["why"] for case in CASES), "every case must say what it defends"


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_score_candidate_golden_vectors(case):
    ebook = EBook(**case["ebook"])
    audiobook = AudioBook(**case["audiobook"])

    score = _score_candidate(ebook, audiobook)

    expected = case["expected_score"]
    if expected is None:
        assert score is None, case["why"]
    else:
        assert score is not None, case["why"]
        assert score == pytest.approx(expected), case["why"]


# ---------------------------------------------------------------------------
# The DB-driving shell
# ---------------------------------------------------------------------------

async def test_auto_match_pairs_a_matching_ebook_and_audiobook(db):
    eb = await make_ebook(db, title="Mistborn", author="Brandon Sanderson")
    ab = await make_audiobook(db, title="Mistborn", author="Brandon Sanderson")

    assert await auto_match_books(db) == 1
    await db.commit()

    pair = (await db.execute(select(BookPair))).scalar_one()
    assert (pair.ebook_id, pair.audiobook_id) == (eb.id, ab.id)
    assert pair.status == PairStatus.AUTO_MATCHED
    assert pair.matched_at is not None


async def test_auto_match_leaves_a_non_matching_candidate_alone(db):
    await make_ebook(db, title="Warbreaker", author="Brandon Sanderson")
    await make_audiobook(db, title="Skyward", author="Brandon Sanderson")

    assert await auto_match_books(db) == 0
    assert (await db.execute(select(BookPair))).scalars().all() == []


async def test_an_unpaired_pair_with_hashes_is_not_recreated(db):
    """The exclusion `delete_pair` records is honoured on the next scan."""
    await make_ebook(db, title="Mistborn", author="Brandon Sanderson",
                     file_hash="ebook-hash",
                     auto_pair_excluded_hashes=["audio-hash"])
    await make_audiobook(db, title="Mistborn", author="Brandon Sanderson",
                         file_hash="audio-hash",
                         auto_pair_excluded_hashes=["ebook-hash"])

    assert await auto_match_books(db) == 0
    assert (await db.execute(select(BookPair))).scalars().all() == []


async def test_a_hashless_unpaired_pair_is_not_recreated(db):
    """The exclusion is keyed by row id too, so it survives a missing hash.

    Previously `delete_pair` recorded nothing unless *both* rows had a
    `file_hash`, so a hash-less row — legacy only, since every ingest and
    upload path computes one — kept no memory of the unpair and was re-paired
    by the very next scan (issue #253).
    """
    eb = await make_ebook(db, title="Mistborn", author="Brandon Sanderson",
                          file_hash=None)
    ab = await make_audiobook(db, title="Mistborn", author="Brandon Sanderson",
                              file_hash=None)
    eb.auto_pair_excluded_ids = [ab.id]
    ab.auto_pair_excluded_ids = [eb.id]
    await db.commit()

    assert await auto_match_books(db) == 0
    assert (await db.execute(select(BookPair))).scalars().all() == []


async def test_unpairing_hashless_books_stops_the_next_scan_repairing_them(
    db, make_client, make_user, auth_header
):
    """End to end: scan pairs them, the user unpairs, the next scan leaves it.

    Neither row has a `file_hash`, which is exactly the case the old
    hash-only exclusion could not record.
    """
    eb = await make_ebook(db, title="Mistborn", author="Brandon Sanderson",
                          file_hash=None)
    ab = await make_audiobook(db, title="Mistborn", author="Brandon Sanderson",
                              file_hash=None)
    eb_id, ab_id = eb.id, ab.id
    assert await auto_match_books(db) == 1
    await db.commit()
    pair = (await db.execute(select(BookPair))).scalar_one()

    editor = await make_user(username="editor", role="editor")
    async with make_client(library.router) as c:
        r = await c.delete(f"/api/library/pairs/{pair.id}",
                           headers=auth_header(editor))
        assert r.status_code == 204, r.text

    await db.rollback()  # pick up the endpoint's committed state
    assert await auto_match_books(db) == 0
    assert (await db.execute(select(BookPair))).scalars().all() == []

    eb = (await db.execute(select(EBook).where(EBook.id == eb_id))).scalar_one()
    ab = (await db.execute(
        select(AudioBook).where(AudioBook.id == ab_id))).scalar_one()
    assert eb.auto_pair_excluded_ids == [ab_id]
    assert ab.auto_pair_excluded_ids == [eb_id]


async def test_unpairing_still_records_the_file_hashes_as_well(
    db, make_client, make_user, auth_header
):
    """Hash exclusions are kept, not replaced — a rehash keeps remapping them."""
    eb = await make_ebook(db, title="Mistborn", author="Brandon Sanderson",
                          file_hash="ebook-hash")
    ab = await make_audiobook(db, title="Mistborn", author="Brandon Sanderson",
                              file_hash="audio-hash")
    eb_id, ab_id = eb.id, ab.id
    assert await auto_match_books(db) == 1
    await db.commit()
    pair = (await db.execute(select(BookPair))).scalar_one()

    editor = await make_user(username="editor", role="editor")
    async with make_client(library.router) as c:
        r = await c.delete(f"/api/library/pairs/{pair.id}",
                           headers=auth_header(editor))
        assert r.status_code == 204, r.text

    await db.rollback()
    eb = (await db.execute(select(EBook).where(EBook.id == eb_id))).scalar_one()
    ab = (await db.execute(
        select(AudioBook).where(AudioBook.id == ab_id))).scalar_one()
    assert eb.auto_pair_excluded_hashes == ["audio-hash"]
    assert ab.auto_pair_excluded_hashes == ["ebook-hash"]
    # …and the id key went in alongside it, not instead of it.
    assert eb.auto_pair_excluded_ids == [ab_id]
    assert ab.auto_pair_excluded_ids == [eb_id]


async def test_the_matcher_never_unpairs_an_existing_pair(db):
    """The tightened rules only stop *new* pairs; existing ones are untouched.

    `auto_match_books` reads only rows that are not in `book_pairs` and never
    deletes. A pair the old rules created — here a #1 ebook against a #1.5
    audiobook, which the float comparison would now reject — survives a scan.
    """
    eb = await make_ebook(db, title="The Emperor's Soul", series="Elantris",
                          series_index=1)
    ab = await make_audiobook(db, title="The Emperor's Soul", series="Elantris",
                              series_index=1.5)
    db.add(BookPair(ebook_id=eb.id, audiobook_id=ab.id,
                    status=PairStatus.AUTO_MATCHED))
    await db.commit()

    assert await auto_match_books(db) == 0

    pair = (await db.execute(select(BookPair))).scalar_one()
    assert (pair.ebook_id, pair.audiobook_id) == (eb.id, ab.id)


async def test_one_audiobook_is_never_claimed_by_two_ebooks_in_one_run(db):
    await make_ebook(db, title="Mistborn", author="Brandon Sanderson",
                     filename="one.epub")
    await make_ebook(db, title="Mistborn", author="Brandon Sanderson",
                     filename="two.epub")
    ab = await make_audiobook(db, title="Mistborn", author="Brandon Sanderson")

    assert await auto_match_books(db) == 1
    await db.commit()

    pairs = (await db.execute(select(BookPair))).scalars().all()
    assert [p.audiobook_id for p in pairs] == [ab.id]


async def test_the_best_scoring_audiobook_wins_for_one_ebook(db):
    """Within one ebook's candidates the highest score wins, not the first."""
    near = await make_audiobook(db, title="Mistborn Saga",
                                author="Brandon Sanderson", filename="near.m4b")
    exact = await make_audiobook(db, title="Mistborn",
                                 author="Brandon Sanderson", filename="exact.m4b")
    await make_ebook(db, title="Mistborn", author="Brandon Sanderson")

    assert await auto_match_books(db) == 1
    await db.commit()

    pair = (await db.execute(select(BookPair))).scalar_one()
    assert pair.audiobook_id == exact.id
    assert pair.audiobook_id != near.id


async def test_the_best_scoring_pair_wins_across_ebooks_not_the_first_ebook_scanned(db):
    """Assignment is global, best pair first (issue #803, replacing the
    greedy scan-order rule issue #253 had pinned as questionable).

    Both ebooks want the one audiobook. The scan reaches "Mistborn Saga" first
    and it clears the bar at 76, but "Mistborn" scores a perfect 100, so it
    takes the audiobook and the weaker ebook is left unpaired.
    """
    first = await make_ebook(db, title="Mistborn Saga",
                             author="Brandon Sanderson", filename="saga.epub")
    second = await make_ebook(db, title="Mistborn",
                              author="Brandon Sanderson", filename="exact.epub")
    await make_audiobook(db, title="Mistborn", author="Brandon Sanderson")

    assert await auto_match_books(db) == 1
    await db.commit()

    pair = (await db.execute(select(BookPair))).scalar_one()
    assert pair.ebook_id == second.id
    assert pair.ebook_id != first.id


async def test_already_paired_books_are_not_considered(db):
    eb = await make_ebook(db, title="Mistborn", author="Brandon Sanderson")
    ab = await make_audiobook(db, title="Mistborn", author="Brandon Sanderson")
    db.add(BookPair(ebook_id=eb.id, audiobook_id=ab.id, status=PairStatus.SYNCED))
    await db.commit()

    # A second, equally good audiobook must not produce a second pair for the
    # already-paired ebook.
    await make_audiobook(db, title="Mistborn", author="Brandon Sanderson",
                         filename="dupe.m4b")

    assert await auto_match_books(db) == 0
    assert len((await db.execute(select(BookPair))).scalars().all()) == 1


async def test_one_ebook_never_gets_two_pairs_from_two_candidates_in_one_run(db):
    """The mirror of `test_one_audiobook_is_never_claimed_by_two_ebooks_in_one_run`
    (issue #691): a single unpaired ebook with two equally-good, unpaired
    audiobook candidates must end the run in exactly one pair, never two —
    `auto_match_books` picks the single best-scoring candidate per ebook and
    stops, and the new `ux_book_pairs_ebook_id` unique index would reject a
    second one at the DB level if it ever tried."""
    eb = await make_ebook(db, title="Mistborn", author="Brandon Sanderson")
    await make_audiobook(db, title="Mistborn", author="Brandon Sanderson",
                         filename="one.m4b")
    await make_audiobook(db, title="Mistborn", author="Brandon Sanderson",
                         filename="two.m4b")

    assert await auto_match_books(db) == 1
    await db.commit()

    pairs = (await db.execute(select(BookPair))).scalars().all()
    assert [p.ebook_id for p in pairs] == [eb.id]


# ---------------------------------------------------------------------------
# Issue #620: word-rate implausible pairs must not be auto-matched at all —
# not created and then flagged in Troubleshoot Library, which still lets the
# pair reach the transcription queue. `_score_candidate`'s pure rules
# (title/author/series) are unchanged and untouched by this; the gate runs
# once per winning candidate, right before the pair would be created.
# ---------------------------------------------------------------------------

def _epub_with_word_count(tmp_path, n_words, name="book.epub"):
    from tests.factories import write_epub
    body = " ".join(f"word{i}" for i in range(n_words))
    return write_epub(
        tmp_path / name,
        [("c1.xhtml", f"<html><body><p>{body}</p></body></html>")],
    )


async def test_auto_match_skips_a_word_rate_implausible_pairing(db, tmp_path):
    """~6,000 words in 6 minutes of audio is ~60,000 words/hour — far past
    `MAX_WORDS_PER_HOUR`. The pairing must never be created, not created and
    flagged, so it never reaches auto-transcribe."""
    path = _epub_with_word_count(tmp_path, 6_000)
    eb = EBook(title="Mistborn", author="Brandon Sanderson", filename="book.epub",
               file_path=path)
    ab = AudioBook(title="Mistborn", author="Brandon Sanderson", filename="book.m4b",
                    file_path=str(tmp_path / "book.m4b"), duration_seconds=360)
    db.add_all([eb, ab])
    await db.commit()

    assert await auto_match_books(db) == 0
    assert (await db.execute(select(BookPair))).scalars().all() == []


async def test_auto_match_still_pairs_a_word_rate_plausible_pairing(db, tmp_path):
    """A normal-paced real book (1,000 words / 6 minutes = 10,000 words/hour)
    must not be blocked by the new gate."""
    path = _epub_with_word_count(tmp_path, 1_000)
    eb = EBook(title="Mistborn", author="Brandon Sanderson", filename="book.epub",
               file_path=path)
    ab = AudioBook(title="Mistborn", author="Brandon Sanderson", filename="book.m4b",
                    file_path=str(tmp_path / "book.m4b"), duration_seconds=360)
    db.add_all([eb, ab])
    await db.commit()

    assert await auto_match_books(db) == 1
    pair = (await db.execute(select(BookPair))).scalar_one()
    assert (pair.ebook_id, pair.audiobook_id) == (eb.id, ab.id)


async def test_auto_match_without_a_readable_ebook_file_is_not_blocked(db):
    """No real file at `file_path` (the common case in this test module's
    other fixtures, and possible in production too) means word_count can't
    be computed — that is "cannot judge", not "implausible", and must not
    block an otherwise-good match."""
    eb = await make_ebook(db, title="Mistborn", author="Brandon Sanderson")
    ab = await make_audiobook(db, title="Mistborn", author="Brandon Sanderson",
                              duration_seconds=360)

    assert await auto_match_books(db) == 1
    assert len((await db.execute(select(BookPair))).scalars().all()) == 1


# ---------------------------------------------------------------------------
# Issue #803: a candidate that fails plausibility must not cost the ebook its
# next-best candidate, and must not let a weaker ebook take the audiobook the
# first one should have had. Assignment is global, highest score first.
# ---------------------------------------------------------------------------

AUTHOR = "Test Author"


def _epub_of_words(tmp_path, n_words, name):
    """A real EPUB with `n_words` words, so the genuine `estimate_word_count`
    runs rather than a stub."""
    from tests.factories import write_epub
    body = " ".join(["word"] * n_words)
    return write_epub(
        tmp_path / name,
        [("c1.xhtml", f"<html><body><p>{body}</p></body></html>")],
    )


def _hours(h):
    return h * 3600


async def _add(db, *rows):
    db.add_all(rows)
    await db.commit()


@pytest.mark.parametrize("novel_first", [True, False],
                         ids=["novel_scanned_first", "companion_scanned_first"])
async def test_a_rejected_top_candidate_does_not_hand_its_audiobook_to_a_weaker_ebook(
    db, tmp_path, novel_first
):
    """The #803 scenario, synthetic titles and numbers.

    - the novel (75,000 words) scores the radio dramatization highest (100) but
      that is 3.2 h of audio: 23,000 words/hour, implausible;
    - the unabridged reading (5.86 h) scores lower (95) and is plausible
      (12,800 words/hour);
    - the short companion ebook (28,871 words) also clears plausibility against
      the unabridged reading (4,900 words/hour) and scores 86 against it.

    Expected in either scan order: novel <-> unabridged reading, the companion
    unpaired. Before the fix the novel got nothing and the companion took the
    reading.
    """
    novel = EBook(title="Ashfall Harbor", author=AUTHOR, filename="novel.epub",
                  file_path=_epub_of_words(tmp_path, 75_000, "novel.epub"))
    companion = EBook(title="Lost Chapters of Ashfall Harbor", author=AUTHOR,
                      filename="companion.epub",
                      file_path=_epub_of_words(tmp_path, 28_871, "companion.epub"))
    radio = AudioBook(title="Ashfall Harbor", author=AUTHOR, filename="radio.m4b",
                      file_path=str(tmp_path / "radio.m4b"),
                      duration_seconds=_hours(3.2))
    unabridged = AudioBook(title="Ashfall Harbor Lost", author=AUTHOR,
                           filename="full.m4b",
                           file_path=str(tmp_path / "full.m4b"),
                           duration_seconds=_hours(5.86))
    ebooks = (novel, companion) if novel_first else (companion, novel)
    await _add(db, *ebooks, radio, unabridged)

    # The scores the scenario depends on, so a rapidfuzz bump that moves them
    # fails here with a clear reason rather than as a confusing pairing change.
    assert _score_candidate(novel, radio) == pytest.approx(100)
    assert _score_candidate(novel, unabridged) == pytest.approx(95, abs=1)
    assert _score_candidate(companion, unabridged) == pytest.approx(86, abs=1)
    assert _score_candidate(companion, radio) is None

    assert await auto_match_books(db) == 1
    await db.commit()

    pairs = (await db.execute(select(BookPair))).scalars().all()
    assert [(p.ebook_id, p.audiobook_id) for p in pairs] == [(novel.id, unabridged.id)]


async def test_an_ebook_with_only_implausible_candidates_stays_unpaired_and_frees_them(
    db, tmp_path
):
    """All of one ebook's viable candidates fail plausibility, so it ends
    unpaired, and the audiobooks it was rejected against stay available to a
    plausible ebook rather than being burned by the rejection."""
    big = EBook(title="Ashfall Harbor", author=AUTHOR, filename="big.epub",
                file_path=_epub_of_words(tmp_path, 75_000, "big.epub"))
    small = EBook(title="Ashfall Harbor Primer", author=AUTHOR,
                  filename="small.epub",
                  file_path=_epub_of_words(tmp_path, 8_000, "small.epub"))
    short_a = AudioBook(title="Ashfall Harbor", author=AUTHOR, filename="a.m4b",
                        file_path=str(tmp_path / "a.m4b"),
                        duration_seconds=_hours(1))
    short_b = AudioBook(title="Ashfall Harbor Unabridged", author=AUTHOR,
                        filename="b.m4b", file_path=str(tmp_path / "b.m4b"),
                        duration_seconds=_hours(1.2))
    await _add(db, big, small, short_a, short_b)

    assert await auto_match_books(db) == 1
    await db.commit()

    pairs = (await db.execute(select(BookPair))).scalars().all()
    assert [(p.ebook_id, p.audiobook_id) for p in pairs] == [(small.id, short_a.id)]


async def test_word_count_is_computed_once_per_ebook_however_many_candidates_are_checked(
    db, monkeypatch
):
    """Three implausible candidates for one ebook reach the plausibility check;
    the EPUB is still parsed exactly once. An ebook with no candidate at all
    is never parsed."""
    import services.auto_match as auto_match

    calls = []

    async def fake_word_count(path):
        calls.append(path)
        return 75_000

    monkeypatch.setattr(auto_match, "estimate_word_count", fake_word_count)

    big = EBook(title="Ashfall Harbor", author=AUTHOR, filename="big.epub",
                file_path="/unused/big.epub")
    unrelated = EBook(title="Unrelated Meadow Tale", author=AUTHOR,
                      filename="other.epub", file_path="/unused/other.epub")
    candidates = [
        AudioBook(title=t, author=AUTHOR, filename=f"{i}.m4b",
                  file_path=f"/unused/{i}.m4b", duration_seconds=_hours(1 + i / 10))
        for i, t in enumerate(["Ashfall Harbor", "Ashfall Harbor Unabridged",
                               "Ashfall Harbor Complete"])
    ]
    await _add(db, big, unrelated, *candidates)

    assert await auto_match_books(db) == 0
    assert calls == ["/unused/big.epub"]


async def test_word_count_is_not_computed_when_no_candidate_has_a_duration(
    db, monkeypatch
):
    import services.auto_match as auto_match

    calls = []

    async def fake_word_count(path):
        calls.append(path)
        return 75_000

    monkeypatch.setattr(auto_match, "estimate_word_count", fake_word_count)

    await make_ebook(db, title="Ashfall Harbor", author=AUTHOR)
    await make_audiobook(db, title="Ashfall Harbor", author=AUTHOR,
                         duration_seconds=None)

    assert await auto_match_books(db) == 1
    assert calls == []


async def test_equal_scores_resolve_to_the_lowest_ids(db):
    """Two ebooks and two audiobooks that all score the same: ties break on
    ebook id, then audiobook id, so the lowest of each pair up, and the
    outcome never depends on the order the database returns rows in."""
    e1 = await make_ebook(db, title="Mistborn", author=AUTHOR, filename="e1.epub")
    e2 = await make_ebook(db, title="Mistborn", author=AUTHOR, filename="e2.epub")
    a1 = await make_audiobook(db, title="Mistborn", author=AUTHOR, filename="a1.m4b")
    a2 = await make_audiobook(db, title="Mistborn", author=AUTHOR, filename="a2.m4b")

    assert await auto_match_books(db) == 2
    await db.commit()

    pairs = (await db.execute(select(BookPair))).scalars().all()
    assert sorted((p.ebook_id, p.audiobook_id) for p in pairs) == sorted(
        [(e1.id, a1.id), (e2.id, a2.id)]
    )


async def test_auto_match_skips_a_padded_audiobook_judged_on_its_real_length(
    db, tmp_path, monkeypatch
):
    """Issue #796: 1,000 words against a file stating 6 minutes is a normal
    10,000 words/hour, but the stream holds only 1.5 minutes of audio, so the
    real rate is 40,000 words/hour. The real length decides."""
    from services import audio_integrity

    monkeypatch.setattr(audio_integrity, "probe_real_audio_seconds", lambda p: 90.0)
    path = _epub_with_word_count(tmp_path, 1_000)
    eb = EBook(title="Mistborn", author="Brandon Sanderson", filename="book.epub",
               file_path=path)
    ab = AudioBook(title="Mistborn", author="Brandon Sanderson", filename="book.m4b",
                   file_path=str(tmp_path / "book.m4b"), duration_seconds=360)
    db.add_all([eb, ab])
    await db.commit()

    assert await auto_match_books(db) == 0
