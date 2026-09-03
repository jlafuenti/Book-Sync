"""
Golden vectors for the automatic ebook/audiobook pairing rules (issue #253).

`auto_match_books` runs on every library scan and *permanently* pairs an ebook
with an audiobook. A wrong pair produces a sync map between two different books.
Until now none of it was tested, so these vectors pin the rules **exactly as
they behave today** — including the ones issue #253 argues are wrong — so that
any later tightening is a deliberate, reviewed edit to this fixture rather than
a silent change in scan results.

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

Three cases are named as pinning questionable behaviour. They are the argument
of issue #253, recorded so the argument can be settled against real numbers:
`series_index_1_and_1_5_truncate_equal`,
`title_near_miss_matches_when_one_side_has_no_author`, and
`exclusion_is_not_honoured_without_a_file_hash`.
"""

import json
import os

import pytest
from sqlalchemy import select

from models.book import AudioBook, BookPair, EBook, PairStatus
from routers.library import _score_candidate, auto_match_books
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


async def test_a_hashless_unpaired_pair_is_recreated(db):
    """PINS A QUESTIONABLE RULE (issue #253).

    The exclusion is keyed by `file_hash`, and `delete_pair` only records one
    when *both* rows have a hash. A hash-less row — legacy only, since every
    ingest and upload path computes one — therefore keeps no memory of the
    unpair and is re-paired by the very next scan. Flip this to `== 0` when the
    exclusion learns a hash-free key.
    """
    await make_ebook(db, title="Mistborn", author="Brandon Sanderson",
                     file_hash=None, auto_pair_excluded_hashes=["audio-hash"])
    await make_audiobook(db, title="Mistborn", author="Brandon Sanderson",
                         file_hash=None, auto_pair_excluded_hashes=["ebook-hash"])

    assert await auto_match_books(db) == 1


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


async def test_assignment_is_greedy_in_scan_order_across_ebooks(db):
    """PINS A QUESTIONABLE RULE (issue #253).

    Candidates are resolved per ebook in scan order, so the *first* ebook takes
    the shared audiobook at its merely-passing score (76) and the later ebook
    that would have scored a perfect 100 is left unpaired. A globally best
    assignment would swap them.
    """
    first = await make_ebook(db, title="Mistborn Saga",
                             author="Brandon Sanderson", filename="saga.epub")
    second = await make_ebook(db, title="Mistborn",
                              author="Brandon Sanderson", filename="exact.epub")
    await make_audiobook(db, title="Mistborn", author="Brandon Sanderson")

    assert await auto_match_books(db) == 1
    await db.commit()

    pair = (await db.execute(select(BookPair))).scalar_one()
    assert pair.ebook_id == first.id
    assert pair.ebook_id != second.id


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
