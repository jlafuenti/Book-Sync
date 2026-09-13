"""File work during a library scan must not run on the event loop (issue #203).

One uvicorn worker serves everything, so any synchronous filesystem work inside
an `async def` stops the whole server: position sync from a phone, audio
streaming, login and `/api/health` all wait behind it. A full scan is minutes of
that, which is long enough for the compose healthcheck to mark the container
unhealthy — and the symptom (unrelated endpoints timing out) never points at the
scan.

The convention in `library.py` was inconsistent rather than absent: cover
extraction and duration probing were already wrapped in `asyncio.to_thread`,
while the EPUB parse, the mutagen container read and the file hash three lines
away were not. These tests pin the rule for the scan/ingest path by asserting
each of those actually executes on a worker thread.
"""

import threading

import pytest

from services import library_scan, metadata_extract
from tests.factories import write_epub

CHAPTER = "<html><body><p>A sentence long enough to survive filtering.</p></body></html>"


class ThreadRecorder:
    """Records, per call, whether the callee ran on the event loop's thread."""

    def __init__(self):
        self.on_main_thread = []

    def note(self):
        self.on_main_thread.append(threading.current_thread() is threading.main_thread())

    @property
    def called(self) -> bool:
        return bool(self.on_main_thread)

    @property
    def always_off_loop(self) -> bool:
        return self.called and not any(self.on_main_thread)


@pytest.fixture
def epub_file(tmp_path):
    path = tmp_path / "Some Author - Some Title.epub"
    write_epub(str(path), [("ch1.xhtml", CHAPTER)])
    return str(path)


@pytest.fixture
def audio_file(tmp_path):
    path = tmp_path / "Some Author - Some Title.m4b"
    path.write_bytes(b"not really an audio container")
    return str(path)


async def test_the_epub_parse_runs_off_the_event_loop(db, epub_file, monkeypatch):
    from ebooklib import epub as ebooklib_epub

    from routers import library

    recorder = ThreadRecorder()
    real = ebooklib_epub.read_epub

    def _watched(*args, **kwargs):
        recorder.note()
        return real(*args, **kwargs)

    monkeypatch.setattr(metadata_extract.epub, "read_epub", _watched)

    await library.extract_metadata(epub_file, "ebook", db)

    assert recorder.called, "the EPUB was never parsed"
    assert recorder.always_off_loop, "epub.read_epub ran on the event loop thread"


async def test_the_audio_container_read_runs_off_the_event_loop(db, audio_file, monkeypatch):
    from routers import library

    recorder = ThreadRecorder()

    def _watched(*args, **kwargs):
        recorder.note()
        return None

    monkeypatch.setattr(metadata_extract.mutagen, "File", _watched)
    monkeypatch.setattr(metadata_extract, "probe_duration_seconds", lambda *a, **k: None)

    await library.extract_metadata(audio_file, "audiobook", db)

    assert recorder.called, "the audio container was never opened"
    assert recorder.always_off_loop, "mutagen.File ran on the event loop thread"


async def test_the_duration_probe_still_runs_off_the_event_loop(db, audio_file, monkeypatch):
    """It was already wrapped; folding it into the sync helper must not undo that."""
    from routers import library

    recorder = ThreadRecorder()

    def _watched(*args, **kwargs):
        recorder.note()
        return None

    monkeypatch.setattr(metadata_extract, "probe_duration_seconds", _watched)
    monkeypatch.setattr(metadata_extract.mutagen, "File", lambda *a, **k: None)

    await library.extract_metadata(audio_file, "audiobook", db)

    assert recorder.always_off_loop, "probe_duration_seconds ran on the event loop thread"


async def test_ingesting_an_ebook_hashes_off_the_event_loop(db, epub_file, tmp_path, monkeypatch):
    """`compute_file_hash` reads the file; it was called bare from the ingest path."""
    from routers import library

    recorder = ThreadRecorder()
    real = library.compute_file_hash

    def _watched(path):
        recorder.note()
        return real(path)

    monkeypatch.setattr(library_scan, "compute_file_hash", _watched)

    assert await library._ingest_one_ebook(db, epub_file, str(tmp_path)) is True

    assert recorder.called, "the new ebook was never hashed"
    assert recorder.always_off_loop, "compute_file_hash ran on the event loop thread"


async def test_ingesting_an_audiobook_hashes_off_the_event_loop(
    db, audio_file, tmp_path, monkeypatch
):
    from routers import library

    recorder = ThreadRecorder()
    real = library.compute_file_hash

    def _watched(path):
        recorder.note()
        return real(path)

    monkeypatch.setattr(library_scan, "compute_file_hash", _watched)
    monkeypatch.setattr(metadata_extract, "probe_duration_seconds", lambda *a, **k: None)

    assert await library._ingest_one_audiobook(db, audio_file, str(tmp_path), {}) is True

    assert recorder.called, "the new audiobook was never hashed"
    assert recorder.always_off_loop, "compute_file_hash ran on the event loop thread"


async def test_registering_a_converted_epub_hashes_off_the_event_loop(
    db, epub_file, monkeypatch
):
    """The Calibre conversion path shares the helper and the same failure."""
    from models.book import EBook
    from routers import library

    recorder = ThreadRecorder()
    real = library.compute_file_hash

    def _watched(path):
        recorder.note()
        return real(path)

    monkeypatch.setattr(library_scan, "compute_file_hash", _watched)

    source = EBook(title="Source", filename="source.azw3", file_path="/books/source.azw3")
    db.add(source)
    await db.flush()

    await library._register_epub_in_db(epub_file, source, db)

    assert recorder.called, "the converted EPUB was never hashed"
    assert recorder.always_off_loop, "compute_file_hash ran on the event loop thread"


