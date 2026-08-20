"""
`AudioBook.duration_seconds` comes from the file itself (issue #127).

Nothing wrote this column before. The scanner opened every audiobook with
mutagen for its tags but threw `info.length` away, and the Audible match
result's `duration_seconds` only ever lived on the search response —
`MetadataUpdate` has no such field, so applying a match never persisted it.
Production had 0 of 298 rows populated, which silently disabled the
server-side audio auto-complete rule from #56: "unknown audio length ⇒ no
audio end zone" (docs/position-sync-contract.md § Completion).

The rules pinned here:
  - the scanner reads the length and writes it — for new rows *and* for rows
    that already exist, which is what backfills a library scanned before this;
  - a length read from the file wins over whatever is stored, so a replaced or
    re-encoded file cannot leave a stale duration behind;
  - a *missing* read never clobbers a stored value with null.
"""

import contextlib

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from config import settings
from models.book import AudioBook
import routers.library as library
import routers.troubleshoot as troubleshoot


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


# --------------------------------------------------------------------------
# Fake mutagen. Same approach as test_abs_metadata.py: patch the mutagen entry
# point rather than ship binary audio fixtures. The class *name* matters —
# extract_metadata routes on `type(audio).__name__`, so calling it MP3 puts us
# on the ID3 branch.
# --------------------------------------------------------------------------

class _Info:
    def __init__(self, length):
        self.length = length


class MP3(dict):
    """Dict-like tags plus `.info.length`, which is all extract_metadata reads.

    Subclassing dict also reproduces the truthiness of the real thing, which
    matters here: mutagen's `FileType` defines `__len__` as its tag count and
    no `__bool__`, so an untagged file is falsy in both.
    """

    def __init__(self, tags=None, length=None):
        super().__init__(tags or {})
        self.tags = self
        self.info = _Info(length) if length is not None else None


@pytest.fixture
def fake_mutagen(monkeypatch):
    """Make `mutagen.File(...)` return a fake with the given length/tags.

    Pass `raises=` to simulate the legacy-`chpl` failure, where
    `mutagen.File()` blows up before yielding anything at all.
    """
    import mutagen

    def _install(length=None, tags=None, raises=None):
        audio = MP3(tags if tags is not None else {"title": ["Dune"]}, length=length)

        def _file(path, **kw):
            if raises is not None:
                raise raises
            return audio

        monkeypatch.setattr(mutagen, "File", _file)
        return audio

    return _install


@pytest.fixture(autouse=True)
def no_ffprobe(monkeypatch):
    """Default every test to "ffprobe found nothing", so the tests that care
    about mutagen exercise the fallback deliberately rather than by accident of
    whether ffprobe happens to be installed on the machine running them."""
    monkeypatch.setattr(library, "probe_duration_seconds", lambda path: None)


@pytest.fixture
def fake_ffprobe(monkeypatch):
    """Make the ffprobe probe return a fixed number of seconds."""
    def _install(seconds):
        monkeypatch.setattr(library, "probe_duration_seconds", lambda path: seconds)

    return _install


@pytest.fixture
def audio_library(monkeypatch, tmp_path):
    """A tmp audiobook dir bound to settings, plus a file inside it."""
    audio_dir = tmp_path / "audiobooks"
    audio_dir.mkdir()
    monkeypatch.setattr(settings, "audiobook_dir", str(audio_dir))
    monkeypatch.setattr(settings, "ebook_dir", str(tmp_path / "ebooks"))
    (tmp_path / "ebooks").mkdir()
    path = audio_dir / "Dune.mp3"
    path.write_bytes(b"not really audio")
    return path


@pytest.fixture
def stub_extract(monkeypatch):
    """Replace extract_metadata with a stub returning fixed metadata.

    Used by the row-level tests so they exercise ingest/rescan wiring without
    depending on how the length is read (that is pinned separately above).
    """
    def _install(**overrides):
        meta = {"title": "Dune", "author": "Frank Herbert",
                "series": None, "series_index": None}
        meta.update(overrides)

        async def _fake_extract(filepath, file_type, db, library_root=None):
            return dict(meta)

        monkeypatch.setattr(library, "extract_metadata", _fake_extract)
        return meta

    return _install


