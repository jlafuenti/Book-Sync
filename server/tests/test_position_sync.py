"""
The canonical position endpoint: one record, one staleness verdict, one
transaction.

Previously a client save was two independent PUTs — `/sync/bookmark/{pair}` and
`/sync/progress/{type}/{id}` — adjudicated separately, so one could be accepted
while the other was rejected and the two rows would then describe different
positions for the same book, forever. Nothing reconciled them, and the two
readers each restored from a different one.
"""

from tests.factories import make_book_pair, make_sync_map

LOCATOR = '{"href":"ch12.xhtml","locations":{"progression":0.4}}'
CFI = "epubcfi(/6/26!/4/2/2/1:0)"


def _body(**kw):
    body = {"source": "ebook", "device_id": "pixel", "device_name": "Jesse's Pixel"}
    body.update(kw)
    return body


async def _put(client, user, auth_header, scope, ident, **kw):
    return await client.put(
        f"/api/sync/position/{scope}/{ident}", headers=auth_header(user), json=_body(**kw)
    )


async def _get(client, user, auth_header, scope, ident):
    return await client.get(
        f"/api/sync/position/{scope}/{ident}", headers=auth_header(user)
    )


# ---------- the canonical record ----------

async def test_one_write_populates_the_record_and_the_derived_progress_rows(
    client, make_user, auth_header, db
):
    pair = await make_book_pair(db)
    user = await make_user(username="reader")

    put = await _put(
        client, user, auth_header, "pair", pair.id,
        epub_chapter=39, epub_sentence_index=4,
        epub_text_preview="althea counted the ships at anchor",
        epub_progress_percent=46.81,
        captured_at="2026-07-30T16:49:36Z",
    )
    assert put.status_code == 200, put.text
    body = put.json()
    assert body["epub_chapter"] == 39
    assert body["epub_progress_percent"] == 46.81

    # The Continue lists read user_progress; it must be a projection of the
    # same write, not a separate thing a client has to remember to send.
    progress = await client.get("/api/sync/progress", headers=auth_header(user))
    ebook_rows = [r for r in progress.json() if r["media_type"] == "ebook"]
    assert len(ebook_rows) == 1
    assert ebook_rows[0]["epub_progress_percent"] == 46.81
    assert ebook_rows[0]["book_pair_id"] == pair.id


async def test_get_on_an_unread_book_returns_204_and_creates_nothing(
    client, make_user, auth_header, db
):
    """A read that manufactures a position makes "do I have one?" unanswerable
    — and the old GET /bookmark did exactly that, inventing a chapter-0 row."""
    pair = await make_book_pair(db)
    user = await make_user(username="reader")

    got = await _get(client, user, auth_header, "pair", pair.id)
    assert got.status_code == 204

    again = await _get(client, user, auth_header, "pair", pair.id)
    assert again.status_code == 204


async def test_a_stale_write_changes_nothing_at_all(client, make_user, auth_header, db):
    """Atomicity: the rejected write must not land in *any* row. With two
    endpoints, a stale bookmark write could be refused while its sibling
    progress write was applied."""
    pair = await make_book_pair(db)
    user = await make_user(username="reader")

    await _put(
        client, user, auth_header, "pair", pair.id,
        epub_chapter=39, epub_progress_percent=46.81,
        captured_at="2026-07-30T16:49:36Z",
    )
    before = (await _get(client, user, auth_header, "pair", pair.id)).json()
    progress_before = (await client.get(
        "/api/sync/progress", headers=auth_header(user))).json()

    stale = await _put(
        client, user, auth_header, "pair", pair.id,
        epub_chapter=2, epub_progress_percent=3.0,
        captured_at="2026-07-30T10:00:00Z",
    )
    assert stale.status_code == 409
    assert stale.json()["epub_chapter"] == 39

    after = (await _get(client, user, auth_header, "pair", pair.id)).json()
    progress_after = (await client.get(
        "/api/sync/progress", headers=auth_header(user))).json()
    assert after == before
    assert progress_after == progress_before


# ---------- hints ----------

