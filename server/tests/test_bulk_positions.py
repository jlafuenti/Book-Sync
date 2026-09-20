"""
`GET /api/sync/positions` — every position the caller has, one page at a time
(issue #653).

Android's fresh-sign-in reconcile (`PositionRepository.syncAllBookmarksAndProgress`)
pulls `GET /api/sync/position/{scope}/{id}` twice per pair, sequentially. On a
library of a few hundred pairs that is several hundred round trips against a
single-process server. This endpoint answers the same question — "what does
this user have?" — in one paged call, reusing `to_response_dict` so the two
response shapes cannot drift.

Read-only: `PUT /api/sync/position/{scope}/{ident}` remains the only write path.
"""

from sqlalchemy import event

import database
from tests.factories import make_book_pair, make_ebook, make_audiobook

LOCATOR = '{"href":"ch1.xhtml","locations":{"progression":0.1}}'


async def _put(client, user, auth_header, scope, ident, **kw):
    body = {"source": "ebook", "device_id": "pixel", "device_name": "Jesse's Pixel"}
    body.update(kw)
    return await client.put(
        f"/api/sync/position/{scope}/{ident}", headers=auth_header(user), json=body
    )


async def _get(client, user, auth_header, scope, ident):
    return await client.get(
        f"/api/sync/position/{scope}/{ident}", headers=auth_header(user)
    )


async def _bulk(client, user, auth_header, **params):
    return await client.get(
        "/api/sync/positions", headers=auth_header(user), params=params
    )


async def test_empty_for_a_user_with_no_positions(client, make_user, auth_header, db):
    user = await make_user(username="reader")

    resp = await _bulk(client, user, auth_header)

    assert resp.status_code == 200
    body = resp.json()
    assert body == {"items": [], "total": 0, "page": 1, "limit": 100}


async def test_covers_every_scope_the_caller_has(
    client, make_user, auth_header, db
):
    """A pair, a standalone ebook and a standalone audiobook, each with a
    written position, all come back in one page — the endpoint is not
    silently pair-only."""
    user = await make_user(username="reader")
    pair = await make_book_pair(db)
    solo_ebook = await make_ebook(db, title="Solo Ebook")
    solo_audiobook = await make_audiobook(db, title="Solo Audio")

    await _put(client, user, auth_header, "pair", pair.id,
               epub_chapter=3, captured_at="2026-07-30T10:00:00Z")
    await _put(client, user, auth_header, "ebook", solo_ebook.id,
               epub_chapter=1, captured_at="2026-07-30T10:00:00Z")
    await _put(client, user, auth_header, "audiobook", solo_audiobook.id,
               audio_position_ms=5000, captured_at="2026-07-30T10:00:00Z")

    resp = await _bulk(client, user, auth_header)
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 3
    scopes = {(item["scope"], item.get("book_pair_id"), item.get("ebook_id"),
               item.get("audiobook_id")) for item in body["items"]}
    assert (
        "pair", pair.id, pair.ebook_id, pair.audiobook_id
    ) in scopes
    assert ("ebook", None, solo_ebook.id, None) in scopes
    assert ("audiobook", None, None, solo_audiobook.id) in scopes


async def test_bulk_item_matches_the_single_get_exactly(
    client, make_user, auth_header, db
):
    """Reuses `to_response_dict`, so the two shapes cannot drift."""
    user = await make_user(username="reader")
    pair = await make_book_pair(db)

    await _put(
        client, user, auth_header, "pair", pair.id,
        epub_chapter=7, epub_sentence_index=2,
        epub_text_preview="a sentence at the anchor",
        epub_progress_percent=12.5,
        hint={"kind": "readium_locator", "value": LOCATOR},
        captured_at="2026-07-30T10:00:00Z",
    )

    single = (await _get(client, user, auth_header, "pair", pair.id)).json()
    bulk = (await _bulk(client, user, auth_header)).json()

    assert bulk["total"] == 1
    assert bulk["items"][0] == single


