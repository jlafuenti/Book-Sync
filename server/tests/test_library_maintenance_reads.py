"""
`GET /api/library/verify` and `GET /api/library/calibre-status` (issue #208).

Both were `async def` doing synchronous work on the event loop, on a server that
runs a **single uvicorn worker** — so for the duration of either, no other
request was served, `/api/health` included (its compose healthcheck gives up
after 10 s). `verify` stats every ebook and audiobook row on a NAS mount and
returned an unbounded list; `calibre-status` shells out to `ebook-convert
--version` with a 10 s timeout on every single call, from a System page tile.

What is pinned here: the blocking work runs in a worker thread, `verify`'s
result is capped and says so when it truncates, `calibre-status` is cached, and
both carry a per-user rate limit.
"""

import subprocess
import threading

from config import settings
from models.book import AudioBook, EBook
from routers import library


async def _orphans(db, n, *, prefix="missing"):
    """`n` ebooks and `n` audiobooks whose files do not exist on disk."""
    rows = []
    for i in range(n):
        rows.append(EBook(
            title=f"{prefix} ebook {i}", author="A", filename=f"{prefix}{i}.epub",
            file_path=f"/nowhere/{prefix}{i}.epub", format="epub",
        ))
        rows.append(AudioBook(
            title=f"{prefix} audiobook {i}", author="A", filename=f"{prefix}{i}.m4b",
            file_path=f"/nowhere/{prefix}{i}.m4b", format="m4b",
        ))
    db.add_all(rows)
    await db.commit()
    return rows


# ---------------------------------------------------------------------------
# /api/library/verify
# ---------------------------------------------------------------------------

async def test_verify_reports_orphans(db, make_client, make_user, auth_header, tmp_path):
    user = await make_user(username="v1", role="editor")
    await _orphans(db, 2)
    present = tmp_path / "here.epub"
    present.write_bytes(b"x")
    db.add(EBook(title="Present", author="A", filename="here.epub",
                 file_path=str(present), format="epub"))
    await db.commit()

    async with make_client(library.router) as c:
        resp = await c.get("/api/library/verify", headers=auth_header(user))

    body = resp.json()
    assert resp.status_code == 200
    assert len(body["orphaned_ebooks"]) == 2
    assert len(body["orphaned_audiobooks"]) == 2
    assert body["truncated"] is False
    assert "Present" not in [e["title"] for e in body["orphaned_ebooks"]]


async def test_verify_runs_off_the_event_loop(
    db, make_client, make_user, auth_header, monkeypatch
):
    user = await make_user(username="v2", role="editor")
    await _orphans(db, 1)
    threads = []
    real_isfile = library.os.path.isfile

    def _spy(path):
        threads.append(threading.current_thread())
        return real_isfile(path)

    monkeypatch.setattr(library.os.path, "isfile", _spy)

    async with make_client(library.router) as c:
        resp = await c.get("/api/library/verify", headers=auth_header(user))

    assert resp.status_code == 200
    assert threads, "nothing was stat'ed"
    assert all(t is not threading.main_thread() for t in threads)


async def test_verify_is_capped_and_says_so(
    db, make_client, make_user, auth_header, monkeypatch
):
    """A library that is entirely missing must not produce an unbounded body."""
    user = await make_user(username="v3", role="editor")
    monkeypatch.setattr(library, "VERIFY_MAX_RESULTS", 3)
    await _orphans(db, 5)

    async with make_client(library.router) as c:
        body = (await c.get("/api/library/verify", headers=auth_header(user))).json()

    assert len(body["orphaned_ebooks"]) == 3
    assert len(body["orphaned_audiobooks"]) == 3
    assert body["truncated"] is True


async def test_verify_is_rate_limited(
    db, make_client, make_user, auth_header, monkeypatch
):
    user = await make_user(username="v4", role="editor")
    monkeypatch.setattr(settings, "expensive_read_limit", 2)

    async with make_client(library.router) as c:
        codes = [
            (await c.get("/api/library/verify", headers=auth_header(user))).status_code
            for _ in range(3)
        ]
        over = await c.get("/api/library/verify", headers=auth_header(user))

    assert codes == [200, 200, 429]
    assert int(over.headers["Retry-After"]) > 0


