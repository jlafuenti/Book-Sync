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

from sqlalchemy import select

from models.book import BookPair, EBook, PairStatus
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
        from models.bookmark import Bookmark
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
