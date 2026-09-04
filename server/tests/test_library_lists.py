"""
The library list endpoints are paginated and searchable (issue #48).

`GET /api/library/{ebooks,audiobooks,pairs,new-pairs}` return a
`{items, total, page, limit}` envelope and `GET /api/library/new-items` returns
one envelope per sub-list. `page`/`limit` follow the audit-log endpoint's
conventions (`page` ≥ 1, `limit` 1..500, default 100); `q` narrows by
title/author/series server-side. Ordering is the old author→series→index→title
sort with `id` as the final tiebreak, so pages never overlap or skip.
"""

import pytest

from models.book import AudioBook, BookPair, EBook, PairStatus


async def _seed_ebooks(db, n, *, author="Same Author", title="Same Title"):
    """`n` ebooks with identical sort keys — the tiebreak is all that orders them."""
    rows = [EBook(title=title, author=author, filename=f"e{i}.epub", file_path=f"/x/e{i}.epub")
            for i in range(n)]
    db.add_all(rows)
    await db.commit()
    return rows


@pytest.fixture
async def library_client(make_client, make_user, auth_header):
    from routers import library
    user = await make_user()
    async with make_client(library.router) as c:
        yield c, auth_header(user)


# ---------------------------------------------------------------------------
# Envelope, paging, ordering
# ---------------------------------------------------------------------------

async def test_ebooks_returns_a_page_envelope_with_the_default_limit(db, library_client):
    c, headers = library_client
    await _seed_ebooks(db, 3)

    resp = await c.get("/api/library/ebooks", headers=headers)

    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"items", "total", "page", "limit"}
    assert body["total"] == 3
    assert body["page"] == 1
    assert body["limit"] == 100
    assert len(body["items"]) == 3


async def test_pages_are_exact_disjoint_and_stably_ordered_by_id(db, library_client):
    c, headers = library_client
    rows = await _seed_ebooks(db, 5)
    ids = sorted(r.id for r in rows)

    p1 = (await c.get("/api/library/ebooks?page=1&limit=2", headers=headers)).json()
    p2 = (await c.get("/api/library/ebooks?page=2&limit=2", headers=headers)).json()
    p3 = (await c.get("/api/library/ebooks?page=3&limit=2", headers=headers)).json()

    assert [e["id"] for e in p1["items"]] == ids[0:2]
    assert [e["id"] for e in p2["items"]] == ids[2:4]
    assert [e["id"] for e in p3["items"]] == ids[4:5]
    assert p1["total"] == p2["total"] == p3["total"] == 5
    assert (p2["page"], p2["limit"]) == (2, 2)


async def test_out_of_range_page_is_empty_but_still_reports_the_total(db, library_client):
    c, headers = library_client
    await _seed_ebooks(db, 3)

    body = (await c.get("/api/library/ebooks?page=9&limit=2", headers=headers)).json()

    assert body["items"] == []
    assert body["total"] == 3


async def test_limit_is_bounded_and_page_starts_at_one(db, library_client):
    c, headers = library_client
    assert (await c.get("/api/library/ebooks?limit=501", headers=headers)).status_code == 422
    assert (await c.get("/api/library/ebooks?limit=0", headers=headers)).status_code == 422
    assert (await c.get("/api/library/ebooks?page=0", headers=headers)).status_code == 422
    assert (await c.get("/api/library/ebooks?limit=500", headers=headers)).status_code == 200


async def test_ordering_is_author_series_index_title_then_id(db, library_client):
    c, headers = library_client
    db.add_all([
        EBook(title="Zeta", author="Beta Author", filename="1.epub", file_path="/x/1.epub"),
        EBook(title="Alpha", author=None, filename="2.epub", file_path="/x/2.epub"),          # null author last
        EBook(title="Book 2", author="Alpha Author", series="S", series_index=2, filename="3.epub", file_path="/x/3.epub"),
        EBook(title="Book 1", author="Alpha Author", series="S", series_index=1, filename="4.epub", file_path="/x/4.epub"),
    ])
    await db.commit()

    titles = [e["title"] for e in (await c.get("/api/library/ebooks", headers=headers)).json()["items"]]

    assert titles == ["Book 1", "Book 2", "Zeta", "Alpha"]


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

