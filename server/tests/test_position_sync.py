"""
The canonical position endpoint: one record, one staleness verdict, one
transaction.

A client save used to be two independent PUTs — the since-removed
`/sync/bookmark/{pair}` and `/sync/progress/{type}/{id}` (issue #102) —
adjudicated separately, so one could be accepted while the other was rejected
and the two rows would then describe different positions for the same book,
forever. Nothing reconciled them, and the two readers each restored from a
different one.
"""

import pytest

from tests.factories import make_book_pair

LOCATOR ='{"href":"ch12.xhtml","locations":{"progression":0.4}}'
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


async def test_a_write_without_device_id_does_not_overwrite_an_identified_devices_hint(
    client, make_user, auth_header, db
):
    """An anonymous write files its hint under `unattributed` (issue #251).

    The hint used to be keyed on `bookmark.device_id` — the *last* device to
    identify itself, which survives an anonymous write — so a scripted or
    third-party client with no `device_id` overwrote whichever real device
    wrote before it. That is exactly what the per-device table exists to
    prevent, and for a Readium locator it means handing phone A a page another
    writer rendered.
    """
    pair = await make_book_pair(db)
    user = await make_user(username="reader")
    other_locator = '{"href":"ch12.xhtml","locations":{"progression":0.9}}'

    await _put(
        client, user, auth_header, "pair", pair.id,
        epub_chapter=12, device_id="a", device_name="Phone A",
        hint={"kind": "readium_locator", "value": LOCATOR},
        captured_at="2026-07-30T10:00:00Z",
    )
    await _put(
        client, user, auth_header, "pair", pair.id,
        epub_chapter=12, device_id=None, device_name=None,
        hint={"kind": "readium_locator", "value": other_locator},
        captured_at="2026-07-30T11:00:00Z",
    )

    hints = (await _get(client, user, auth_header, "pair", pair.id)).json()["hints"]
    by_device = {h["device_id"]: h["value"] for h in hints}
    assert by_device == {"a": LOCATOR, "unattributed": other_locator}


async def test_unattributed_hint_is_not_served_to_an_identified_device(
    client, make_user, auth_header, db
):
    """The consequence the key fixes: the restore ladder for device "a" plans
    from A's own locator, never the anonymous writer's."""
    from services.position_resolver import plan_restore

    pair = await make_book_pair(db)
    user = await make_user(username="reader")
    other_locator = '{"href":"ch12.xhtml","locations":{"progression":0.9}}'

    await _put(
        client, user, auth_header, "pair", pair.id,
        epub_chapter=12, device_id="a", device_name="Phone A",
        hint={"kind": "readium_locator", "value": LOCATOR},
        captured_at="2026-07-30T10:00:00Z",
    )
    await _put(
        client, user, auth_header, "pair", pair.id,
        epub_chapter=12, device_id=None, device_name=None,
        hint={"kind": "readium_locator", "value": other_locator},
        captured_at="2026-07-30T11:00:00Z",
    )

    position = (await _get(client, user, auth_header, "pair", pair.id)).json()
    steps = plan_restore(
        position, spine_count=40, device_id="a", hint_kind="readium_locator"
    )
    assert steps[0].kind == "hint"
    assert steps[0].value == LOCATOR


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


# ---------- source claiming (issue: background writes re-stamping source) ----------

async def test_a_background_write_without_source_keeps_the_stored_source(
    client, make_user, auth_header, db
):
    """Only a foreground, user-initiated write claims the format. A background
    player save (service teardown, Android Auto heartbeat) must be able to
    move the position without re-stamping `source` — that re-stamp is the
    production bug this test pins."""
    pair = await make_book_pair(db)
    user = await make_user(username="reader")

    await _put(
        client, user, auth_header, "pair", pair.id,
        source="ebook", epub_chapter=12, epub_progress_percent=30.0,
        captured_at="2026-07-30T10:00:00Z",
    )

    background = await _put(
        client, user, auth_header, "pair", pair.id,
        source=None, audio_position_ms=900000,
        captured_at="2026-07-30T11:00:00Z",
    )
    assert background.status_code == 200, background.text
    assert background.json()["source"] == "ebook"
    assert background.json()["audio_position_ms"] == 900000


