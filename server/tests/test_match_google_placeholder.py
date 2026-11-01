"""Google's "image not available" placeholder must never be applied as a
cover (issue #888).

The Google Books search rewrites a thumbnail to its `zoom=0` rendition. For
some volumes Google has none, and that URL answers 200 `image/png` with a
generic grey placeholder while `zoom=1` serves the real JPEG. apply-cover only
checked for an image content type, so the placeholder was saved and the book
dropped off the missing-cover list.
"""

import hashlib

import httpx
import pytest

from config import settings
from models.book import EBook
import routers.match as match

GOOGLE = "https://books.google.com/books/content?id=VOLUME&printsec=frontcover&img=1"
PLACEHOLDER_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
REAL_JPEG = b"\xff\xd8\xff\xe0real-cover"


@pytest.fixture(autouse=True)
def _allow_any_host(monkeypatch):
    async def safe(url, allow_private=False):
        return None

    monkeypatch.setattr(match, "assert_safe_url_async", safe)


def _patch_transport(monkeypatch, handler):
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
def covers_dir(monkeypatch, tmp_path):
    path = tmp_path / "covers"
    path.mkdir()
    monkeypatch.setattr(settings, "covers_dir", str(path))
    return path


async def _apply(make_user, auth_header, make_client, db, cover_url):
    editor = await make_user(username="ed", role="editor")
    book = EBook(title="A Book", author="An Author", filename="a.epub", file_path="/x/a.epub",
                 cover_path="/api/files/covers/old.jpg")
    db.add(book)
    await db.commit()
    await db.refresh(book)
    async with make_client(match.router) as client:
        r = await client.post(
            "/match/apply-cover",
            json={"book_type": "ebook", "book_id": book.id, "cover_url": cover_url},
            headers=auth_header(editor),
        )
    await db.refresh(book)
    return r, book


def _google(zoom0_png):
    """Google's two renditions: zoom=0 is the placeholder when `zoom0_png`,
    zoom=1 is always the real JPEG."""
    requested = []

    def handler(request):
        url = str(request.url)
        requested.append(url)
        if "zoom=0" in url and zoom0_png:
            return httpx.Response(200, headers={"content-type": "image/png"}, content=PLACEHOLDER_PNG)
        return httpx.Response(200, headers={"content-type": "image/jpeg"}, content=REAL_JPEG)

    return handler, requested


async def test_a_zoom0_placeholder_falls_back_to_the_zoom1_cover(
    make_user, auth_header, make_client, db, covers_dir, monkeypatch,
):
    handler, requested = _google(zoom0_png=True)
    _patch_transport(monkeypatch, handler)

    r, book = await _apply(make_user, auth_header, make_client, db, GOOGLE + "&zoom=0&source=gbs_api")

    assert r.status_code == 200, r.text
    saved = covers_dir / r.json()["cover_path"].split("/")[-1]
    assert saved.read_bytes() == REAL_JPEG
    assert saved.suffix == ".jpg"
    assert any("zoom=1" in url for url in requested)


async def test_a_google_url_with_no_real_cover_is_refused_and_nothing_changes(
    make_user, auth_header, make_client, db, covers_dir, monkeypatch,
):
    def handler(request):
        return httpx.Response(200, headers={"content-type": "image/png"}, content=PLACEHOLDER_PNG)

    _patch_transport(monkeypatch, handler)

    r, book = await _apply(make_user, auth_header, make_client, db, GOOGLE + "&zoom=0&source=gbs_api")

    assert r.status_code == 422
    assert not list(covers_dir.iterdir())
    assert book.cover_path == "/api/files/covers/old.jpg"


async def test_the_known_placeholder_is_refused_from_any_url(
    make_user, auth_header, make_client, db, covers_dir, monkeypatch,
):
    """The placeholder's fixed hash catches it behind a proxy or an image CDN
    that doesn't look like a Google Books URL."""
    monkeypatch.setattr(match, "_PLACEHOLDER_COVER_MD5", {hashlib.md5(PLACEHOLDER_PNG).hexdigest()})

    def handler(request):
        return httpx.Response(200, headers={"content-type": "image/png"}, content=PLACEHOLDER_PNG)

    _patch_transport(monkeypatch, handler)

    r, _ = await _apply(make_user, auth_header, make_client, db, "https://images.example/cover.png")

    assert r.status_code == 422
    assert not list(covers_dir.iterdir())


async def test_a_real_google_jpeg_is_applied_with_one_fetch(
    make_user, auth_header, make_client, db, covers_dir, monkeypatch,
):
    handler, requested = _google(zoom0_png=False)
    _patch_transport(monkeypatch, handler)

    r, _ = await _apply(make_user, auth_header, make_client, db, GOOGLE + "&zoom=0&source=gbs_api")

    assert r.status_code == 200, r.text
    assert len(requested) == 1


async def test_a_png_cover_from_another_provider_is_still_applied(
    make_user, auth_header, make_client, db, covers_dir, monkeypatch,
):
    def handler(request):
        return httpx.Response(200, headers={"content-type": "image/png"}, content=PLACEHOLDER_PNG)

    _patch_transport(monkeypatch, handler)

    r, _ = await _apply(make_user, auth_header, make_client, db, "https://images.example/cover.png")

    assert r.status_code == 200, r.text
    assert (covers_dir / r.json()["cover_path"].split("/")[-1]).suffix == ".png"
