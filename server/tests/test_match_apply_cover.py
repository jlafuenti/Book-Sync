"""Tests for POST /match/apply-cover's SSRF guard (issue #51).

apply_remote_cover is the highest-risk site: cover_url is free-text from an
editor-level request body, and the previous implementation followed
redirects with no re-validation. These tests cover the guard rejecting
private/metadata URLs outright, rejecting a redirect to a private target,
and the content-type/size checks, alongside a happy-path regression case.
"""

import socket
from pathlib import Path

import httpx
import pytest

from config import settings
from models.book import EBook
import routers.match as match


@pytest.fixture(autouse=True)
def _stub_public_dns(monkeypatch):
    """public.example isn't a real domain -- stub DNS so it resolves to a
    public-looking IP. Literal IP URLs (127.0.0.1, 169.254.169.254) don't
    need this: getaddrinfo resolves an IP literal without a real lookup."""
    real_getaddrinfo = socket.getaddrinfo

    def fake_getaddrinfo(host, *args, **kwargs):
        if host == "public.example":
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]
        return real_getaddrinfo(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)


def _patch_match_transport(monkeypatch, handler):
    """Redirect outbound httpx.AsyncClient calls made by routers.match to a
    mock transport, mirroring test_settings.py's _patch_jetson_transport."""
    transport = httpx.MockTransport(handler)
    real_async_client = httpx.AsyncClient

    def fake_async_client(*args, **kwargs):
        if "transport" in kwargs:
            return real_async_client(*args, **kwargs)  # the test harness's own ASGI client
        kwargs.pop("timeout", None)
        kwargs.pop("follow_redirects", None)
        return real_async_client(*args, transport=transport, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", fake_async_client)


@pytest.fixture
def temp_covers_dir(monkeypatch, tmp_path):
    covers_dir = tmp_path / "covers"
    covers_dir.mkdir()
    monkeypatch.setattr(settings, "covers_dir", str(covers_dir))
    return covers_dir


async def _make_ebook(db):
    book = EBook(title="A Book", author="An Author", filename="a.epub", file_path="/x/a.epub")
    db.add(book)
    await db.commit()
    await db.refresh(book)
    return book


async def _apply_cover(client, headers, book_id, cover_url):
    return await client.post(
        "/match/apply-cover",
        json={"book_type": "ebook", "book_id": book_id, "cover_url": cover_url},
        headers=headers,
    )


async def test_apply_cover_rejects_private_url(
    make_user, auth_header, make_client, db, temp_covers_dir, monkeypatch,
):
    def handler(request):
        raise AssertionError("transport should not be reached for a private URL")

    _patch_match_transport(monkeypatch, handler)
    editor = await make_user(username="ed", role="editor")
    book = await _make_ebook(db)

    async with make_client(match.router) as client:
        r = await _apply_cover(client, auth_header(editor), book.id, "http://127.0.0.1/x.jpg")

    assert r.status_code == 400


async def test_apply_cover_rejects_metadata_url(
    make_user, auth_header, make_client, db, temp_covers_dir, monkeypatch,
):
    def handler(request):
        raise AssertionError("transport should not be reached for the metadata URL")

    _patch_match_transport(monkeypatch, handler)
    editor = await make_user(username="ed", role="editor")
    book = await _make_ebook(db)

    async with make_client(match.router) as client:
        r = await _apply_cover(client, auth_header(editor), book.id, "http://169.254.169.254/latest/meta-data/")

    assert r.status_code == 400


async def test_apply_cover_rejects_redirect_to_private(
    make_user, auth_header, make_client, db, temp_covers_dir, monkeypatch,
):
    calls = []

    def handler(request):
        calls.append(str(request.url))
        if str(request.url) == "http://public.example/cover.jpg":
            return httpx.Response(302, headers={"location": "http://127.0.0.1/internal.jpg"})
        raise AssertionError(f"internal URL should never be requested, got {request.url}")

    _patch_match_transport(monkeypatch, handler)
    editor = await make_user(username="ed", role="editor")
    book = await _make_ebook(db)

    async with make_client(match.router) as client:
        r = await _apply_cover(client, auth_header(editor), book.id, "http://public.example/cover.jpg")

    assert r.status_code == 400
    assert calls == ["http://public.example/cover.jpg"]


async def test_apply_cover_caps_redirect_chain(
    make_user, auth_header, make_client, db, temp_covers_dir, monkeypatch,
):
    call_count = 0

    def handler(request):
        nonlocal call_count
        call_count += 1
        return httpx.Response(302, headers={"location": "http://public.example/cover.jpg"})

    _patch_match_transport(monkeypatch, handler)
    editor = await make_user(username="ed", role="editor")
    book = await _make_ebook(db)

    async with make_client(match.router) as client:
        r = await _apply_cover(client, auth_header(editor), book.id, "http://public.example/cover.jpg")

    assert r.status_code == 400
    assert call_count == match.MAX_COVER_REDIRECTS + 1


async def test_apply_cover_rejects_non_image_content_type(
    make_user, auth_header, make_client, db, temp_covers_dir, monkeypatch,
):
    def handler(request):
        return httpx.Response(200, headers={"content-type": "text/html"}, content=b"<html></html>")

    _patch_match_transport(monkeypatch, handler)
    editor = await make_user(username="ed", role="editor")
    book = await _make_ebook(db)

    async with make_client(match.router) as client:
        r = await _apply_cover(client, auth_header(editor), book.id, "http://public.example/cover.jpg")

    assert r.status_code == 400
    assert not list(temp_covers_dir.iterdir())


async def test_apply_cover_rejects_oversized_response(
    make_user, auth_header, make_client, db, temp_covers_dir, monkeypatch,
):
    big_content = b"x" * (match.MAX_COVER_BYTES + 1)

    def handler(request):
        return httpx.Response(200, headers={"content-type": "image/jpeg"}, content=big_content)

    _patch_match_transport(monkeypatch, handler)
    editor = await make_user(username="ed", role="editor")
    book = await _make_ebook(db)

    async with make_client(match.router) as client:
        r = await _apply_cover(client, auth_header(editor), book.id, "http://public.example/cover.jpg")

    assert r.status_code == 413


async def test_apply_cover_aborts_stream_before_reading_entire_oversized_body(
    make_user, auth_header, make_client, db, temp_covers_dir, monkeypatch,
):
    """The size cap must be enforced incrementally during the stream, not
    only after the full body is buffered in memory -- otherwise a
    malicious server can force an effectively unbounded read before the
    413 is returned."""
    chunk_size = 1024 * 1024  # 1 MiB
    max_chunks = 200  # 200 MiB -- far more than MAX_COVER_BYTES (25 MiB); bounds the test if the fix regresses
    produced = 0

    async def body():
        nonlocal produced
        for _ in range(max_chunks):
            produced += 1
            yield b"x" * chunk_size

    def handler(request):
        return httpx.Response(200, headers={"content-type": "image/jpeg"}, content=body())

    _patch_match_transport(monkeypatch, handler)
    editor = await make_user(username="ed", role="editor")
    book = await _make_ebook(db)

    async with make_client(match.router) as client:
        r = await _apply_cover(client, auth_header(editor), book.id, "http://public.example/cover.jpg")

    assert r.status_code == 413
    # Must abort shortly after crossing MAX_COVER_BYTES, not after draining
    # the full (far larger) body.
    assert produced * chunk_size <= match.MAX_COVER_BYTES + chunk_size


async def test_apply_cover_accepts_public_image_and_saves(
    make_user, auth_header, make_client, db, temp_covers_dir, monkeypatch,
):
    image_bytes = b"\xff\xd8\xff\xe0fake-jpeg-bytes"

    def handler(request):
        return httpx.Response(200, headers={"content-type": "image/jpeg"}, content=image_bytes)

    _patch_match_transport(monkeypatch, handler)
    editor = await make_user(username="ed", role="editor")
    book = await _make_ebook(db)

    async with make_client(match.router) as client:
        r = await _apply_cover(client, auth_header(editor), book.id, "http://public.example/cover.jpg")

    assert r.status_code == 200, r.text
    cover_path = r.json()["cover_path"]
    assert cover_path.startswith("/api/files/covers/")
    saved = temp_covers_dir / cover_path.split("/")[-1]
    assert saved.read_bytes() == image_bytes


async def test_apply_cover_deletes_old_cover_file(
    make_user, auth_header, make_client, db, temp_covers_dir, monkeypatch,
):
    """Issue #44: book.cover_path is a URL, not a filesystem path -- applying
    a new cover must still delete the file the old URL pointed to."""
    old_cover = temp_covers_dir / "old_cover.jpg"
    old_cover.write_bytes(b"old-jpeg-bytes")

    image_bytes = b"\xff\xd8\xff\xe0new-fake-jpeg-bytes"

    def handler(request):
        return httpx.Response(200, headers={"content-type": "image/jpeg"}, content=image_bytes)

    _patch_match_transport(monkeypatch, handler)
    editor = await make_user(username="ed", role="editor")
    book = await _make_ebook(db)
    book.cover_path = "/api/files/covers/old_cover.jpg"
    await db.commit()

    async with make_client(match.router) as client:
        r = await _apply_cover(client, auth_header(editor), book.id, "http://public.example/cover.jpg")

    assert r.status_code == 200, r.text
    assert not old_cover.exists()
    new_cover_path = r.json()["cover_path"]
    saved = temp_covers_dir / new_cover_path.split("/")[-1]
    assert saved.read_bytes() == image_bytes
    assert [f.name for f in temp_covers_dir.iterdir()] == [saved.name]


async def test_apply_cover_logs_and_continues_when_old_unlink_fails(
    make_user, auth_header, make_client, db, temp_covers_dir, monkeypatch, caplog,
):
    """If deleting the old cover file fails (e.g. permissions/locked file),
    the request must still succeed and the new cover must still be recorded --
    the delete failure is logged, not fatal."""
    old_cover = temp_covers_dir / "old_cover.jpg"
    old_cover.write_bytes(b"old-jpeg-bytes")

    def raising_unlink(self, *args, **kwargs):
        raise OSError("locked")

    monkeypatch.setattr(Path, "unlink", raising_unlink)

    image_bytes = b"\xff\xd8\xff\xe0new-fake-jpeg-bytes"

    def handler(request):
        return httpx.Response(200, headers={"content-type": "image/jpeg"}, content=image_bytes)

    _patch_match_transport(monkeypatch, handler)
    editor = await make_user(username="ed", role="editor")
    book = await _make_ebook(db)
    book.cover_path = "/api/files/covers/old_cover.jpg"
    await db.commit()

    async with make_client(match.router) as client:
        r = await _apply_cover(client, auth_header(editor), book.id, "http://public.example/cover.jpg")

    assert r.status_code == 200, r.text
    assert old_cover.exists()  # unlink failed, so the old file is still there
    new_cover_path = r.json()["cover_path"]
    saved = temp_covers_dir / new_cover_path.split("/")[-1]
    assert saved.read_bytes() == image_bytes
    assert "Failed to delete old cover" in caplog.text
