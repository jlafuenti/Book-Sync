"""Issue #713: the transcript editor's edits never reached alignment.

`PUT /api/transcription/{pair_id}/text` rewrote `SyncPoint.audio_text`, which
only the editor itself displayed, while re-alignment rebuilds the map from the
cached `AudioTranscript` - so an edit changed nothing either client used. The
owner chose to make the page a read-only view with a Re-align button instead,
so the write endpoint is gone, and Re-align, which the web now calls, declares
its response.
"""
from fastapi.routing import APIRoute

from routers import transcription
from schemas import RealignResponse
from tests.factories import make_book_pair


async def test_the_text_edit_endpoint_is_gone(db, make_client, make_user, auth_header):
    pair = await make_book_pair(db)
    user = await make_user(username="ed", role="editor")
    async with make_client(transcription.router) as c:
        r = await c.put(
            f"/api/transcription/{pair.id}/text",
            json={"points": [{"id": 1, "audio_text": "edited"}]},
            headers=auth_header(user),
        )
    assert r.status_code in (404, 405)


def test_realign_declares_its_response():
    route = next(
        r for r in transcription.router.routes
        if isinstance(r, APIRoute) and r.path.endswith("/{pair_id}/realign")
    )
    assert route.response_model is RealignResponse
    assert set(RealignResponse.model_fields) == {"status", "points", "matched", "interpolated"}


async def test_a_viewer_cannot_realign(db, make_client, make_user, auth_header):
    pair = await make_book_pair(db)
    user = await make_user(username="vi", role="viewer")
    async with make_client(transcription.router) as c:
        r = await c.post(f"/api/transcription/{pair.id}/realign", headers=auth_header(user))
    assert r.status_code == 403
