"""
Cross-device position contract (issues #40, #61).

`epub_chapter` + `epub_sentence_index` is the portable anchor. A Readium
locator (Android) and an epub.js CFI (web) are *device-local hints*, stored in
`position_hints` and keyed by device.

A hint is only meaningful while it still matches the anchor — but the server
must never enforce that by **deleting** it. An earlier revision did, clearing
the locator whenever an ebook-source write moved the anchor without supplying
one. The Android reader treated the resulting "no locator" as "no position",
opened at the title page, and its autosave wrote chapter 0 over a real
position. Freshness is tracked by tagging a hint with the anchor it was
captured at; a stale hint is ignored, never destroyed.

These tests pin the preservation rules. The tagging itself lives in
`position_hints` (see test_position_sync.py). They originally drove the legacy
bookmark/progress adapters and the `epub_locator`/`epub_cfi` mirror columns
those wrote; both are gone (issue #102) and the same rules are asserted here
against the canonical endpoint and the hints it returns.
"""

from tests.factories import make_book_pair

LOCATOR = '{"href":"/ch1.xhtml","locations":{"progression":0.4}}'
NEW_LOCATOR = '{"href":"/ch2.xhtml","locations":{"progression":0.1}}'
CFI = "epubcfi(/6/4!/4/2/2/1:12)"

PHONE = "pixel"
LAPTOP = "laptop"


async def _put(client, user, auth_header, scope, ident, **body):
    return await client.put(
        f"/api/sync/position/{scope}/{ident}", headers=auth_header(user), json=body
    )


async def _get(client, user, auth_header, scope, ident):
    return await client.get(
        f"/api/sync/position/{scope}/{ident}", headers=auth_header(user)
    )


def _hint(payload, kind):
    """The stored hint of [kind], current or not — the point of these tests is
    that it is still *there*."""
    return next((h for h in payload["hints"] if h["kind"] == kind), None)


async def test_ebook_write_without_a_locator_preserves_the_stored_one(
    client, make_user, auth_header, db
):
    """The web reader sends chapter/sentence but no Readium locator. That must
    leave the phone's locator alone.

    This is the regression that lost a real position: clearing it here left the
    Android reader with no position to restore, so it opened at page one and
    then persisted chapter 0.
    """
    pair = await make_book_pair(db)
    user = await make_user(username="reader")

    seeded = await _put(
        client, user, auth_header, "pair", pair.id,
        source="ebook", epub_chapter=1, epub_sentence_index=3, device_id=PHONE,
        hint={"kind": "readium_locator", "value": LOCATOR, "audio_position_ms": 12000},
    )
    assert seeded.status_code == 200, seeded.text
    assert _hint(seeded.json(), "readium_locator")["value"] == LOCATOR

    moved = await _put(
        client, user, auth_header, "pair", pair.id,
        source="ebook", epub_chapter=5, epub_sentence_index=1, device_id=LAPTOP,
    )
    assert moved.status_code == 200, moved.text
    stored = _hint(moved.json(), "readium_locator")
    assert stored["value"] == LOCATOR
    assert stored["audio_position_ms"] == 12000
    # The anchor moved, so it is no longer current — but it survives, and the
    # phone makes it current again by re-capturing.
    assert stored["current"] is False

    got = await _get(client, user, auth_header, "pair", pair.id)
    assert _hint(got.json(), "readium_locator")["value"] == LOCATOR


async def test_audiobook_write_without_a_locator_preserves_it_and_keeps_it_current(
    client, make_user, auth_header, db
):
    """Audio drift doesn't invalidate the ebook page — `_anchor_of` deliberately
    excludes `audio_position_ms`, and Android's own audio-delta check handles
    the rest locally."""
    pair = await make_book_pair(db)
    user = await make_user(username="reader")

    await _put(
        client, user, auth_header, "pair", pair.id,
        source="ebook", epub_chapter=1, epub_sentence_index=3, device_id=PHONE,
        hint={"kind": "readium_locator", "value": LOCATOR, "audio_position_ms": 12000},
    )
    aud = await _put(
        client, user, auth_header, "pair", pair.id,
        source="audiobook", audio_position_ms=99000, device_id=PHONE,
    )
    assert aud.status_code == 200, aud.text
    stored = _hint(aud.json(), "readium_locator")
    assert stored["value"] == LOCATOR
    assert stored["audio_position_ms"] == 12000
    assert stored["current"] is True


async def test_ebook_write_at_the_same_position_keeps_the_locator_current(
    client, make_user, auth_header, db
):
    """A no-op re-save (same anchor) is not a position change, so the hint
    stays valid."""
    pair = await make_book_pair(db)
    user = await make_user(username="reader")

    await _put(
        client, user, auth_header, "pair", pair.id,
        source="ebook", epub_chapter=1, epub_sentence_index=3, device_id=PHONE,
        hint={"kind": "readium_locator", "value": LOCATOR},
    )
    same = await _put(
        client, user, auth_header, "pair", pair.id,
        source="ebook", epub_chapter=1, epub_sentence_index=3, device_id=LAPTOP,
    )
    assert same.status_code == 200, same.text
    stored = _hint(same.json(), "readium_locator")
    assert stored["value"] == LOCATOR
    assert stored["current"] is True


