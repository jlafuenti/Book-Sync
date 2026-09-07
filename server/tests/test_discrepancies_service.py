"""`services.discrepancies` — pair metadata comparison, driven without HTTP (issue #255).

`test_request_transactions.py` pins the resolve endpoint's commit-before-
write-back ordering over HTTP and stays the contract for that. This file
reaches the comparison itself: the empty-string and int/float normalisation,
the ignore list, the listing's stringified values, resolve's coercions and
auto-acknowledge, and the ignore path's auto-acknowledge and commit.
"""

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from models.book import AudioBook, BookPair, EBook, PairStatus
from schemas import ResolveDiscrepancyRequest
from services import discrepancies as svc


async def _seed_pair(db, *, ebook=None, audiobook=None, ignored=None, n=1):
    """A pair whose halves carry the given overrides; identical by default."""
    base = dict(title="Title", author="Author", series="S", series_index=1, publish_year=2000,
                is_explicit=False, is_abridged=False, cover_path=None)
    e = EBook(filename=f"e{n}.epub", file_path=f"/x/e{n}.epub", **{**base, **(ebook or {})})
    a = AudioBook(filename=f"a{n}.m4b", file_path=f"/x/a{n}.m4b", **{**base, **(audiobook or {})})
    db.add_all([e, a])
    await db.flush()
    p = BookPair(ebook_id=e.id, audiobook_id=a.id, status=PairStatus.SYNCED,
                 ignored_fields=list(ignored or []), acknowledged=False)
    db.add(p)
    await db.commit()
    return await _load(db, p.id)


async def _load(db, pair_id):
    result = await db.execute(
        select(BookPair)
        .options(selectinload(BookPair.ebook), selectinload(BookPair.audiobook))
        .where(BookPair.id == pair_id)
    )
    return result.scalar_one()


# ------------------------------------------------------------------ the comparison


def test_fields_to_compare_drops_identifiers_and_adds_cover_path():
    assert "cover_path" in svc.FIELDS_TO_COMPARE
    assert "isbn" not in svc.FIELDS_TO_COMPARE
    assert "narrator" not in svc.FIELDS_TO_COMPARE


async def test_identical_halves_have_no_discrepancies(db):
    pair = await _seed_pair(db)
    assert svc._pair_has_discrepancies(pair) is False


async def test_a_pair_missing_a_half_has_no_discrepancies(db):
    pair = await _seed_pair(db)
    pair.audiobook = None
    assert svc._pair_has_discrepancies(pair) is False


async def test_empty_string_and_none_compare_equal(db):
    pair = await _seed_pair(db, ebook={"series": ""}, audiobook={"series": None})
    assert svc._pair_has_discrepancies(pair) is False


async def test_series_index_compares_numerically(db):
    pair = await _seed_pair(db, ebook={"series_index": 1}, audiobook={"series_index": 1.0})
    assert svc._pair_has_discrepancies(pair) is False


async def test_a_differing_field_is_a_discrepancy_unless_ignored(db):
    differing = await _seed_pair(db, ebook={"author": "A"}, audiobook={"author": "B"}, n=1)
    ignored = await _seed_pair(db, ebook={"author": "A"}, audiobook={"author": "B"},
                               ignored=["author"], n=2)
    assert svc._pair_has_discrepancies(differing) is True
    assert svc._pair_has_discrepancies(ignored) is False


# ------------------------------------------------------------------ the listing


async def test_find_discrepancies_lists_only_pairs_that_differ_with_stringified_values(db):
    await _seed_pair(db, n=1)                                              # identical
    differing = await _seed_pair(db, ebook={"series_index": 2, "publish_year": None},
                                 audiobook={"series_index": 3, "publish_year": 1999}, n=2)
    await _seed_pair(db, ebook={"author": "A"}, audiobook={"author": "B"},
                     ignored=["author"], n=3)                               # ignored

    found = await svc.find_discrepancies_impl(db)

    assert [d.pair_id for d in found] == [differing.id]
    d = found[0]
    assert (d.ebook_id, d.audiobook_id, d.title) == (differing.ebook_id, differing.audiobook_id, "Title")
    assert {(f.field, f.ebook_value, f.audiobook_value) for f in d.discrepancies} == {
        ("series_index", "2.0", "3.0"),
        ("publish_year", None, "1999"),
    }


