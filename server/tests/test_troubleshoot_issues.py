"""
`GET /api/troubleshoot/issues` must not stall the server (issue #208).

The handler is `async def` but was doing all of its work on the event loop: two
full-table loads, then per row `os.path.isfile` + `os.path.getsize`, then per
audiobook a `mutagen.mp4.MP4` atom parse — every one of those a synchronous call
against a NAS mount. Tandem runs a **single uvicorn worker**, so for the whole
duration nothing else was served, including login and `/api/health` (whose
compose healthcheck gives up after 10 s). The Troubleshoot page fires it on
load, and any editor could fire it in a loop.

Three fixes, one test each below: the scan runs in a worker thread, the
per-audiobook chapter check is memoized on `(path, mtime, size)` so a second
request inside the TTL re-parses nothing, and the endpoint has a per-user rate
limit. The category results themselves are covered by
`test_troubleshoot_chapter_encoding.py`.
"""

import os
import threading

import pytest

from config import settings
from models.book import AudioBook, EBook
from routers import troubleshoot
from services import chapter_repair


async def _audiobook(db, path, title="Some Audiobook"):
    ab = AudioBook(
        title=title,
        author="An Author",
        filename=os.path.basename(path),
        file_path=path,
        format="m4b",
    )
    db.add(ab)
    await db.commit()
    await db.refresh(ab)
    return ab


async def _ebook(db, path, title="Some Ebook"):
    eb = EBook(
        title=title,
        author="An Author",
        filename=os.path.basename(path),
        file_path=path,
        format="epub",
    )
    db.add(eb)
    await db.commit()
    await db.refresh(eb)
    return eb


def _write(path, size=2048):
    with open(path, "wb") as fh:
        fh.write(b"x" * size)
    return str(path)


async def test_issue_scan_runs_off_the_event_loop(
    db, make_client, make_user, auth_header, monkeypatch, tmp_path
):
    user = await make_user(role="editor")
    path = _write(tmp_path / "book.m4b")
    await _audiobook(db, path)
    await _ebook(db, _write(tmp_path / "book.epub"))

    threads = []

    def _spy(p):
        threads.append(threading.current_thread())
        return True, None

    monkeypatch.setattr(chapter_repair, "check_chapter_encoding", _spy)

    async with make_client(troubleshoot.router) as c:
        resp = await c.get("/api/troubleshoot/issues", headers=auth_header(user))

    assert resp.status_code == 200
    assert threads, "the chapter check never ran"
    assert all(t is not threading.main_thread() for t in threads), (
        "the filesystem scan is still on the event loop"
    )


async def test_chapter_check_runs_once_per_file_across_requests(
    db, make_client, make_user, auth_header, monkeypatch, tmp_path
):
    """Two back-to-back loads of the page must not re-parse every audiobook."""
    user = await make_user(role="editor")
    path = _write(tmp_path / "book.m4b")
    await _audiobook(db, path)

    calls = []
    monkeypatch.setattr(
        chapter_repair,
        "check_chapter_encoding",
        lambda p: (calls.append(p), (True, None))[1],
    )

    async with make_client(troubleshoot.router) as c:
        await c.get("/api/troubleshoot/issues", headers=auth_header(user))
        await c.get("/api/troubleshoot/issues", headers=auth_header(user))

    assert calls == [path]


async def test_chapter_check_reruns_when_the_file_changes(
    db, make_client, make_user, auth_header, monkeypatch, tmp_path
):
    """Keyed on content, not just path — a repaired file must not stay flagged.

    Repair rewrites the audiobook in place; if the memo were keyed on the path
    alone, Troubleshoot would keep reporting a problem that no longer exists
    until the TTL happened to lapse.
    """
    user = await make_user(role="editor")
    path = _write(tmp_path / "book.m4b", size=2048)
    await _audiobook(db, path)

    calls = []
    monkeypatch.setattr(
        chapter_repair,
        "check_chapter_encoding",
        lambda p: (calls.append(p), (True, None))[1],
    )

    async with make_client(troubleshoot.router) as c:
        await c.get("/api/troubleshoot/issues", headers=auth_header(user))
        _write(tmp_path / "book.m4b", size=4096)
        await c.get("/api/troubleshoot/issues", headers=auth_header(user))

    assert calls == [path, path]


async def test_issues_is_rate_limited(
    db, make_client, make_user, auth_header, monkeypatch, tmp_path
):
    user = await make_user(role="editor")
    monkeypatch.setattr(settings, "expensive_read_limit", 2)

    async with make_client(troubleshoot.router) as c:
        codes = [
            (
                await c.get("/api/troubleshoot/issues", headers=auth_header(user))
            ).status_code
            for _ in range(3)
        ]
        over = await c.get("/api/troubleshoot/issues", headers=auth_header(user))

    assert codes == [200, 200, 429]
    assert int(over.headers["Retry-After"]) > 0


async def test_issues_still_requires_editor(db, make_client, make_user, auth_header):
    """The rate-limit wrapper must not have swallowed the role check."""
    reader = await make_user(username="plainreader", role="user")

    async with make_client(troubleshoot.router) as c:
        resp = await c.get("/api/troubleshoot/issues", headers=auth_header(reader))

    assert resp.status_code == 403


@pytest.mark.parametrize("size,expected", [(0, True), (10 * 1024 * 1024, False)])
async def test_scan_still_finds_tiny_audiobooks(
    db, make_client, make_user, auth_header, monkeypatch, tmp_path, size, expected
):
    """The thread hop must not have changed what the scan reports."""
    user = await make_user(role="editor")
    path = _write(tmp_path / "book.m4b", size=size)
    ab = await _audiobook(db, path)
    monkeypatch.setattr(chapter_repair, "check_chapter_encoding", lambda p: (True, None))

    async with make_client(troubleshoot.router) as c:
        resp = await c.get("/api/troubleshoot/issues", headers=auth_header(user))

    flagged = [i["item_id"] for i in resp.json()["categories"]["zero_byte"]]
    assert (ab.id in flagged) is expected
