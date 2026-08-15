"""
Composite file-hash tests (issue #45).

The old scheme hashed only the first 10 MiB of a file, so two different
audiobooks sharing an identical opening (publisher bumper, "This is Audible"
preamble) produced the *same* hash — false duplicates in troubleshoot and
false hits in the auto-pair unpair-exclusion memory. The composite scheme
hashes size + head + tail; these tests pin it, the equivalence of the
bytes/file implementations, and the one-time rehash endpoint that migrates
existing rows (including remapping `auto_pair_excluded_hashes`, which stores
raw hash values and would otherwise silently go stale).
"""

import contextlib
import hashlib

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from models.book import EBook, AudioBook
import routers.library as library
import routers.troubleshoot as troubleshoot
import services.file_hash as file_hash


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
def small_chunk(monkeypatch):
    """Shrink the head/tail window so fixtures stay tiny."""
    monkeypatch.setattr(file_hash, "CHUNK", 1024)
    return 1024


# --- The scheme itself -------------------------------------------------------

def test_shared_head_different_body_produces_different_hashes(small_chunk):
    """The bug: identical first-CHUNK bytes must no longer collide."""
    head = b"\x01" * small_chunk
    a = head + b"the rest of audiobook A"
    b = head + b"the rest of audiobook B"
    assert file_hash.hash_bytes(a) != file_hash.hash_bytes(b)


def test_identical_content_produces_identical_hashes(small_chunk, tmp_path):
    """True duplicates must still dedup, via both implementations."""
    content = b"\x02" * (small_chunk * 3) + b"tail"
    p1 = tmp_path / "one.m4b"
    p2 = tmp_path / "two.m4b"
    p1.write_bytes(content)
    p2.write_bytes(content)
    assert file_hash.hash_file(str(p1)) == file_hash.hash_file(str(p2))
    assert file_hash.hash_bytes(content) == file_hash.hash_file(str(p1))


def test_same_head_and_tail_but_different_size_differ(small_chunk):
    """The size term distinguishes files whose head+tail windows coincide."""
    head = b"\x03" * small_chunk
    tail = b"\x04" * small_chunk
    assert file_hash.hash_bytes(head + tail) != \
        file_hash.hash_bytes(head + b"\x00" * 100 + tail)


def test_hash_file_matches_hash_bytes_for_small_files(small_chunk, tmp_path):
    """Files shorter than the window (head and tail overlap) still agree."""
    content = b"tiny"
    p = tmp_path / "tiny.epub"
    p.write_bytes(content)
    assert file_hash.hash_file(str(p)) == file_hash.hash_bytes(content)


def test_real_window_size_intro_bumper_no_longer_collides(tmp_path):
    """Un-patched sanity check at the real 10 MiB window: the exact reported
    scenario — two >10 MiB audiobooks identical up to the window, differing
    after — must hash differently."""
    shared_intro = b"\x05" * file_hash.CHUNK
    a = shared_intro + b"story A"
    b = shared_intro + b"story B"
    assert file_hash.hash_bytes(a) != file_hash.hash_bytes(b)


def test_library_compute_file_hash_uses_composite_scheme(tmp_path):
    """Scan/upload ingest goes through library.compute_file_hash — it must be
    the composite scheme, not the old head-only sha256."""
    content = b"some ebook bytes"
    p = tmp_path / "book.epub"
    p.write_bytes(content)
    assert library.compute_file_hash(str(p)) == file_hash.hash_bytes(content)
    assert library.compute_file_hash(str(p)) != hashlib.sha256(content).hexdigest()


# --- replace_file stores the composite hash ---------------------------------

