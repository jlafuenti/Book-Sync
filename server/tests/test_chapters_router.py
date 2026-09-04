"""
`PUT /audiobooks/{id}/chapters` — the editor's chapter write-back.

The remux itself moved into `services.chapter_repair.remux_with_chapters`
(issue #192), shared with the chapter-encoding repair so the two paths that
rewrite a purchased audiobook cannot drift apart. Its rules — freeform tags
preserved, staged next to the target, atomic install, output verified — are
covered by tests/test_chapter_remux.py. What is left here is the router's own
contract: who may call it, what it does when the row or the file is missing,
that the user's chapters are the ones handed to the remux, and that a remux
failure surfaces as a 500 rather than a silent success.
"""

import asyncio

import pytest

from models.book import AudioBook
from routers import chapters


@pytest.fixture
async def audiobook(db, tmp_path):
    filepath = str(tmp_path / "book.m4b")
    with open(filepath, "wb") as f:
        f.write(b"fake m4b")
    ab = AudioBook(title="Some Book", filename="book.m4b", file_path=filepath)
    db.add(ab)
    await db.commit()
    await db.refresh(ab)
    return ab


def _capture_remux(monkeypatch, *, ok=True, error=None):
    calls = []

    def _remux(filepath, chapter_rows, timeout=60):
        calls.append((filepath, chapter_rows))
        return ok, error

    monkeypatch.setattr(chapters.chapter_repair, "remux_with_chapters", _remux)
    return calls


async def test_update_chapters_hands_the_users_chapters_to_the_shared_remux(
    db, make_client, make_user, auth_header, monkeypatch, audiobook
):
    user = await make_user(role="editor")
    calls = _capture_remux(monkeypatch)

    async with make_client(chapters.router) as c:
        resp = await c.put(
            f"/audiobooks/{audiobook.id}/chapters",
            json=[
                {"id": 0, "start_time": 0.0, "end_time": 10.0, "title": "One"},
                {"id": 1, "start_time": 10.0, "end_time": 25.5, "title": "Part 1 = Intro"},
            ],
            headers=auth_header(user),
        )

    assert resp.status_code == 200
    assert len(calls) == 1
    filepath, rows = calls[0]
    assert filepath == audiobook.file_path
    assert rows == [
        {"start": 0.0, "end": 10.0, "title": "One"},
        {"start": 10.0, "end": 25.5, "title": "Part 1 = Intro"},
    ]


async def test_update_chapters_runs_the_remux_off_the_event_loop(
    db, make_client, make_user, auth_header, monkeypatch, audiobook
):
    """`remux_with_chapters` shells out to ffmpeg synchronously and can take
    minutes on a multi-GB book; running it inline would stall every other
    request for the duration."""
    user = await make_user(role="editor")
    _capture_remux(monkeypatch)
    threaded = []

    real_to_thread = asyncio.to_thread

    async def _spy(fn, *a, **kw):
        threaded.append(fn)
        return await real_to_thread(fn, *a, **kw)

    monkeypatch.setattr(asyncio, "to_thread", _spy)

    async with make_client(chapters.router) as c:
        resp = await c.put(
            f"/audiobooks/{audiobook.id}/chapters",
            json=[{"id": 0, "start_time": 0.0, "end_time": 10.0, "title": "One"}],
            headers=auth_header(user),
        )

    assert resp.status_code == 200
    assert threaded, "the remux must run in a worker thread"


async def test_a_failed_remux_is_a_500_with_the_reason(
    db, make_client, make_user, auth_header, monkeypatch, audiobook
):
    user = await make_user(role="editor")
    _capture_remux(monkeypatch, ok=False, error="Failed to write chapters: no space left")

    async with make_client(chapters.router) as c:
        resp = await c.put(
            f"/audiobooks/{audiobook.id}/chapters",
            json=[{"id": 0, "start_time": 0.0, "end_time": 10.0, "title": "One"}],
            headers=auth_header(user),
        )

    assert resp.status_code == 500
    assert "no space left" in resp.json()["detail"]


async def test_update_chapters_requires_an_editor(
    db, make_client, make_user, auth_header, monkeypatch, audiobook
):
    plain = await make_user(username="reader", role="user")
    calls = _capture_remux(monkeypatch)

    async with make_client(chapters.router) as c:
        resp = await c.put(
            f"/audiobooks/{audiobook.id}/chapters",
            json=[{"id": 0, "start_time": 0.0, "end_time": 10.0, "title": "One"}],
            headers=auth_header(plain),
        )

    assert resp.status_code == 403
    assert calls == [], "a rejected caller must not reach the file"


async def test_update_chapters_404s_for_an_unknown_audiobook(
    db, make_client, make_user, auth_header, monkeypatch
):
    user = await make_user(role="editor")
    calls = _capture_remux(monkeypatch)

    async with make_client(chapters.router) as c:
        resp = await c.put(
            "/audiobooks/999999/chapters",
            json=[{"id": 0, "start_time": 0.0, "end_time": 10.0, "title": "One"}],
            headers=auth_header(user),
        )

    assert resp.status_code == 404
    assert calls == []


async def test_update_chapters_404s_when_the_file_is_gone(
    db, make_client, make_user, auth_header, monkeypatch, tmp_path
):
    user = await make_user(role="editor")
    ab = AudioBook(title="Missing", filename="gone.m4b",
                   file_path=str(tmp_path / "gone.m4b"))
    db.add(ab)
    await db.commit()
    await db.refresh(ab)
    calls = _capture_remux(monkeypatch)

    async with make_client(chapters.router) as c:
        resp = await c.put(
            f"/audiobooks/{ab.id}/chapters",
            json=[{"id": 0, "start_time": 0.0, "end_time": 10.0, "title": "One"}],
            headers=auth_header(user),
        )

    assert resp.status_code == 404
    assert calls == []
