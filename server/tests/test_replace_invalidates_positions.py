"""
Replacing an ebook's file must invalidate the *parse* coordinates of every
position that references it (issue #303).

`POST /api/troubleshoot/replace/ebook/{id}` swaps the file behind an existing
`EBook` row and keeps the row id, so every bookmark still points at it. The new
file is a different parse: `epub_sentence_index` is a coordinate of the old
parse and the old sync map, and a device's Readium locator / epub.js CFI
addresses the DOM of the file that is now gone. Nothing invalidated them, so a
reader restored to a hint or a sentence index that named different text.

What the fix keeps, and why: chapter, percent and text preview are roughly
right across a re-parse and the restore ladder does the fine landing from the
preview. What it clears: `epub_sentence_index` and `sync_map_version`. Hints
are marked stale by an `anchor_revision` bump and are **never deleted** — the
contract's rule; a client that finds no hint reads it as "no position" and
writes chapter 0 over a real one.
"""

import contextlib

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from models.book import AudioBook, BookPair, EBook, PairStatus
from models.bookmark import Bookmark, BookmarkSource, PositionHint
from models.sync_map import SyncMap
from models.transcript import AudioTranscript
from models.transcription_queue import TranscriptionQueueItem
from routers import sync, troubleshoot
import routers.library as library
from services.alignment import AlignedPoint
from services.position_service import invalidate_parse_coordinates_for_ebook
from services.sync_engine import save_sync_map
from utils import utcnow

from tests.factories import make_book_pair, make_ebook, make_sync_map

# A map before and after a re-transcription: the same sentences, with two new
# ones ahead of them, so every index in chapter 0 shifts by two.
OLD_MAP = [
    ("the quick brown fox jumps over the lazy dog", 0, 0, 0),
    ("pack my box with five dozen liquor jugs", 0, 1, 5_000),
    ("how vexingly quick daft zebras jump", 1, 0, 10_000),
]
NEW_MAP = [
    ("a wizard job is to vex chumps quickly in fog", 0, 0, 0),
    ("jackdaws love my big sphinx of quartz", 0, 1, 1_500),
    ("the quick brown fox jumps over the lazy dog", 0, 2, 3_000),
    ("pack my box with five dozen liquor jugs", 0, 3, 6_500),
    ("how vexingly quick daft zebras jump", 1, 0, 11_000),
]


def _aligned(points):
    return [
        AlignedPoint(
            epub_chapter=ch, epub_sentence_index=si, epub_text_preview=text,
            audio_start_ms=ms, audio_end_ms=ms + 1_000, confidence=1.0,
        )
        for text, ch, si, ms in points
    ]

PAIR_POSITION = {
    "source": "ebook",
    "epub_chapter": 1,
    "epub_sentence_index": 2,
    "sync_map_version": 1,
    "epub_text_preview": "later sentence in two",
    "epub_progress_percent": 41.5,
    "audio_position_ms": 123456,
    "captured_at": "2026-08-01T12:00:00Z",
    "device_id": "web-1",
    "device_name": "Firefox on desk",
    "hint": {
        "kind": "epubjs_cfi",
        "value": "epubcfi(/6/8!/4/2)",
        "audio_position_ms": 123456,
    },
}

STANDALONE_POSITION = {
    "source": "ebook",
    "epub_chapter": 4,
    "epub_sentence_index": 7,
    "epub_text_preview": "a line from the old parse",
    "epub_progress_percent": 62.0,
    "captured_at": "2026-08-02T09:00:00Z",
    "device_id": "phone-1",
    "device_name": "Pixel",
    "hint": {
        "kind": "readium_locator",
        "value": '{"href":"/ch4.xhtml"}',
    },
}


@contextlib.asynccontextmanager
async def _client():
    app = FastAPI()
    app.include_router(troubleshoot.router)
    app.include_router(sync.router)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