async def test_q_narrows_ebooks_by_title_author_or_series_case_insensitively(db, library_client):
    c, headers = library_client
    db.add_all([
        EBook(title="Dune", author="Frank Herbert", filename="1.epub", file_path="/x/1.epub"),
        EBook(title="Mistborn", author="Brandon Sanderson", series="Cosmere", filename="2.epub", file_path="/x/2.epub"),
        EBook(title="Elantris", author="Brandon Sanderson", series="Cosmere", filename="3.epub", file_path="/x/3.epub"),
    ])
    await db.commit()

    async def titles(q):
        body = (await c.get(f"/api/library/ebooks?q={q}", headers=headers)).json()
        return sorted(e["title"] for e in body["items"]), body["total"]

    assert await titles("dune") == (["Dune"], 1)
    assert await titles("SANDERSON") == (["Elantris", "Mistborn"], 2)
    assert await titles("cosmere") == (["Elantris", "Mistborn"], 2)
    assert await titles("nothing-here") == ([], 0)


async def test_audiobooks_paginate_and_search_the_same_way(db, library_client):
    c, headers = library_client
    db.add_all([
        AudioBook(title="Dune", author="Frank Herbert", filename="1.m4b", file_path="/x/1.m4b"),
        AudioBook(title="Mistborn", author="Brandon Sanderson", filename="2.m4b", file_path="/x/2.m4b"),
    ])
    await db.commit()

    body = (await c.get("/api/library/audiobooks?limit=1", headers=headers)).json()
    assert body["total"] == 2 and len(body["items"]) == 1 and body["limit"] == 1

    body = (await c.get("/api/library/audiobooks?q=herbert", headers=headers)).json()
    assert [a["title"] for a in body["items"]] == ["Dune"]


async def test_pairs_paginate_search_either_side_and_keep_sync_map_version(db, library_client):
    c, headers = library_client
    e1 = EBook(title="Dune", author="Frank Herbert", filename="1.epub", file_path="/x/1.epub")
    a1 = AudioBook(title="Dune (unabridged)", author="Frank Herbert", filename="1.m4b", file_path="/x/1.m4b")
    e2 = EBook(title="Mistborn", author="Brandon Sanderson", filename="2.epub", file_path="/x/2.epub")
    a2 = AudioBook(title="The Final Empire", author="Sanderson, B.", filename="2.m4b", file_path="/x/2.m4b")
    db.add_all([e1, a1, e2, a2])
    await db.flush()
    db.add_all([BookPair(ebook_id=e1.id, audiobook_id=a1.id, status=PairStatus.AUTO_MATCHED),
                BookPair(ebook_id=e2.id, audiobook_id=a2.id, status=PairStatus.AUTO_MATCHED)])
    await db.commit()

    body = (await c.get("/api/library/pairs?limit=1", headers=headers)).json()
    assert body["total"] == 2 and len(body["items"]) == 1
    assert "sync_map_version" in body["items"][0]

    # Matches through the audiobook's title, not just the ebook's.
    body = (await c.get("/api/library/pairs?q=final+empire", headers=headers)).json()
    assert [p["ebook"]["title"] for p in body["items"]] == ["Mistborn"]
    body = (await c.get("/api/library/pairs?q=herbert", headers=headers)).json()
    assert [p["ebook"]["title"] for p in body["items"]] == ["Dune"]


# ---------------------------------------------------------------------------
# New items / new pairs
# ---------------------------------------------------------------------------

