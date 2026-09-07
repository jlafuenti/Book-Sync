"""`services.library_browse` — the browse query builders, driven without HTTP (issue #255).

`test_library_lists.py` characterises every list endpoint over HTTP and stays
the contract. This file reaches the builders directly: the LIKE escaping, the
arm selection per tab/kind, the order keys, the pagination envelope, and the
hydrate step's tolerance of rows deleted between the count and the fetch —
the branches an HTTP request cannot aim at.
"""

from collections import namedtuple

import pytest
from sqlalchemy import select

from models.book import AudioBook, BookPair, EBook, PairStatus
from schemas import LibraryItemKind, LibrarySort, LibraryTab, SortDir
from services import library_browse as browse

# The shape of one row of the browse UNION, as `_hydrate_items` reads it.
Row = namedtuple("Row", "kind item_id pair_id ebook_id audiobook_id")


async def _seed_ebooks(db, n, *, author="Same Author", title="Same Title"):
    tag = title.lower().replace(" ", "-")
    rows = [EBook(title=title, author=author, filename=f"{tag}{i}.epub", file_path=f"/x/{tag}{i}.epub")
            for i in range(n)]
    db.add_all(rows)
    await db.commit()
    return rows


async def _seed_library(db):
    """The same small library `test_library_lists.py` uses: one of everything.

    pair P1: ebook "Alpha" (Author A, S1 #1) + audiobook "Alpha (audio)"
    pair P2: ebook "Beta"  (Author B)        + audiobook — unacknowledged pair
    ebook  E3: "Gamma"   (Author A, S1 #2)  unpaired, unacknowledged
    ebook  E4: "Delta"   (Author C)         unpaired
    audio  A5: "Epsilon" (Author B, S2 #1)  unpaired, unacknowledged
    """
    e1 = EBook(title="Alpha", author="Author A", series="S1", series_index=1,
               filename="e1.epub", file_path="/x/e1.epub", file_size=100, acknowledged=True)
    a1 = AudioBook(title="Alpha (audio)", author="Author A", series="S1", series_index=1,
                   filename="a1.m4b", file_path="/x/a1.m4b", file_size=1000, acknowledged=True)
    e2 = EBook(title="Beta", author="Author B",
               filename="e2.epub", file_path="/x/e2.epub", file_size=200, acknowledged=True)
    a2 = AudioBook(title="Beta (audio)", author="Author B",
                   filename="a2.m4b", file_path="/x/a2.m4b", file_size=2000, acknowledged=True)
    e3 = EBook(title="Gamma", author="Author A", series="S1", series_index=2,
               filename="e3.epub", file_path="/x/e3.epub", file_size=300, acknowledged=False)
    e4 = EBook(title="Delta", author="Author C",
               filename="e4.epub", file_path="/x/e4.epub", file_size=50, acknowledged=True)
    a5 = AudioBook(title="Epsilon", author="Author B", series="S2", series_index=1,
                   filename="a5.m4b", file_path="/x/a5.m4b", file_size=5000, acknowledged=False)
    db.add_all([e1, a1, e2, a2, e3, e4, a5])
    await db.flush()
    p1 = BookPair(ebook_id=e1.id, audiobook_id=a1.id, status=PairStatus.SYNCED, acknowledged=True)
    p2 = BookPair(ebook_id=e2.id, audiobook_id=a2.id, status=PairStatus.AUTO_MATCHED, acknowledged=False)
    db.add_all([p1, p2])
    await db.commit()
    return dict(e1=e1, a1=a1, e2=e2, a2=a2, e3=e3, e4=e4, a5=a5, p1=p1, p2=p2)


# ------------------------------------------------------------------ the search term


def test_like_term_neutralises_the_wildcards_and_lowercases():
    # The backslash is escaped first, so the escapes are not re-escaped.
    assert browse._like_term("A%b_c\\") == "%a\\%b\\_c\\\\%"


def test_search_clause_is_none_without_a_term():
    assert browse._search_clause(EBook, None) is None
    assert browse._search_clause(EBook, "") is None


async def test_search_clause_matches_title_author_or_series_case_insensitively(db):
    db.add_all([
        EBook(title="Alpha", author="X", filename="1.epub", file_path="/x/1.epub"),
        EBook(title="Other", author="alpha smith", filename="2.epub", file_path="/x/2.epub"),
        EBook(title="Other", author="Y", series="The ALPHA saga", filename="3.epub", file_path="/x/3.epub"),
        EBook(title="Beta", author="Z", filename="4.epub", file_path="/x/4.epub"),
    ])
    await db.commit()

    rows = (await db.execute(select(EBook).where(browse._search_clause(EBook, "ALPHA")))).scalars().all()

    assert sorted(r.filename for r in rows) == ["1.epub", "2.epub", "3.epub"]


def test_library_order_ends_in_id_so_pages_are_stable():
    keys = browse._library_order(EBook)
    assert len(keys) == 5
    assert keys[-1] is EBook.id


# ------------------------------------------------------------------ the envelope