async def test_first_ever_write_with_no_source_serializes_cleanly(
    client, make_user, auth_header, db
):
    """A brand-new row created by a source-less write must not break the
    response serializer — whatever the model default ends up being."""
    pair = await make_book_pair(db)
    user = await make_user(username="reader")

    put = await _put(
        client, user, auth_header, "pair", pair.id,
        source=None, audio_position_ms=5000,
        captured_at="2026-07-30T10:00:00Z",
    )
    assert put.status_code == 200, put.text

    got = await _get(client, user, auth_header, "pair", pair.id)
    assert got.status_code == 200
    assert got.json()["audio_position_ms"] == 5000


async def test_an_explicit_later_claim_still_updates_source(
    client, make_user, auth_header, db
):
    """Omission means "leave alone" — it must not mean "can never be
    claimed". A write that does carry a source still updates it."""
    pair = await make_book_pair(db)
    user = await make_user(username="reader")

    await _put(
        client, user, auth_header, "pair", pair.id,
        source="ebook", epub_chapter=12, captured_at="2026-07-30T10:00:00Z",
    )
    claimed = await _put(
        client, user, auth_header, "pair", pair.id,
        source="audiobook", audio_position_ms=42000,
        captured_at="2026-07-30T11:00:00Z",
    )
    assert claimed.status_code == 200, claimed.text
    assert claimed.json()["source"] == "audiobook"


async def test_a_background_write_with_no_source_can_still_append_to_the_log(
    client, make_user, auth_header, db
):
    """A background save at a session boundary (pause/stop) sets
    `append_to_log=True` but may carry no `source` — the log row's `source`
    column is NOT NULL with no default, so this must fall back to the
    bookmark's resolved (kept) source rather than trying to insert None."""
    pair = await make_book_pair(db)
    user = await make_user(username="reader")

    await _put(
        client, user, auth_header, "pair", pair.id,
        source="audiobook", audio_position_ms=1000,
        captured_at="2026-07-30T10:00:00Z",
    )

    boundary = await _put(
        client, user, auth_header, "pair", pair.id,
        source=None, audio_position_ms=900000, append_to_log=True,
        captured_at="2026-07-30T11:00:00Z",
    )
    assert boundary.status_code == 200, boundary.text
    assert boundary.json()["source"] == "audiobook"

    log = await client.get(
        f"/api/sync/bookmark/{pair.id}/log", headers=auth_header(user))
    assert log.status_code == 200
    assert len(log.json()) == 1
    assert log.json()[0]["source"] == "audiobook"


@pytest.mark.parametrize(
    "query,expected",
    [
        ("limit=100000", 422),
        ("limit=0", 422),
        ("limit=200", 200),
        ("limit=50", 200),
    ],
)
async def test_the_bookmark_log_limit_is_bounded(
    client, make_user, auth_header, db, query, expected
):
    """`limit` was a bare int with no ceiling — the same defect issue #208 found
    on `queue/history`, in the one read endpoint every reading client polls."""
    pair = await make_book_pair(db)
    user = await make_user(username=f"logreader{abs(hash(query)) % 10000}")

    resp = await client.get(
        f"/api/sync/bookmark/{pair.id}/log?{query}", headers=auth_header(user)
    )

    assert resp.status_code == expected


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


# ---------- completion (issue #56) ----------
#
# "Finished" used to be whatever each client remembered to send: the web
# player set it on the audio `ended` event, the Android player on STATE_ENDED,
# and no reader ever set it. The rule now lives on the server so every client
# inherits it: a write whose position *crosses into* the end zone completes
# the book. Explicit values always win, and nothing ever auto-clears.

async def test_ebook_write_crossing_the_end_zone_completes_the_book(
    client, make_user, auth_header, db
):
    pair = await make_book_pair(db)
    user = await make_user(username="reader")

    mid = await _put(
        client, user, auth_header, "pair", pair.id,
        epub_chapter=10, epub_progress_percent=50.0,
        captured_at="2026-07-30T10:00:00Z",
    )
    assert mid.json()["is_completed"] is False

    end = await _put(
        client, user, auth_header, "pair", pair.id,
        epub_chapter=40, epub_progress_percent=98.5,
        captured_at="2026-07-30T11:00:00Z",
    )
    assert end.status_code == 200, end.text
    assert end.json()["is_completed"] is True

    # The projection the Home/Continue lists read follows — both rows of a pair.
    progress = await client.get("/api/sync/progress", headers=auth_header(user))
    flags = {r["media_type"]: r["is_completed"] for r in progress.json()}
    assert flags == {"ebook": True, "audiobook": True}