# ---------- which source wins ----------

async def test_ffprobe_beats_a_mutagen_length_that_disagrees(
    db, fake_ffprobe, fake_mutagen, audio_library
):
    """The real reason ffprobe is the authority (issue #127, found on deploy).

    "Artemis Fowl and the Time Paradox" is a 9.9-hour MP3 whose header makes
    mutagen report **12 seconds**. Storing that is worse than storing nothing:
    `_in_audio_end_zone` is `duration*1000 - position <= 120_000`, which at 12 s
    is true for *every* position, so the first audio write would silently mark
    the book finished. Nothing about mutagen's answer looks wrong on its own —
    only a second opinion catches it.
    """
    fake_ffprobe(35648.2)
    fake_mutagen(length=12.0)

    meta = await library.extract_metadata(
        str(audio_library), "audiobook", db, library_root=settings.audiobook_dir
    )

    assert meta["duration_seconds"] == 35648


async def test_ffprobe_reads_a_file_mutagen_cannot_open_at_all(
    db, fake_ffprobe, fake_mutagen, audio_library
):
    """The 17 m4b files on prod with a legacy Nero `chpl` atom: `mutagen.File()`
    raises before yielding anything, while ffprobe reads every one of them."""
    import mutagen.mp4

    fake_ffprobe(45492.6)
    fake_mutagen(raises=mutagen.mp4.MP4MetadataError("unpack requires a buffer of 8 bytes"))

    meta = await library.extract_metadata(
        str(audio_library), "audiobook", db, library_root=settings.audiobook_dir
    )

    assert meta["duration_seconds"] == 45492


async def test_mutagen_is_used_when_ffprobe_is_unavailable(
    db, fake_mutagen, audio_library
):
    """`no_ffprobe` is autouse, so this is the ffprobe-missing path: a machine
    without ffmpeg on PATH still gets a duration."""
    fake_mutagen(length=3600.7)

    meta = await library.extract_metadata(
        str(audio_library), "audiobook", db, library_root=settings.audiobook_dir
    )

    assert meta["duration_seconds"] == 3600


async def test_neither_source_yields_a_length(db, fake_mutagen, audio_library):
    import mutagen.mp4

    fake_mutagen(raises=mutagen.mp4.MP4MetadataError("boom"))

    meta = await library.extract_metadata(
        str(audio_library), "audiobook", db, library_root=settings.audiobook_dir
    )

    assert "duration_seconds" not in meta


# ---------- reading the length out of the file ----------

async def test_extract_metadata_reads_the_length_from_the_audio_file(
    db, fake_mutagen, audio_library
):
    fake_mutagen(length=3600.7)

    meta = await library.extract_metadata(
        str(audio_library), "audiobook", db, library_root=settings.audiobook_dir
    )

    # Truncated, not rounded: the column is an Integer of whole seconds.
    assert meta["duration_seconds"] == 3600


async def test_a_file_with_no_tags_at_all_still_reports_its_length(
    db, fake_mutagen, audio_library
):
    """Untagged files must not be skipped along with their (absent) tags.

    mutagen's `FileType` defines `__len__` as its tag count and no `__bool__`,
    so an untagged file is *falsy* — `if audio:` in extract_metadata is really
    "if this file has any tags". Reading the length under that guard silently
    lost it for exactly the files where the container header is the only
    metadata worth having.
    """
    fake_mutagen(length=3600.7, tags={})

    meta = await library.extract_metadata(
        str(audio_library), "audiobook", db, library_root=settings.audiobook_dir
    )

    assert meta["duration_seconds"] == 3600