async def test_paginate_counts_the_filtered_set_and_offsets_the_page(db):
    await _seed_ebooks(db, 5)
    await _seed_ebooks(db, 2, author="Someone Else", title="Noise")
    base = select(EBook).where(EBook.author == "Same Author")
    ordered = base.order_by(EBook.id)

    p2 = await browse._paginate(db, ordered, base, 2, 2)
    p3 = await browse._paginate(db, ordered, base, 3, 2)
    p4 = await browse._paginate(db, ordered, base, 4, 2)

    assert (p2.total, p2.page, p2.limit, len(p2.items)) == (5, 2, 2, 2)
    assert len(p3.items) == 1
    assert (p4.total, p4.items) == (5, [])
    assert [e.id for e in p2.items] == [3, 4]


async def test_list_media_applies_where_search_and_a_custom_order(db):
    await _seed_ebooks(db, 3)
    await _seed_ebooks(db, 2, author="Someone Else", title="Noise")

    default = await browse._list_media(db, EBook, page=1, limit=10, q=None)
    narrowed = await browse._list_media(db, EBook, page=1, limit=10, q="noise")
    filtered = await browse._list_media(
        db, EBook, page=1, limit=10, q=None,
        where=EBook.author == "Same Author", order=(EBook.id.desc(),),
    )

    assert default.total == 5
    assert narrowed.total == 2 and {e.title for e in narrowed.items} == {"Noise"}
    assert filtered.total == 3
    assert [e.id for e in filtered.items] == sorted((e.id for e in filtered.items), reverse=True)


async def test_pairs_base_matches_either_side_of_the_pair(db):
    await _seed_library(db)

    async def ids(q):
        return sorted(p.id for p in (await db.execute(browse._pairs_base(q))).scalars())

    assert len(await ids(None)) == 2
    assert len(await ids("(audio)")) == 2      # only the audiobook titles carry it
    assert len(await ids("Alpha")) == 1
    assert await ids("Gamma") == []            # unpaired: not a pair at all


async def test_count_counts_the_statement(db):
    await _seed_library(db)
    assert await browse._count(db, select(EBook.id)) == 4
    assert await browse._count(db, select(BookPair.id).where(BookPair.acknowledged == False)) == 1  # noqa: E712


# ------------------------------------------------------------------ the browse union


@pytest.mark.parametrize("tab, kind, arms", [
    (LibraryTab.ALL, None, 3),
    (LibraryTab.EBOOKS, None, 1),
    (LibraryTab.AUDIOBOOKS, None, 1),
    (LibraryTab.PAIRED, None, 1),
    (LibraryTab.PAIRED, LibraryItemKind.EBOOK, 1),      # kind is ignored here
    (LibraryTab.UNPAIRED, None, 2),
    (LibraryTab.UNPAIRED, LibraryItemKind.EBOOK, 1),
    (LibraryTab.UNPAIRED, LibraryItemKind.AUDIOBOOK, 1),
    (LibraryTab.UNPAIRED, LibraryItemKind.PAIR, 0),     # impossible: no arms
    (LibraryTab.NEW, None, 2),
    (LibraryTab.NEW, LibraryItemKind.PAIR, 1),
    (LibraryTab.NEW, LibraryItemKind.EBOOK, 1),
    (LibraryTab.NEW, LibraryItemKind.AUDIOBOOK, 1),
])
def test_browse_arms_follow_the_tab_and_its_kind(tab, kind, arms):
    assert len(browse._browse_arms(tab, kind)) == arms


async def test_browse_subquery_for_an_impossible_combination_is_empty_not_an_error(db):
    await _seed_library(db)
    u = browse._browse_subquery(LibraryTab.UNPAIRED, LibraryItemKind.PAIR)
    assert (await db.execute(select(u))).all() == []


async def test_browse_subquery_all_tab_lists_each_pair_once_plus_unpaired_media(db):
    seeded = await _seed_library(db)
    u = browse._browse_subquery(LibraryTab.ALL, None)
    rows = (await db.execute(select(u.c.kind, u.c.item_id, u.c.title))).all()

    assert sorted((k, i) for k, i, _ in rows) == sorted([
        ("pair", seeded["p1"].id), ("pair", seeded["p2"].id),
        ("ebook", seeded["e3"].id), ("ebook", seeded["e4"].id),
        ("audiobook", seeded["a5"].id),
    ])
    # A pair displays its ebook's title.
    assert {t for k, _, t in rows if k == "pair"} == {"Alpha", "Beta"}


@pytest.mark.parametrize("sort, n_keys", [
    (LibrarySort.TITLE, 1), (LibrarySort.AUTHOR, 4), (LibrarySort.SERIES, 3),
    (LibrarySort.DATE, 2), (LibrarySort.SIZE, 2),
])
def test_browse_order_keys_end_in_kind_and_item_id(sort, n_keys):
    u = browse._browse_subquery(LibraryTab.ALL, None)
    asc = browse._browse_order(u, sort, SortDir.ASC)
    desc = browse._browse_order(u, sort, SortDir.DESC)

    assert len(asc) == n_keys + 2
    assert asc[-2] is u.c.kind and asc[-1] is u.c.item_id
    assert " ASC NULLS LAST" in str(asc[0]) and " DESC NULLS LAST" in str(desc[0])
    # The tiebreak is never reversed, so pages stay disjoint in either direction.
    assert desc[-2] is u.c.kind and desc[-1] is u.c.item_id


