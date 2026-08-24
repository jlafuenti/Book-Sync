"""Upload size-cap enforcement across every file-accepting endpoint (issue #151).

Caps are monkeypatched down to ~1 KiB so oversized bodies stay tiny — never
build gigabyte bodies in httpx. The contract under test: an over-cap body gets
a 413, nothing lands on disk (no dest, no ``.part``), no DB row changes, and a
pre-existing destination file is never truncated.
"""

import pytest
from sqlalchemy import select

from config import settings
from database import async_session
from models.book import AudioBook, EBook
import routers.import_sources as import_sources
import routers.library as library
import routers.troubleshoot as troubleshoot

CAP = 1024  # bytes; the tiny test-time upload cap


@pytest.fixture
def tiny_caps(monkeypatch):
    monkeypatch.setattr(settings, "max_upload_bytes", CAP)
    monkeypatch.setattr(settings, "max_cover_bytes", CAP)


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


@pytest.fixture
def temp_covers_dir(monkeypatch, tmp_path):
    covers_dir = tmp_path / "covers"
    covers_dir.mkdir()
    monkeypatch.setattr(settings, "covers_dir", str(covers_dir))
    return covers_dir


@pytest.fixture
def upload_client(make_client):
    """Async-CM factory over every router with a file-accepting endpoint."""
    def _open():
        return make_client(library.router, troubleshoot.router, import_sources.router)
    return _open


# --- library uploads --------------------------------------------------------

async def test_oversized_ebook_upload_413(
    make_user, auth_header, tiny_caps, temp_library_dirs, upload_client, db,
):
    ebook_dir, _ = temp_library_dirs
    editor = await make_user(username="ed", role="editor")

    async with upload_client() as client:
        r = await client.post(
            "/api/library/upload/ebook",
            headers=auth_header(editor),
            files={"file": ("big.epub", b"x" * (CAP + 1), "application/epub+zip")},
        )

    assert r.status_code == 413, r.text
    assert list(ebook_dir.iterdir()) == []  # no file, no .part
    rows = (await db.execute(select(EBook))).scalars().all()
    assert rows == []


async def test_oversized_audiobook_upload_413(
    make_user, auth_header, tiny_caps, temp_library_dirs, upload_client, db,
):
    _, audio_dir = temp_library_dirs
    editor = await make_user(username="ed", role="editor")

    async with upload_client() as client:
        r = await client.post(
            "/api/library/upload/audiobook",
            headers=auth_header(editor),
            files={"file": ("big.m4b", b"x" * (CAP + 1), "audio/mp4")},
        )

    assert r.status_code == 413, r.text
    assert list(audio_dir.iterdir()) == []
    rows = (await db.execute(select(AudioBook))).scalars().all()
    assert rows == []


async def test_at_limit_ebook_upload_succeeds(
    make_user, auth_header, tiny_caps, stub_metadata, temp_library_dirs, upload_client,
):
    """The boundary is total > limit — a body of exactly the cap is accepted."""
    ebook_dir, _ = temp_library_dirs
    stub_metadata()
    editor = await make_user(username="ed", role="editor")
    content = b"x" * CAP

    async with upload_client() as client:
        r = await client.post(
            "/api/library/upload/ebook",
            headers=auth_header(editor),
            files={"file": ("exact.epub", content, "application/epub+zip")},
        )

    assert r.status_code == 201, r.text
    assert (ebook_dir / "exact.epub").read_bytes() == content
    assert not (ebook_dir / "exact.epub.part").exists()


# --- troubleshoot replace ---------------------------------------------------

