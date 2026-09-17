"""`services.audio_change` -- detecting an audiobook replaced in place and
reacting to it (issue #588).

Covers the module directly (no HTTP): the shared invalidation helper
(extracted out of `routers.troubleshoot.replace_file`), the cheap-signal
pre-check, and the full detect-and-refresh flow against real files.
`test_library_scan_service.py` and `test_replace_invalidates_positions.py`
exercise the callers (scan ingest, the rescan endpoints, the troubleshoot
replace endpoint).
"""

import os

import pytest
from sqlalchemy import select

from models.book import AudioBook, BookPair, PairStatus
from models.transcript import AudioTranscript
from services import audio_change
from services.file_hash import hash_file
from tests.factories import make_audiobook, make_book_pair


def _touch(path, data: bytes):
    path.write_bytes(data)
    return str(path)


class TestInvalidateAudiobookTranscripts:
    async def test_drops_the_transcript_and_demotes_a_synced_pair(self, db):
        pair = await make_book_pair(db, status=PairStatus.SYNCED)
        db.add(AudioTranscript(pair_id=pair.id, audiobook_path="/x/a.m4b",
                               sentence_count=1, sentences_json="[]"))
        await db.commit()

        touched = await audio_change.invalidate_audiobook_transcripts(db, pair.audiobook_id)
        await db.commit()

        assert touched == 1
        fresh = await db.get(BookPair, pair.id)
        assert fresh.status == PairStatus.MANUAL_MATCHED
        assert (await db.execute(
            select(AudioTranscript).where(AudioTranscript.pair_id == pair.id)
        )).scalar_one_or_none() is None

    @pytest.mark.parametrize("status", [
        PairStatus.UNMATCHED, PairStatus.AUTO_MATCHED, PairStatus.MANUAL_MATCHED,
    ])
    async def test_leaves_a_not_yet_synced_pairs_status_alone(self, db, status):
        pair = await make_book_pair(db, status=status)
        db.add(AudioTranscript(pair_id=pair.id, audiobook_path="/x/a.m4b",
                               sentence_count=1, sentences_json="[]"))
        await db.commit()

        await audio_change.invalidate_audiobook_transcripts(db, pair.audiobook_id)
        await db.commit()

        fresh = await db.get(BookPair, pair.id)
        assert fresh.status == status
        # The transcript is still dropped regardless of status -- it describes
        # audio that no longer exists at this path either way.
        assert (await db.execute(
            select(AudioTranscript).where(AudioTranscript.pair_id == pair.id)
        )).scalar_one_or_none() is None

    async def test_touches_every_pair_on_a_multi_pair_audiobook(self, db):
        ab = await make_audiobook(db)
        from tests.factories import make_ebook
        eb1 = await make_ebook(db, title="One")
        eb2 = await make_ebook(db, title="Two")
        pair1 = BookPair(ebook_id=eb1.id, audiobook_id=ab.id, status=PairStatus.SYNCED)
        pair2 = BookPair(ebook_id=eb2.id, audiobook_id=ab.id, status=PairStatus.ERROR)
        db.add_all([pair1, pair2])
        await db.commit()
        db.add(AudioTranscript(pair_id=pair1.id, audiobook_path="/x/a.m4b",
                               sentence_count=1, sentences_json="[]"))
        db.add(AudioTranscript(pair_id=pair2.id, audiobook_path="/x/a.m4b",
                               sentence_count=1, sentences_json="[]"))
        await db.commit()

        touched = await audio_change.invalidate_audiobook_transcripts(db, ab.id)
        await db.commit()

        assert touched == 2
        for pid in (pair1.id, pair2.id):
            fresh = await db.get(BookPair, pid)
            assert fresh.status == PairStatus.MANUAL_MATCHED
        assert (await db.execute(select(AudioTranscript))).scalars().all() == []


