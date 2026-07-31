"""
`DELETE /api/sync/progress/pair/{pair_id}` must actually reset the position,
not just its projection.

The canonical record now lives in `bookmarks` (see
`services/position_service.py`); `user_progress` is only a derived projection
written in the same transaction. The reset endpoint used to delete only
`UserProgress` rows and never touch the canonical `Bookmark` — so the very
next write re-seeded `user_progress` from the still-present bookmark and
"reset" silently un-reset itself (issue #6, docs/handoff-position-sync.md).
"""

from sqlalchemy import select

from models.bookmark import Bookmark, PositionHint
from models.progress import UserProgress
from tests.factories import make_book_pair

LOCATOR = '{"href":"ch1.xhtml","locations":{"progression":0.1}}'


def _body(source, **kw):
    body = {"source": source, "device_id": "pixel", "device_name": "Jesse's Pixel"}
    body.update(kw)
    return body


async def _put(client, user, auth_header, scope, ident, source="ebook", **kw):
    return await client.put(
        f"/api/sync/position/{scope}/{ident}",
        headers=auth_header(user), json=_body(source, **kw),
    )


async def _get(client, user, auth_header, scope, ident):
    return await client.get(
        f"/api/sync/position/{scope}/{ident}", headers=auth_header(user)
    )


async def _reset(client, user, auth_header, pair_id):
    return await client.delete(
        f"/api/sync/progress/pair/{pair_id}", headers=auth_header(user)
    )


async def _all_bookmarks(db, user_id):
    return (await db.execute(
        select(Bookmark).where(Bookmark.user_id == user_id)
    )).scalars().all()


async def _all_hints(db):
    return (await db.execute(select(PositionHint))).scalars().all()


async def _all_progress(db, user_id):
    return (await db.execute(
        select(UserProgress).where(UserProgress.user_id == user_id)
    )).scalars().all()


async def test_reset_deletes_the_canonical_bookmark_its_hints_and_progress(
    client, make_user, auth_header, db
):
    """The central fix: a reset must clear the pair-scoped bookmark AND the
    standalone ebook/audiobook bookmarks for the same underlying media, plus
    their hints — not just the user_progress projection."""
    pair = await make_book_pair(db)
    user = await make_user(username="reader")

    put = await _put(
        client, user, auth_header, "pair", pair.id,
        epub_chapter=12, epub_progress_percent=30.0,
        hint={"kind": "readium_locator", "value": LOCATOR},
        captured_at="2026-07-30T10:00:00Z",
    )
    assert put.status_code == 200, put.text

    # Standalone rows for the pair's own ebook/audiobook ids — a real scope a
    # client can reach independently of the pair (e.g. an unpaired reader view).
    standalone_ebook = await _put(
        client, user, auth_header, "ebook", pair.ebook_id,
        epub_chapter=3, captured_at="2026-07-30T09:00:00Z",
    )
    assert standalone_ebook.status_code == 200, standalone_ebook.text

    standalone_audio = await _put(
        client, user, auth_header, "audiobook", pair.audiobook_id,
        source="audiobook", audio_position_ms=5000,
        captured_at="2026-07-30T09:00:00Z",
    )
    assert standalone_audio.status_code == 200, standalone_audio.text

    # Sanity: three bookmark rows and one hint exist before reset.
    assert len(await _all_bookmarks(db, user.id)) == 3
    assert len(await _all_hints(db)) == 1

    resp = await _reset(client, user, auth_header, pair.id)
    assert resp.status_code == 200, resp.text

    assert await _all_bookmarks(db, user.id) == []
    assert await _all_hints(db) == []
    assert await _all_progress(db, user.id) == []

    got = await _get(client, user, auth_header, "pair", pair.id)
    assert got.status_code == 204


async def test_reset_is_scoped_to_the_requesting_user(
    client, make_user, auth_header, db
):
    """Another user's position on the same pair must survive someone else's
    reset."""
    pair = await make_book_pair(db)
    owner = await make_user(username="owner")
    other = await make_user(username="other")

    await _put(
        client, owner, auth_header, "pair", pair.id,
        epub_chapter=12, captured_at="2026-07-30T10:00:00Z",
    )
    await _put(
        client, other, auth_header, "pair", pair.id,
        epub_chapter=5, captured_at="2026-07-30T10:00:00Z",
    )

    resp = await _reset(client, owner, auth_header, pair.id)
    assert resp.status_code == 200, resp.text

    assert await _all_bookmarks(db, owner.id) == []

    other_bookmarks = await _all_bookmarks(db, other.id)
    assert len(other_bookmarks) == 1
    assert other_bookmarks[0].epub_chapter == 5

    other_get = await _get(client, other, auth_header, "pair", pair.id)
    assert other_get.status_code == 200
    assert other_get.json()["epub_chapter"] == 5


async def test_reset_of_a_pair_with_no_position_is_idempotent(
    client, make_user, auth_header, db
):
    """Resetting a pair the user never opened must still succeed, not 500 or
    404 on the absence of a bookmark."""
    pair = await make_book_pair(db)
    user = await make_user(username="reader")

    resp = await _reset(client, user, auth_header, pair.id)
    assert resp.status_code == 200, resp.text

    again = await _reset(client, user, auth_header, pair.id)
    assert again.status_code == 200, again.text


async def test_a_fresh_write_after_reset_starts_a_brand_new_record(
    client, make_user, auth_header, db
):
    """No resurrection: after a reset, the next PUT must create a brand-new
    canonical record — not update the row the reset was supposed to have
    deleted — and re-project user_progress from it.

    `anchor_revision` starts at 1 on row creation but a write that supplies
    epub_chapter/sentence_index/percent to a brand-new (all-None) row always
    bumps it once more, to 2 (see `apply_position`'s `before_anchor` check).
    So a genuinely fresh row produces the *same* revision as any other first
    substantive write — 2 here — rather than continuing from whatever the
    pre-reset row's counter had reached (which would read >= 3 if the reset
    had left the old row in place for this write to mutate)."""
    pair = await make_book_pair(db)
    user = await make_user(username="reader")

    first = await _put(
        client, user, auth_header, "pair", pair.id,
        epub_chapter=39, epub_sentence_index=4, epub_progress_percent=46.81,
        hint={"kind": "readium_locator", "value": LOCATOR},
        captured_at="2026-07-30T10:00:00Z",
    )
    assert first.status_code == 200, first.text
    revision_of_a_fresh_first_write = first.json()["anchor_revision"]

    reset = await _reset(client, user, auth_header, pair.id)
    assert reset.status_code == 200, reset.text

    fresh = await _put(
        client, user, auth_header, "pair", pair.id,
        epub_chapter=1, epub_sentence_index=0, epub_progress_percent=0.5,
        captured_at="2026-07-30T12:00:00Z",
    )
    assert fresh.status_code == 200, fresh.text
    body = fresh.json()
    assert body["anchor_revision"] == revision_of_a_fresh_first_write
    assert body["epub_chapter"] == 1
    assert body["epub_progress_percent"] == 0.5
    assert body["hints"] == []

    got = await _get(client, user, auth_header, "pair", pair.id)
    assert got.status_code == 200
    assert got.json()["epub_chapter"] == 1
    assert got.json()["hints"] == []

    progress = await client.get("/api/sync/progress", headers=auth_header(user))
    ebook_rows = [r for r in progress.json() if r["media_type"] == "ebook"]
    assert len(ebook_rows) == 1
    assert ebook_rows[0]["epub_progress_percent"] == 0.5
