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
