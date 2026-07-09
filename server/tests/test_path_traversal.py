"""Path traversal regression tests (issue #49).

Covers the read-side cover-serving endpoint, the cover-upload extension
allow-list, and the untrusted-fallback branch in replace_file -- the sites not
already exercised by test_uploads.py.
"""

import contextlib

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from config import settings
from models.book import EBook, AudioBook
import routers.files as files
import routers.library as library
import routers.troubleshoot as troubleshoot


@contextlib.asynccontextmanager
async def _files_client():
    app = FastAPI()
    app.include_router(files.router)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@contextlib.asynccontextmanager
async def _library_client():
    app = FastAPI()
    app.include_router(library.router)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@contextlib.asynccontextmanager
async def _troubleshoot_client():
    app = FastAPI()
    app.include_router(troubleshoot.router)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
def temp_covers_dir(monkeypatch, tmp_path):
    covers_dir = tmp_path / "covers"
    covers_dir.mkdir()
    monkeypatch.setattr(settings, "covers_dir", str(covers_dir))
    return covers_dir


@pytest.fixture
def stub_extract_metadata(monkeypatch):
    """troubleshoot.replace_file imports extract_metadata from routers.library
    at call time; stub it so the test doesn't need real ebook/audio parsing."""
    async def _fake_extract(filepath, file_type, db, library_root=None):
        return {}

    monkeypatch.setattr(library, "extract_metadata", _fake_extract)


# --- Read-side: GET /api/files/covers/{filename} ---------------------------

async def test_get_cover_rejects_dotdot_traversal(
    make_user, auth_header, temp_covers_dir, tmp_path,
):
    """A path-traversal filename must never escape covers_dir -- assert both
    the status code and that the canary file's content never leaks out."""
    canary = tmp_path / "secret.txt"
    canary.write_bytes(b"outside-covers-dir-secret")
    user = await make_user(username="reader", role="user")

    async with _files_client() as client:
        r = await client.get(
            "/api/files/covers/..%2Fsecret.txt",
            headers=auth_header(user),
        )

    assert r.status_code in (400, 404)
    assert b"outside-covers-dir-secret" not in r.content


async def test_get_cover_serves_legitimate_file(
    make_user, auth_header, temp_covers_dir,
):
    (temp_covers_dir / "cover.jpg").write_bytes(b"jpeg-bytes")
    user = await make_user(username="reader", role="user")

    async with _files_client() as client:
        r = await client.get(
            "/api/files/covers/cover.jpg",
            headers=auth_header(user),
        )

    assert r.status_code == 200
    assert r.content == b"jpeg-bytes"


async def test_get_cover_missing_file_404(make_user, auth_header, temp_covers_dir):
    user = await make_user(username="reader", role="user")
    async with _files_client() as client:
        r = await client.get(
            "/api/files/covers/nope.jpg",
            headers=auth_header(user),
        )
    assert r.status_code == 404


# --- Cover upload extension allow-list --------------------------------------

async def test_upload_ebook_cover_rejects_bad_extension(
    make_user, auth_header, temp_covers_dir, db,
):
    editor = await make_user(username="ed", role="editor")
    book = EBook(title="Some Book", filename="b.epub", file_path="/x/b.epub")
    db.add(book)
    await db.commit()
    await db.refresh(book)

    async with _library_client() as client:
        r = await client.post(
            f"/api/library/ebooks/{book.id}/cover",
            headers=auth_header(editor),
            files={"file": ("evil.svg", b"<svg/>", "image/svg+xml")},
        )

    assert r.status_code == 400
    assert "Unsupported cover format" in r.json()["detail"]


async def test_upload_ebook_cover_accepts_allowed_extension(
    make_user, auth_header, temp_covers_dir, db,
):
    editor = await make_user(username="ed", role="editor")
    book = EBook(title="Some Book", filename="b.epub", file_path="/x/b.epub")
    db.add(book)
    await db.commit()
    await db.refresh(book)

    async with _library_client() as client:
        r = await client.post(
            f"/api/library/ebooks/{book.id}/cover",
            headers=auth_header(editor),
            files={"file": ("cover.png", b"png-bytes", "image/png")},
        )

    assert r.status_code == 200, r.text


async def test_upload_audiobook_cover_rejects_bad_extension(
    make_user, auth_header, temp_covers_dir, db,
):
    editor = await make_user(username="ed", role="editor")
    book = AudioBook(title="Some Audiobook", filename="b.m4b", file_path="/x/b.m4b")
    db.add(book)
    await db.commit()
    await db.refresh(book)

    async with _library_client() as client:
        r = await client.post(
            f"/api/library/audiobooks/{book.id}/cover",
            headers=auth_header(editor),
            files={"file": ("evil.svg", b"<svg/>", "image/svg+xml")},
        )

    assert r.status_code == 400
    assert "Unsupported cover format" in r.json()["detail"]


async def test_upload_audiobook_cover_accepts_allowed_extension(
    make_user, auth_header, temp_covers_dir, db,
):
    editor = await make_user(username="ed", role="editor")
    book = AudioBook(title="Some Audiobook", filename="b.m4b", file_path="/x/b.m4b")
    db.add(book)
    await db.commit()
    await db.refresh(book)

    async with _library_client() as client:
        r = await client.post(
            f"/api/library/audiobooks/{book.id}/cover",
            headers=auth_header(editor),
            files={"file": ("cover.png", b"png-bytes", "image/png")},
        )

    assert r.status_code == 200, r.text


# --- replace_file's untrusted fallback branch -------------------------------

async def test_replace_file_fallback_sanitizes_traversal_filename(
    make_user, auth_header, stub_extract_metadata, monkeypatch, tmp_path, db,
):
    """When the DB row has no existing file_path, replace_file falls back to
    deriving the base filename from the uploaded name -- that fallback must
    stay inside the configured ebook_dir even for a traversal filename."""
    ebook_dir = tmp_path / "ebooks"
    ebook_dir.mkdir()
    monkeypatch.setattr(settings, "ebook_dir", str(ebook_dir))

    editor = await make_user(username="ed", role="editor")
    book = EBook(title="No File Yet", filename="", file_path="")
    db.add(book)
    await db.commit()
    await db.refresh(book)
    book_id = book.id

    async with _troubleshoot_client() as client:
        r = await client.post(
            f"/api/troubleshoot/replace/ebook/{book_id}",
            headers=auth_header(editor),
            files={"file": ("../../evil.epub", b"new-content", "application/epub+zip")},
        )

    assert r.status_code == 200, r.text
    assert r.json()["status"] == "replaced"
    assert (ebook_dir / "evil.epub").read_bytes() == b"new-content"
    assert not (tmp_path / "evil.epub").exists()

    await db.refresh(book)
    assert book.file_path == str(ebook_dir / "evil.epub")
    assert book.filename == "evil.epub"
