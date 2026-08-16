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