async def test_replace_file_stores_composite_hash(
    make_user, auth_header, monkeypatch, tmp_path, db,
):
    from config import settings

    ebook_dir = tmp_path / "ebooks"
    ebook_dir.mkdir()
    monkeypatch.setattr(settings, "ebook_dir", str(ebook_dir))

    async def _fake_extract(filepath, file_type, db, library_root=None):
        return {}

    monkeypatch.setattr(library, "extract_metadata", _fake_extract)

    editor = await make_user(username="ed", role="editor")
    book = EBook(title="B", filename="", file_path="")
    db.add(book)
    await db.commit()
    await db.refresh(book)
    book_id = book.id

    content = b"replacement bytes"
    async with _troubleshoot_client() as client:
        r = await client.post(
            f"/api/troubleshoot/replace/ebook/{book_id}",
            headers=auth_header(editor),
            files={"file": ("new.epub", content, "application/epub+zip")},
        )

    assert r.status_code == 200, r.text
    await db.refresh(book)
    assert book.file_hash == file_hash.hash_bytes(content)


# --- POST /api/library/rehash ------------------------------------------------

async def test_rehash_updates_hashes_and_remaps_exclusions(
    make_user, auth_header, tmp_path, db,
):
    """Rows carrying old-scheme hashes get the composite hash, and every
    occurrence of an old hash inside auto_pair_excluded_hashes is remapped so
    unpair memory keeps blocking the same books (issue #45)."""
    eb_content = b"ebook bytes"
    ab_content = b"audiobook bytes"
    eb_path = tmp_path / "e.epub"
    ab_path = tmp_path / "a.m4b"
    eb_path.write_bytes(eb_content)
    ab_path.write_bytes(ab_content)

    # Old scheme: sha256 of the head only (== whole file for small files).
    old_eb_hash = hashlib.sha256(eb_content).hexdigest()
    old_ab_hash = hashlib.sha256(ab_content).hexdigest()

    eb = EBook(
        title="E", filename="e.epub", file_path=str(eb_path),
        file_hash=old_eb_hash, auto_pair_excluded_hashes=[old_ab_hash],
    )
    ab = AudioBook(
        title="A", filename="a.m4b", file_path=str(ab_path),
        file_hash=old_ab_hash, auto_pair_excluded_hashes=[old_eb_hash],
    )
    missing = AudioBook(
        title="Gone", filename="gone.m4b", file_path=str(tmp_path / "gone.m4b"),
        file_hash="feedface",
    )
    db.add_all([eb, ab, missing])
    await db.commit()

    editor = await make_user(username="ed", role="editor")
    async with _library_client() as client:
        r = await client.post("/api/library/rehash", headers=auth_header(editor))

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["rehashed"] == 2
    assert body["skipped_missing"] == 1
    assert body["exclusions_remapped"] == 2

    await db.refresh(eb)
    await db.refresh(ab)
    await db.refresh(missing)
    assert eb.file_hash == file_hash.hash_bytes(eb_content)
    assert ab.file_hash == file_hash.hash_bytes(ab_content)
    assert eb.auto_pair_excluded_hashes == [ab.file_hash]
    assert ab.auto_pair_excluded_hashes == [eb.file_hash]
    # Rows whose file is gone keep their stale hash instead of erroring the run.
    assert missing.file_hash == "feedface"


async def test_rehash_is_idempotent(make_user, auth_header, tmp_path, db):
    content = b"already composite"
    p = tmp_path / "e.epub"
    p.write_bytes(content)
    eb = EBook(
        title="E", filename="e.epub", file_path=str(p),
        file_hash=file_hash.hash_bytes(content),
    )
    db.add(eb)
    await db.commit()

    editor = await make_user(username="ed", role="editor")
    async with _library_client() as client:
        r = await client.post("/api/library/rehash", headers=auth_header(editor))

    assert r.status_code == 200, r.text
    assert r.json()["rehashed"] == 0


async def test_rehash_requires_editor(make_user, auth_header, db):
    viewer = await make_user(username="viewer", role="user")
    async with _library_client() as client:
        r = await client.post("/api/library/rehash", headers=auth_header(viewer))
    assert r.status_code == 403