class TestLooksChanged:
    def test_size_difference_is_a_change(self):
        assert audio_change._looks_changed(
            old_size=100, new_size=200,
            old_duration_seconds=3600, new_duration_seconds=3600,
        ) is True

    def test_duration_moving_more_than_the_tolerance_is_a_change(self):
        assert audio_change._looks_changed(
            old_size=100, new_size=100,
            old_duration_seconds=3600, new_duration_seconds=3603,
        ) is True

    def test_duration_within_tolerance_is_not_a_change(self):
        assert audio_change._looks_changed(
            old_size=100, new_size=100,
            old_duration_seconds=3600, new_duration_seconds=3601,
        ) is False

    def test_nothing_known_is_not_a_change(self):
        assert audio_change._looks_changed(
            old_size=None, new_size=None,
            old_duration_seconds=None, new_duration_seconds=None,
        ) is False


class TestRefreshIfAudiobookFileChanged:
    async def test_a_same_path_replacement_refreshes_hash_and_invalidates(
        self, db, tmp_path
    ):
        """The issue's reproduce case: same path, different recording."""
        path = tmp_path / "book.m4b"
        old_bytes = b"the original ten-hour recording" * 100
        _touch(path, old_bytes)
        old_hash = hash_file(str(path))

        pair = await make_book_pair(db, status=PairStatus.SYNCED,
                                     duration_seconds=36000)
        book = await db.get(AudioBook, pair.audiobook_id)
        book.file_path = str(path)
        book.file_hash = old_hash
        book.file_size = os.path.getsize(path)
        await db.commit()
        db.add(AudioTranscript(pair_id=pair.id, audiobook_path=str(path),
                               sentence_count=1, sentences_json="[]"))
        await db.commit()

        # Replaced in place: a shorter, differently-encoded recording.
        _touch(path, b"a completely different nine-hour recording" * 80)

        changed = await audio_change.refresh_if_audiobook_file_changed(
            db, book, str(path), new_duration_seconds=32400,
        )
        await db.commit()

        assert changed is True
        await db.refresh(book)
        assert book.file_hash == hash_file(str(path))
        assert book.file_hash != old_hash
        assert book.file_size == os.path.getsize(path)

        fresh_pair = await db.get(BookPair, pair.id)
        assert fresh_pair.status == PairStatus.MANUAL_MATCHED
        assert (await db.execute(
            select(AudioTranscript).where(AudioTranscript.pair_id == pair.id)
        )).scalar_one_or_none() is None

    async def test_an_unchanged_file_is_left_alone(self, db, tmp_path):
        path = tmp_path / "book.m4b"
        _touch(path, b"same file every time" * 50)
        the_hash = hash_file(str(path))

        pair = await make_book_pair(db, status=PairStatus.SYNCED, duration_seconds=3600)
        book = await db.get(AudioBook, pair.audiobook_id)
        book.file_path = str(path)
        book.file_hash = the_hash
        book.file_size = os.path.getsize(path)
        await db.commit()
        db.add(AudioTranscript(pair_id=pair.id, audiobook_path=str(path),
                               sentence_count=1, sentences_json="[]"))
        await db.commit()

        changed = await audio_change.refresh_if_audiobook_file_changed(
            db, book, str(path), new_duration_seconds=3600,
        )
        await db.commit()

        assert changed is False
        fresh_pair = await db.get(BookPair, pair.id)
        assert fresh_pair.status == PairStatus.SYNCED
        assert (await db.execute(
            select(AudioTranscript).where(AudioTranscript.pair_id == pair.id)
        )).scalar_one_or_none() is not None

    async def test_a_tag_only_write_back_does_not_look_like_a_replacement(
        self, db, tmp_path
    ):
        """The file's bytes (and hash) changed, but only because Tandem wrote
        tags into it -- `refresh_after_write_back` already moved `file_hash`
        forward, so the detector sees no drift to react to."""
        path = tmp_path / "book.m4b"
        _touch(path, b"original bytes before tagging" * 50)

        pair = await make_book_pair(db, status=PairStatus.SYNCED, duration_seconds=3600)
        book = await db.get(AudioBook, pair.audiobook_id)
        book.file_path = str(path)
        book.file_hash = hash_file(str(path))
        book.file_size = os.path.getsize(path)
        await db.commit()
        db.add(AudioTranscript(pair_id=pair.id, audiobook_path=str(path),
                               sentence_count=1, sentences_json="[]",
                               audio_file_hash=book.file_hash))
        await db.commit()

        # Tandem writes tags: the bytes (and hash) change, but not the length.
        _touch(path, b"same recording, new tag bytes appended" * 50)
        await audio_change.refresh_after_write_back(db, book, str(path))
        await db.commit()

        changed = await audio_change.refresh_if_audiobook_file_changed(
            db, book, str(path), new_duration_seconds=3600,
        )
        await db.commit()

        assert changed is False
        fresh_pair = await db.get(BookPair, pair.id)
        assert fresh_pair.status == PairStatus.SYNCED
        transcript = (await db.execute(
            select(AudioTranscript).where(AudioTranscript.pair_id == pair.id)
        )).scalar_one()
        assert transcript is not None
        assert transcript.audio_file_hash == book.file_hash

    async def test_a_row_with_no_stored_hash_backfills_without_invalidating(
        self, db, tmp_path
    ):
        """A row whose `file_hash` was never computed (predates hashing, or
        an unusual ingest state) must not have every scan treat it as a
        replacement -- there is no prior fingerprint to have drifted from."""
        path = tmp_path / "book.m4b"
        _touch(path, b"never hashed before" * 50)

        pair = await make_book_pair(db, status=PairStatus.SYNCED, duration_seconds=None)
        book = await db.get(AudioBook, pair.audiobook_id)
        book.file_path = str(path)
        book.file_hash = None
        book.file_size = None
        await db.commit()
        db.add(AudioTranscript(pair_id=pair.id, audiobook_path=str(path),
                               sentence_count=1, sentences_json="[]"))
        await db.commit()

        changed = await audio_change.refresh_if_audiobook_file_changed(
            db, book, str(path), new_duration_seconds=3600,
        )
        await db.commit()

        assert changed is False
        await db.refresh(book)
        assert book.file_hash == hash_file(str(path))   # backfilled
        fresh_pair = await db.get(BookPair, pair.id)
        assert fresh_pair.status == PairStatus.SYNCED
        assert (await db.execute(
            select(AudioTranscript).where(AudioTranscript.pair_id == pair.id)
        )).scalar_one_or_none() is not None

    async def test_a_missing_file_is_not_a_detected_change(self, db, tmp_path):
        path = tmp_path / "gone.m4b"
        pair = await make_book_pair(db, status=PairStatus.SYNCED)
        book = await db.get(AudioBook, pair.audiobook_id)
        book.file_path = str(path)
        book.file_hash = "deadbeef"
        book.file_size = 123
        await db.commit()

        changed = await audio_change.refresh_if_audiobook_file_changed(
            db, book, str(path), new_duration_seconds=3600,
        )

        assert changed is False


