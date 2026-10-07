"""
GET /api/files/syncmap/{pair_id}/words: the per-token start times of a pair's
sync map, in a payload of its own so the existing map response stays as small as
it is (issue #835).
"""

from sqlalchemy import select

from models.sync_map import SyncMap, SyncPoint
from routers import files
from tests.factories import make_book_pair, make_sync_map


async def _set_word_starts(db, pair_id, by_index):
    sync_map = (await db.execute(
        select(SyncMap).where(SyncMap.book_pair_id == pair_id))).scalar_one()
    points = (await db.execute(
        select(SyncPoint).where(SyncPoint.sync_map_id == sync_map.id))).scalars().all()
    for p in points:
        value = by_index.get((p.epub_chapter, p.epub_sentence_index))
        if value is not None:
            p.word_starts = value
    await db.commit()
    return sync_map


async def test_requires_authentication(db, make_client):
    pair = await make_book_pair(db)
    await make_sync_map(db, pair.id)
    async with make_client(files.router) as c:
        r = await c.get(f"/api/files/syncmap/{pair.id}/words")
    assert r.status_code in (401, 403)


async def test_unknown_pair_is_404(db, make_client, make_user, auth_header):
    user = await make_user()
    async with make_client(files.router) as c:
        r = await c.get("/api/files/syncmap/9999/words", headers=auth_header(user))
    assert r.status_code == 404


async def test_pair_without_a_map_is_404(db, make_client, make_user, auth_header):
    pair = await make_book_pair(db)
    user = await make_user()
    async with make_client(files.router) as c:
        r = await c.get(f"/api/files/syncmap/{pair.id}/words", headers=auth_header(user))
    assert r.status_code == 404


async def test_returns_only_points_with_starts_as_ints_in_order(
    db, make_client, make_user, auth_header
):
    pair = await make_book_pair(db)
    sync_map = await make_sync_map(db, pair.id, points=[
        (0, 0, 0, "one two"),
        (0, 1, 3000, "three four five"),
        (1, 0, 6000, "six"),
        (1, 1, 9000, "seven"),
    ])
    await _set_word_starts(db, pair.id, {
        (1, 0): "6000,6200",
        (0, 1): "3000,3300,3600",
    })
    user = await make_user()
    async with make_client(files.router) as c:
        r = await c.get(f"/api/files/syncmap/{pair.id}/words", headers=auth_header(user))

    assert r.status_code == 200
    body = r.json()
    assert body["sync_map_id"] == sync_map.id
    assert body["version"] == 1
    assert body["points"] == [
        {"epub_chapter": 0, "epub_sentence_index": 1, "word_starts": [3000, 3300, 3600]},
        {"epub_chapter": 1, "epub_sentence_index": 0, "word_starts": [6000, 6200]},
    ]


async def test_empty_list_when_no_point_has_starts(db, make_client, make_user, auth_header):
    pair = await make_book_pair(db)
    await make_sync_map(db, pair.id)
    user = await make_user()
    async with make_client(files.router) as c:
        r = await c.get(f"/api/files/syncmap/{pair.id}/words", headers=auth_header(user))
    assert r.status_code == 200
    assert r.json()["points"] == []


async def test_the_existing_map_response_is_unchanged(db, make_client, make_user, auth_header):
    pair = await make_book_pair(db)
    await make_sync_map(db, pair.id)
    await _set_word_starts(db, pair.id, {(0, 0): "1,2"})
    user = await make_user()
    async with make_client(files.router) as c:
        r = await c.get(f"/api/files/syncmap/{pair.id}", headers=auth_header(user))
    assert r.status_code == 200
    assert all("word_starts" not in p for p in r.json()["sync_points"])