async def test_the_scan_walks_the_library_off_the_event_loop(db, tmp_path, monkeypatch):
    """`os.walk` and the multi-file folder classifier both stat every file."""
    from config import settings
    from routers import library

    ebook_dir = tmp_path / "ebooks"
    audio_dir = tmp_path / "audiobooks"
    ebook_dir.mkdir()
    audio_dir.mkdir()
    write_epub(str(ebook_dir / "Some Author - Some Title.epub"), [("ch1.xhtml", CHAPTER)])
    monkeypatch.setattr(settings, "ebook_dir", str(ebook_dir))
    monkeypatch.setattr(settings, "audiobook_dir", str(audio_dir))

    async def _no_abs(db_):
        return {}

    monkeypatch.setattr(library_scan, "_maybe_load_abs_index", _no_abs)

    recorder = ThreadRecorder()
    real_walk = library_scan.os.walk

    def _watched(*args, **kwargs):
        recorder.note()
        return real_walk(*args, **kwargs)

    monkeypatch.setattr(library_scan.os, "walk", _watched)

    await library.scan_library_impl(db)

    assert recorder.called, "the library was never walked"
    assert recorder.always_off_loop, "os.walk ran on the event loop thread"


# ---------------------------------------------------------------------------
# Metadata PATCH and resolve-discrepancies write files off the event loop too
# (issue #523).
# ---------------------------------------------------------------------------
#
# `_write_ebook_metadata` rewrites every entry of the EPUB zip and
# `_write_audiobook_metadata` opens the file with mutagen and saves it, which
# for an MP3 whose ID3 padding is too small rewrites the whole file. Both were
# called bare from inside the `async def` handler -- the same shape the tests
# above already pin for the scan/ingest path, just on the manual-edit path
# instead. The sibling `/enrich-abs` endpoints got this right already
# (`await asyncio.to_thread(write_metadata_to_file, ...)`); these three call
# sites are the ones that did not.


async def test_ebook_patch_writes_metadata_off_the_event_loop(
    db, make_client, make_user, auth_header, monkeypatch
):
    from models.book import EBook
    from routers import library

    user = await make_user(username="patch-eb", role="editor")
    book = EBook(title="Before", filename="b.epub", file_path="/x/eloop/b.epub")
    db.add(book)
    await db.commit()
    await db.refresh(book)

    recorder = ThreadRecorder()
    monkeypatch.setattr(library, "_write_ebook_metadata", lambda *a, **k: recorder.note())

    async with make_client(library.router) as c:
        resp = await c.patch(
            f"/api/library/ebooks/{book.id}",
            json={"title": "After"},
            headers=auth_header(user),
        )

    assert resp.status_code == 200, resp.text
    assert recorder.called, "_write_ebook_metadata was never called"
    assert recorder.always_off_loop, "_write_ebook_metadata ran on the event loop thread"


async def test_audiobook_patch_writes_metadata_off_the_event_loop(
    db, make_client, make_user, auth_header, monkeypatch
):
    from models.book import AudioBook
    from routers import library

    user = await make_user(username="patch-ab", role="editor")
    book = AudioBook(title="Before", filename="b.m4b", file_path="/x/eloop/b.m4b")
    db.add(book)
    await db.commit()
    await db.refresh(book)

    recorder = ThreadRecorder()
    monkeypatch.setattr(library, "_write_audiobook_metadata", lambda *a, **k: recorder.note())

    async with make_client(library.router) as c:
        resp = await c.patch(
            f"/api/library/audiobooks/{book.id}",
            json={"title": "After"},
            headers=auth_header(user),
        )

    assert resp.status_code == 200, resp.text
    assert recorder.called, "_write_audiobook_metadata was never called"
    assert recorder.always_off_loop, "_write_audiobook_metadata ran on the event loop thread"


async def test_resolve_discrepancies_writes_metadata_off_the_event_loop(
    db, make_client, make_user, auth_header, monkeypatch
):
    """Both halves of a pair can be rewritten by one resolve call; both must
    cross to a thread. This must not disturb the commit-before-write ordering
    pinned by `tests/test_request_transactions.py` (issue #259)."""
    from routers import library
    from tests.factories import make_book_pair

    user = await make_user(username="resolve-disc", role="editor")
    pair = await make_book_pair(db, ebook_title="Old title", audiobook_title="Old title")

    ebook_recorder = ThreadRecorder()
    audio_recorder = ThreadRecorder()
    monkeypatch.setattr(
        library, "_write_ebook_metadata", lambda *a, **k: ebook_recorder.note()
    )
    monkeypatch.setattr(
        library, "_write_audiobook_metadata", lambda *a, **k: audio_recorder.note()
    )

    async with make_client(library.router) as c:
        resp = await c.post(
            f"/api/library/pairs/{pair.id}/resolve-discrepancies",
            headers=auth_header(user),
            json={
                "ebook_updates": {"title": "New title"},
                "audiobook_updates": {"title": "New title"},
            },
        )

    assert resp.status_code == 200, resp.text
    assert ebook_recorder.called, "_write_ebook_metadata was never called"
    assert ebook_recorder.always_off_loop, "_write_ebook_metadata ran on the event loop thread"
    assert audio_recorder.called, "_write_audiobook_metadata was never called"
    assert audio_recorder.always_off_loop, "_write_audiobook_metadata ran on the event loop thread"