# ------------------------------------------------------------------ hydration


async def test_hydrate_items_returns_nothing_for_no_rows(db):
    assert await browse._hydrate_items(db, []) == []


async def test_hydrate_items_skips_rows_deleted_between_the_count_and_the_fetch(db):
    s = await _seed_library(db)
    rows = [
        Row("pair", s["p1"].id, s["p1"].id, s["e1"].id, s["a1"].id),
        Row("pair", 999, 999, None, None),
        Row("ebook", 999, None, 999, None),
        Row("audiobook", 999, None, None, 999),
        Row("ebook", s["e1"].id, None, s["e1"].id, None),     # paired ebook, asked as an ebook
        Row("ebook", s["e3"].id, None, s["e3"].id, None),     # unpaired
        Row("audiobook", s["a5"].id, None, None, s["a5"].id),
    ]

    items = await browse._hydrate_items(db, rows)

    assert [i.kind for i in items] == [
        LibraryItemKind.PAIR, LibraryItemKind.EBOOK, LibraryItemKind.EBOOK, LibraryItemKind.AUDIOBOOK,
    ]
    assert items[0].pair.id == s["p1"].id and items[0].pair.ebook.title == "Alpha"
    assert items[1].ebook.id == s["e1"].id and items[1].pair.id == s["p1"].id
    assert items[2].ebook.id == s["e3"].id and items[2].pair is None
    assert items[3].audiobook.id == s["a5"].id and items[3].pair is None


# ------------------------------------------------------------------ the endpoint bodies


async def test_search_impl_caps_each_list_and_says_so(db, monkeypatch):
    monkeypatch.setattr(browse, "SEARCH_MAX_RESULTS", 2)
    await _seed_ebooks(db, 3, title="Capped")
    await _seed_ebooks(db, 1, title="Fits")

    capped = await browse.search_impl(db, "capped")
    fits = await browse.search_impl(db, "fits")

    assert capped.query == "capped"
    assert len(capped.ebooks) == 2 and capped.truncated is True
    assert capped.audiobooks == [] and capped.book_pairs == []
    assert fits.truncated is False and len(fits.ebooks) == 1


async def test_search_impl_treats_a_wildcard_as_the_character(db):
    await _seed_ebooks(db, 2, title="Plain")
    db.add(EBook(title="100% done", author="A", filename="p.epub", file_path="/x/p.epub"))
    await db.commit()

    resp = await browse.search_impl(db, "%")

    assert [e.title for e in resp.ebooks] == ["100% done"]


async def test_list_items_impl_filters_orders_and_pages(db):
    s = await _seed_library(db)

    def call(**kw):
        args = dict(tab=LibraryTab.ALL, kind=None, q=None, author=None, series=None,
                    sort=LibrarySort.TITLE, direction=SortDir.ASC, page=1, limit=10)
        args.update(kw)
        return browse.list_items_impl(db, **args)

    by_author = await call(author="Author A")
    assert by_author.total == 2
    assert [(i.kind, (i.pair or i.ebook).id) for i in by_author.items] == [
        (LibraryItemKind.PAIR, s["p1"].id), (LibraryItemKind.EBOOK, s["e3"].id),
    ]

    by_alt_title = await call(q="alpha (audio)")   # the audiobook half of a pair
    assert by_alt_title.total == 1 and by_alt_title.items[0].kind == LibraryItemKind.PAIR

    by_series = await call(series="S2")
    assert by_series.total == 1 and by_series.items[0].kind == LibraryItemKind.AUDIOBOOK

    desc = await call(sort=LibrarySort.SIZE, direction=SortDir.DESC, limit=2, page=2)
    assert (desc.total, desc.page, desc.limit, len(desc.items)) == (5, 2, 2, 2)


async def test_facets_impl_reports_pills_for_the_tab_and_library_wide_counts(db):
    await _seed_library(db)

    f = await browse.facets_impl(db, LibraryTab.ALL, None)

    assert [(a.name, a.count) for a in f.authors] == [("Author A", 2), ("Author B", 2), ("Author C", 1)]
    assert [(x.name, x.count) for x in f.series] == [("S1", 2), ("S2", 1)]
    c = f.counts
    assert (c.ebooks, c.audiobooks, c.pairs, c.unpaired) == (4, 3, 2, 3)
    assert (c.new_ebooks, c.new_audiobooks, c.new_pairs) == (1, 1, 1)

    narrowed = await browse.facets_impl(db, LibraryTab.UNPAIRED, LibraryItemKind.AUDIOBOOK)
    assert [(a.name, a.count) for a in narrowed.authors] == [("Author B", 1)]
    assert narrowed.counts == c   # the counts are library-wide, not per tab