def library_dirs(monkeypatch, tmp_path):
    """Point the library at a tmp dir and stub out metadata extraction.

    `replace_file` imports `extract_metadata` from `routers.library` at call
    time, so patching the module attribute is enough — and it keeps the test
    off ebooklib, which cannot parse the byte blobs used as stand-in files.
    """
    from config import settings

    ebook_dir = tmp_path / "ebooks"
    audiobook_dir = tmp_path / "audiobooks"
    ebook_dir.mkdir()
    audiobook_dir.mkdir()
    monkeypatch.setattr(settings, "ebook_dir", str(ebook_dir))
    monkeypatch.setattr(settings, "audiobook_dir", str(audiobook_dir))

    async def _fake_extract(filepath, file_type, db, library_root=None):
        return {}

    monkeypatch.setattr(library, "extract_metadata", _fake_extract)
    return ebook_dir, audiobook_dir


async def _put(client, user, auth_header, scope, ident, body):
    r = await client.put(
        f"/api/sync/position/{scope}/{ident}", json=body, headers=auth_header(user)
    )
    assert r.status_code == 200, r.text
    return r.json()


async def _replace(client, editor, auth_header, item_type, item_id, name, content):
    r = await client.post(
        f"/api/troubleshoot/replace/{item_type}/{item_id}",
        headers=auth_header(editor),
        files={"file": (name, content, "application/octet-stream")},
    )
    assert r.status_code == 200, r.text
    return r.json()


async def _bookmark(db, **filters):
    q = select(Bookmark)
    for col, val in filters.items():
        attr = getattr(Bookmark, col)
        q = q.where(attr.is_(None) if val is None else attr == val)
    return (await db.execute(q.execution_options(populate_existing=True))).scalar_one()


async def _prepare_file_backed_ebook(db, ebook_id, ebook_dir):
    """Give an EBook row a real file on disk, the way the scanner would."""
    path = ebook_dir / f"book-{ebook_id}.epub"
    path.write_bytes(b"the old parse")
    eb = await db.get(EBook, ebook_id)
    eb.file_path = str(path)
    eb.filename = path.name
    eb.format = "epub"
    await db.commit()
    return path


