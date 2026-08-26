"""
Converting an unsupported ebook rebuilds the pair's sync map (issue #101).

`_relink_or_cleanup_pairs` re-points a pair at the Calibre-converted EPUB and
used to leave the old `SyncMap` in place. That map was built from the `.mobi` —
a single flattened chapter — so after the re-link the pair's stored coordinates
described a document nobody renders any more. Nothing announced it; positions
just quietly stopped landing on the right page.

Conversion now re-aligns the pair against the artifact it just re-pointed to,
reusing the cached transcript (no re-transcription) and `save_sync_map`, which
bumps the version and re-maps the bookmarks onto the new coordinates
(`docs/position-sync-contract.md` §Re-transcription). When there is no cached
transcript there is nothing to rebuild from, so the pair is put back to
`manual_matched` — visibly needing transcription — rather than left claiming a
map that no longer describes its ebook.
"""

import json

from sqlalchemy import select, update as sa_update

from models.book import BookPair, EBook, PairStatus
from models.bookmark import Bookmark
from models.progress import ProgressType, UserProgress
from models.sync_map import SyncMap, SyncPoint
from models.transcript import AudioTranscript
from services.epub_parser import EpubSentence

from tests.factories import make_book_pair, make_sync_map

# The transcript the pair already has. Two chapters' worth of audio.
TRANSCRIPT = [
    {"text": "chapter one opening line", "start_ms": 0, "end_ms": 3000},
    {"text": "second sentence here", "start_ms": 5000, "end_ms": 8000},
    {"text": "chapter two begins now", "start_ms": 10000, "end_ms": 13000},
    {"text": "later sentence in two", "start_ms": 20000, "end_ms": 23000},
]

# What the *converted EPUB* yields: the same text, on a real two-chapter spine.
EPUB_SENTENCES = [
    EpubSentence(chapter=0, sentence_index=0, text="chapter one opening line"),
    EpubSentence(chapter=0, sentence_index=1, text="second sentence here"),
    EpubSentence(chapter=1, sentence_index=0, text="chapter two begins now"),
    EpubSentence(chapter=1, sentence_index=1, text="later sentence in two"),
]

# The map the .mobi produced: everything crushed into chapter 0.
FLATTENED_POINTS = [
    (0, 0, 0, "chapter one opening line"),
    (0, 1, 5000, "second sentence here"),
    (0, 2, 10000, "chapter two begins now"),
    (0, 3, 20000, "later sentence in two"),
]


async def _mobi_pair(db, tmp_path, *, with_transcript=True, with_map=True):
    """A pair whose ebook is a .mobi on disk. Returns (pair_id, ebook_id).

    Ids rather than instances: the endpoint commits, and reading an expired ORM
    attribute back in the test would lazy-load outside the async context.
    """
    pair = await make_book_pair(db, status=PairStatus.SYNCED)
    mobi = tmp_path / "book.mobi"
    mobi.write_bytes(b"fake mobi bytes")

    ebook = await db.get(EBook, pair.ebook_id)
    ebook.filename = "book.mobi"
    ebook.file_path = str(mobi)
    ebook.format = "mobi"

    if with_transcript:
        db.add(AudioTranscript(
            pair_id=pair.id,
            audiobook_path="/x/a.m4b",
            sentence_count=len(TRANSCRIPT),
            sentences_json=json.dumps(TRANSCRIPT),
        ))
    await db.commit()

    pair_id, ebook_id = pair.id, ebook.id
    if with_map:
        await make_sync_map(db, book_pair_id=pair_id, points=FLATTENED_POINTS)
    return pair_id, ebook_id


def _fake_calibre(tmp_path, monkeypatch):
    """Stand in for `ebook-convert`: writes the .epub sibling and returns it."""
    def _convert(src_path):
        out = tmp_path / "book.epub"
        out.write_bytes(b"fake epub bytes")
        return str(out)

    monkeypatch.setattr("routers.library._convert_to_epub_sync", _convert)


async def _convert(client, ebook_id, user, auth_header):
    return await client.post(
        f"/api/library/unsupported/{ebook_id}/convert?delete_source=true",
        headers=auth_header(user),
    )