async def test_a_readable_length_alone_does_not_claim_embedded_metadata(
    db, fake_mutagen, audio_library
):
    """Duration is a physical property of the container, not something a tagger
    wrote. A file with a length but no usable tags must still report where its
    *metadata* came from — otherwise every audiobook would claim "embedded" and
    the ingest would stop filling title/author from the filename pattern."""
    fake_mutagen(length=3600.7, tags={})

    meta = await library.extract_metadata(
        str(audio_library), "audiobook", db, library_root=settings.audiobook_dir
    )

    assert meta.get("_metadata_source") != "embedded"


@pytest.mark.parametrize("length", [None, 0.0])
async def test_no_readable_length_leaves_the_key_absent(
    db, fake_mutagen, audio_library, length
):
    """A file mutagen can open but can't measure must not report a length.

    The key has to be *absent* rather than None — every consumer below treats
    "the file told us nothing" as "leave the stored value alone".
    """
    fake_mutagen(length=length, tags={"title": ["Dune"]})

    meta = await library.extract_metadata(
        str(audio_library), "audiobook", db, library_root=settings.audiobook_dir
    )

    assert "duration_seconds" not in meta
    assert meta["title"] == "Dune"      # the rest of the extraction still ran


# ---------- writing it onto the row ----------

async def test_scanning_a_new_audiobook_stores_the_duration(
    db, stub_extract, audio_library
):
    stub_extract(duration_seconds=3600)

    await library.scan_files_impl(db, [str(audio_library)])
    await db.commit()

    row = (await db.execute(select(AudioBook))).scalar_one()
    assert row.duration_seconds == 3600


async def test_scanning_backfills_an_existing_row_that_has_no_duration(
    db, stub_extract, audio_library
):
    """The backfill for the 298 rows scanned before this shipped: they are
    already in the DB, so they take the *existing row* branch of the ingest."""
    stub_extract(duration_seconds=3600)
    db.add(AudioBook(title="Dune", filename="Dune.mp3",
                     file_path=str(audio_library), format="mp3",
                     duration_seconds=None))
    await db.commit()

    await library.scan_files_impl(db, [str(audio_library)])
    await db.commit()

    row = (await db.execute(select(AudioBook))).scalar_one()
    assert row.duration_seconds == 3600


async def test_the_file_wins_over_a_stored_duration(db, stub_extract, audio_library):
    """A re-encoded or replaced file must not keep the old row's length."""
    stub_extract(duration_seconds=3600)
    db.add(AudioBook(title="Dune", filename="Dune.mp3",
                     file_path=str(audio_library), format="mp3",
                     duration_seconds=1000))
    await db.commit()

    await library.scan_files_impl(db, [str(audio_library)])
    await db.commit()

    row = (await db.execute(select(AudioBook))).scalar_one()
    assert row.duration_seconds == 3600


async def test_an_unreadable_length_never_clobbers_a_stored_one(
    db, stub_extract, audio_library
):
    stub_extract()                      # no duration_seconds key at all
    db.add(AudioBook(title="Dune", filename="Dune.mp3",
                     file_path=str(audio_library), format="mp3",
                     duration_seconds=3600))
    await db.commit()

    await library.scan_files_impl(db, [str(audio_library)])
    await db.commit()

    row = (await db.execute(select(AudioBook))).scalar_one()
    assert row.duration_seconds == 3600


# ---------- the forced-rescan paths ----------

async def _seed(db, path, duration_seconds=None):
    ab = AudioBook(title="Dune", filename="Dune.mp3", file_path=str(path),
                   format="mp3", duration_seconds=duration_seconds)
    db.add(ab)
    await db.commit()
    await db.refresh(ab)
    return ab


async def test_rescan_all_stores_the_duration(
    db, stub_extract, audio_library, make_user, auth_header
):
    stub_extract(duration_seconds=3600)
    ab = await _seed(db, audio_library)
    editor = await make_user(username="ed", role="editor")

    async with _library_client() as c:
        r = await c.post("/api/library/rescan-all", headers=auth_header(editor))

    assert r.status_code == 200, r.text
    await db.refresh(ab)
    assert ab.duration_seconds == 3600


