"""
GET /api/sync/audio-to-epub/{pair_id} — the audio rung of the restore ladder,
executable from the web (issue #159).

A pair only ever *listened* to has a record with `audio_position_ms` and no
ebook anchor. Android resolves that rung locally through its cached sync map;
the web has no local map, so this endpoint exposes the same
`services.sync_engine.audio_to_epub` translation: audio ms in, EPUB
chapter/sentence (+ that point's preview and the map version) out.
"""

from routers import sync
from tests.factories import make_book_pair, make_sync_map


async def test_resolves_audio_ms_to_chapter_and_preview(
    db, make_client, make_user, auth_header
):
    pair = await make_book_pair(db)
    await make_sync_map(db, book_pair_id=pair.id)
    user = await make_user()

    async with make_client(sync.router) as c:
        resp = await c.get(
            f"/api/sync/audio-to-epub/{pair.id}",
            params={"audio_ms": 12000},
            headers=auth_header(user),
        )

    assert resp.status_code == 200
    body = resp.json()
    # 12000ms falls inside the (1, 0) point starting at 10000ms.
    assert body["epub_chapter"] == 1
    assert body["epub_sentence_index"] == 0
    assert body["preview"] == "chapter two begins now"
    assert body["sync_map_version"] == 1


async def test_position_before_first_point_resolves_to_origin(
    db, make_client, make_user, auth_header
):
    pair = await make_book_pair(db)
    await make_sync_map(db, book_pair_id=pair.id)
    user = await make_user()

    async with make_client(sync.router) as c:
        resp = await c.get(
            f"/api/sync/audio-to-epub/{pair.id}",
            params={"audio_ms": 0},
            headers=auth_header(user),
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["epub_chapter"] == 0
    assert body["epub_sentence_index"] == 0


async def test_404_when_pair_has_no_sync_map(db, make_client, make_user, auth_header):
    pair = await make_book_pair(db)
    user = await make_user()

    async with make_client(sync.router) as c:
        resp = await c.get(
            f"/api/sync/audio-to-epub/{pair.id}",
            params={"audio_ms": 12000},
            headers=auth_header(user),
        )

    assert resp.status_code == 404


async def test_requires_auth(db, make_client):
    async with make_client(sync.router) as c:
        resp = await c.get("/api/sync/audio-to-epub/1", params={"audio_ms": 1})
    assert resp.status_code in (401, 403)
