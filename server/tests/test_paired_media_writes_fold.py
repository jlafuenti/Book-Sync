"""
Issue #720: a write addressed to a paired book's own scope lands on the pair.

`PUT /api/sync/position/{ebook|audiobook}/{id}` used to create a standalone
record even when that ebook or audiobook was half of a pair, next to the pair's
own record. Measured on a live instance: 236 such records across three users,
200 written after the book was paired, 25 of them the book's *only* position.
Opening the pair reads the pair record, so those 25 opened at the start, and
the Android client (which never downloads a paired book's standalone rows) did
not see them at all.

A write is now folded onto the pair: same staleness verdict, same record, same
projection. Reads and resets keep their meaning. `docs/position-sync-contract.md`
§ Reset: a standalone reset "must not wipe the pair's record".
"""
from sqlalchemy import select

from models.bookmark import Bookmark
from tests.factories import make_audiobook, make_book_pair


async def _put(client, user, auth_header, scope, ident, **body):
    return await client.put(
        f"/api/sync/position/{scope}/{ident}", headers=auth_header(user), json=body
    )


async def _bookmarks(db, user_id):
    return (await db.execute(select(Bookmark).where(Bookmark.user_id == user_id))).scalars().all()


async def test_an_audiobook_write_on_a_paired_audiobook_lands_on_the_pair(
    client, make_user, auth_header, db
):
    pair = await make_book_pair(db)
    user = await make_user(username="listener")

    resp = await _put(client, user, auth_header, "audiobook", pair.audiobook_id,
                      source="audiobook", audio_position_ms=600_000, device_id="phone")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["scope"] == "pair"
    assert body["book_pair_id"] == pair.id
    assert body["audio_position_ms"] == 600_000
    rows = await _bookmarks(db, user.id)
    assert [(r.book_pair_id, r.ebook_id, r.audiobook_id) for r in rows] == [(pair.id, None, None)]


async def test_an_ebook_write_on_a_paired_ebook_lands_on_the_pair(
    client, make_user, auth_header, db
):
    pair = await make_book_pair(db)
    user = await make_user(username="reader")

    resp = await _put(client, user, auth_header, "ebook", pair.ebook_id,
                      source="ebook", epub_chapter=3, epub_progress_percent=12.5, device_id="web")

    assert resp.status_code == 200, resp.text
    assert resp.json()["scope"] == "pair"
    pair_read = await client.get(f"/api/sync/position/pair/{pair.id}", headers=auth_header(user))
    assert pair_read.status_code == 200
    assert pair_read.json()["epub_chapter"] == 3
    assert [r.book_pair_id for r in await _bookmarks(db, user.id)] == [pair.id]


async def test_an_unpaired_audiobook_keeps_its_own_record(client, make_user, auth_header, db):
    ab = await make_audiobook(db, title="Loose")
    user = await make_user(username="listener")

    resp = await _put(client, user, auth_header, "audiobook", ab.id,
                      source="audiobook", audio_position_ms=1_000, device_id="phone")

    assert resp.status_code == 200, resp.text
    assert resp.json()["scope"] == "audiobook"
    rows = await _bookmarks(db, user.id)
    assert [(r.book_pair_id, r.audiobook_id) for r in rows] == [(None, ab.id)]


async def test_a_folded_write_is_judged_against_the_pair_record(
    client, make_user, auth_header, db
):
    """The pair already holds a newer position: the folded write is stale."""
    pair = await make_book_pair(db)
    user = await make_user(username="listener")
    newer = await _put(client, user, auth_header, "pair", pair.id,
                       source="audiobook", audio_position_ms=900_000, device_id="web",
                       captured_at="2026-09-20T12:00:00Z")
    assert newer.status_code == 200, newer.text

    stale = await _put(client, user, auth_header, "audiobook", pair.audiobook_id,
                       source="audiobook", audio_position_ms=5_000, device_id="phone",
                       captured_at="2026-09-01T12:00:00Z")

    assert stale.status_code == 409, stale.text
    assert stale.json()["audio_position_ms"] == 900_000
    assert len(await _bookmarks(db, user.id)) == 1


async def test_a_standalone_reset_on_a_paired_book_still_leaves_the_pair_alone(
    client, make_user, auth_header, db
):
    """Contract § Reset: only the write is folded. Deleting the book's own
    scope must not wipe the pair's record."""
    pair = await make_book_pair(db)
    user = await make_user(username="listener")
    await _put(client, user, auth_header, "pair", pair.id,
               source="audiobook", audio_position_ms=300_000, device_id="web")

    reset = await client.delete(
        f"/api/sync/position/audiobook/{pair.audiobook_id}", headers=auth_header(user))

    assert reset.status_code == 200, reset.text
    still = await client.get(f"/api/sync/position/pair/{pair.id}", headers=auth_header(user))
    assert still.status_code == 200
    assert still.json()["audio_position_ms"] == 300_000