async def test_hint_survives_an_anchor_move_and_stops_being_current(
    client, make_user, auth_header, db
):
    """The central fix. The old design deleted the hint here, which left the
    other reader with no position and let chapter 0 be written over a real
    one."""
    pair = await make_book_pair(db)
    user = await make_user(username="reader")

    await _put(
        client, user, auth_header, "pair", pair.id,
        epub_chapter=12, epub_progress_percent=30.0,
        hint={"kind": "readium_locator", "value": LOCATOR},
        captured_at="2026-07-30T10:00:00Z",
    )
    first = (await _get(client, user, auth_header, "pair", pair.id)).json()
    assert [h["current"] for h in first["hints"]] == [True]
    revision = first["anchor_revision"]

    # Another client moves the anchor and has no Readium locator to offer.
    moved = await _put(
        client, user, auth_header, "pair", pair.id,
        epub_chapter=39, epub_progress_percent=46.81,
        device_id="web-firefox", device_name="Web · Firefox",
        captured_at="2026-07-30T11:00:00Z",
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["anchor_revision"] > revision

    after = (await _get(client, user, auth_header, "pair", pair.id)).json()
    hints = after["hints"]
    assert len(hints) == 1, "the hint must still exist"
    assert hints[0]["current"] is False
    assert hints[0]["value"] == LOCATOR


async def test_audio_only_movement_keeps_the_hint_current(
    client, make_user, auth_header, db
):
    """Audio drift doesn't invalidate a page, so it must not bump the anchor."""
    pair = await make_book_pair(db)
    user = await make_user(username="reader")

    await _put(
        client, user, auth_header, "pair", pair.id,
        epub_chapter=12, hint={"kind": "readium_locator", "value": LOCATOR},
        captured_at="2026-07-30T10:00:00Z",
    )
    await _put(
        client, user, auth_header, "pair", pair.id,
        source="audiobook", audio_position_ms=900000,
        captured_at="2026-07-30T11:00:00Z",
    )

    after = (await _get(client, user, auth_header, "pair", pair.id)).json()
    assert after["hints"][0]["current"] is True


async def test_two_devices_keep_independent_hints(client, make_user, auth_header, db):
    """They used to share one epub_locator column, so whichever wrote last
    destroyed the other's precise position."""
    pair = await make_book_pair(db)
    user = await make_user(username="reader")

    await _put(
        client, user, auth_header, "pair", pair.id,
        epub_chapter=12, hint={"kind": "readium_locator", "value": LOCATOR},
        captured_at="2026-07-30T10:00:00Z",
    )
    await _put(
        client, user, auth_header, "pair", pair.id,
        epub_chapter=12, device_id="web-firefox", device_name="Web · Firefox",
        hint={"kind": "epubjs_cfi", "value": CFI},
        captured_at="2026-07-30T11:00:00Z",
    )

    hints = (await _get(client, user, auth_header, "pair", pair.id)).json()["hints"]
    by_device = {h["device_id"]: h for h in hints}
    assert by_device["pixel"]["value"] == LOCATOR
    assert by_device["web-firefox"]["value"] == CFI
    # Same anchor throughout, so both are still usable.
    assert all(h["current"] for h in hints)


async def test_re_saving_the_same_anchor_does_not_bump_the_revision(
    client, make_user, auth_header, db
):
    pair = await make_book_pair(db)
    user = await make_user(username="reader")

    first = await _put(
        client, user, auth_header, "pair", pair.id,
        epub_chapter=12, epub_progress_percent=30.0,
        captured_at="2026-07-30T10:00:00Z",
    )
    again = await _put(
        client, user, auth_header, "pair", pair.id,
        epub_chapter=12, epub_progress_percent=30.0,
        captured_at="2026-07-30T10:00:05Z",
    )
    assert again.json()["anchor_revision"] == first.json()["anchor_revision"]


# ---------- partial writes ----------

async def test_a_write_carrying_no_anchor_never_clears_one(
    client, make_user, auth_header, db
):
    pair = await make_book_pair(db)
    user = await make_user(username="reader")

    await _put(
        client, user, auth_header, "pair", pair.id,
        epub_chapter=39, epub_sentence_index=4,
        epub_text_preview="althea counted the ships at anchor",
        epub_progress_percent=46.81, captured_at="2026-07-30T10:00:00Z",
    )
    await _put(
        client, user, auth_header, "pair", pair.id,
        is_completed=True, captured_at="2026-07-30T11:00:00Z",
    )

    after = (await _get(client, user, auth_header, "pair", pair.id)).json()
    assert after["is_completed"] is True
    assert after["epub_chapter"] == 39
    assert after["epub_text_preview"] == "althea counted the ships at anchor"
    assert after["epub_progress_percent"] == 46.81


# ---------- standalone media ----------

async def test_standalone_ebook_gets_a_canonical_record(
    client, make_user, auth_header, db
):
    pair = await make_book_pair(db)
    user = await make_user(username="reader")

    put = await _put(
        client, user, auth_header, "ebook", pair.ebook_id,
        epub_chapter=4, epub_progress_percent=11.0,
        captured_at="2026-07-30T10:00:00Z",
    )
    assert put.status_code == 200, put.text

    got = await _get(client, user, auth_header, "ebook", pair.ebook_id)
    assert got.status_code == 200
    assert got.json()["epub_chapter"] == 4


# ---------- legacy adapters ----------

async def test_legacy_bookmark_put_writes_the_same_canonical_record(
    client, make_user, auth_header, db
):
    """Installed app builds keep working, and an old phone and a new one
    converge on one row instead of fighting over two."""
    pair = await make_book_pair(db)
    await make_sync_map(db, book_pair_id=pair.id)
    user = await make_user(username="reader")

    legacy = await client.put(
        f"/api/sync/bookmark/{pair.id}", headers=auth_header(user),
        json={"source": "ebook", "epub_chapter": 1, "epub_sentence_index": 0,
              "epub_locator": LOCATOR, "device_id": "old-phone",
              "captured_at": "2026-07-30T10:00:00Z"},
    )
    assert legacy.status_code == 200, legacy.text

    canonical = await _get(client, user, auth_header, "pair", pair.id)
    assert canonical.status_code == 200
    body = canonical.json()
    assert body["epub_chapter"] == 1
    assert [h["value"] for h in body["hints"]] == [LOCATOR]


async def test_legacy_bookmark_get_still_returns_the_mirror_columns(
    client, make_user, auth_header, db
):
    pair = await make_book_pair(db)
    user = await make_user(username="reader")

    await _put(
        client, user, auth_header, "pair", pair.id,
        epub_chapter=12, hint={"kind": "readium_locator", "value": LOCATOR},
        captured_at="2026-07-30T10:00:00Z",
    )

    legacy = await client.get(
        f"/api/sync/bookmark/{pair.id}", headers=auth_header(user))
    assert legacy.status_code == 200
    assert legacy.json()["epub_locator"] == LOCATOR


async def test_the_write_response_includes_the_hint_it_just_stored(
    client, make_user, auth_header, db
):
    """The PUT response is meant to be authoritative — a client adopts it as
    its new local state.

    It came back with `hints: []` on production: the session is configured
    `expire_on_commit=False`, so re-reading after the commit returned the
    identity-mapped bookmark still holding the empty `hints` collection loaded
    before the hint was written. The client then believed it had no current
    hint until its next fetch.
    """
    pair = await make_book_pair(db)
    user = await make_user(username="reader")

    # The record must already exist: that is what puts it in the session's
    # identity map with an empty `hints` collection, which the post-commit
    # re-read then hands straight back.
    await _put(
        client, user, auth_header, "pair", pair.id,
        epub_chapter=1, captured_at="2026-07-30T09:00:00Z",
    )

    put = await _put(
        client, user, auth_header, "pair", pair.id,
        epub_chapter=12, hint={"kind": "readium_locator", "value": LOCATOR},
        captured_at="2026-07-30T10:00:00Z",
    )
    assert put.status_code == 200, put.text
    hints = put.json()["hints"]
    assert [h["value"] for h in hints] == [LOCATOR]
    assert hints[0]["current"] is True