async def test_verify_requires_editor(db, make_client, make_user, auth_header):
    """A plain reader may not stat the whole library (issue #208).

    `verify` is a curation view: it exists to tell whoever maintains the shelves
    which rows have lost their file. A read-only account can act on none of it,
    and letting it ask means any account can spend a full pass over every row on
    a NAS mount.
    """
    user = await make_user(username="v5", role="user")
    await _orphans(db, 1, prefix="gated")

    async with make_client(library.router) as c:
        resp = await c.get("/api/library/verify", headers=auth_header(user))

    assert resp.status_code == 403

# ---------------------------------------------------------------------------
# /api/library/calibre-status
# ---------------------------------------------------------------------------

class _FakeCompleted:
    returncode = 0
    stdout = "calibre 7.0.0\n"
    stderr = ""


async def test_calibre_status_runs_off_the_event_loop(
    make_client, make_user, auth_header, monkeypatch
):
    user = await make_user(username="c1", role="editor")
    threads = []

    def _spy(*args, **kwargs):
        threads.append(threading.current_thread())
        return _FakeCompleted()

    monkeypatch.setattr(library.subprocess, "run", _spy)

    async with make_client(library.router) as c:
        resp = await c.get("/api/library/calibre-status", headers=auth_header(user))

    assert resp.json() == {"available": True, "version": "calibre 7.0.0"}
    assert threads and all(t is not threading.main_thread() for t in threads)


async def test_calibre_status_is_cached(
    make_client, make_user, auth_header, monkeypatch
):
    """The answer changes on an image rebuild, not between two page loads."""
    user = await make_user(username="c2", role="editor")
    calls = []
    monkeypatch.setattr(
        library.subprocess, "run",
        lambda *a, **k: (calls.append(1), _FakeCompleted())[1],
    )

    async with make_client(library.router) as c:
        first = await c.get("/api/library/calibre-status", headers=auth_header(user))
        second = await c.get("/api/library/calibre-status", headers=auth_header(user))

    assert first.json() == second.json()
    assert len(calls) == 1


async def test_calibre_status_reports_a_missing_binary(
    make_client, make_user, auth_header, monkeypatch
):
    user = await make_user(username="c3", role="editor")

    def _missing(*a, **k):
        raise FileNotFoundError()

    monkeypatch.setattr(library.subprocess, "run", _missing)

    async with make_client(library.router) as c:
        body = (await c.get("/api/library/calibre-status",
                            headers=auth_header(user))).json()

    assert body["available"] is False
    assert "ebook-convert not found" in body["error"]


async def test_calibre_status_reports_a_timeout(
    make_client, make_user, auth_header, monkeypatch
):
    """A wedged subprocess must surface as an answer, not as a hung request."""
    user = await make_user(username="c4", role="editor")

    def _slow(*a, **k):
        raise subprocess.TimeoutExpired(cmd="ebook-convert", timeout=10)

    monkeypatch.setattr(library.subprocess, "run", _slow)

    async with make_client(library.router) as c:
        body = (await c.get("/api/library/calibre-status",
                            headers=auth_header(user))).json()

    assert body == {"available": False, "error": "version check timed out"}


async def test_calibre_status_is_rate_limited(
    make_client, make_user, auth_header, monkeypatch
):
    user = await make_user(username="c5", role="editor")
    monkeypatch.setattr(settings, "expensive_read_limit", 2)
    monkeypatch.setattr(library.subprocess, "run", lambda *a, **k: _FakeCompleted())

    async with make_client(library.router) as c:
        codes = [
            (await c.get("/api/library/calibre-status",
                         headers=auth_header(user))).status_code
            for _ in range(3)
        ]
        over = await c.get("/api/library/calibre-status", headers=auth_header(user))

    assert codes == [200, 200, 429]
    assert int(over.headers["Retry-After"]) > 0


async def test_calibre_status_requires_editor(
    make_client, make_user, auth_header, monkeypatch
):
    """Same gate on the same reasoning, and the subprocess never starts.

    Whether calibre is installed is an operator's question -- a read-only
    account cannot convert anything -- and the answer costs a `subprocess.run`
    with a 10 s timeout. The 403 lands before the probe: `rate_limited` runs the
    role dependency first, so a refused caller neither shells out nor spends
    from the bucket.
    """
    user = await make_user(username="c6", role="user")
    calls = []
    monkeypatch.setattr(
        library.subprocess, "run",
        lambda *a, **k: (calls.append(1), _FakeCompleted())[1],
    )

    async with make_client(library.router) as c:
        resp = await c.get("/api/library/calibre-status", headers=auth_header(user))

    assert resp.status_code == 403
    assert calls == []