class TestReplaceInvalidatesParseCoordinates:
    async def test_pair_and_standalone_positions_are_invalidated(
        self, db, make_user, auth_header, library_dirs
    ):
        """Both scopes lose their parse coordinates, keep their portable
        anchors, and have every hint marked stale without losing one."""
        ebook_dir, _ = library_dirs
        pair = await make_book_pair(db)
        pair_id, ebook_id = pair.id, pair.ebook_id
        await make_sync_map(db, book_pair_id=pair_id)
        await _prepare_file_backed_ebook(db, ebook_id, ebook_dir)

        reader = await make_user(username="reader")
        other = await make_user(username="other")
        editor = await make_user(username="editor", role="editor")

        async with _client() as c:
            await _put(c, reader, auth_header, "pair", pair_id, PAIR_POSITION)
            await _put(c, other, auth_header, "ebook", ebook_id, STANDALONE_POSITION)

            # A standalone row only ever gets a `sync_map_version` from the
            # server, never from the wire — set it directly so the clearing is
            # actually exercised on this scope too.
            standalone = await _bookmark(db, user_id=other.id, ebook_id=ebook_id)
            standalone.sync_map_version = 1
            await db.commit()

            before_pair = await _bookmark(db, user_id=reader.id, book_pair_id=pair_id)
            before = {
                "pair_revision": before_pair.anchor_revision,
                "pair_captured": before_pair.captured_at,
                "standalone_revision": standalone.anchor_revision,
                "standalone_captured": standalone.captured_at,
            }

            await _replace(c, editor, auth_header, "ebook", ebook_id,
                           "new.epub", b"a different parse entirely")

        pair_row = await _bookmark(db, user_id=reader.id, book_pair_id=pair_id)
        assert pair_row.epub_sentence_index is None
        assert pair_row.sync_map_version is None
        assert pair_row.epub_chapter == 1
        assert pair_row.epub_progress_percent == 41.5
        assert pair_row.epub_text_preview == "later sentence in two"
        assert pair_row.audio_position_ms == 123456
        assert pair_row.anchor_revision == before["pair_revision"] + 1
        assert pair_row.captured_at == before["pair_captured"]

        standalone_row = await _bookmark(db, user_id=other.id, ebook_id=ebook_id)
        assert standalone_row.epub_sentence_index is None
        assert standalone_row.sync_map_version is None
        assert standalone_row.epub_chapter == 4
        assert standalone_row.epub_progress_percent == 62.0
        assert standalone_row.epub_text_preview == "a line from the old parse"
        assert standalone_row.anchor_revision == before["standalone_revision"] + 1
        assert standalone_row.captured_at == before["standalone_captured"]

    async def test_hints_survive_and_are_stale_not_deleted(
        self, db, make_user, auth_header, library_dirs
    ):
        """The contract's rule: a hint goes stale, it never disappears."""
        ebook_dir, _ = library_dirs
        pair = await make_book_pair(db)
        pair_id, ebook_id = pair.id, pair.ebook_id
        await _prepare_file_backed_ebook(db, ebook_id, ebook_dir)

        reader = await make_user(username="reader")
        editor = await make_user(username="editor", role="editor")

        async with _client() as c:
            await _put(c, reader, auth_header, "pair", pair_id, PAIR_POSITION)
            await _replace(c, editor, auth_header, "ebook", ebook_id,
                           "new.epub", b"a different parse entirely")

            r = await c.get(
                f"/api/sync/position/pair/{pair_id}", headers=auth_header(reader)
            )
            assert r.status_code == 200, r.text
            body = r.json()

        hints = (await db.execute(
            select(PositionHint).execution_options(populate_existing=True)
        )).scalars().all()
        assert len(hints) == 1
        assert hints[0].hint_value == "epubcfi(/6/8!/4/2)"

        assert len(body["hints"]) == 1
        assert body["hints"][0]["value"] == "epubcfi(/6/8!/4/2)"
        assert body["hints"][0]["current"] is False

    async def test_positions_on_other_books_are_untouched(
        self, db, make_user, auth_header, library_dirs
    ):
        """Only the replaced ebook's positions move."""
        ebook_dir, _ = library_dirs
        pair = await make_book_pair(db)
        pair_id, ebook_id = pair.id, pair.ebook_id
        await _prepare_file_backed_ebook(db, ebook_id, ebook_dir)
        unrelated = await make_ebook(db, title="Other", filename="other.epub")

        reader = await make_user(username="reader")
        editor = await make_user(username="editor", role="editor")

        async with _client() as c:
            await _put(c, reader, auth_header, "ebook", unrelated.id,
                       STANDALONE_POSITION)
            await _put(c, reader, auth_header, "pair", pair_id, PAIR_POSITION)

            untouched = await _bookmark(db, user_id=reader.id, ebook_id=unrelated.id)
            untouched.sync_map_version = 9
            await db.commit()
            before_revision = untouched.anchor_revision

            await _replace(c, editor, auth_header, "ebook", ebook_id,
                           "new.epub", b"a different parse entirely")

        row = await _bookmark(db, user_id=reader.id, ebook_id=unrelated.id)
        assert row.epub_sentence_index == 7
        assert row.sync_map_version == 9
        assert row.anchor_revision == before_revision

    async def test_replacing_an_audiobook_leaves_epub_coordinates_alone(
        self, db, make_user, auth_header, library_dirs
    ):
        """The audiobook branch is deliberately exempt: the ebook parse and the
        live map are both unchanged, so the epub coordinates still mean what
        they said. What a new audio file invalidates is `audio_position_ms`,
        which re-transcription's re-map re-derives."""
        _, audiobook_dir = library_dirs
        pair = await make_book_pair(db)
        pair_id, audiobook_id = pair.id, pair.audiobook_id
        ab = await db.get(AudioBook, audiobook_id)
        ab_path = audiobook_dir / "book.m4b"
        ab_path.write_bytes(b"old audio")
        ab.file_path = str(ab_path)
        await db.commit()

        reader = await make_user(username="reader")
        editor = await make_user(username="editor", role="editor")

        async with _client() as c:
            await _put(c, reader, auth_header, "pair", pair_id, PAIR_POSITION)
            before = await _bookmark(db, user_id=reader.id, book_pair_id=pair_id)
            before_revision = before.anchor_revision

            await _replace(c, editor, auth_header, "audiobook", audiobook_id,
                           "new.m4b", b"a different recording")

        row = await _bookmark(db, user_id=reader.id, book_pair_id=pair_id)
        assert row.epub_sentence_index == 2
        assert row.sync_map_version == 1
        assert row.anchor_revision == before_revision


