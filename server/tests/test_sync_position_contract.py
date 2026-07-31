"""
Cross-device position contract (issues #40, #61).

`epub_chapter` + `epub_sentence_index` is the portable anchor. `epub_locator`
(Readium, Android) and `epub_cfi` (epub.js, web) are *device-local hints*.

A hint is only meaningful while it still matches the anchor — but the server
must never enforce that by **deleting** it. An earlier revision did, clearing
the locator whenever an ebook-source write moved the anchor without supplying
one. The Android reader treated the resulting "no locator" as "no position",
opened at the title page, and its autosave wrote chapter 0 over a real
position. Freshness is tracked by tagging a hint with the anchor it was
captured at; a stale hint is ignored, never destroyed.

These tests pin the preservation rules. The tagging itself lives in
`position_hints` (see test_position_sync.py).
"""

from tests.factories import make_book_pair

LOCATOR = '{"href":"/ch1.xhtml","locations":{"progression":0.4}}'
NEW_LOCATOR = '{"href":"/ch2.xhtml","locations":{"progression":0.1}}'


async def _put_bookmark(client, user, auth_header, pair_id, **body):
    return await client.put(
        f"/api/sync/bookmark/{pair_id}", headers=auth_header(user), json=body
    )


async def test_ebook_write_without_locator_preserves_the_stored_locator(
    client, make_user, auth_header, db
):
    """The web reader sends chapter/sentence but no locator. That must leave
    the phone's locator alone.

    This is the regression that lost a real position: clearing it here left the
    Android reader with no position to restore, so it opened at page one and
    then persisted chapter 0.
    """
    pair = await make_book_pair(db)
    user = await make_user(username="reader")

    seeded = await _put_bookmark(
        client, user, auth_header, pair.id,
        source="ebook", epub_chapter=1, epub_sentence_index=3,
        epub_locator=LOCATOR, locator_audio_ms=12000,
    )
    assert seeded.status_code == 200, seeded.text
    assert seeded.json()["epub_locator"] == LOCATOR

    moved = await _put_bookmark(
        client, user, auth_header, pair.id,
        source="ebook", epub_chapter=5, epub_sentence_index=1,
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["epub_locator"] == LOCATOR
    assert moved.json()["locator_audio_ms"] == 12000

    got = await client.get(f"/api/sync/bookmark/{pair.id}", headers=auth_header(user))
    assert got.json()["epub_locator"] == LOCATOR


async def test_audiobook_write_without_locator_preserves_locator(
    client, make_user, auth_header, db
):
    """Audio drift doesn't invalidate the ebook page — Android's
    locator_audio_ms delta check handles that locally."""
    pair = await make_book_pair(db)
    user = await make_user(username="reader")

    await _put_bookmark(
        client, user, auth_header, pair.id,
        source="ebook", epub_chapter=1, epub_sentence_index=3,
        epub_locator=LOCATOR, locator_audio_ms=12000,
    )
    aud = await _put_bookmark(
        client, user, auth_header, pair.id,
        source="audiobook", audio_position_ms=99000,
    )
    assert aud.status_code == 200, aud.text
    assert aud.json()["epub_locator"] == LOCATOR
    assert aud.json()["locator_audio_ms"] == 12000


async def test_ebook_write_at_same_position_preserves_locator(
    client, make_user, auth_header, db
):
    """A no-op re-save (same anchor) is not a position change, so the hint
    stays valid."""
    pair = await make_book_pair(db)
    user = await make_user(username="reader")

    await _put_bookmark(
        client, user, auth_header, pair.id,
        source="ebook", epub_chapter=1, epub_sentence_index=3,
        epub_locator=LOCATOR,
    )
    same = await _put_bookmark(
        client, user, auth_header, pair.id,
        source="ebook", epub_chapter=1, epub_sentence_index=3,
    )
    assert same.status_code == 200, same.text
    assert same.json()["epub_locator"] == LOCATOR


async def test_supplied_locator_replaces_stored_one(client, make_user, auth_header, db):
    pair = await make_book_pair(db)
    user = await make_user(username="reader")

    await _put_bookmark(
        client, user, auth_header, pair.id,
        source="ebook", epub_chapter=1, epub_sentence_index=3, epub_locator=LOCATOR,
    )
    upd = await _put_bookmark(
        client, user, auth_header, pair.id,
        source="ebook", epub_chapter=2, epub_sentence_index=0,
        epub_locator=NEW_LOCATOR, locator_audio_ms=30000,
    )
    assert upd.status_code == 200, upd.text
    assert upd.json()["epub_locator"] == NEW_LOCATOR
    assert upd.json()["locator_audio_ms"] == 30000


async def test_locator_audio_ms_round_trips(client, make_user, auth_header, db):
    """A second Android device needs the audio anchor to reuse the locator
    (issue #40 step 4) — the server previously had no such field."""
    pair = await make_book_pair(db)
    user = await make_user(username="reader")

    put = await _put_bookmark(
        client, user, auth_header, pair.id,
        source="ebook", epub_chapter=1, epub_sentence_index=3,
        epub_locator=LOCATOR, locator_audio_ms=42000,
    )
    assert put.status_code == 200, put.text
    assert put.json()["locator_audio_ms"] == 42000

    got = await client.get(f"/api/sync/bookmark/{pair.id}", headers=auth_header(user))
    assert got.json()["locator_audio_ms"] == 42000


async def test_ebook_progress_write_without_cfi_preserves_the_stored_cfi(
    client, make_user, auth_header, db
):
    """Android writes ebook progress with chapter/percent and no CFI (#61).
    Android cannot produce a CFI, so clearing the web reader's would leave web
    with nothing precise to restore from — the same failure as the locator."""
    pair = await make_book_pair(db)
    user = await make_user(username="reader")
    url = f"/api/sync/progress/ebook/{pair.ebook_id}"
    cfi = "epubcfi(/6/4!/4/2/2/1:12)"

    seeded = await client.put(url, headers=auth_header(user), json={
        "book_pair_id": pair.id, "epub_cfi": cfi,
        "epub_chapter": 1, "epub_progress_percent": 10.0,
    })
    assert seeded.status_code == 200, seeded.text
    assert seeded.json()["epub_cfi"] == cfi

    moved = await client.put(url, headers=auth_header(user), json={
        "book_pair_id": pair.id, "epub_chapter": 6, "epub_progress_percent": 55.0,
    })
    assert moved.status_code == 200, moved.text
    assert moved.json()["epub_cfi"] == cfi


async def test_progress_write_at_same_position_preserves_cfi(
    client, make_user, auth_header, db
):
    pair = await make_book_pair(db)
    user = await make_user(username="reader")
    url = f"/api/sync/progress/ebook/{pair.ebook_id}"
    cfi = "epubcfi(/6/4!/4/2/2/1:12)"

    await client.put(url, headers=auth_header(user), json={
        "book_pair_id": pair.id, "epub_cfi": cfi,
        "epub_chapter": 1, "epub_progress_percent": 10.0,
    })
    same = await client.put(url, headers=auth_header(user), json={
        "book_pair_id": pair.id, "epub_chapter": 1, "epub_progress_percent": 10.0,
    })
    assert same.status_code == 200, same.text
    assert same.json()["epub_cfi"] == cfi
