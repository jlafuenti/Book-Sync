"""
Upload endpoint tests (issue #46, Phase 2).

These exercise the /api/library/upload/{ebook,audiobook} handlers that shipped
broken (missing `await`/`db` on extract_metadata; missing `return` on the
audiobook handler). Metadata extraction itself (ebooklib/mutagen) is stubbed so
the test focuses on endpoint wiring: save file → hash → persist row → 201.
"""

import contextlib

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from config import settings
from models.book import EBook, AudioBook
import routers.library as library


@contextlib.asynccontextmanager
async def _library_client():
    app = FastAPI()
    app.include_router(library.router)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
def stub_metadata(monkeypatch):
    """Replace extract_metadata with an async stub returning known values."""
    def _install(**overrides):
        meta = {"title": "Stub Title", "author": "Stub Author",
                "series": "Stub Series", "series_index": 2.0}
        meta.update(overrides)

        async def _fake_extract(filepath, file_type, db, library_root=None):
            return meta

        monkeypatch.setattr(library, "extract_metadata", _fake_extract)
        return meta

    return _install


@pytest.fixture
def temp_library_dirs(monkeypatch, tmp_path):
    ebook_dir = tmp_path / "ebooks"
    audio_dir = tmp_path / "audiobooks"
    ebook_dir.mkdir()
    audio_dir.mkdir()
    monkeypatch.setattr(settings, "ebook_dir", str(ebook_dir))
    monkeypatch.setattr(settings, "audiobook_dir", str(audio_dir))
    return ebook_dir, audio_dir


async def test_upload_ebook_success(make_user, auth_header, stub_metadata, temp_library_dirs, db):
    ebook_dir, _ = temp_library_dirs
    stub_metadata()
    editor = await make_user(username="ed", role="editor")
    content = b"fake-epub-bytes"

    async with _library_client() as client:
        r = await client.post(
            "/api/library/upload/ebook",
            headers=auth_header(editor),
            files={"file": ("book.epub", content, "application/epub+zip")},
        )

    assert r.status_code == 201, r.text
    body = r.json()
    assert body["title"] == "Stub Title"
    assert body["author"] == "Stub Author"
    assert body["series"] == "Stub Series"
    assert body["series_index"] == 2.0
    assert body["format"] == "epub"
    assert body["file_size"] == len(content)

    # Row persisted
    row = (await db.execute(select(EBook).where(EBook.filename == "book.epub"))).scalar_one()
    assert row.file_hash and row.file_size == len(content)
    # Bytes written under the temp ebook dir
    assert (ebook_dir / "book.epub").read_bytes() == content


async def test_upload_audiobook_success_returns_row(make_user, auth_header, stub_metadata, temp_library_dirs, db):
    """The case that proves the missing-`return` fix: a None body would 500."""
    _, audio_dir = temp_library_dirs
    stub_metadata(title="Audio Title")
    editor = await make_user(username="ed", role="editor")
    content = b"fake-m4b-bytes"

    async with _library_client() as client:
        r = await client.post(
            "/api/library/upload/audiobook",
            headers=auth_header(editor),
            files={"file": ("audio.m4b", content, "audio/mp4")},
        )

    assert r.status_code == 201, r.text
    body = r.json()
    assert body["title"] == "Audio Title"
    assert body["format"] == "m4b"

    row = (await db.execute(select(AudioBook).where(AudioBook.filename == "audio.m4b"))).scalar_one()
    assert row.file_size == len(content)
    assert (audio_dir / "audio.m4b").read_bytes() == content


async def test_upload_ebook_rejects_unsupported_extension(make_user, auth_header, temp_library_dirs):
    editor = await make_user(username="ed", role="editor")
    async with _library_client() as client:
        r = await client.post(
            "/api/library/upload/ebook",
            headers=auth_header(editor),
            files={"file": ("notes.txt", b"x", "text/plain")},
        )
    assert r.status_code == 400
    assert "Unsupported ebook format" in r.json()["detail"]


async def test_upload_audiobook_rejects_unsupported_extension(make_user, auth_header, temp_library_dirs):
    editor = await make_user(username="ed", role="editor")
    async with _library_client() as client:
        r = await client.post(
            "/api/library/upload/audiobook",
            headers=auth_header(editor),
            files={"file": ("cover.png", b"x", "image/png")},
        )
    assert r.status_code == 400
    assert "Unsupported audiobook format" in r.json()["detail"]


async def test_upload_ebook_title_falls_back_to_filename(make_user, auth_header, stub_metadata, temp_library_dirs, db):
    stub_metadata(title=None)
    editor = await make_user(username="ed", role="editor")
    async with _library_client() as client:
        r = await client.post(
            "/api/library/upload/ebook",
            headers=auth_header(editor),
            files={"file": ("Fallback Name.epub", b"data", "application/epub+zip")},
        )
    assert r.status_code == 201, r.text
    assert r.json()["title"] == "Fallback Name.epub"


async def test_upload_requires_editor_role(make_user, auth_header, temp_library_dirs):
    """A plain user cannot upload (get_editor_user gate)."""
    plain = await make_user(username="plain", role="user")
    async with _library_client() as client:
        r = await client.post(
            "/api/library/upload/ebook",
            headers=auth_header(plain),
            files={"file": ("book.epub", b"data", "application/epub+zip")},
        )
    assert r.status_code == 403


# --- Path traversal (issue #49) -------------------------------------------

async def test_upload_ebook_neutralizes_path_traversal_filename(
    make_user, auth_header, stub_metadata, temp_library_dirs, tmp_path,
):
    """A `../../evil.epub`-style filename must not escape ebook_dir: the
    upload lands, sanitized to a basename, inside the configured directory."""
    ebook_dir, _ = temp_library_dirs
    stub_metadata()
    editor = await make_user(username="ed", role="editor")
    content = b"traversal-attempt"

    async with _library_client() as client:
        r = await client.post(
            "/api/library/upload/ebook",
            headers=auth_header(editor),
            files={"file": ("../../evil.epub", content, "application/epub+zip")},
        )

    assert r.status_code == 201, r.text
    assert (ebook_dir / "evil.epub").read_bytes() == content
    # Nothing was written outside the configured library dir.
    assert not (tmp_path / "evil.epub").exists()


async def test_upload_audiobook_neutralizes_path_traversal_filename(
    make_user, auth_header, stub_metadata, temp_library_dirs, tmp_path,
):
    _, audio_dir = temp_library_dirs
    stub_metadata()
    editor = await make_user(username="ed", role="editor")
    content = b"traversal-attempt"

    async with _library_client() as client:
        r = await client.post(
            "/api/library/upload/audiobook",
            headers=auth_header(editor),
            files={"file": ("../../evil.m4b", content, "audio/mp4")},
        )

    assert r.status_code == 201, r.text
    assert (audio_dir / "evil.m4b").read_bytes() == content
    assert not (tmp_path / "evil.m4b").exists()


async def test_upload_ebook_rejects_empty_basename_after_traversal(
    make_user, auth_header, temp_library_dirs,
):
    """A filename that reduces to nothing after stripping directory
    components (e.g. `../../`) is rejected rather than written with an empty
    name."""
    editor = await make_user(username="ed", role="editor")
    async with _library_client() as client:
        r = await client.post(
            "/api/library/upload/ebook",
            headers=auth_header(editor),
            files={"file": ("../../", b"x", "application/epub+zip")},
        )
    # Starlette/httpx may treat this as an empty filename, or it reaches the
    # extension check with ext == "" first -- both are rejections, just with
    # possibly different messages, so only assert the safe outcome: no 2xx.
    assert r.status_code == 400