class TestReplaceRealignmentBehaviourUnchanged:
    """Pins what a replacement does about re-transcription today, so the
    invalidation and any future re-map cannot start fighting silently."""

    async def test_replacing_an_ebook_queues_nothing_and_keeps_the_map(
        self, db, make_user, auth_header, library_dirs
    ):
        """An ebook replacement does **not** re-align: the transcript, the pair
        status and the sync map (with its version) all survive, and nothing is
        queued. The invalidation is therefore the whole story until a user
        re-queues the pair — and that later re-map is a clean upgrade, since it
        re-derives coordinates from the surviving text preview and never reads
        the cleared sentence index."""
        ebook_dir, _ = library_dirs
        pair = await make_book_pair(db, status=PairStatus.SYNCED)
        pair_id, ebook_id = pair.id, pair.ebook_id
        sync_map = await make_sync_map(db, book_pair_id=pair_id)
        map_version = sync_map.version
        db.add(AudioTranscript(pair_id=pair_id, audiobook_path="/x/a.m4b",
                               sentence_count=1, sentences_json="[]"))
        await db.commit()
        await _prepare_file_backed_ebook(db, ebook_id, ebook_dir)

        editor = await make_user(username="editor", role="editor")
        async with _client() as c:
            await _replace(c, editor, auth_header, "ebook", ebook_id,
                           "new.epub", b"a different parse entirely")

        fresh_pair = (await db.execute(
            select(BookPair).where(BookPair.id == pair_id)
            .execution_options(populate_existing=True)
        )).scalar_one()
        assert fresh_pair.status == PairStatus.SYNCED
        assert (await db.execute(
            select(AudioTranscript).where(AudioTranscript.pair_id == pair_id)
        )).scalar_one_or_none() is not None
        live_map = (await db.execute(
            select(SyncMap).where(SyncMap.book_pair_id == pair_id)
        )).scalar_one()
        assert live_map.version == map_version
        assert (await db.execute(select(TranscriptionQueueItem))).scalars().all() == []

    async def test_replacing_an_audiobook_still_drops_the_transcript(
        self, db, make_user, auth_header, library_dirs
    ):
        """Unchanged behaviour: the cached transcript dies with the old audio
        file and a synced pair drops back to `manual_matched`."""
        _, audiobook_dir = library_dirs
        pair = await make_book_pair(db, status=PairStatus.SYNCED)
        pair_id, audiobook_id = pair.id, pair.audiobook_id
        db.add(AudioTranscript(pair_id=pair_id, audiobook_path="/x/a.m4b",
                               sentence_count=1, sentences_json="[]"))
        ab = await db.get(AudioBook, audiobook_id)
        ab_path = audiobook_dir / "book.m4b"
        ab_path.write_bytes(b"old audio")
        ab.file_path = str(ab_path)
        await db.commit()

        editor = await make_user(username="editor", role="editor")
        async with _client() as c:
            await _replace(c, editor, auth_header, "audiobook", audiobook_id,
                           "new.m4b", b"a different recording")

        fresh_pair = (await db.execute(
            select(BookPair).where(BookPair.id == pair_id)
            .execution_options(populate_existing=True)
        )).scalar_one()
        assert fresh_pair.status == PairStatus.MANUAL_MATCHED
        assert (await db.execute(
            select(AudioTranscript).where(AudioTranscript.pair_id == pair_id)
        )).scalar_one_or_none() is None