class TestConversionRealignsThePair:
    async def test_relinked_pair_gets_a_map_on_the_epub_axis(
        self, db, tmp_path, monkeypatch, make_client, make_user, auth_header
    ):
        from routers import library

        pair_id, ebook_id = await _mobi_pair(db, tmp_path)
        _fake_calibre(tmp_path, monkeypatch)
        monkeypatch.setattr(
            "services.realign.extract_book_sentences", lambda _p: EPUB_SENTENCES
        )
        user = await make_user(username="admin", role="admin")

        async with make_client(library.router) as c:
            r = await _convert(c, ebook_id, user, auth_header)

        assert r.status_code == 200, r.text
        assert r.json()["realigned"] is True
        assert r.json()["realign_error"] is None

        db.expire_all()
        refreshed = await db.get(BookPair, pair_id)
        new_ebook = await db.get(EBook, refreshed.ebook_id)
        assert new_ebook.format == "epub"
        assert new_ebook.file_path.endswith("book.epub")

        sm = (await db.execute(
            select(SyncMap).where(SyncMap.book_pair_id == pair_id)
        )).scalar_one()
        assert sm.version == 2, "the rebuilt map must supersede the .mobi one"
        chapters = set((await db.execute(
            select(SyncPoint.epub_chapter).where(SyncPoint.sync_map_id == sm.id)
        )).scalars().all())
        assert chapters == {0, 1}, "points now describe the EPUB's real spine"
        assert refreshed.status == PairStatus.SYNCED

    async def test_without_a_transcript_the_pair_is_marked_unsynced(
        self, db, tmp_path, monkeypatch, make_client, make_user, auth_header
    ):
        """Nothing to rebuild from — say so instead of keeping a wrong map."""
        from routers import library

        pair_id, ebook_id = await _mobi_pair(db, tmp_path, with_transcript=False)
        _fake_calibre(tmp_path, monkeypatch)
        user = await make_user(username="admin", role="admin")

        async with make_client(library.router) as c:
            r = await _convert(c, ebook_id, user, auth_header)

        assert r.status_code == 200, r.text
        assert r.json()["realigned"] is False
        assert "transcript" in r.json()["realign_error"].lower()

        db.expire_all()
        refreshed = await db.get(BookPair, pair_id)
        assert refreshed.status == PairStatus.MANUAL_MATCHED
        sm = (await db.execute(
            select(SyncMap).where(SyncMap.book_pair_id == pair_id)
        )).scalar_one()
        assert sm.version == 1, "the old map is kept — bookmarks still translate from it"

    async def test_a_failing_realign_does_not_roll_back_the_conversion(
        self, db, tmp_path, monkeypatch, make_client, make_user, auth_header
    ):
        from routers import library

        pair_id, ebook_id = await _mobi_pair(db, tmp_path)
        _fake_calibre(tmp_path, monkeypatch)

        def _explode(_path):
            raise RuntimeError("epub is corrupt")

        monkeypatch.setattr("services.realign.extract_book_sentences", _explode)
        user = await make_user(username="admin", role="admin")

        async with make_client(library.router) as c:
            r = await _convert(c, ebook_id, user, auth_header)

        assert r.status_code == 200, r.text
        assert r.json()["realigned"] is False
        assert "corrupt" in r.json()["realign_error"]

        db.expire_all()
        refreshed = await db.get(BookPair, pair_id)
        new_ebook = await db.get(EBook, refreshed.ebook_id)
        assert new_ebook.format == "epub", "the conversion itself still stands"

    async def test_an_unreadable_epub_is_reported_as_a_realign_failure(
        self, db, tmp_path, monkeypatch, make_client, make_user, auth_header
    ):
        """The typed failure path: the parser can't open the converted file."""
        from routers import library

        pair_id, ebook_id = await _mobi_pair(db, tmp_path)
        _fake_calibre(tmp_path, monkeypatch)

        def _missing(path):
            raise FileNotFoundError(path)

        monkeypatch.setattr("services.realign.extract_book_sentences", _missing)
        user = await make_user(username="admin", role="admin")

        async with make_client(library.router) as c:
            r = await _convert(c, ebook_id, user, auth_header)

        assert r.status_code == 200, r.text
        assert r.json()["realigned"] is False
        assert "Could not read ebook file" in r.json()["realign_error"]

        db.expire_all()
        sm = (await db.execute(
            select(SyncMap).where(SyncMap.book_pair_id == pair_id)
        )).scalar_one()
        assert sm.version == 1, "a failed rebuild leaves the old map in place"

    async def test_cleanup_without_replacement_keeps_the_audiobook_position(
        self, db, tmp_path, make_client, make_user, auth_header
    ):
        """Force-deleting an unsupported ebook dissolves its pairs with no EPUB
        replacement (`_relink_or_cleanup_pairs(eb_id, None, db)`). The pair dies,
        but each user's position must be demoted onto the surviving audiobook —
        not cascaded away with the pair (issue #155)."""
        from routers import library

        pair_id, ebook_id = await _mobi_pair(
            db, tmp_path, with_transcript=False, with_map=False
        )
        pair = await db.get(BookPair, pair_id)
        audiobook_id = pair.audiobook_id

        reader = await make_user(username="reader")
        db.add(Bookmark(
            user_id=reader.id, book_pair_id=pair_id,
            epub_chapter=2, epub_progress_percent=33.0, audio_position_ms=90000,
        ))
        await db.commit()

        admin = await make_user(username="admin", role="admin")
        async with make_client(library.router) as c:
            r = await c.delete(
                f"/api/library/unsupported/{ebook_id}/force",
                headers=auth_header(admin),
            )
        assert r.status_code == 200, r.text

        db.expire_all()
        row = (await db.execute(
            select(Bookmark).where(Bookmark.user_id == reader.id)
        )).scalar_one()
        assert row.book_pair_id is None
        assert row.ebook_id is None
        assert row.audiobook_id == audiobook_id
        assert row.audio_position_ms == 90000

    async def test_convert_all_reports_realign_failures_without_aborting(
        self, db, tmp_path, monkeypatch, make_client, make_user, auth_header
    ):
        from routers import library

        pair_id, ebook_id = await _mobi_pair(db, tmp_path, with_transcript=False)
        _fake_calibre(tmp_path, monkeypatch)
        user = await make_user(username="admin", role="admin")

        async with make_client(library.router) as c:
            r = await c.post(
                "/api/library/unsupported/convert-all?delete_source=true",
                headers=auth_header(user),
            )

        assert r.status_code == 200, r.text
        body = r.json()
        assert body["succeeded"] == ["book.mobi"]
        assert body["realign_failures"] == [
            {"pair_id": pair_id, "error": body["realign_failures"][0]["error"]}
        ]
        assert "transcript" in body["realign_failures"][0]["error"].lower()


