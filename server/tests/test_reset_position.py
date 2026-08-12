"""
`DELETE /api/sync/position/{scope}/{ident}` — the canonical reset.

Resetting a *paired* book already had a real endpoint
(`DELETE /api/sync/progress/pair/{id}`, covered by `test_reset_progress.py`).
Standalone media had nothing: the web client faked it by PUTting zeros through
the legacy progress endpoint, which left the canonical `Bookmark` in place so
the next write re-seeded `user_progress` straight back from it — the same
failure mode issue #6 fixed for pairs. This endpoint generalises the pair reset
to every scope, and the pair route is now a thin alias for it.
"""

from sqlalchemy import select

from models.bookmark import Bookmark, PositionHint
from models.progress import UserProgress
from tests.factories import make_book_pair, make_ebook, make_audiobook

LOCATOR = '{"href":"ch1.xhtml","locations":{"progression":0.1}}'


async def _put(client, user, auth_header, scope, ident, source="ebook", **kw):
    body = {"source": source, "device_id": "pixel", "device_name": "Jesse's Pixel"}
    body.update(kw)
    return await client.put(
        f"/api/sync/position/{scope}/{ident}", headers=auth_header(user), json=body
    )


async def _delete(client, user, auth_header, scope, ident):
    return await client.delete(
        f"/api/sync/position/{scope}/{ident}", headers=auth_header(user)
    )


async def _bookmarks(db, user_id):
    return (await db.execute(
        select(Bookmark).where(Bookmark.user_id == user_id)
    )).scalars().all()


async def _hints(db):
    return (await db.execute(select(PositionHint))).scalars().all()


async def _progress(db, user_id):
    return (await db.execute(
        select(UserProgress).where(UserProgress.user_id == user_id)
    )).scalars().all()


async def test_deleting_a_standalone_ebook_position_clears_bookmark_hints_and_progress(
    client, make_user, auth_header, db
):
    """The case that had no endpoint at all: an unpaired ebook."""
    ebook = await make_ebook(db)
    user = await make_user(username="reader")

    put = await _put(
        client, user, auth_header, "ebook", ebook.id,
        epub_chapter=7, epub_progress_percent=22.5,
        hint={"kind": "epubjs_cfi", "value": "epubcfi(/6/4!/4/2)"},
        captured_at="2026-07-30T10:00:00Z",
    )
    assert put.status_code == 200, put.text
    assert len(await _bookmarks(db, user.id)) == 1
    assert len(await _hints(db)) == 1
    assert len(await _progress(db, user.id)) == 1

    resp = await _delete(client, user, auth_header, "ebook", ebook.id)
    assert resp.status_code == 200, resp.text

    assert await _bookmarks(db, user.id) == []
    assert await _hints(db) == []
    assert await _progress(db, user.id) == []

    got = await client.get(
        f"/api/sync/position/ebook/{ebook.id}", headers=auth_header(user)
    )
    assert got.status_code == 204


async def test_deleting_a_standalone_audiobook_position_clears_it(
    client, make_user, auth_header, db
):
    audiobook = await make_audiobook(db)
    user = await make_user(username="listener")

    put = await _put(
        client, user, auth_header, "audiobook", audiobook.id,
        source="audiobook", audio_position_ms=90_000,
        captured_at="2026-07-30T10:00:00Z",
    )
    assert put.status_code == 200, put.text

    resp = await _delete(client, user, auth_header, "audiobook", audiobook.id)
    assert resp.status_code == 200, resp.text

    assert await _bookmarks(db, user.id) == []
    assert await _progress(db, user.id) == []


async def test_deleting_a_pair_position_matches_the_legacy_pair_route(
    client, make_user, auth_header, db
):
    """`DELETE /position/pair/{id}` must do what `DELETE /progress/pair/{id}`
    does — including sweeping the standalone rows for the pair's own media,
    which a client can reach independently of the pair."""
    pair = await make_book_pair(db)
    user = await make_user(username="reader")

    await _put(client, user, auth_header, "pair", pair.id, epub_chapter=12,
               captured_at="2026-07-30T10:00:00Z")
    await _put(client, user, auth_header, "ebook", pair.ebook_id, epub_chapter=3,
               captured_at="2026-07-30T09:00:00Z")
    await _put(client, user, auth_header, "audiobook", pair.audiobook_id,
               source="audiobook", audio_position_ms=5000,
               captured_at="2026-07-30T09:00:00Z")
    assert len(await _bookmarks(db, user.id)) == 3

    resp = await _delete(client, user, auth_header, "pair", pair.id)
    assert resp.status_code == 200, resp.text

    assert await _bookmarks(db, user.id) == []
    assert await _progress(db, user.id) == []


