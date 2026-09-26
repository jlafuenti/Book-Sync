"""
A write that hands the server its own stamp back is not a capture (issue #726).

Before the #679 fix, a server-side rewrite bumped `updated_at` on positions that
had no `captured_at`, and migration 0024 then copied that into `captured_at`: the
rewrite's time became the position's "last read". Migration 0027 moves those
capture times back, leaving `updated_at` alone. The Android client already holds
the false stamp, though, and reconciles by comparing capture times, so on its
next sync it would find its copy "newer" and push the false date back.

That push carries a `captured_at` equal (to the millisecond, or the few ms the
projection rows differ by) to the record's own `updated_at`, on a record whose
capture predates that update by far more than any network delay. Such a write
is answered as stale: 409 with the stored state, which the client adopts.
"""
from datetime import datetime, timedelta

from sqlalchemy import select

from models.bookmark import Bookmark
from models.progress import UserProgress
from tests.factories import make_book_pair

REPAIRED = datetime(2026, 4, 23, 2, 15, 3)          # what 0027 restores
SERVER_STAMP = datetime(2026, 9, 21, 13, 7, 23, 615241)  # the realign's bump


def _iso(dt):
    """How Android sends a capture time: millisecond precision, `Z`-suffixed."""
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


async def _put(client, user, auth_header, scope, ident, **body):
    return await client.put(
        f"/api/sync/position/{scope}/{ident}", headers=auth_header(user), json=body)


async def _repaired_pair_record(client, db, user, auth_header):
    """A pair record in the state migration 0027 leaves: captured long before
    its last server-side update."""
    pair = await make_book_pair(db)
    resp = await _put(client, user, auth_header, "pair", pair.id,
                      source="ebook", epub_chapter=2, device_id="seed")
    assert resp.status_code == 200, resp.text
    row = (await db.execute(
        select(Bookmark).where(Bookmark.book_pair_id == pair.id))).scalar_one()
    row.captured_at = REPAIRED
    row.updated_at = SERVER_STAMP
    await db.commit()
    return pair


async def test_the_server_stamp_pushed_back_is_answered_as_stale(
    client, make_user, auth_header, db
):
    user = await make_user(username="reader")
    pair = await _repaired_pair_record(client, db, user, auth_header)

    resp = await _put(client, user, auth_header, "pair", pair.id,
                      source="ebook", epub_chapter=2, device_id="phone",
                      captured_at=_iso(SERVER_STAMP))

    assert resp.status_code == 409, resp.text
    assert resp.json()["captured_at"].startswith("2026-04-23T02:15:03")


async def test_a_projection_rows_stamp_a_few_ms_off_is_an_echo_too(
    client, make_user, auth_header, db
):
    """The audiobook projection row was stamped a few ms after the bookmark;
    a write folded onto the pair from that row is the same echo."""
    user = await make_user(username="reader")
    pair = await _repaired_pair_record(client, db, user, auth_header)

    resp = await _put(client, user, auth_header, "audiobook", pair.audiobook_id,
                      audio_position_ms=240, device_id="phone",
                      captured_at=_iso(SERVER_STAMP + timedelta(milliseconds=5)))

    assert resp.status_code == 409, resp.text


async def test_a_projection_rows_own_stamp_seconds_later_is_an_echo_too(
    client, make_user, auth_header, db
):
    """A batch could write a projection row well after its bookmark (up to 16 s
    measured on a live instance). The client pushes the progress row's stamp
    back, so that row's `updated_at` identifies the echo too."""
    user = await make_user(username="reader")
    pair = await _repaired_pair_record(client, db, user, auth_header)
    proj_stamp = SERVER_STAMP + timedelta(seconds=16, milliseconds=459)
    rows = (await db.execute(
        select(UserProgress).where(UserProgress.user_id == user.id))).scalars().all()
    assert rows
    for row in rows:
        row.captured_at = REPAIRED
        row.updated_at = proj_stamp
    await db.commit()

    resp = await _put(client, user, auth_header, "audiobook", pair.audiobook_id,
                      audio_position_ms=240, device_id="phone",
                      captured_at=proj_stamp.isoformat() + "Z")

    assert resp.status_code == 409, resp.text
    assert resp.json()["captured_at"].startswith("2026-04-23T02:15:03")


async def test_a_real_capture_after_the_server_stamp_is_accepted(
    client, make_user, auth_header, db
):
    user = await make_user(username="reader")
    pair = await _repaired_pair_record(client, db, user, auth_header)

    resp = await _put(client, user, auth_header, "pair", pair.id,
                      source="ebook", epub_chapter=5, device_id="phone",
                      captured_at=_iso(SERVER_STAMP + timedelta(seconds=5)))

    assert resp.status_code == 200, resp.text
    assert resp.json()["epub_chapter"] == 5


async def test_a_capture_at_the_updated_at_of_an_ordinary_record_is_accepted(
    client, make_user, auth_header, db
):
    """An ordinary record's capture is only a network delay behind its update;
    matching that update is no evidence of an echo."""
    user = await make_user(username="reader")
    pair = await make_book_pair(db)
    first = await _put(client, user, auth_header, "pair", pair.id,
                       source="ebook", epub_chapter=2, device_id="web",
                       captured_at="2026-09-21T13:07:23.000Z")
    assert first.status_code == 200, first.text
    row = (await db.execute(
        select(Bookmark).where(Bookmark.book_pair_id == pair.id))).scalar_one()
    row.updated_at = datetime(2026, 9, 21, 13, 7, 23, 400000)
    await db.commit()

    resp = await _put(client, user, auth_header, "pair", pair.id,
                      source="ebook", epub_chapter=3, device_id="phone",
                      captured_at="2026-09-21T13:07:23.400Z")

    assert resp.status_code == 200, resp.text