# ---------------------------------------------------------------------------
# Standalone positions on the source ebook (issue #298)
# ---------------------------------------------------------------------------

# A standalone (`ebook`-scoped) position on the .mobi the user is converting.
STANDALONE_POSITION = {
    "source": "ebook",
    "epub_chapter": 4,
    "epub_sentence_index": 9,
    "epub_text_preview": "second sentence here",
    "epub_progress_percent": 41.5,
    "captured_at": "2026-08-01T12:00:00Z",
    "device_id": "web-1",
    "device_name": "Firefox on desk",
    "hint": {
        "kind": "epubjs_cfi",
        "value": "epubcfi(/6/8!/4/2)",
        "audio_position_ms": None,
    },
}


async def _put_position(client, user, auth_header, scope, ident, body):
    r = await client.put(
        f"/api/sync/position/{scope}/{ident}", json=body, headers=auth_header(user)
    )
    assert r.status_code == 200, r.text
    return r


async def _stamp_map_version(db, user_id, ebook_id, version=1):
    """Pin a sync-map version on a standalone row.

    A standalone-scoped PUT never stamps one (only a pair write attests a
    version), but a demoted row can carry coordinates it inherited from a pair,
    and those are exactly what the conversion has to invalidate.
    """
    await db.execute(
        sa_update(Bookmark)
        .where(Bookmark.user_id == user_id,
               Bookmark.book_pair_id.is_(None),
               Bookmark.ebook_id == ebook_id)
        .values(sync_map_version=version)
    )
    await db.commit()


async def _preregister_epub(db, tmp_path):
    """Register the .epub sibling up front and return its id.

    `_register_epub_in_db` reuses an existing row for the same `file_path`, so
    this is the row the conversion will re-point at — which lets a test put a
    *competing* position on it before converting.
    """
    out = tmp_path / "book.epub"
    out.write_bytes(b"fake epub bytes")
    eb = EBook(title="E", filename="book.epub", file_path=str(out), format="epub")
    db.add(eb)
    await db.commit()
    await db.refresh(eb)
    return eb.id