async def test_deleting_a_standalone_position_leaves_the_pair_row_alone(
    client, make_user, auth_header, db
):
    """The reverse direction is NOT symmetric: a pair reset sweeps the
    standalone rows for its media (a client can reach them independently and a
    survivor would resurrect the position), but resetting one standalone scope
    must not wipe the pair's own record."""
    pair = await make_book_pair(db)
    user = await make_user(username="reader")

    await _put(client, user, auth_header, "pair", pair.id, epub_chapter=12,
               captured_at="2026-07-30T10:00:00Z")
    await _put(client, user, auth_header, "ebook", pair.ebook_id, epub_chapter=3,
               captured_at="2026-07-30T09:00:00Z")

    resp = await _delete(client, user, auth_header, "ebook", pair.ebook_id)
    assert resp.status_code == 200, resp.text

    remaining = await _bookmarks(db, user.id)
    assert len(remaining) == 1
    assert remaining[0].book_pair_id == pair.id
    assert remaining[0].epub_chapter == 12

    # `user_progress` is keyed by media, not by scope, so both writes projected
    # into the SAME ebook row. The standalone reset must leave that row alone —
    # deleting it would silently strip the pair of its projection too. (Its
    # contents are whatever the last write put there, as always; the pair's own
    # next write re-projects it.)
    ebook_rows = [p for p in await _progress(db, user.id) if p.ebook_id == pair.ebook_id]
    assert len(ebook_rows) == 1

    # The pair's canonical position is what actually has to survive intact.
    got = await client.get(
        f"/api/sync/position/pair/{pair.id}", headers=auth_header(user))
    assert got.status_code == 200
    assert got.json()["epub_chapter"] == 12


async def test_delete_is_scoped_to_the_requesting_user(
    client, make_user, auth_header, db
):
    ebook = await make_ebook(db)
    owner = await make_user(username="owner")
    other = await make_user(username="other")

    await _put(client, owner, auth_header, "ebook", ebook.id, epub_chapter=12,
               captured_at="2026-07-30T10:00:00Z")
    await _put(client, other, auth_header, "ebook", ebook.id, epub_chapter=5,
               captured_at="2026-07-30T10:00:00Z")

    resp = await _delete(client, owner, auth_header, "ebook", ebook.id)
    assert resp.status_code == 200, resp.text

    assert await _bookmarks(db, owner.id) == []
    survivors = await _bookmarks(db, other.id)
    assert len(survivors) == 1
    assert survivors[0].epub_chapter == 5


async def test_deleting_a_position_that_was_never_written_is_idempotent(
    client, make_user, auth_header, db
):
    ebook = await make_ebook(db)
    user = await make_user(username="reader")

    first = await _delete(client, user, auth_header, "ebook", ebook.id)
    assert first.status_code == 200, first.text
    again = await _delete(client, user, auth_header, "ebook", ebook.id)
    assert again.status_code == 200, again.text


async def test_deleting_an_unknown_target_is_404(client, make_user, auth_header, db):
    user = await make_user(username="reader")
    resp = await _delete(client, user, auth_header, "ebook", 999_999)
    assert resp.status_code == 404


async def test_the_pair_progress_alias_404s_on_an_unknown_pair(
    client, make_user, auth_header, db
):
    """The alias must not 500 on a bad id just because it delegates."""
    user = await make_user(username="reader")
    resp = await client.delete(
        "/api/sync/progress/pair/999999", headers=auth_header(user)
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Book pair not found"


async def test_a_fresh_write_after_a_standalone_reset_starts_a_new_record(
    client, make_user, auth_header, db
):
    """No resurrection — the whole reason the legacy zero-write was wrong."""
    ebook = await make_ebook(db)
    user = await make_user(username="reader")

    first = await _put(
        client, user, auth_header, "ebook", ebook.id,
        epub_chapter=39, epub_sentence_index=4, epub_progress_percent=46.81,
        hint={"kind": "epubjs_cfi", "value": "epubcfi(/6/4!/4/2)"},
        captured_at="2026-07-30T10:00:00Z",
    )
    assert first.status_code == 200, first.text
    revision_of_a_fresh_first_write = first.json()["anchor_revision"]

    reset = await _delete(client, user, auth_header, "ebook", ebook.id)
    assert reset.status_code == 200, reset.text

    fresh = await _put(
        client, user, auth_header, "ebook", ebook.id,
        epub_chapter=1, epub_sentence_index=0, epub_progress_percent=0.5,
        captured_at="2026-07-30T12:00:00Z",
    )
    assert fresh.status_code == 200, fresh.text
    body = fresh.json()
    assert body["anchor_revision"] == revision_of_a_fresh_first_write
    assert body["epub_chapter"] == 1
    assert body["hints"] == []
