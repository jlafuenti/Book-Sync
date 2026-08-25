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
from database import async_session
from models.book import EBook, AudioBook, BookPair
import routers.library as library


@contextlib.asynccontextmanager
async def _library_client():
    from middleware import MultipartBodyLimitMiddleware

    app = FastAPI()
    # Mirror production (main.py): the pre-parse multipart size guard sits in
    # front of every route, so the upload tests exercise the same stack.
    app.add_middleware(MultipartBodyLimitMiddleware)
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


async def test_upload_ebook_auto_matches_existing_audiobook(
    make_user, auth_header, stub_metadata, temp_library_dirs, db,
):
    """Uploads must run through the same ingest path as scan (issue #43
    follow-up), which includes auto-matching. Seed an unpaired audiobook,
    upload a title/author-matching ebook, and confirm a BookPair appears."""
    async with async_session() as session:
        audiobook = AudioBook(
            title="Same Title Book",
            author="Same Author",
            filename="audio.m4b",
            file_path="/fake/audio.m4b",
            file_hash="deadbeef",
            file_size=123,
            format="m4b",
        )
        session.add(audiobook)
        await session.commit()

    stub_metadata(title="Same Title Book", author="Same Author", series=None, series_index=None)
    editor = await make_user(username="ed", role="editor")

    async with _library_client() as client:
        r = await client.post(
            "/api/library/upload/ebook",
            headers=auth_header(editor),
            files={"file": ("book.epub", b"fake-epub-bytes", "application/epub+zip")},
        )

    assert r.status_code == 201, r.text
    pairs = (await db.execute(select(BookPair))).scalars().all()
    assert len(pairs) == 1


async def test_upload_ebook_duplicate_path_rejected(
    make_user, auth_header, stub_metadata, temp_library_dirs, db,
):
    """Re-uploading a filename that already exists in the library must not
    silently overwrite the file or create a second DB row (issue #43)."""
    ebook_dir, _ = temp_library_dirs
    stub_metadata()
    editor = await make_user(username="ed", role="editor")
    original_content = b"original-bytes"

    async with _library_client() as client:
        r1 = await client.post(
            "/api/library/upload/ebook",
            headers=auth_header(editor),
            files={"file": ("book.epub", original_content, "application/epub+zip")},
        )
        assert r1.status_code == 201, r1.text

        r2 = await client.post(
            "/api/library/upload/ebook",
            headers=auth_header(editor),
            files={"file": ("book.epub", b"new-conflicting-bytes", "application/epub+zip")},
        )

    assert r2.status_code == 409, r2.text
    assert (ebook_dir / "book.epub").read_bytes() == original_content
    rows = (await db.execute(select(EBook).where(EBook.filename == "book.epub"))).scalars().all()
    assert len(rows) == 1


# --- Pre-auth multipart body cap (issue #157) -----------------------------
#
# FastAPI parses a multipart/form-data body (`await request.form()`) BEFORE it
# resolves the route's auth dependency, so an unauthenticated caller can drive
# the multipart parser with an arbitrarily large body. The
# MultipartBodyLimitMiddleware must refuse oversized bodies with 413 before any
# parsing — proven here by getting 413 (not 401) with NO token attached.

async def test_oversized_multipart_is_rejected_before_auth(monkeypatch, temp_library_dirs):
    monkeypatch.setattr(settings, "upload_max_bytes", 64)
    async with _library_client() as client:
        r = await client.post(
            "/api/library/upload/ebook",
            # No Authorization header on purpose.
            files={"file": ("big.epub", b"x" * 1024, "application/epub+zip")},
        )
    assert r.status_code == 413, r.text
    assert "exceeds" in r.json()["detail"]


async def test_under_cap_multipart_still_reaches_auth(temp_library_dirs):
    """A multipart request under the cap passes the middleware untouched and
    fails on auth (401), not on size — no false positives."""
    async with _library_client() as client:
        r = await client.post(
            "/api/library/upload/ebook",
            files={"file": ("small.epub", b"tiny", "application/epub+zip")},
        )
    assert r.status_code == 401


async def test_oversized_non_multipart_is_not_blocked(monkeypatch, temp_library_dirs):
    """The cap targets the multipart parser only: a non-multipart body over the
    cap is left for the route/framework to judge (here: 401 at auth)."""
    monkeypatch.setattr(settings, "upload_max_bytes", 8)
    async with _library_client() as client:
        r = await client.post(
            "/api/library/upload/ebook",
            content=b"{" + b"x" * 1024 + b"}",
            headers={"Content-Type": "application/json"},
        )
    assert r.status_code == 401


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