async def test_a_supplied_locator_replaces_that_devices_stored_one(
    client, make_user, auth_header, db
):
    pair = await make_book_pair(db)
    user = await make_user(username="reader")

    await _put(
        client, user, auth_header, "pair", pair.id,
        source="ebook", epub_chapter=1, epub_sentence_index=3, device_id=PHONE,
        hint={"kind": "readium_locator", "value": LOCATOR},
    )
    upd = await _put(
        client, user, auth_header, "pair", pair.id,
        source="ebook", epub_chapter=2, epub_sentence_index=0, device_id=PHONE,
        hint={"kind": "readium_locator", "value": NEW_LOCATOR,
              "audio_position_ms": 30000},
    )
    assert upd.status_code == 200, upd.text
    hints = [h for h in upd.json()["hints"] if h["kind"] == "readium_locator"]
    assert len(hints) == 1, "one row per (record, device, kind) — not an append"
    assert hints[0]["value"] == NEW_LOCATOR
    assert hints[0]["audio_position_ms"] == 30000
    assert hints[0]["current"] is True


async def test_the_locators_audio_anchor_round_trips(client, make_user, auth_header, db):
    """A second Android device needs the audio anchor to reuse the locator
    (issue #40 step 4) — the server previously had no such field."""
    pair = await make_book_pair(db)
    user = await make_user(username="reader")

    put = await _put(
        client, user, auth_header, "pair", pair.id,
        source="ebook", epub_chapter=1, epub_sentence_index=3, device_id=PHONE,
        hint={"kind": "readium_locator", "value": LOCATOR, "audio_position_ms": 42000},
    )
    assert put.status_code == 200, put.text
    assert _hint(put.json(), "readium_locator")["audio_position_ms"] == 42000

    got = await _get(client, user, auth_header, "pair", pair.id)
    assert _hint(got.json(), "readium_locator")["audio_position_ms"] == 42000


async def test_an_ebook_write_without_a_cfi_preserves_the_stored_cfi(
    client, make_user, auth_header, db
):
    """Android writes ebook progress with chapter/percent and no CFI (#61).
    Android cannot produce a CFI, so clearing the web reader's would leave web
    with nothing precise to restore from — the same failure as the locator."""
    pair = await make_book_pair(db)
    user = await make_user(username="reader")

    seeded = await _put(
        client, user, auth_header, "pair", pair.id,
        source="ebook", epub_chapter=1, epub_progress_percent=10.0,
        device_id=LAPTOP, hint={"kind": "epubjs_cfi", "value": CFI},
    )
    assert seeded.status_code == 200, seeded.text
    assert _hint(seeded.json(), "epubjs_cfi")["value"] == CFI

    moved = await _put(
        client, user, auth_header, "pair", pair.id,
        source="ebook", epub_chapter=6, epub_progress_percent=55.0, device_id=PHONE,
    )
    assert moved.status_code == 200, moved.text
    assert _hint(moved.json(), "epubjs_cfi")["value"] == CFI


async def test_a_write_at_the_same_position_keeps_the_cfi_current(
    client, make_user, auth_header, db
):
    pair = await make_book_pair(db)
    user = await make_user(username="reader")

    await _put(
        client, user, auth_header, "pair", pair.id,
        source="ebook", epub_chapter=1, epub_progress_percent=10.0,
        device_id=LAPTOP, hint={"kind": "epubjs_cfi", "value": CFI},
    )
    same = await _put(
        client, user, auth_header, "pair", pair.id,
        source="ebook", epub_chapter=1, epub_progress_percent=10.0, device_id=PHONE,
    )
    assert same.status_code == 200, same.text
    stored = _hint(same.json(), "epubjs_cfi")
    assert stored["value"] == CFI
    assert stored["current"] is True


async def test_two_devices_hints_coexist_rather_than_overwriting_each_other(
    client, make_user, auth_header, db
):
    """The reason `position_hints` exists at all: the phone's locator and the
    laptop's CFI used to share one column each, so whoever saved last destroyed
    the other's precision."""
    pair = await make_book_pair(db)
    user = await make_user(username="reader")

    await _put(
        client, user, auth_header, "pair", pair.id,
        source="ebook", epub_chapter=1, epub_sentence_index=3, device_id=PHONE,
        hint={"kind": "readium_locator", "value": LOCATOR},
    )
    both = await _put(
        client, user, auth_header, "pair", pair.id,
        source="ebook", epub_chapter=1, epub_sentence_index=3, device_id=LAPTOP,
        hint={"kind": "epubjs_cfi", "value": CFI},
    )
    assert both.status_code == 200, both.text

    got = (await _get(client, user, auth_header, "pair", pair.id)).json()
    assert _hint(got, "readium_locator")["value"] == LOCATOR
    assert _hint(got, "readium_locator")["device_id"] == PHONE
    assert _hint(got, "epubjs_cfi")["value"] == CFI
    assert _hint(got, "epubjs_cfi")["device_id"] == LAPTOP