async def test_rescan_all_keeps_a_stored_duration_when_the_file_says_nothing(
    db, stub_extract, audio_library, make_user, auth_header
):
    stub_extract()
    ab = await _seed(db, audio_library, duration_seconds=3600)
    editor = await make_user(username="ed", role="editor")

    async with _library_client() as c:
        r = await c.post("/api/library/rescan-all", headers=auth_header(editor))

    assert r.status_code == 200, r.text
    await db.refresh(ab)
    assert ab.duration_seconds == 3600


async def test_single_book_rescan_stores_the_duration(
    db, stub_extract, audio_library, make_user, auth_header
):
    stub_extract(duration_seconds=3600)
    ab = await _seed(db, audio_library, duration_seconds=1000)
    editor = await make_user(username="ed", role="editor")

    async with _library_client() as c:
        r = await c.post(f"/api/library/audiobooks/{ab.id}/rescan",
                         headers=auth_header(editor))

    assert r.status_code == 200, r.text
    await db.refresh(ab)
    assert ab.duration_seconds == 3600


async def test_single_book_rescan_keeps_a_stored_duration(
    db, stub_extract, audio_library, make_user, auth_header
):
    stub_extract()
    ab = await _seed(db, audio_library, duration_seconds=3600)
    editor = await make_user(username="ed", role="editor")

    async with _library_client() as c:
        r = await c.post(f"/api/library/audiobooks/{ab.id}/rescan",
                         headers=auth_header(editor))

    assert r.status_code == 200, r.text
    await db.refresh(ab)
    assert ab.duration_seconds == 3600


# ---------- replacing the file ----------

async def test_replacing_an_audiobook_updates_the_duration(
    db, monkeypatch, audio_library, make_user, auth_header
):
    """Replace keeps the row (and its pairing) but swaps the file underneath.
    Carrying the old file's length forward would be a silently wrong end zone."""
    async def _fake_extract(filepath, file_type, db, library_root=None):
        return {"title": "Dune", "duration_seconds": 7200}

    monkeypatch.setattr(library, "extract_metadata", _fake_extract)
    monkeypatch.setattr(
        troubleshoot, "check_audio_integrity", lambda p: (True, "ok"), raising=False
    )
    ab = await _seed(db, audio_library, duration_seconds=3600)
    editor = await make_user(username="ed", role="editor")

    async with _troubleshoot_client() as c:
        r = await c.post(
            f"/api/troubleshoot/replace/audiobook/{ab.id}",
            headers=auth_header(editor),
            files={"file": ("Dune.mp3", b"new bytes", "audio/mpeg")},
        )

    assert r.status_code == 200, r.text
    await db.refresh(ab)
    assert ab.duration_seconds == 7200


# ---------- the point of the whole issue ----------

async def test_a_scanned_audiobook_can_auto_complete_from_its_own_length(
    db, stub_extract, audio_library, make_client, make_user, auth_header
):
    """End to end: the #56 audio end zone was unreachable on production purely
    because no row had a length. Scan one, then write a position inside the
    120 s tail and the book finishes itself — with the client never sending
    `is_completed`."""
    from routers import auth as auth_router, sync as sync_router

    stub_extract(duration_seconds=3600)
    await library.scan_files_impl(db, [str(audio_library)])
    await db.commit()
    ab = (await db.execute(select(AudioBook))).scalar_one()
    user = await make_user(username="reader")

    async with make_client(auth_router.router, sync_router.router) as c:
        body = {"source": "audiobook", "device_id": "pixel"}
        mid = await c.put(
            f"/api/sync/position/audiobook/{ab.id}", headers=auth_header(user),
            json={**body, "audio_position_ms": 1_800_000,
                  "captured_at": "2026-08-19T10:00:00Z"},
        )
        assert mid.json()["is_completed"] is False

        end = await c.put(
            f"/api/sync/position/audiobook/{ab.id}", headers=auth_header(user),
            json={**body, "audio_position_ms": 3_540_000,
                  "captured_at": "2026-08-19T11:00:00Z"},
        )

    assert end.status_code == 200, end.text
    assert end.json()["is_completed"] is True
