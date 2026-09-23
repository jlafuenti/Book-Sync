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

    async def test_an_audiobook_cannot_be_in_two_pairs(self, db):
        """This used to prove `invalidate_audiobook_transcripts` walked every
        pair on a multi-paired audiobook. Issue #691 made that state
        unrepresentable — an audiobook is in at most one pair — so the two
        single-pair tests above are the whole of this function's contract now;
        this just pins that the second pair is rejected at the DB level."""
        from sqlalchemy.exc import IntegrityError

        from tests.factories import make_ebook

        ab = await make_audiobook(db)
        eb1 = await make_ebook(db, title="One")
        eb2 = await make_ebook(db, title="Two")
        db.add(BookPair(ebook_id=eb1.id, audiobook_id=ab.id, status=PairStatus.SYNCED))
        await db.commit()

        db.add(BookPair(ebook_id=eb2.id, audiobook_id=ab.id, status=PairStatus.ERROR))
        with pytest.raises(IntegrityError):
            await db.commit()
        await db.rollback()


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

    async def test_an_external_tag_edit_refreshes_the_hash_but_keeps_the_transcript(
        self, db, tmp_path, caplog
    ):
        """The owner edits tags outside Tandem routinely (Audiobookshelf's own
        metadata tools, a tag editor) -- unlike the write-back case above,
        nothing ever calls `refresh_after_write_back` for an edit like this,
        so `book.file_hash` is genuinely stale by the time a scan finds it.
        The hash changed, but the *duration* didn't -- this must be read as
        an edit, not a replacement: refresh the hash, keep the hours-long
        transcript, and say so in the log."""
        path = tmp_path / "book.m4b"
        _touch(path, b"original bytes before an external tag edit" * 50)
        old_hash = hash_file(str(path))

        pair = await make_book_pair(db, status=PairStatus.SYNCED, duration_seconds=3600)
        book = await db.get(AudioBook, pair.audiobook_id)
        book.file_path = str(path)
        book.file_hash = old_hash
        book.file_size = os.path.getsize(path)
        await db.commit()
        db.add(AudioTranscript(pair_id=pair.id, audiobook_path=str(path),
                               sentence_count=1, sentences_json="[]",
                               audio_file_hash=old_hash))
        await db.commit()

        # Some other tool rewrites the tags -- same length, different bytes,
        # and Tandem never sees it happen (no write-back refresh runs).
        _touch(path, b"same recording, tagged by another program entirely" * 40)
        new_hash = hash_file(str(path))
        assert new_hash != old_hash  # the premise: an edit does move the hash

        import logging
        with caplog.at_level(logging.INFO, logger="services.audio_change"):
            changed = await audio_change.refresh_if_audiobook_file_changed(
                db, book, str(path), new_duration_seconds=3600,
            )
        await db.commit()

        assert changed is False, "an edit must not report itself as a replacement"
        await db.refresh(book)
        assert book.file_hash == new_hash
        assert book.file_size == os.path.getsize(path)

        fresh_pair = await db.get(BookPair, pair.id)
        assert fresh_pair.status == PairStatus.SYNCED
        transcript = (await db.execute(
            select(AudioTranscript).where(AudioTranscript.pair_id == pair.id)
        )).scalar_one()
        assert transcript is not None, "the hours-long transcript must survive an edit"
        assert transcript.audio_file_hash == new_hash, "its fingerprint still moves forward"
        assert any(
            "external tag edit" in r.message and str(book.id) in r.message
            for r in caplog.records
        ), "the correction must be logged with the audiobook id"

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
    async def test_a_transcript_that_already_carries_a_fingerprint_is_updated(self, db):
        pair = await make_book_pair(db)
        db.add(AudioTranscript(pair_id=pair.id, audiobook_path="/x/a.m4b",
                               sentence_count=1, sentences_json="[]",
                               audio_file_hash="old-hash"))
        await db.commit()

        await audio_change.refresh_transcript_fingerprints(db, pair.audiobook_id, "new-hash")
        await db.commit()

        row = (await db.execute(
            select(AudioTranscript).where(AudioTranscript.pair_id == pair.id)
        )).scalar_one()
        assert row.audio_file_hash == "new-hash"

    async def test_a_transcript_with_no_fingerprint_is_left_alone(self, db):
        """`audio_file_hash IS NULL` means unknown provenance, not "known
        unchanged" (the model's own docstring) — only a row that already
        carries a fingerprint needs correcting.

        A separate pair/audiobook from the "is updated" case above: an
        audiobook can be in at most one pair (issue #691), and a pair can
        have at most one transcript (`audio_transcripts.pair_id` is unique),
        so there is no longer a way to put two transcripts — one with a
        fingerprint, one without — under a single `audiobook_id` to compare
        within one test.
        """
        pair = await make_book_pair(db)
        db.add(AudioTranscript(pair_id=pair.id, audiobook_path="/x/a.m4b",
                               sentence_count=1, sentences_json="[]",
                               audio_file_hash=None))
        await db.commit()

        await audio_change.refresh_transcript_fingerprints(db, pair.audiobook_id, "new-hash")
        await db.commit()

        row = (await db.execute(
            select(AudioTranscript).where(AudioTranscript.pair_id == pair.id)
        )).scalar_one()
        assert row.audio_file_hash is None