async def _standalone_rows(db, user_id):
    return (await db.execute(
        select(Bookmark).where(
            Bookmark.user_id == user_id, Bookmark.book_pair_id.is_(None)
        )
    )).scalars().all()


class TestRelinkRepointsStandalonePositions:
    """Converting an ebook re-points positions on it, it does not release them.

    The relink branch used to hand the source ebook's standalone rows to
    `release_standalone_positions` — the id was dropped and, with no audiobook
    side to keep, the row went with it. But unlike a deletion there *is* a
    successor here: the converted EPUB is the same book. The row now moves onto
    it, keeping the anchors that still describe the text (chapter, percent,
    preview) and dropping only the sync-map coordinates the old parse produced.
    """

    async def test_a_standalone_position_moves_onto_the_converted_epub(
        self, db, tmp_path, monkeypatch, make_client, make_user, auth_header
    ):
        from routers import library, sync

        pair_id, ebook_id = await _mobi_pair(
            db, tmp_path, with_transcript=False, with_map=False)
        _fake_calibre(tmp_path, monkeypatch)
        reader = await make_user(username="reader")
        admin = await make_user(username="admin", role="admin")

        async with make_client(library.router, sync.router) as c:
            await _put_position(
                c, reader, auth_header, "ebook", ebook_id, STANDALONE_POSITION)
            await _stamp_map_version(db, reader.id, ebook_id)

            r = await _convert(c, ebook_id, admin, auth_header)
            assert r.status_code == 200, r.text
            new_ebook_id = r.json()["epub_ebook_id"]
            assert new_ebook_id != ebook_id

            r = await c.get(
                f"/api/sync/position/ebook/{new_ebook_id}",
                headers=auth_header(reader),
            )
            assert r.status_code == 200, "the position must survive the conversion"
            body = r.json()

        # The anchors that still describe the text are kept: a chapter/percent
        # anchor is roughly right after a conversion, and the reader's restore
        # ladder does the fine landing from the preview.
        assert body["epub_chapter"] == 4
        assert body["epub_progress_percent"] == 41.5
        assert body["epub_text_preview"] == "second sentence here"
        assert body["device_id"] == "web-1"
        assert body["device_name"] == "Firefox on desk"
        assert body["captured_at"].startswith("2026-08-01T12:00:00"), \
            "a server-side re-point is not a device capture"

        db.expire_all()
        rows = await _standalone_rows(db, reader.id)
        assert len(rows) == 1, "the position moved; it was not copied"
        assert rows[0].ebook_id == new_ebook_id

    async def test_the_map_coordinates_are_dropped_and_the_hints_go_stale(
        self, db, tmp_path, monkeypatch, make_client, make_user, auth_header
    ):
        """The sentence index and its version name text in the *old* parse.

        The hints are marked stale rather than deleted, per the contract: a
        CFI from the .mobi's DOM cannot address the converted EPUB, but a
        deleted hint reads to a client as "no position at all".
        """
        from routers import library, sync

        pair_id, ebook_id = await _mobi_pair(
            db, tmp_path, with_transcript=False, with_map=False)
        _fake_calibre(tmp_path, monkeypatch)
        reader = await make_user(username="reader")
        admin = await make_user(username="admin", role="admin")

        async with make_client(library.router, sync.router) as c:
            await _put_position(
                c, reader, auth_header, "ebook", ebook_id, STANDALONE_POSITION)
            await _stamp_map_version(db, reader.id, ebook_id)

            r = await _convert(c, ebook_id, admin, auth_header)
            new_ebook_id = r.json()["epub_ebook_id"]

            r = await c.get(
                f"/api/sync/position/ebook/{new_ebook_id}",
                headers=auth_header(reader),
            )
            assert r.status_code == 200, r.text
            body = r.json()

        assert body["epub_sentence_index"] is None
        assert body["sync_map_version"] is None
        assert [h["value"] for h in body["hints"]] == ["epubcfi(/6/8!/4/2)"], \
            "hints are never deleted, only marked stale"
        assert body["hints"][0]["current"] is False

    async def test_a_newer_position_on_the_converted_epub_wins(
        self, db, tmp_path, monkeypatch, make_client, make_user, auth_header
    ):
        """Same collision rule as `demote_pair_positions`: newer `captured_at`
        wins the (user, ebook) scope and the loser is deleted."""
        from routers import library, sync

        pair_id, ebook_id = await _mobi_pair(
            db, tmp_path, with_transcript=False, with_map=False)
        epub_id = await _preregister_epub(db, tmp_path)
        _fake_calibre(tmp_path, monkeypatch)
        reader = await make_user(username="reader")
        admin = await make_user(username="admin", role="admin")

        async with make_client(library.router, sync.router) as c:
            await _put_position(
                c, reader, auth_header, "ebook", ebook_id, STANDALONE_POSITION)
            await _put_position(
                c, reader, auth_header, "ebook", epub_id,
                {"source": "ebook", "epub_chapter": 9,
                 "epub_progress_percent": 90.0,
                 "captured_at": "2026-08-02T12:00:00Z"},  # newer
            )

            r = await _convert(c, ebook_id, admin, auth_header)
            assert r.json()["epub_ebook_id"] == epub_id

            r = await c.get(
                f"/api/sync/position/ebook/{epub_id}", headers=auth_header(reader)
            )
            assert r.status_code == 200
            assert r.json()["epub_chapter"] == 9
            assert r.json()["captured_at"].startswith("2026-08-02T12:00:00")

        db.expire_all()
        rows = await _standalone_rows(db, reader.id)
        assert len(rows) == 1, "the older re-pointed row lost and was deleted"

    async def test_the_repointed_row_replaces_an_older_row_on_the_epub(
        self, db, tmp_path, monkeypatch, make_client, make_user, auth_header
    ):
        from routers import library, sync

        pair_id, ebook_id = await _mobi_pair(
            db, tmp_path, with_transcript=False, with_map=False)
        epub_id = await _preregister_epub(db, tmp_path)
        _fake_calibre(tmp_path, monkeypatch)
        reader = await make_user(username="reader")
        admin = await make_user(username="admin", role="admin")

        async with make_client(library.router, sync.router) as c:
            await _put_position(
                c, reader, auth_header, "ebook", epub_id,
                {"source": "ebook", "epub_chapter": 1,
                 "captured_at": "2026-07-01T12:00:00Z"},  # older
            )
            await _put_position(
                c, reader, auth_header, "ebook", ebook_id, STANDALONE_POSITION)

            r = await _convert(c, ebook_id, admin, auth_header)
            assert r.json()["epub_ebook_id"] == epub_id

            r = await c.get(
                f"/api/sync/position/ebook/{epub_id}", headers=auth_header(reader)
            )
            assert r.status_code == 200
            assert r.json()["epub_chapter"] == 4
            assert r.json()["captured_at"].startswith("2026-08-01T12:00:00")

        db.expire_all()
        rows = await _standalone_rows(db, reader.id)
        assert len(rows) == 1, "the older row was replaced, not kept alongside"

    async def test_two_users_each_keep_their_own_repointed_position(
        self, db, tmp_path, monkeypatch, make_client, make_user, auth_header
    ):
        from routers import library, sync

        pair_id, ebook_id = await _mobi_pair(
            db, tmp_path, with_transcript=False, with_map=False)
        _fake_calibre(tmp_path, monkeypatch)
        alice = await make_user(username="alice")
        bob = await make_user(username="bob")
        admin = await make_user(username="admin", role="admin")

        async with make_client(library.router, sync.router) as c:
            await _put_position(
                c, alice, auth_header, "ebook", ebook_id,
                dict(STANDALONE_POSITION, epub_chapter=2,
                     epub_progress_percent=20.0))
            await _put_position(
                c, bob, auth_header, "ebook", ebook_id,
                dict(STANDALONE_POSITION, epub_chapter=7,
                     epub_progress_percent=70.0))

            r = await _convert(c, ebook_id, admin, auth_header)
            new_ebook_id = r.json()["epub_ebook_id"]

            for user, chapter, percent in ((alice, 2, 20.0), (bob, 7, 70.0)):
                r = await c.get(
                    f"/api/sync/position/ebook/{new_ebook_id}",
                    headers=auth_header(user),
                )
                assert r.status_code == 200, f"{user.username} lost their position"
                assert r.json()["epub_chapter"] == chapter
                assert r.json()["epub_progress_percent"] == percent

    async def test_the_progress_projection_follows_the_repointed_position(
        self, db, tmp_path, monkeypatch, make_client, make_user, auth_header
    ):
        """`user_progress` is the projection, so it moves with the record.

        It used to be deleted outright for the source ebook — which took the
        book off the Continue list even when the pair itself was re-linked and
        kept reading fine.
        """
        from routers import library, sync

        pair_id, ebook_id = await _mobi_pair(
            db, tmp_path, with_transcript=False, with_map=False)
        _fake_calibre(tmp_path, monkeypatch)
        reader = await make_user(username="reader")
        admin = await make_user(username="admin", role="admin")

        async with make_client(library.router, sync.router) as c:
            await _put_position(
                c, reader, auth_header, "ebook", ebook_id, STANDALONE_POSITION)
            r = await _convert(c, ebook_id, admin, auth_header)
            new_ebook_id = r.json()["epub_ebook_id"]

        db.expire_all()
        rows = (await db.execute(
            select(UserProgress).where(
                UserProgress.user_id == reader.id,
                UserProgress.media_type == ProgressType.EBOOK,
            )
        )).scalars().all()
        assert len(rows) == 1
        assert rows[0].ebook_id == new_ebook_id
        assert rows[0].epub_chapter == 4
        assert rows[0].epub_progress_percent == 41.5

    async def test_a_repointed_row_keeps_the_audiobook_it_also_carries(
        self, db, tmp_path, monkeypatch, make_client, make_user, auth_header
    ):
        """A demoted row references both media at once; re-pointing the ebook
        side must not disturb the audio position it also holds."""
        from routers import library, sync

        pair_id, ebook_id = await _mobi_pair(
            db, tmp_path, with_transcript=False, with_map=False)
        pair = await db.get(BookPair, pair_id)
        audiobook_id = pair.audiobook_id
        _fake_calibre(tmp_path, monkeypatch)
        reader = await make_user(username="reader")
        admin = await make_user(username="admin", role="admin")

        db.add(Bookmark(
            user_id=reader.id, ebook_id=ebook_id, audiobook_id=audiobook_id,
            epub_chapter=4, epub_progress_percent=41.5, audio_position_ms=90000,
        ))
        await db.commit()

        async with make_client(library.router, sync.router) as c:
            r = await _convert(c, ebook_id, admin, auth_header)
            new_ebook_id = r.json()["epub_ebook_id"]

        db.expire_all()
        row = (await db.execute(
            select(Bookmark).where(Bookmark.user_id == reader.id)
        )).scalar_one()
        assert row.ebook_id == new_ebook_id
        assert row.audiobook_id == audiobook_id
        assert row.audio_position_ms == 90000

    async def test_repointing_an_ebook_at_itself_is_a_noop(self, db):
        """Defensive guard: re-registering the same file must not stale a
        position's hints for a move that never happened."""
        from services.position_service import repoint_standalone_positions_to_ebook

        await repoint_standalone_positions_to_ebook(
            db, old_ebook_id=999999, new_ebook_id=999999)  # must not raise

    async def test_without_a_replacement_the_standalone_position_is_released(
        self, db, tmp_path, make_client, make_user, auth_header
    ):
        """Regression guard on the no-replacement branch (issue #155).

        Force-delete has no successor ebook to point at, so a row that
        describes only the dying ebook still goes — re-pointing is strictly
        the relink branch's behaviour.
        """
        from routers import library, sync

        pair_id, ebook_id = await _mobi_pair(
            db, tmp_path, with_transcript=False, with_map=False)
        reader = await make_user(username="reader")
        admin = await make_user(username="admin", role="admin")

        async with make_client(library.router, sync.router) as c:
            await _put_position(
                c, reader, auth_header, "ebook", ebook_id, STANDALONE_POSITION)
            r = await c.delete(
                f"/api/library/unsupported/{ebook_id}/force",
                headers=auth_header(admin),
            )
            assert r.status_code == 200, r.text

        db.expire_all()
        assert await _standalone_rows(db, reader.id) == []