async def test_new_items_returns_one_envelope_per_sub_list(db, library_client):
    c, headers = library_client
    db.add_all([
        EBook(title=f"E{i}", filename=f"{i}.epub", file_path=f"/x/{i}.epub", acknowledged=False)
        for i in range(3)
    ] + [
        EBook(title="Old", filename="old.epub", file_path="/x/old.epub", acknowledged=True),
        AudioBook(title="A0", filename="0.m4b", file_path="/x/0.m4b", acknowledged=False),
    ])
    await db.commit()

    body = (await c.get("/api/library/new-items?limit=2", headers=headers)).json()

    assert body["ebooks"]["total"] == 3
    assert len(body["ebooks"]["items"]) == 2
    assert body["audiobooks"]["total"] == 1
    assert [a["title"] for a in body["audiobooks"]["items"]] == ["A0"]
    assert body["ebooks"]["limit"] == body["audiobooks"]["limit"] == 2


async def test_new_pairs_is_paginated(db, library_client):
    c, headers = library_client
    for i in range(3):
        e = EBook(title=f"E{i}", filename=f"{i}.epub", file_path=f"/x/{i}.epub")
        a = AudioBook(title=f"A{i}", filename=f"{i}.m4b", file_path=f"/x/{i}.m4b")
        db.add_all([e, a])
        await db.flush()
        db.add(BookPair(ebook_id=e.id, audiobook_id=a.id, status=PairStatus.AUTO_MATCHED,
                        acknowledged=(i == 0)))
    await db.commit()

    body = (await c.get("/api/library/new-pairs?limit=1", headers=headers)).json()

    assert body["total"] == 2
    assert len(body["items"]) == 1


# ---------------------------------------------------------------------------
# Mixed browse endpoint + facets (issue #120)
#
# `GET /api/library/items` is the list the web LibraryPage renders: one entry
# per pair plus every unpaired ebook/audiobook, filtered, sorted and paged on
# the server. `GET /api/library/facets` gives the filter pills (distinct
# authors/series with counts, scoped to the tab) and the tab counts. Before
# this the page pulled the entire library and did all of it in memory.
# ---------------------------------------------------------------------------

async def _seed_library(db):
    """A small library with one of everything.

    pair P1: ebook "Alpha" (Author A, series S1 #1) + audiobook "Alpha (audio)"
    pair P2: ebook "Beta"  (Author B, no series)      + audiobook — unacknowledged pair
    ebook  E3: "Gamma"  (Author A, S1 #2)  unpaired, unacknowledged
    ebook  E4: "Delta"  (Author C)         unpaired
    audio  A5: "Epsilon" (Author B, S2 #1) unpaired, unacknowledged
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


def _keys(body):
    """(kind, id) per item — the identity the web keys cards by."""
    out = []
    for it in body["items"]:
        obj = it[it["kind"]]
        out.append((it["kind"], obj["id"]))
    return out


def _titles(body):
    """Display title per item: a pair shows its ebook's title (ebook-primary)."""
    return [it["pair"]["ebook"]["title"] if it["kind"] == "pair" else it[it["kind"]]["title"]
            for it in body["items"]]


async def test_items_all_tab_shows_each_pair_once_plus_unpaired_media(db, library_client):
    c, headers = library_client
    s = await _seed_library(db)

    body = (await c.get("/api/library/items", headers=headers)).json()

    assert set(body) == {"items", "total", "page", "limit"}
    assert body["total"] == 5
    assert set(_keys(body)) == {
        ("pair", s["p1"].id), ("pair", s["p2"].id),
        ("ebook", s["e3"].id), ("ebook", s["e4"].id), ("audiobook", s["a5"].id),
    }
    # A pair item carries the nested pair with both sides; a media item
    # carries the media and (here) no pair.
    pair_item = next(it for it in body["items"] if it["kind"] == "pair" and it["pair"]["id"] == s["p1"].id)
    assert pair_item["pair"]["ebook"]["title"] == "Alpha"
    assert pair_item["pair"]["audiobook"]["title"] == "Alpha (audio)"
    assert pair_item["ebook"] is None and pair_item["audiobook"] is None
    ebook_item = next(it for it in body["items"] if it["kind"] == "ebook" and it["ebook"]["id"] == s["e3"].id)
    assert ebook_item["pair"] is None