class TestRemapAfterInvalidationIsAnUpgrade:
    """The invalidation and a later re-map must not fight.

    An ebook replacement queues no re-alignment, so a re-map only happens once
    a user re-queues the pair — strictly after the clearing. When it runs,
    `remap_bookmarks_for_pair` re-derives coordinates from the bookmark's own
    text preview and the *new* map's points; it never reads the cleared
    sentence index back, so the row is upgraded onto the new map, never
    resurrected onto the old one.
    """

    async def test_remap_re_expresses_a_cleared_row_from_its_preview(self, db):
        pair = await make_book_pair(db)
        await save_sync_map(db, pair.id, _aligned(OLD_MAP))
        await db.commit()

        bookmark = Bookmark(
            user_id=1, book_pair_id=pair.id, source=BookmarkSource.EBOOK,
            epub_chapter=0, epub_sentence_index=1,
            epub_text_preview="pack my box with five dozen liquor jugs",
            epub_progress_percent=42.0, audio_position_ms=5_000,
            anchor_revision=3, sync_map_version=1, captured_at=utcnow(),
        )
        db.add(bookmark)
        await db.commit()

        await invalidate_parse_coordinates_for_ebook(db, pair.ebook_id)
        await db.commit()
        assert bookmark.epub_sentence_index is None
        assert bookmark.sync_map_version is None
        assert bookmark.anchor_revision == 4

        new_map = await save_sync_map(db, pair.id, _aligned(NEW_MAP))
        await db.commit()
        await db.refresh(bookmark)

        # The same sentence, in the new map's coordinates — not the 1 that was
        # cleared, and not a NULL left behind.
        assert bookmark.epub_sentence_index == 3
        assert bookmark.sync_map_version == new_map.version == 2
        assert bookmark.epub_chapter == 0
        # The chapter didn't move, so the re-map leaves the (already stale)
        # hints where the invalidation put them.
        assert bookmark.anchor_revision == 4

    async def test_remap_of_a_preview_less_cleared_row_never_restores_the_index(
        self, db
    ):
        """Worst case: nothing but a chapter survives. The re-map falls back to
        the outgoing map's text at the *start* of that chapter, so the row
        degrades to the top of the chapter it already named — it does not come
        back holding a coordinate of a parse that no longer exists."""
        pair = await make_book_pair(db)
        await save_sync_map(db, pair.id, _aligned(OLD_MAP))
        await db.commit()

        bookmark = Bookmark(
            user_id=1, book_pair_id=pair.id, source=BookmarkSource.EBOOK,
            epub_chapter=0, epub_sentence_index=1, epub_text_preview=None,
            anchor_revision=3, sync_map_version=1, captured_at=utcnow(),
        )
        db.add(bookmark)
        await db.commit()

        await invalidate_parse_coordinates_for_ebook(db, pair.ebook_id)
        await db.commit()

        await save_sync_map(db, pair.id, _aligned(NEW_MAP))
        await db.commit()
        await db.refresh(bookmark)

        assert bookmark.epub_chapter == 0
        assert bookmark.epub_sentence_index == 2  # chapter 0's first sentence
        assert bookmark.sync_map_version == 2


class TestRescanIsExempt:
    async def test_metadata_rescan_keeps_parse_coordinates(
        self, db, make_user, auth_header, library_dirs, make_client
    ):
        """`POST /api/library/{type}s/{id}/rescan` re-reads metadata from the
        *same* file — the parse is unchanged, so the coordinates still hold and
        invalidating them would drop every reader to a text search for nothing.
        """
        ebook_dir, _ = library_dirs
        pair = await make_book_pair(db)
        pair_id, ebook_id = pair.id, pair.ebook_id
        await _prepare_file_backed_ebook(db, ebook_id, ebook_dir)

        reader = await make_user(username="reader")
        editor = await make_user(username="editor", role="editor")

        async with make_client(library.router, sync.router) as c:
            await _put(c, reader, auth_header, "pair", pair_id, PAIR_POSITION)
            before = await _bookmark(db, user_id=reader.id, book_pair_id=pair_id)
            before_revision = before.anchor_revision

            r = await c.post(f"/api/library/ebooks/{ebook_id}/rescan",
                             headers=auth_header(editor))
            assert r.status_code == 200, r.text

        row = await _bookmark(db, user_id=reader.id, book_pair_id=pair_id)
        assert row.epub_sentence_index == 2
        assert row.sync_map_version == 1
        assert row.anchor_revision == before_revision