async def test_find_discrepancies_falls_back_to_the_audiobook_title(db):
    # `ebooks.title` is NOT NULL; an empty title is the real-world shape.
    pair = await _seed_pair(db, ebook={"title": ""}, audiobook={"title": "Audio Title"})
    found = await svc.find_discrepancies_impl(db)
    assert [d.title for d in found] == ["Audio Title"]
    assert {f.field for f in found[0].discrepancies} == {"title"}
    assert pair.id == found[0].pair_id


# ------------------------------------------------------------------ resolve


async def test_apply_resolution_coerces_and_reports_which_halves_changed(db):
    pair = await _seed_pair(db, ebook={"series_index": 1, "publish_year": 2000, "is_explicit": False},
                            audiobook={"series_index": 2, "publish_year": 2001, "is_explicit": True})
    req = ResolveDiscrepancyRequest(
        ebook_updates={"series_index": "2", "publish_year": "2001", "is_explicit": "true",
                       "narrator": "ignored: not a compared field", "series": None},
        audiobook_updates={},
    )

    changed = svc.apply_resolution(pair, req)

    assert changed == (True, False)
    assert (pair.ebook.series_index, pair.ebook.publish_year, pair.ebook.is_explicit) == (2.0, 2001, True)
    assert pair.ebook.series is None and not hasattr(pair.ebook, "narrator")


async def test_apply_resolution_acknowledges_the_pair_only_when_nothing_is_left(db):
    pair = await _seed_pair(db, ebook={"author": "A", "series": "X"}, audiobook={"author": "B", "series": "Y"})

    partial = svc.apply_resolution(pair, ResolveDiscrepancyRequest(
        ebook_updates={}, audiobook_updates={"author": "A"}))
    assert partial == (False, True) and pair.acknowledged is False

    full = svc.apply_resolution(pair, ResolveDiscrepancyRequest(
        ebook_updates={"series": "Y"}, audiobook_updates={}))
    assert full == (True, False) and pair.acknowledged is True


async def test_apply_resolution_with_nothing_applicable_changes_nothing(db):
    pair = await _seed_pair(db, ebook={"author": "A"}, audiobook={"author": "B"})
    req = ResolveDiscrepancyRequest(ebook_updates={"isbn": "x"}, audiobook_updates={"narrator": "y"})

    assert svc.apply_resolution(pair, req) == (False, False)
    assert pair.acknowledged is False


async def test_flag_fields_accept_true_1_and_anything_else_is_false(db):
    pair = await _seed_pair(db)
    svc.apply_resolution(pair, ResolveDiscrepancyRequest(
        ebook_updates={"is_explicit": "1", "is_abridged": "no"},
        audiobook_updates={"is_explicit": "TRUE", "is_abridged": "0"}))
    assert (pair.ebook.is_explicit, pair.ebook.is_abridged) == (True, False)
    assert (pair.audiobook.is_explicit, pair.audiobook.is_abridged) == (True, False)


# ------------------------------------------------------------------ ignore


async def test_ignore_fields_extends_the_list_without_duplicates_and_commits(db):
    pair = await _seed_pair(db, ebook={"author": "A", "series": "X"},
                            audiobook={"author": "B", "series": "Y"}, ignored=["author"])

    pair_id = pair.id
    await svc.ignore_fields_impl(db, pair, ["author", "publisher"])

    db.expire_all()
    fresh = await _load(db, pair_id)
    assert sorted(fresh.ignored_fields) == ["author", "publisher"]
    assert fresh.acknowledged is False            # `series` still differs


async def test_ignore_fields_acknowledges_the_pair_once_nothing_is_left(db):
    pair = await _seed_pair(db, ebook={"author": "A"}, audiobook={"author": "B"})

    pair_id = pair.id
    await svc.ignore_fields_impl(db, pair, ["author"])

    db.expire_all()
    fresh = await _load(db, pair_id)
    assert fresh.ignored_fields == ["author"] and fresh.acknowledged is True