async def test_items_default_order_is_title_and_pairs_sort_by_their_ebook(db, library_client):
    c, headers = library_client
    await _seed_library(db)

    body = (await c.get("/api/library/items", headers=headers)).json()

    assert _titles(body) == ["Alpha", "Beta", "Delta", "Epsilon", "Gamma"]


async def test_items_ebooks_and_audiobooks_tabs_include_paired_media_with_their_pair(db, library_client):
    c, headers = library_client
    s = await _seed_library(db)

    ebooks = (await c.get("/api/library/items?tab=ebooks", headers=headers)).json()
    assert ebooks["total"] == 4
    assert all(it["kind"] == "ebook" for it in ebooks["items"])
    paired = next(it for it in ebooks["items"] if it["ebook"]["id"] == s["e1"].id)
    assert paired["pair"]["id"] == s["p1"].id
    assert paired["pair"]["audiobook"]["id"] == s["a1"].id

    audios = (await c.get("/api/library/items?tab=audiobooks", headers=headers)).json()
    assert audios["total"] == 3
    assert all(it["kind"] == "audiobook" for it in audios["items"])


async def test_items_paired_and_unpaired_tabs(db, library_client):
    c, headers = library_client
    s = await _seed_library(db)

    paired = (await c.get("/api/library/items?tab=paired", headers=headers)).json()
    assert set(_keys(paired)) == {("pair", s["p1"].id), ("pair", s["p2"].id)}

    unpaired = (await c.get("/api/library/items?tab=unpaired", headers=headers)).json()
    assert set(_keys(unpaired)) == {("ebook", s["e3"].id), ("ebook", s["e4"].id), ("audiobook", s["a5"].id)}

    only_ebooks = (await c.get("/api/library/items?tab=unpaired&kind=ebook", headers=headers)).json()
    assert set(_keys(only_ebooks)) == {("ebook", s["e3"].id), ("ebook", s["e4"].id)}

    only_audio = (await c.get("/api/library/items?tab=unpaired&kind=audiobook", headers=headers)).json()
    assert set(_keys(only_audio)) == {("audiobook", s["a5"].id)}


async def test_items_new_tab_and_its_kinds(db, library_client):
    c, headers = library_client
    s = await _seed_library(db)

    new = (await c.get("/api/library/items?tab=new", headers=headers)).json()
    assert set(_keys(new)) == {("ebook", s["e3"].id), ("audiobook", s["a5"].id)}

    new_pairs = (await c.get("/api/library/items?tab=new&kind=pair", headers=headers)).json()
    assert set(_keys(new_pairs)) == {("pair", s["p2"].id)}

    new_ebooks = (await c.get("/api/library/items?tab=new&kind=ebook", headers=headers)).json()
    assert set(_keys(new_ebooks)) == {("ebook", s["e3"].id)}


async def test_items_q_matches_either_side_of_a_pair_and_author_series_are_exact(db, library_client):
    c, headers = library_client
    s = await _seed_library(db)

    by_audio_title = (await c.get("/api/library/items?q=alpha%20(audio)", headers=headers)).json()
    assert set(_keys(by_audio_title)) == {("pair", s["p1"].id)}

    by_author = (await c.get("/api/library/items?author=Author%20A", headers=headers)).json()
    assert set(_keys(by_author)) == {("pair", s["p1"].id), ("ebook", s["e3"].id)}

    # Exact, not substring: "Author" alone matches nobody.
    none = (await c.get("/api/library/items?author=Author", headers=headers)).json()
    assert none["total"] == 0

    by_series = (await c.get("/api/library/items?series=S1", headers=headers)).json()
    assert set(_keys(by_series)) == {("pair", s["p1"].id), ("ebook", s["e3"].id)}