async def test_scoped_to_the_caller_not_leaked_across_users(
    client, make_user, auth_header, db
):
    """The worst possible bug here: one user's positions must never appear in
    another user's bulk list, even when they share the same book pair."""
    pair = await make_book_pair(db)
    alice = await make_user(username="alice")
    bob = await make_user(username="bob")

    await _put(client, alice, auth_header, "pair", pair.id,
               epub_chapter=10, captured_at="2026-07-30T10:00:00Z")
    await _put(client, bob, auth_header, "pair", pair.id,
               epub_chapter=99, captured_at="2026-07-30T10:00:00Z")

    alice_bulk = (await _bulk(client, alice, auth_header)).json()
    bob_bulk = (await _bulk(client, bob, auth_header)).json()

    assert alice_bulk["total"] == 1
    assert alice_bulk["items"][0]["epub_chapter"] == 10
    assert bob_bulk["total"] == 1
    assert bob_bulk["items"][0]["epub_chapter"] == 99

    # Neither list carries the other user's device/capture data either.
    assert alice_bulk["items"] != bob_bulk["items"]


async def test_paged(client, make_user, auth_header, db):
    user = await make_user(username="reader")
    for i in range(3):
        eb = await make_ebook(db, title=f"Book {i}")
        await _put(client, user, auth_header, "ebook", eb.id,
                   epub_chapter=i, captured_at="2026-07-30T10:00:00Z")

    page1 = (await _bulk(client, user, auth_header, page=1, limit=2)).json()
    assert page1["total"] == 3
    assert len(page1["items"]) == 2
    assert page1["page"] == 1
    assert page1["limit"] == 2

    page2 = (await _bulk(client, user, auth_header, page=2, limit=2)).json()
    assert len(page2["items"]) == 1

    seen_chapters = {i["epub_chapter"] for i in page1["items"] + page2["items"]}
    assert seen_chapters == {0, 1, 2}


async def test_hints_reported_current_or_stale_same_as_single_get(
    client, make_user, auth_header, db
):
    user = await make_user(username="reader")
    pair = await make_book_pair(db)

    await _put(
        client, user, auth_header, "pair", pair.id,
        epub_chapter=1, hint={"kind": "readium_locator", "value": LOCATOR},
        captured_at="2026-07-30T10:00:00Z",
    )
    # Move the anchor so the hint goes stale.
    await _put(
        client, user, auth_header, "pair", pair.id,
        epub_chapter=2, captured_at="2026-07-30T11:00:00Z",
    )

    bulk = (await _bulk(client, user, auth_header)).json()
    assert bulk["items"][0]["hints"][0]["current"] is False


async def test_query_count_does_not_grow_with_row_count(
    client, make_user, auth_header, db
):
    """The whole point of a bulk endpoint is fewer round trips; it must not
    turn into one query per row server-side. Insert a handful of positions
    and assert the number of SQL statements issued stays flat."""
    user = await make_user(username="reader")

    async def _seed(n):
        for i in range(n):
            eb = await make_ebook(db, title=f"Book {i}")
            await _put(client, user, auth_header, "ebook", eb.id,
                       epub_chapter=i, captured_at="2026-07-30T10:00:00Z")

    await _seed(1)
    resp_small = await _bulk(client, user, auth_header)
    assert resp_small.status_code == 200

    counted = {"n": 0}

    def _before_cursor_execute(*args, **kwargs):
        counted["n"] += 1

    event.listen(database.engine.sync_engine, "before_cursor_execute", _before_cursor_execute)
    try:
        await _seed(9)  # 10 total ebook positions now
        counted["n"] = 0  # only count the bulk GET itself
        resp_large = await _bulk(client, user, auth_header, limit=100)
    finally:
        event.remove(database.engine.sync_engine, "before_cursor_execute", _before_cursor_execute)

    assert resp_large.status_code == 200
    assert resp_large.json()["total"] == 10
    # Bounded: one count query, one page query, plus one apiece for the two
    # eager-loaded relationships (hints, book_pair) — never one per row.
    assert counted["n"] <= 4