async def test_first_ever_write_already_in_the_end_zone_completes(
    client, make_user, auth_header, db
):
    """No previous position counts as "below the threshold" — a book opened
    straight at the acknowledgements is finished, not merely started."""
    pair = await make_book_pair(db)
    user = await make_user(username="reader")

    put = await _put(
        client, user, auth_header, "ebook", pair.ebook_id,
        epub_chapter=40, epub_progress_percent=99.0,
        captured_at="2026-07-30T10:00:00Z",
    )
    assert put.json()["is_completed"] is True


async def test_audio_write_within_the_tail_completes(
    client, make_user, auth_header, db
):
    pair = await make_book_pair(db, duration_seconds=3600)
    user = await make_user(username="reader")

    mid = await _put(
        client, user, auth_header, "pair", pair.id, source="audiobook",
        audio_position_ms=1_800_000, captured_at="2026-07-30T10:00:00Z",
    )
    assert mid.json()["is_completed"] is False

    # 60 s before the end — inside the 120 s tail.
    end = await _put(
        client, user, auth_header, "pair", pair.id, source="audiobook",
        audio_position_ms=3_540_000, captured_at="2026-07-30T11:00:00Z",
    )
    assert end.json()["is_completed"] is True


async def test_audio_write_with_unknown_duration_never_auto_completes(
    client, make_user, auth_header, db
):
    """No length, no end zone. The client's own end-of-stream write still
    completes the book (it sends `is_completed` explicitly)."""
    pair = await make_book_pair(db, duration_seconds=None)
    user = await make_user(username="reader")

    put = await _put(
        client, user, auth_header, "audiobook", pair.audiobook_id,
        source="audiobook", audio_position_ms=99_999_999,
        captured_at="2026-07-30T10:00:00Z",
    )
    assert put.json()["is_completed"] is False


async def test_explicit_is_completed_false_beats_the_rule(
    client, make_user, auth_header, db
):
    pair = await make_book_pair(db)
    user = await make_user(username="reader")

    put = await _put(
        client, user, auth_header, "pair", pair.id,
        epub_chapter=40, epub_progress_percent=99.0, is_completed=False,
        captured_at="2026-07-30T10:00:00Z",
    )
    assert put.json()["is_completed"] is False


async def test_un_finishing_sticks_while_the_position_stays_in_the_end_zone(
    client, make_user, auth_header, db
):
    """The rule fires on *crossing* into the zone, not on being in it. A user
    who un-finishes a book they're still sitting at the end of must not have
    the very next heartbeat finish it again."""
    pair = await make_book_pair(db, duration_seconds=3600)
    user = await make_user(username="reader")

    await _put(
        client, user, auth_header, "pair", pair.id, source="audiobook",
        audio_position_ms=3_590_000, captured_at="2026-07-30T10:00:00Z",
    )
    unfinish = await _put(
        client, user, auth_header, "pair", pair.id,
        is_completed=False, captured_at="2026-07-30T10:01:00Z",
    )
    assert unfinish.json()["is_completed"] is False

    heartbeat = await _put(
        client, user, auth_header, "pair", pair.id, source="audiobook",
        audio_position_ms=3_595_000, captured_at="2026-07-30T10:02:00Z",
    )
    assert heartbeat.json()["is_completed"] is False

    # Never auto-cleared either: a completed book stays completed on a
    # mid-book write (re-reading a chapter is not un-finishing).
    await _put(
        client, user, auth_header, "pair", pair.id,
        is_completed=True, captured_at="2026-07-30T10:03:00Z",
    )
    back = await _put(
        client, user, auth_header, "pair", pair.id, source="audiobook",
        audio_position_ms=1_000_000, captured_at="2026-07-30T10:04:00Z",
    )
    assert back.json()["is_completed"] is True


async def test_re_entering_the_end_zone_completes_again(
    client, make_user, auth_header, db
):
    pair = await make_book_pair(db)
    user = await make_user(username="reader")

    await _put(client, user, auth_header, "pair", pair.id,
               epub_progress_percent=99.0, captured_at="2026-07-30T10:00:00Z")
    await _put(client, user, auth_header, "pair", pair.id,
               is_completed=False, captured_at="2026-07-30T10:01:00Z")
    await _put(client, user, auth_header, "pair", pair.id,
               epub_progress_percent=50.0, captured_at="2026-07-30T10:02:00Z")
    again = await _put(client, user, auth_header, "pair", pair.id,
                       epub_progress_percent=98.0, captured_at="2026-07-30T10:03:00Z")
    assert again.json()["is_completed"] is True