@pytest.mark.parametrize("sort,direction,expected", [
    ("title", "asc", ["Alpha", "Beta", "Delta", "Epsilon", "Gamma"]),
    ("title", "desc", ["Gamma", "Epsilon", "Delta", "Beta", "Alpha"]),
    # author -> series -> index -> title; nulls last within a level.
    ("author", "asc", ["Alpha", "Gamma", "Epsilon", "Beta", "Delta"]),
    ("series", "asc", ["Alpha", "Gamma", "Epsilon", "Beta", "Delta"]),
    # pair size = its ebook's size (ebook-primary, like everything else on a pair).
    ("size", "asc", ["Delta", "Alpha", "Beta", "Gamma", "Epsilon"]),
    ("size", "desc", ["Epsilon", "Gamma", "Beta", "Alpha", "Delta"]),
])
async def test_items_sort_keys(db, library_client, sort, direction, expected):
    c, headers = library_client
    await _seed_library(db)

    body = (await c.get(f"/api/library/items?sort={sort}&dir={direction}", headers=headers)).json()
    assert _titles(body) == expected


async def test_items_date_sort_uses_uploaded_at(db, library_client):
    from datetime import datetime, timedelta
    c, headers = library_client
    s = await _seed_library(db)
    base = datetime(2026, 1, 1)
    # Newest first: A5, then E4, E3, then the pairs' ebooks E2, E1.
    for i, key in enumerate(["e1", "e2", "e3", "e4", "a5"]):
        s[key].uploaded_at = base + timedelta(days=i)
    await db.commit()

    body = (await c.get("/api/library/items?sort=date&dir=desc", headers=headers)).json()
    assert _keys(body)[:3] == [("audiobook", s["a5"].id), ("ebook", s["e4"].id), ("ebook", s["e3"].id)]


async def test_items_pages_are_disjoint_and_report_the_filtered_total(db, library_client):
    c, headers = library_client
    await _seed_library(db)

    p1 = (await c.get("/api/library/items?limit=2&page=1", headers=headers)).json()
    p2 = (await c.get("/api/library/items?limit=2&page=2", headers=headers)).json()
    p3 = (await c.get("/api/library/items?limit=2&page=3", headers=headers)).json()

    assert p1["total"] == p2["total"] == p3["total"] == 5
    seen = _keys(p1) + _keys(p2) + _keys(p3)
    assert len(seen) == 5 and len(set(seen)) == 5
    assert p3["limit"] == 2 and p3["page"] == 3

    bounded = await c.get("/api/library/items?limit=501", headers=headers)
    assert bounded.status_code == 422
    bad_tab = await c.get("/api/library/items?tab=nope", headers=headers)
    assert bad_tab.status_code == 422


async def test_items_on_an_empty_library(db, library_client):
    c, headers = library_client
    body = (await c.get("/api/library/items", headers=headers)).json()
    assert body == {"items": [], "total": 0, "page": 1, "limit": 100}


async def test_facets_counts_and_pills_follow_the_tab(db, library_client):
    c, headers = library_client
    await _seed_library(db)

    all_f = (await c.get("/api/library/facets", headers=headers)).json()
    assert all_f["counts"] == {
        "ebooks": 4, "audiobooks": 3, "pairs": 2, "unpaired": 3,
        "new_ebooks": 1, "new_audiobooks": 1, "new_pairs": 1,
    }
    # Pills for the All tab: pairs count once (by their ebook's author).
    assert all_f["authors"] == [
        {"name": "Author A", "count": 2}, {"name": "Author B", "count": 2}, {"name": "Author C", "count": 1},
    ]
    assert all_f["series"] == [{"name": "S1", "count": 2}, {"name": "S2", "count": 1}]

    unpaired_audio = (await c.get("/api/library/facets?tab=unpaired&kind=audiobook", headers=headers)).json()
    assert unpaired_audio["authors"] == [{"name": "Author B", "count": 1}]
    assert unpaired_audio["series"] == [{"name": "S2", "count": 1}]
    # Counts are global, not per tab.
    assert unpaired_audio["counts"] == all_f["counts"]