class TestRefreshTranscriptFingerprints:
    async def test_updates_only_transcripts_that_already_carry_a_fingerprint(self, db):
        pair_known = await make_book_pair(db)
        pair_unknown = await make_book_pair(db)
        # Both pairs' audiobooks are distinct rows from make_book_pair; give
        # pair_unknown's transcript the *same* audiobook_id as pair_known's
        # so both belong to one audiobook.
        ab_id = pair_known.audiobook_id
        pair_unknown_row = await db.get(BookPair, pair_unknown.id)
        pair_unknown_row.audiobook_id = ab_id
        await db.commit()

        db.add(AudioTranscript(pair_id=pair_known.id, audiobook_path="/x/a.m4b",
                               sentence_count=1, sentences_json="[]",
                               audio_file_hash="old-hash"))
        db.add(AudioTranscript(pair_id=pair_unknown.id, audiobook_path="/x/a.m4b",
                               sentence_count=1, sentences_json="[]",
                               audio_file_hash=None))
        await db.commit()

        await audio_change.refresh_transcript_fingerprints(db, ab_id, "new-hash")
        await db.commit()

        known = (await db.execute(
            select(AudioTranscript).where(AudioTranscript.pair_id == pair_known.id)
        )).scalar_one()
        unknown = (await db.execute(
            select(AudioTranscript).where(AudioTranscript.pair_id == pair_unknown.id)
        )).scalar_one()
        assert known.audio_file_hash == "new-hash"
        assert unknown.audio_file_hash is None
