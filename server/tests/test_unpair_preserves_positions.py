"""
Unpairing (or deleting one half of a pair) must not destroy reading positions
(issue #155).

`DELETE /api/library/pairs/{id}` used to delete the pair row and let the ORM
cascade take every user's pair-scoped `Bookmark` — the canonical position
record — with its hints, logs, and the `user_progress` projection. Unpairing is
the *normal* way to correct an auto-match, so fixing a mis-pair wiped everyone's
position in both books.

Now the pair-scoped bookmark is **demoted** to the standalone media scopes
(`book_pair_id = NULL`, `ebook_id`/`audiobook_id` set to the surviving media)
before the pair is deleted, in the same transaction. Deleting an ebook or
audiobook demotes onto the *other* medium only. A pre-existing standalone row
for the same (user, medium) collides on the partial unique indexes; the row
with the newer `captured_at` wins (None counts as oldest) and the loser is
deleted. `sync_map_version` is cleared on demotion — the sentence index is a
sync-map coordinate and the map dies with the pair — while chapter, percent,
audio position and hints all survive.
"""

from sqlalchemy import select

from models.bookmark import Bookmark
from models.progress import ProgressType, UserProgress
from routers import library, sync, troubleshoot

from tests.factories import make_book_pair

POSITION = {
    "source": "ebook",
    "epub_chapter": 3,
    "epub_sentence_index": 1,
    "sync_map_version": 1,
    "epub_text_preview": "second sentence here",
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


async def _put_position(client, user, auth_header, scope, ident, body):
    r = await client.put(
        f"/api/sync/position/{scope}/{ident}", json=body, headers=auth_header(user)
    )
    assert r.status_code == 200, r.text
    return r


async def _pair_ids(db, pair):
    """(pair_id, ebook_id, audiobook_id) snapshotted before any endpoint runs."""
    return pair.id, pair.ebook_id, pair.audiobook_id


class TestUnpairDemotesPositions:
    async def test_unpairing_demotes_each_users_position_to_standalone(
        self, db, make_client, make_user, auth_header
    ):
        pair = await make_book_pair(db)
        pair_id, ebook_id, audiobook_id = await _pair_ids(db, pair)
        reader = await make_user(username="reader")
        editor = await make_user(username="editor", role="editor")

        async with make_client(library.router, sync.router) as c:
            await _put_position(c, reader, auth_header, "pair", pair_id, POSITION)

            r = await c.delete(
                f"/api/library/pairs/{pair_id}", headers=auth_header(editor)
            )
            assert r.status_code == 204, r.text

            for scope, ident in (("ebook", ebook_id), ("audiobook", audiobook_id)):
                r = await c.get(
                    f"/api/sync/position/{scope}/{ident}", headers=auth_header(reader)
                )
                assert r.status_code == 200, f"{scope} position was lost: {r.status_code}"
                body = r.json()
                assert body["book_pair_id"] is None
                assert body["epub_chapter"] == 3
                assert body["epub_progress_percent"] == 41.5
                assert body["audio_position_ms"] == 123456
                assert body["epub_text_preview"] == "second sentence here"
                assert body["device_id"] == "web-1"
                assert body["device_name"] == "Firefox on desk"
                assert body["captured_at"].startswith("2026-08-01T12:00:00")
                # The map died with the pair; the sentence index is no longer
                # vouched for by any version.
                assert body["sync_map_version"] is None
                # Hints survive the demotion, still current.
                assert [h["value"] for h in body["hints"]] == ["epubcfi(/6/8!/4/2)"]
                assert body["hints"][0]["current"] is True

        db.expire_all()
        orphans = (await db.execute(
            select(Bookmark).where(Bookmark.book_pair_id == pair_id)
        )).scalars().all()
        assert orphans == [], "no pair-scoped bookmark may survive the unpair"
        rows = (await db.execute(
            select(Bookmark).where(Bookmark.user_id == reader.id)
        )).scalars().all()
        assert len(rows) == 1, "one demoted row serves both standalone scopes"

    async def test_two_users_each_keep_their_own_position_after_unpair(
        self, db, make_client, make_user, auth_header
    ):
        pair = await make_book_pair(db)
        pair_id, ebook_id, audiobook_id = await _pair_ids(db, pair)
        alice = await make_user(username="alice")
        bob = await make_user(username="bob")
        editor = await make_user(username="editor", role="editor")

        alice_pos = dict(POSITION, epub_chapter=2, audio_position_ms=20000,
                         captured_at="2026-08-01T10:00:00Z")
        bob_pos = dict(POSITION, epub_chapter=7, audio_position_ms=70000,
                       captured_at="2026-08-01T11:00:00Z")

        async with make_client(library.router, sync.router) as c:
            await _put_position(c, alice, auth_header, "pair", pair_id, alice_pos)
            await _put_position(c, bob, auth_header, "pair", pair_id, bob_pos)

            r = await c.delete(
                f"/api/library/pairs/{pair_id}", headers=auth_header(editor)
            )
            assert r.status_code == 204, r.text

            for user, expected_ch, expected_ms in (
                (alice, 2, 20000), (bob, 7, 70000)
            ):
                r = await c.get(
                    f"/api/sync/position/ebook/{ebook_id}", headers=auth_header(user)
                )
                assert r.status_code == 200
                assert r.json()["epub_chapter"] == expected_ch
                r = await c.get(
                    f"/api/sync/position/audiobook/{audiobook_id}",
                    headers=auth_header(user),
                )
                assert r.status_code == 200
                assert r.json()["audio_position_ms"] == expected_ms

    async def test_newer_standalone_row_wins_over_the_demoted_pair_row(
        self, db, make_client, make_user, auth_header
    ):
        pair = await make_book_pair(db)
        pair_id, ebook_id, audiobook_id = await _pair_ids(db, pair)
        reader = await make_user(username="reader")
        editor = await make_user(username="editor", role="editor")

        pair_pos = dict(POSITION, epub_chapter=5,
                        captured_at="2026-08-01T12:00:00Z")
        standalone_pos = {
            "source": "ebook", "epub_chapter": 9,
            "epub_progress_percent": 90.0,
            "captured_at": "2026-08-02T12:00:00Z",  # newer than the pair write
        }

        async with make_client(library.router, sync.router) as c:
            await _put_position(c, reader, auth_header, "pair", pair_id, pair_pos)
            await _put_position(
                c, reader, auth_header, "ebook", ebook_id, standalone_pos)

            r = await c.delete(
                f"/api/library/pairs/{pair_id}", headers=auth_header(editor)
            )
            assert r.status_code == 204, r.text

            # The newer standalone ebook row survives; the demoted row lost
            # its ebook claim but still carries the audiobook side.
            r = await c.get(
                f"/api/sync/position/ebook/{ebook_id}", headers=auth_header(reader)
            )
            assert r.status_code == 200
            assert r.json()["epub_chapter"] == 9
            assert r.json()["captured_at"].startswith("2026-08-02T12:00:00")

            r = await c.get(
                f"/api/sync/position/audiobook/{audiobook_id}",
                headers=auth_header(reader),
            )
            assert r.status_code == 200
            assert r.json()["epub_chapter"] == 5
            assert r.json()["audio_position_ms"] == 123456

        db.expire_all()
        standalone_ebook_rows = (await db.execute(
            select(Bookmark).where(
                Bookmark.user_id == reader.id,
                Bookmark.book_pair_id.is_(None),
                Bookmark.ebook_id == ebook_id,
            )
        )).scalars().all()
        assert len(standalone_ebook_rows) == 1

    async def test_the_demoted_pair_row_replaces_an_older_standalone_row(
        self, db, make_client, make_user, auth_header
    ):
        pair = await make_book_pair(db)
        pair_id, ebook_id, audiobook_id = await _pair_ids(db, pair)
        reader = await make_user(username="reader")
        editor = await make_user(username="editor", role="editor")

        standalone_pos = {
            "source": "ebook", "epub_chapter": 1,
            "captured_at": "2026-07-01T12:00:00Z",  # older than the pair write
        }
        pair_pos = dict(POSITION, epub_chapter=5,
                        captured_at="2026-08-01T12:00:00Z")

        async with make_client(library.router, sync.router) as c:
            await _put_position(
                c, reader, auth_header, "ebook", ebook_id, standalone_pos)
            await _put_position(c, reader, auth_header, "pair", pair_id, pair_pos)

            r = await c.delete(
                f"/api/library/pairs/{pair_id}", headers=auth_header(editor)
            )
            assert r.status_code == 204, r.text

            r = await c.get(
                f"/api/sync/position/ebook/{ebook_id}", headers=auth_header(reader)
            )
            assert r.status_code == 200
            assert r.json()["epub_chapter"] == 5
            assert r.json()["audio_position_ms"] == 123456
            assert r.json()["captured_at"].startswith("2026-08-01T12:00:00")

        db.expire_all()
        rows = (await db.execute(
            select(Bookmark).where(Bookmark.user_id == reader.id)
        )).scalars().all()
        assert len(rows) == 1, "the older standalone row was replaced, not kept"

    async def test_audiobook_side_collisions_resolve_by_captured_at_too(
        self, db, make_client, make_user, auth_header
    ):
        """Both media can collide in the same demotion: a newer standalone
        ebook row keeps its medium, an older standalone audiobook row loses
        its medium to the demoted row and is deleted."""
        pair = await make_book_pair(db)
        pair_id, ebook_id, audiobook_id = await _pair_ids(db, pair)
        reader = await make_user(username="reader")
        editor = await make_user(username="editor", role="editor")

        old_audio_pos = {
            "source": "audiobook", "audio_position_ms": 111,
            "captured_at": "2026-07-01T12:00:00Z",  # older than the pair write
        }
        pair_pos = dict(POSITION, epub_chapter=5,
                        captured_at="2026-08-01T12:00:00Z")
        new_ebook_pos = {
            "source": "ebook", "epub_chapter": 9,
            "captured_at": "2026-08-02T12:00:00Z",  # newer than the pair write
        }

        async with make_client(library.router, sync.router) as c:
            await _put_position(
                c, reader, auth_header, "audiobook", audiobook_id, old_audio_pos)
            await _put_position(c, reader, auth_header, "pair", pair_id, pair_pos)
            await _put_position(
                c, reader, auth_header, "ebook", ebook_id, new_ebook_pos)

            r = await c.delete(
                f"/api/library/pairs/{pair_id}", headers=auth_header(editor)
            )
            assert r.status_code == 204, r.text

            r = await c.get(
                f"/api/sync/position/ebook/{ebook_id}", headers=auth_header(reader)
            )
            assert r.status_code == 200
            assert r.json()["epub_chapter"] == 9

            r = await c.get(
                f"/api/sync/position/audiobook/{audiobook_id}",
                headers=auth_header(reader),
            )
            assert r.status_code == 200
            assert r.json()["audio_position_ms"] == 123456, \
                "the demoted row replaces the older standalone audiobook row"

        db.expire_all()
        audio_rows = (await db.execute(
            select(Bookmark).where(
                Bookmark.user_id == reader.id,
                Bookmark.book_pair_id.is_(None),
                Bookmark.audiobook_id == audiobook_id,
            )
        )).scalars().all()
        assert len(audio_rows) == 1

    async def test_demoted_row_vanishes_when_newer_rows_cover_both_media(
        self, db, make_client, make_user, auth_header
    ):
        pair = await make_book_pair(db)
        pair_id, ebook_id, audiobook_id = await _pair_ids(db, pair)
        reader = await make_user(username="reader")
        editor = await make_user(username="editor", role="editor")

        pair_pos = dict(POSITION, captured_at="2026-08-01T12:00:00Z")
        new_ebook_pos = {"source": "ebook", "epub_chapter": 9,
                         "captured_at": "2026-08-02T12:00:00Z"}
        new_audio_pos = {"source": "audiobook", "audio_position_ms": 999,
                         "captured_at": "2026-08-02T12:00:00Z"}

        async with make_client(library.router, sync.router) as c:
            await _put_position(c, reader, auth_header, "pair", pair_id, pair_pos)
            await _put_position(
                c, reader, auth_header, "ebook", ebook_id, new_ebook_pos)
            await _put_position(
                c, reader, auth_header, "audiobook", audiobook_id, new_audio_pos)

            r = await c.delete(
                f"/api/library/pairs/{pair_id}", headers=auth_header(editor)
            )
            assert r.status_code == 204, r.text

            r = await c.get(
                f"/api/sync/position/ebook/{ebook_id}", headers=auth_header(reader)
            )
            assert r.json()["epub_chapter"] == 9
            r = await c.get(
                f"/api/sync/position/audiobook/{audiobook_id}",
                headers=auth_header(reader),
            )
            assert r.json()["audio_position_ms"] == 999

        db.expire_all()
        rows = (await db.execute(
            select(Bookmark).where(Bookmark.user_id == reader.id)
        )).scalars().all()
        assert len(rows) == 2, "the demoted row claimed nothing and is gone"
        assert all(row.book_pair_id is None for row in rows)

    async def test_demoting_a_vanished_pair_is_a_noop(self, db):
        """Defensive guard: a pair deleted by a concurrent request leaves
        nothing to demote."""
        from services.position_service import demote_pair_positions

        await demote_pair_positions(db, 999999)  # must not raise

    async def test_unpair_keeps_user_progress_rows_with_pair_id_cleared(
        self, db, make_client, make_user, auth_header
    ):
        pair = await make_book_pair(db)
        pair_id, ebook_id, audiobook_id = await _pair_ids(db, pair)
        reader = await make_user(username="reader")
        editor = await make_user(username="editor", role="editor")

        async with make_client(library.router, sync.router) as c:
            await _put_position(c, reader, auth_header, "pair", pair_id, POSITION)
            r = await c.delete(
                f"/api/library/pairs/{pair_id}", headers=auth_header(editor)
            )
            assert r.status_code == 204, r.text

        db.expire_all()
        rows = (await db.execute(
            select(UserProgress).where(UserProgress.user_id == reader.id)
        )).scalars().all()
        by_type = {row.media_type: row for row in rows}
        assert set(by_type) == {ProgressType.EBOOK, ProgressType.AUDIOBOOK}
        for row in rows:
            assert row.book_pair_id is None
        assert by_type[ProgressType.EBOOK].ebook_id == ebook_id
        assert by_type[ProgressType.EBOOK].epub_chapter == 3
        assert by_type[ProgressType.AUDIOBOOK].audiobook_id == audiobook_id
        assert by_type[ProgressType.AUDIOBOOK].audio_position_ms == 123456


class TestDeletingOneMediumKeepsTheOther:
    async def test_deleting_an_ebook_keeps_the_audiobook_position(
        self, db, make_client, make_user, auth_header
    ):
        pair = await make_book_pair(db)
        pair_id, ebook_id, audiobook_id = await _pair_ids(db, pair)
        reader = await make_user(username="reader")
        editor = await make_user(username="editor", role="editor")

        async with make_client(library.router, sync.router) as c:
            await _put_position(c, reader, auth_header, "pair", pair_id, POSITION)

            r = await c.delete(
                f"/api/library/ebooks/{ebook_id}", headers=auth_header(editor)
            )
            assert r.status_code == 204, r.text

            r = await c.get(
                f"/api/sync/position/audiobook/{audiobook_id}",
                headers=auth_header(reader),
            )
            assert r.status_code == 200, "the audiobook position must survive"
            assert r.json()["audio_position_ms"] == 123456

        db.expire_all()
        row = (await db.execute(
            select(Bookmark).where(Bookmark.user_id == reader.id)
        )).scalar_one()
        assert row.book_pair_id is None
        assert row.ebook_id is None, "must not reference the deleted ebook"
        assert row.audiobook_id == audiobook_id

    async def test_deleting_an_audiobook_keeps_the_ebook_position(
        self, db, make_client, make_user, auth_header
    ):
        pair = await make_book_pair(db)
        pair_id, ebook_id, audiobook_id = await _pair_ids(db, pair)
        reader = await make_user(username="reader")
        editor = await make_user(username="editor", role="editor")

        async with make_client(library.router, sync.router) as c:
            await _put_position(c, reader, auth_header, "pair", pair_id, POSITION)

            r = await c.delete(
                f"/api/library/audiobooks/{audiobook_id}", headers=auth_header(editor)
            )
            assert r.status_code == 204, r.text

            r = await c.get(
                f"/api/sync/position/ebook/{ebook_id}", headers=auth_header(reader)
            )
            assert r.status_code == 200, "the ebook position must survive"
            assert r.json()["epub_chapter"] == 3
            assert r.json()["epub_progress_percent"] == 41.5

        db.expire_all()
        row = (await db.execute(
            select(Bookmark).where(Bookmark.user_id == reader.id)
        )).scalar_one()
        assert row.book_pair_id is None
        assert row.audiobook_id is None, "must not reference the deleted audiobook"
        assert row.ebook_id == ebook_id

    async def test_deleting_an_ebook_drops_its_own_standalone_position(
        self, db, make_client, make_user, auth_header
    ):
        """A standalone row for the *deleted* medium has nothing left to
        describe — it is removed rather than left dangling on the dead FK."""
        pair = await make_book_pair(db)
        pair_id, ebook_id, audiobook_id = await _pair_ids(db, pair)
        reader = await make_user(username="reader")
        editor = await make_user(username="editor", role="editor")

        async with make_client(library.router, sync.router) as c:
            await _put_position(
                c, reader, auth_header, "ebook", ebook_id,
                {"source": "ebook", "epub_chapter": 4},
            )
            r = await c.delete(
                f"/api/library/ebooks/{ebook_id}", headers=auth_header(editor)
            )
            assert r.status_code == 204, r.text

        db.expire_all()
        rows = (await db.execute(
            select(Bookmark).where(Bookmark.user_id == reader.id)
        )).scalars().all()
        assert rows == []

    async def test_deleting_an_ebook_after_unpair_keeps_the_audiobook_side(
        self, db, make_client, make_user, auth_header
    ):
        """A demoted row references both media at once; deleting one of them
        later must strip that id and keep the other side's position."""
        pair = await make_book_pair(db)
        pair_id, ebook_id, audiobook_id = await _pair_ids(db, pair)
        reader = await make_user(username="reader")
        editor = await make_user(username="editor", role="editor")

        async with make_client(library.router, sync.router) as c:
            await _put_position(c, reader, auth_header, "pair", pair_id, POSITION)
            r = await c.delete(
                f"/api/library/pairs/{pair_id}", headers=auth_header(editor)
            )
            assert r.status_code == 204, r.text

            r = await c.delete(
                f"/api/library/ebooks/{ebook_id}", headers=auth_header(editor)
            )
            assert r.status_code == 204, r.text

            r = await c.get(
                f"/api/sync/position/audiobook/{audiobook_id}",
                headers=auth_header(reader),
            )
            assert r.status_code == 200, "the audiobook position must survive"
            assert r.json()["audio_position_ms"] == 123456

        db.expire_all()
        row = (await db.execute(
            select(Bookmark).where(Bookmark.user_id == reader.id)
        )).scalar_one()
        assert row.ebook_id is None, "must not reference the deleted ebook"
        assert row.audiobook_id == audiobook_id

    async def test_orphan_cleanup_keeps_the_other_mediums_position(
        self, db, make_client, make_user, auth_header
    ):
        """`POST /api/library/cleanup` is a sibling of `delete_ebook` /
        `delete_audiobook` and must demote identically — both branches."""
        pair_a = await make_book_pair(db, ebook_title="A-e", audiobook_title="A-a")
        pair_a_id, ebook_a, audiobook_a = await _pair_ids(db, pair_a)
        pair_b = await make_book_pair(db, ebook_title="B-e", audiobook_title="B-a")
        pair_b_id, ebook_b, audiobook_b = await _pair_ids(db, pair_b)
        reader = await make_user(username="reader")
        editor = await make_user(username="editor", role="editor")

        async with make_client(library.router, sync.router) as c:
            await _put_position(c, reader, auth_header, "pair", pair_a_id, POSITION)
            await _put_position(c, reader, auth_header, "pair", pair_b_id, POSITION)

            # Clean up pair A's ebook and pair B's audiobook in one request.
            r = await c.post(
                "/api/library/cleanup",
                json={"ebook_ids": [ebook_a], "audiobook_ids": [audiobook_b]},
                headers=auth_header(editor),
            )
            assert r.status_code == 200, r.text

            r = await c.get(
                f"/api/sync/position/audiobook/{audiobook_a}",
                headers=auth_header(reader),
            )
            assert r.status_code == 200, "pair A's audiobook position must survive"
            assert r.json()["audio_position_ms"] == 123456

            r = await c.get(
                f"/api/sync/position/ebook/{ebook_b}", headers=auth_header(reader)
            )
            assert r.status_code == 200, "pair B's ebook position must survive"
            assert r.json()["epub_chapter"] == 3

            # Now clean up the surviving media too: with no medium left the
            # demoted rows have nothing to describe and are removed.
            r = await c.post(
                "/api/library/cleanup",
                json={"ebook_ids": [ebook_b], "audiobook_ids": [audiobook_a]},
                headers=auth_header(editor),
            )
            assert r.status_code == 200, r.text

        db.expire_all()
        rows = (await db.execute(
            select(Bookmark).where(Bookmark.user_id == reader.id)
        )).scalars().all()
        assert rows == []

    async def test_troubleshoot_bulk_delete_keeps_the_other_mediums_position(
        self, db, make_client, make_user, auth_header
    ):
        """The Troubleshoot delete path is a sibling of `delete_ebook` and must
        demote identically."""
        pair = await make_book_pair(db)
        pair_id, ebook_id, audiobook_id = await _pair_ids(db, pair)
        reader = await make_user(username="reader")
        editor = await make_user(username="editor", role="editor")

        async with make_client(troubleshoot.router, sync.router) as c:
            await _put_position(c, reader, auth_header, "pair", pair_id, POSITION)

            r = await c.post(
                "/api/troubleshoot/bulk-delete?delete_file=false",
                json={"items": [{"item_type": "ebook", "item_id": ebook_id}]},
                headers=auth_header(editor),
            )
            assert r.status_code == 200, r.text

            r = await c.get(
                f"/api/sync/position/audiobook/{audiobook_id}",
                headers=auth_header(reader),
            )
            assert r.status_code == 200, "the audiobook position must survive"
            assert r.json()["audio_position_ms"] == 123456