async def test_facets_on_an_empty_library(db, library_client):
    c, headers = library_client
    body = (await c.get("/api/library/facets", headers=headers)).json()
    assert body["authors"] == [] and body["series"] == []
    assert body["counts"] == {"ebooks": 0, "audiobooks": 0, "pairs": 0, "unpaired": 0,
                              "new_ebooks": 0, "new_audiobooks": 0, "new_pairs": 0}


async def test_items_kind_is_ignored_where_it_does_not_apply_and_never_errors(db, library_client):
    """`kind` only means something for unpaired/new. On any other tab it is
    ignored (the web clears it on a tab change, but a stale URL may carry it),
    and a combination that names nothing — unpaired pairs — is an empty shelf,
    not a 4xx/5xx."""
    c, headers = library_client
    s = await _seed_library(db)

    paired = (await c.get("/api/library/items?tab=paired&kind=ebook", headers=headers)).json()
    assert set(_keys(paired)) == {("pair", s["p1"].id), ("pair", s["p2"].id)}

    nothing = (await c.get("/api/library/items?tab=unpaired&kind=pair", headers=headers)).json()
    assert nothing["total"] == 0 and nothing["items"] == []
    facets = (await c.get("/api/library/facets?tab=unpaired&kind=pair", headers=headers)).json()
    assert facets["authors"] == [] and facets["series"] == []


# ---------------------------------------------------------------------------
# GET /api/library/search — bounded and wildcard-safe (issue #208)
#
# The paginated browse above is capped at `limit=500`; `/search` was not
# paginated at all and ran three unbounded queries. Worse, it built its LIKE
# term with no escaping, so `q=%` matched every row: one request returned the
# entire library three times over, and any authenticated user could ask for it
# in a loop.
# ---------------------------------------------------------------------------

async def test_search_treats_sql_wildcards_as_literal_text(db, library_client):
    """`%` and `_` are characters someone typed, not a request for everything."""
    c, headers = library_client
    await _seed_ebooks(db, 3, title="Ordinary Title")

    for wildcard in ("%", "_"):
        body = (await c.get(f"/api/library/search?q={wildcard}", headers=headers)).json()
        assert body["ebooks"] == [], f"{wildcard!r} still matched everything"
        assert body["audiobooks"] == []
        assert body["book_pairs"] == []


async def test_search_still_finds_a_literal_wildcard_character(db, library_client):
    """Escaping must not make such a title unfindable — it is a real title."""
    c, headers = library_client
    db.add_all([
        EBook(title="100% Wolf", author="A", filename="w.epub", file_path="/x/w.epub"),
        EBook(title="Ordinary Title", author="A", filename="o.epub", file_path="/x/o.epub"),
    ])
    await db.commit()

    body = (await c.get("/api/library/search?q=100%25", headers=headers)).json()

    assert [e["title"] for e in body["ebooks"]] == ["100% Wolf"]


async def test_search_results_are_capped(db, library_client):
    c, headers = library_client
    from routers import library

    await _seed_ebooks(db, library.SEARCH_MAX_RESULTS + 5, title="Capped Title")

    body = (await c.get("/api/library/search?q=capped", headers=headers)).json()

    assert len(body["ebooks"]) == library.SEARCH_MAX_RESULTS
    assert body["truncated"] is True


async def test_search_does_not_claim_truncation_when_it_fits(db, library_client):
    c, headers = library_client
    await _seed_ebooks(db, 3, title="Small Result")

    body = (await c.get("/api/library/search?q=small", headers=headers)).json()

    assert len(body["ebooks"]) == 3
    assert body["truncated"] is False


async def test_search_is_rate_limited(db, library_client, monkeypatch):
    from config import settings

    c, headers = library_client
    monkeypatch.setattr(settings, "search_read_limit", 2)

    codes = [
        (await c.get("/api/library/search?q=x", headers=headers)).status_code
        for _ in range(3)
    ]
    over = await c.get("/api/library/search?q=x", headers=headers)

    assert codes == [200, 200, 429]
    assert int(over.headers["Retry-After"]) > 0