# ---------- the projection carries `source` (issue #215) ----------
#
# `bookmarks.source` is what decides which format a pair opens next, but the
# Home/Continue lists read `user_progress`, which has no such column. The web
# compensated by comparing the two rows' `updated_at` — and since a pair-scoped
# write stamps both rows inside one loop, that comparison always tied and the
# pair always opened in the reader. The projection reports the canonical
# record's `source` instead; no schema change, one extra query.

async def test_progress_rows_report_the_bookmarks_source(
    client, make_user, auth_header, db
):
    pair = await make_book_pair(db)
    user = await make_user(username="listener")

    await _put(
        client, user, auth_header, "pair", pair.id,
        source="audiobook", audio_position_ms=90_000,
        captured_at="2026-07-30T10:00:00Z",
    )

    rows = (await client.get("/api/sync/progress", headers=auth_header(user))).json()
    sources = {r["media_type"]: r["source"] for r in rows}
    # Both halves of a pair share one canonical record, so both rows report
    # the same claim -- either one answers "where does this pair open?".
    assert sources == {"ebook": "audiobook", "audiobook": "audiobook"}


async def test_progress_source_follows_a_later_claim(
    client, make_user, auth_header, db
):
    """The claim moves with actual consumption, and a save that omits `source`
    leaves it alone (contract § Who may claim `source`)."""
    pair = await make_book_pair(db)
    user = await make_user(username="reader")

    await _put(client, user, auth_header, "pair", pair.id, source="audiobook",
               audio_position_ms=90_000, captured_at="2026-07-30T10:00:00Z")
    await _put(client, user, auth_header, "pair", pair.id, source="ebook",
               epub_chapter=4, captured_at="2026-07-30T10:05:00Z")

    rows = (await client.get("/api/sync/progress", headers=auth_header(user))).json()
    assert {r["source"] for r in rows} == {"ebook"}

    # A background save omits `source`; the stored claim survives it.
    body = {"device_id": "pixel", "audio_position_ms": 95_000,
            "captured_at": "2026-07-30T10:06:00Z"}
    await client.put(f"/api/sync/position/pair/{pair.id}",
                     headers=auth_header(user), json=body)

    rows = (await client.get("/api/sync/progress", headers=auth_header(user))).json()
    assert {r["source"] for r in rows} == {"ebook"}


async def test_progress_source_is_null_when_no_bookmark_backs_the_row(
    client, make_user, auth_header, db
):
    """A row with no canonical record behind it has nothing to claim, and must
    say so rather than defaulting to a format."""
    from models.progress import ProgressType, UserProgress
    from tests.factories import make_ebook

    user = await make_user(username="reader")
    eb = await make_ebook(db, title="Orphan", filename="orphan.epub")
    db.add(UserProgress(user_id=user.id, media_type=ProgressType.EBOOK,
                        ebook_id=eb.id, epub_progress_percent=12.0,
                        is_completed=False))
    await db.commit()

    rows = (await client.get("/api/sync/progress", headers=auth_header(user))).json()
    assert len(rows) == 1
    assert rows[0]["source"] is None


async def test_standalone_progress_rows_report_their_own_source(
    client, make_user, auth_header, db
):
    """Standalone media keep their own scope, so the row reads the standalone
    bookmark rather than a pair's."""
    from tests.factories import make_audiobook

    user = await make_user(username="listener")
    ab = await make_audiobook(db, title="Solo", filename="solo.m4b")

    await _put(client, user, auth_header, "audiobook", ab.id, source="audiobook",
               audio_position_ms=30_000, captured_at="2026-07-30T10:00:00Z")

    rows = (await client.get("/api/sync/progress", headers=auth_header(user))).json()
    assert [r["source"] for r in rows] == ["audiobook"]


async def test_completion_thresholds_are_settings(monkeypatch, client, make_user,
                                                  auth_header, db):
    """One place decides the thresholds (issue #56 asked for exactly that)."""
    from config import settings
    monkeypatch.setattr(settings, "auto_complete_epub_percent", 90.0)
    pair = await make_book_pair(db)
    user = await make_user(username="reader")

    put = await _put(client, user, auth_header, "pair", pair.id,
                     epub_progress_percent=91.0, captured_at="2026-07-30T10:00:00Z")
    assert put.json()["is_completed"] is True