async def test_oversized_replace_does_not_truncate_live_file(
    make_user, auth_header, tiny_caps, temp_library_dirs, upload_client, db,
):
    """The key regression: before #151 an oversized replace body truncated the
    live library file in place (dest == old path). Now: 413, old bytes intact,
    row untouched, no .part debris."""
    ebook_dir, _ = temp_library_dirs
    original = b"original-ebook-bytes"
    live = ebook_dir / "book.epub"
    live.write_bytes(original)

    async with async_session() as session:
        row = EBook(
            title="Book", filename="book.epub", file_path=str(live),
            file_hash="deadbeef", file_size=len(original), format="epub",
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
        book_id = row.id

    editor = await make_user(username="ed", role="editor")
    async with upload_client() as client:
        r = await client.post(
            f"/api/troubleshoot/replace/ebook/{book_id}",
            headers=auth_header(editor),
            files={"file": ("new.epub", b"x" * (CAP + 1), "application/epub+zip")},
        )

    assert r.status_code == 413, r.text
    assert live.read_bytes() == original
    assert not (ebook_dir / "book.epub.part").exists()
    fresh = (await db.execute(select(EBook).where(EBook.id == book_id))).scalar_one()
    assert fresh.file_path == str(live)
    assert fresh.file_hash == "deadbeef"
    assert fresh.file_size == len(original)


# --- cover uploads ----------------------------------------------------------

async def _seed_ebook(tmp_path):
    async with async_session() as session:
        row = EBook(
            title="Book", filename="book.epub",
            file_path=str(tmp_path / "book.epub"),
            file_hash="deadbeef", file_size=1, format="epub",
            cover_path="/api/files/covers/old.jpg",
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
        return row.id


async def _seed_audiobook(tmp_path):
    async with async_session() as session:
        row = AudioBook(
            title="Audio", filename="audio.m4b",
            file_path=str(tmp_path / "audio.m4b"),
            file_hash="deadbeef", file_size=1, format="m4b",
            cover_path="/api/files/covers/old.jpg",
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
        return row.id


async def test_oversized_ebook_cover_413(
    make_user, auth_header, tiny_caps, temp_covers_dir, upload_client, db, tmp_path,
):
    book_id = await _seed_ebook(tmp_path)
    editor = await make_user(username="ed", role="editor")

    async with upload_client() as client:
        r = await client.post(
            f"/api/library/ebooks/{book_id}/cover",
            headers=auth_header(editor),
            files={"file": ("cover.jpg", b"x" * (CAP + 1), "image/jpeg")},
        )

    assert r.status_code == 413, r.text
    assert list(temp_covers_dir.iterdir()) == []
    fresh = (await db.execute(select(EBook).where(EBook.id == book_id))).scalar_one()
    assert fresh.cover_path == "/api/files/covers/old.jpg"


async def test_oversized_audiobook_cover_413(
    make_user, auth_header, tiny_caps, temp_covers_dir, upload_client, db, tmp_path,
):
    book_id = await _seed_audiobook(tmp_path)
    editor = await make_user(username="ed", role="editor")

    async with upload_client() as client:
        r = await client.post(
            f"/api/library/audiobooks/{book_id}/cover",
            headers=auth_header(editor),
            files={"file": ("cover.jpg", b"x" * (CAP + 1), "image/jpeg")},
        )

    assert r.status_code == 413, r.text
    assert list(temp_covers_dir.iterdir()) == []
    fresh = (await db.execute(select(AudioBook).where(AudioBook.id == book_id))).scalar_one()
    assert fresh.cover_path == "/api/files/covers/old.jpg"


async def test_small_cover_uses_cover_cap_not_upload_cap(
    make_user, auth_header, monkeypatch, temp_covers_dir, upload_client, tmp_path,
):
    """Covers are governed by max_cover_bytes; a tiny max_upload_bytes must not
    reject them."""
    monkeypatch.setattr(settings, "max_upload_bytes", CAP)
    monkeypatch.setattr(settings, "max_cover_bytes", 64 * 1024)
    book_id = await _seed_ebook(tmp_path)
    editor = await make_user(username="ed", role="editor")
    content = b"x" * (CAP * 4)  # over max_upload_bytes, under max_cover_bytes

    async with upload_client() as client:
        r = await client.post(
            f"/api/library/ebooks/{book_id}/cover",
            headers=auth_header(editor),
            files={"file": ("cover.jpg", content, "image/jpeg")},
        )

    assert r.status_code == 200, r.text
    files = list(temp_covers_dir.iterdir())
    assert len(files) == 1
    assert files[0].read_bytes() == content


# --- ACSM upload ------------------------------------------------------------

async def test_oversized_acsm_upload_413_and_never_processed(
    make_user, auth_header, tiny_caps, upload_client, monkeypatch,
):
    calls = []

    async def _spy(db, path, original_filename=None):
        calls.append(path)
        return {"status": "added"}

    monkeypatch.setattr(import_sources, "acsm_process_file", _spy)
    admin = await make_user(username="boss", role="admin")

    async with upload_client() as client:
        r = await client.post(
            "/api/import/acsm/upload",
            headers=auth_header(admin),
            files={"file": ("book.acsm", b"x" * (CAP + 1), "application/xml")},
        )

    assert r.status_code == 413, r.text
    assert calls == []
