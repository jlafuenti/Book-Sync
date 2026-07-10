"""Tests for POST /match/apply-cover's SSRF guard (issue #51).

apply_remote_cover is the highest-risk site: cover_url is free-text from an
editor-level request body, and the previous implementation followed
redirects with no re-validation. These tests cover the guard rejecting
private/metadata URLs outright, rejecting a redirect to a private target,
and the content-type/size checks, alongside a happy-path regression case.
"""

import socket

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
