"""
Conflict-resolution contract tests (issue #54, Task 1).

The server previously had blind last-write-wins semantics on both
`PUT /api/sync/bookmark/{pair_id}` and `PUT /api/sync/progress/{media_type}/{media_id}`:
an offline device replaying a stale position could silently clobber a newer
position written by another device. This adds a `captured_at`/`device_id`/
`device_name` contract: an incoming update whose `captured_at` is older than
the currently stored `captured_at` is rejected with 409 instead of applied.

Legacy clients (no `captured_at` sent) must keep working exactly as before
(last-write-wins, no 409) — that's the back-compat guarantee.
"""

from datetime import datetime, timedelta

from tests.factories import make_book_pair

from models.book import EBook
from models.progress import ProgressType


# ---------------------------------------------------------------------------
# Bookmark
# ---------------------------------------------------------------------------

async def test_stale_bookmark_put_returns_409_and_leaves_position_unchanged(client, db, make_user, auth_header):
    user = await make_user()
    pair = await make_book_pair(db)
    headers = auth_header(user)

    newer = datetime(2026, 1, 1, 12, 0, 0)
    older = newer - timedelta(hours=1)

    # First write establishes the "stored" state at `newer`.
    resp = await client.put(
        f"/api/sync/bookmark/{pair.id}",
        json={
            "source": "ebook",
            "epub_chapter": 3,
            "epub_sentence_index": 5,
            "captured_at": newer.isoformat(),
            "device_id": "device-a",
            "device_name": "Pixel 8",
        },
        headers=headers,
    )
    assert resp.status_code == 200

    # A stale replay from an offline device with an older captured_at.
    resp = await client.put(
        f"/api/sync/bookmark/{pair.id}",
        json={
            "source": "ebook",
            "epub_chapter": 0,
            "epub_sentence_index": 0,
            "captured_at": older.isoformat(),
            "device_id": "device-b",
            "device_name": "Old Kindle",
        },
        headers=headers,
    )
    assert resp.status_code == 409
    body = resp.json()
    # The response body is the CURRENT (unchanged) state, not the rejected one.
    assert body["epub_chapter"] == 3
    assert body["epub_sentence_index"] == 5
    assert body["device_id"] == "device-a"

    # Confirm via GET that nothing was overwritten.
    resp = await client.get(f"/api/sync/bookmark/{pair.id}", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["epub_chapter"] == 3
    assert resp.json()["epub_sentence_index"] == 5


async def test_newer_captured_at_bookmark_put_applies_and_persists_device_fields(client, db, make_user, auth_header):
    user = await make_user()
    pair = await make_book_pair(db)
    headers = auth_header(user)

    older = datetime(2026, 1, 1, 12, 0, 0)
    newer = older + timedelta(hours=1)

    resp = await client.put(
        f"/api/sync/bookmark/{pair.id}",
        json={
            "source": "ebook",
            "epub_chapter": 1,
            "epub_sentence_index": 1,
            "captured_at": older.isoformat(),
            "device_id": "device-a",
            "device_name": "Pixel 8",
        },
        headers=headers,
    )
    assert resp.status_code == 200

    resp = await client.put(
        f"/api/sync/bookmark/{pair.id}",
        json={
            "source": "ebook",
            "epub_chapter": 4,
            "epub_sentence_index": 2,
            "captured_at": newer.isoformat(),
            "device_id": "device-b",
            "device_name": "Web Reader",
            "append_to_log": True,
        },
        headers=headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["epub_chapter"] == 4
    assert body["epub_sentence_index"] == 2
    assert body["device_id"] == "device-b"
    assert body["device_name"] == "Web Reader"
    assert body["captured_at"] is not None

    # The appended BookmarkLog row carries the device_name for Session History.
    resp = await client.get(f"/api/sync/bookmark/{pair.id}/log", headers=headers)
    assert resp.status_code == 200
    logs = resp.json()
    assert len(logs) == 1
    assert logs[0]["device_name"] == "Web Reader"
    assert logs[0]["device_id"] == "device-b"
    assert logs[0]["captured_at"] is not None


async def test_legacy_bookmark_put_without_captured_at_applies_lww(client, db, make_user, auth_header):
    """Legacy clients that never send captured_at must keep working: no 409,
    plain last-write-wins, exactly like before this change."""
    user = await make_user()
    pair = await make_book_pair(db)
    headers = auth_header(user)

    resp = await client.put(
        f"/api/sync/bookmark/{pair.id}",
        json={
            "source": "ebook",
            "epub_chapter": 1,
            "epub_sentence_index": 1,
            "captured_at": datetime(2026, 1, 1, 12, 0, 0).isoformat(),
            "device_id": "device-a",
        },
        headers=headers,
    )
    assert resp.status_code == 200

    # Legacy client omits captured_at entirely.
    resp = await client.put(
        f"/api/sync/bookmark/{pair.id}",
        json={
            "source": "ebook",
            "epub_chapter": 9,
            "epub_sentence_index": 9,
        },
        headers=headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["epub_chapter"] == 9
    assert body["epub_sentence_index"] == 9


# ---------------------------------------------------------------------------
# Progress
# ---------------------------------------------------------------------------

async def test_stale_progress_put_returns_409_and_leaves_position_unchanged(client, db, make_user, auth_header):
    user = await make_user()
    eb = EBook(title="Book", filename="b.epub", file_path="/x/b.epub")
    db.add(eb)
    await db.commit()
    await db.refresh(eb)
    headers = auth_header(user)

    newer = datetime(2026, 1, 1, 12, 0, 0)
    older = newer - timedelta(hours=1)

    resp = await client.put(
        f"/api/sync/progress/{ProgressType.EBOOK.value}/{eb.id}",
        json={
            "epub_chapter": 3,
            "captured_at": newer.isoformat(),
            "device_id": "device-a",
            "device_name": "Pixel 8",
        },
        headers=headers,
    )
    assert resp.status_code == 200

    resp = await client.put(
        f"/api/sync/progress/{ProgressType.EBOOK.value}/{eb.id}",
        json={
            "epub_chapter": 0,
            "captured_at": older.isoformat(),
            "device_id": "device-b",
            "device_name": "Old Kindle",
        },
        headers=headers,
    )
    assert resp.status_code == 409
    body = resp.json()
    assert body["epub_chapter"] == 3
    assert body["device_id"] == "device-a"

    resp = await client.get(f"/api/sync/progress/{ProgressType.EBOOK.value}/{eb.id}", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["epub_chapter"] == 3


async def test_newer_captured_at_progress_put_applies_and_persists_device_fields(client, db, make_user, auth_header):
    user = await make_user()
    eb = EBook(title="Book", filename="b.epub", file_path="/x/b.epub")
    db.add(eb)
    await db.commit()
    await db.refresh(eb)
    headers = auth_header(user)

    older = datetime(2026, 1, 1, 12, 0, 0)
    newer = older + timedelta(hours=1)

    resp = await client.put(
        f"/api/sync/progress/{ProgressType.EBOOK.value}/{eb.id}",
        json={
            "epub_chapter": 1,
            "captured_at": older.isoformat(),
            "device_id": "device-a",
            "device_name": "Pixel 8",
        },
        headers=headers,
    )
    assert resp.status_code == 200

    resp = await client.put(
        f"/api/sync/progress/{ProgressType.EBOOK.value}/{eb.id}",
        json={
            "epub_chapter": 7,
            "captured_at": newer.isoformat(),
            "device_id": "device-b",
            "device_name": "Web Reader",
        },
        headers=headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["epub_chapter"] == 7
    assert body["device_id"] == "device-b"
    assert body["device_name"] == "Web Reader"
    assert body["captured_at"] is not None


async def test_legacy_progress_put_without_captured_at_applies_lww(client, db, make_user, auth_header):
    user = await make_user()
    eb = EBook(title="Book", filename="b.epub", file_path="/x/b.epub")
    db.add(eb)
    await db.commit()
    await db.refresh(eb)
    headers = auth_header(user)

    resp = await client.put(
        f"/api/sync/progress/{ProgressType.EBOOK.value}/{eb.id}",
        json={
            "epub_chapter": 1,
            "captured_at": datetime(2026, 1, 1, 12, 0, 0).isoformat(),
            "device_id": "device-a",
        },
        headers=headers,
    )
    assert resp.status_code == 200

    resp = await client.put(
        f"/api/sync/progress/{ProgressType.EBOOK.value}/{eb.id}",
        json={"epub_chapter": 9},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["epub_chapter"] == 9


# ---------------------------------------------------------------------------
# Fix pass — Finding 1: timezone-aware `captured_at` (Z-suffixed) must not crash
# ---------------------------------------------------------------------------

async def test_bookmark_put_with_tz_aware_captured_at_across_two_writes_no_crash(client, db, make_user, auth_header):
    """Regression test for Finding 1: Pydantic parses a 'Z'-suffixed captured_at
    into a timezone-aware datetime, while the value read back from the DB is
    naive. Before the fix, the second PUT's `_is_stale` comparison raised
    TypeError: can't compare offset-naive and offset-aware datetimes (500).
    """
    user = await make_user()
    pair = await make_book_pair(db)
    headers = auth_header(user)

    # First write: establishes a stored (naive, per DB column) captured_at.
    resp = await client.put(
        f"/api/sync/bookmark/{pair.id}",
        json={
            "source": "ebook",
            "epub_chapter": 1,
            "epub_sentence_index": 1,
            "captured_at": "2026-07-20T12:00:00Z",
            "device_id": "device-a",
        },
        headers=headers,
    )
    assert resp.status_code == 200

    # Second write: a newer, tz-aware, Z-suffixed captured_at — must not crash.
    resp = await client.put(
        f"/api/sync/bookmark/{pair.id}",
        json={
            "source": "ebook",
            "epub_chapter": 2,
            "epub_sentence_index": 2,
            "captured_at": "2026-07-20T13:00:00Z",
            "device_id": "device-b",
        },
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["epub_chapter"] == 2

    # Third write: an older, tz-aware, Z-suffixed captured_at — must be
    # rejected as stale (409), not crash.
    resp = await client.put(
        f"/api/sync/bookmark/{pair.id}",
        json={
            "source": "ebook",
            "epub_chapter": 0,
            "epub_sentence_index": 0,
            "captured_at": "2026-07-20T11:00:00Z",
            "device_id": "device-c",
        },
        headers=headers,
    )
    assert resp.status_code == 409
    assert resp.json()["epub_chapter"] == 2


async def test_progress_put_with_tz_aware_captured_at_across_two_writes_no_crash(client, db, make_user, auth_header):
    """Regression test for Finding 1, progress endpoint variant."""
    user = await make_user()
    eb = EBook(title="Book", filename="b.epub", file_path="/x/b.epub")
    db.add(eb)
    await db.commit()
    await db.refresh(eb)
    headers = auth_header(user)

    resp = await client.put(
        f"/api/sync/progress/{ProgressType.EBOOK.value}/{eb.id}",
        json={
            "epub_chapter": 1,
            "captured_at": "2026-07-20T12:00:00Z",
            "device_id": "device-a",
        },
        headers=headers,
    )
    assert resp.status_code == 200

    resp = await client.put(
        f"/api/sync/progress/{ProgressType.EBOOK.value}/{eb.id}",
        json={
            "epub_chapter": 2,
            "captured_at": "2026-07-20T13:00:00Z",
            "device_id": "device-b",
        },
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["epub_chapter"] == 2

    resp = await client.put(
        f"/api/sync/progress/{ProgressType.EBOOK.value}/{eb.id}",
        json={
            "epub_chapter": 0,
            "captured_at": "2026-07-20T11:00:00Z",
            "device_id": "device-c",
        },
        headers=headers,
    )
    assert resp.status_code == 409
    assert resp.json()["epub_chapter"] == 2


# ---------------------------------------------------------------------------
# Fix pass — Finding 2: bookmark device_id/device_name overwrite must be
# conditional (matching progress), not unconditional-null-on-omit.
# ---------------------------------------------------------------------------

async def test_bookmark_put_omitting_device_fields_preserves_prior_values(client, db, make_user, auth_header):
    """Regression test for Finding 2: a bookmark write that omits device_id/
    device_name must preserve whatever was previously stored, not null it out."""
    user = await make_user()
    pair = await make_book_pair(db)
    headers = auth_header(user)

    resp = await client.put(
        f"/api/sync/bookmark/{pair.id}",
        json={
            "source": "ebook",
            "epub_chapter": 1,
            "epub_sentence_index": 1,
            "device_id": "device-a",
            "device_name": "Pixel 8",
        },
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["device_id"] == "device-a"
    assert resp.json()["device_name"] == "Pixel 8"

    # Second write omits device_id/device_name entirely.
    resp = await client.put(
        f"/api/sync/bookmark/{pair.id}",
        json={
            "source": "ebook",
            "epub_chapter": 2,
            "epub_sentence_index": 2,
            "append_to_log": True,
        },
        headers=headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["epub_chapter"] == 2
    # Must preserve the prior device_id/device_name, not null them out.
    assert body["device_id"] == "device-a"
    assert body["device_name"] == "Pixel 8"

    # The appended BookmarkLog row should reflect what was actually applied
    # (the preserved device_id/device_name), not the raw incoming None values.
    resp = await client.get(f"/api/sync/bookmark/{pair.id}/log", headers=headers)
    assert resp.status_code == 200
    logs = resp.json()
    assert len(logs) == 1
    assert logs[0]["device_id"] == "device-a"
    assert logs[0]["device_name"] == "Pixel 8"
