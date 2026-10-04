"""
Re-alignment: rebuild a pair's sync map from its cached transcript.

Cheap (CPU only, no Whisper) because the transcription is already banked in
`AudioTranscript`. Two callers:

  * `POST /api/transcription/{pair_id}/realign` — the manual "the map looks
    wrong, rebuild it" action;
  * the Convert flow in `routers/library.py`, which re-points a pair from a
    `.mobi`/`.azw3` at its Calibre-converted EPUB. The old map was built from
    the source file, so after the re-link it describes a document nobody
    renders — it has to be rebuilt against the artifact the reader now gets
    (issue #101).

`save_sync_map` does the delicate part: it bumps `sync_maps.version` and re-maps
every bookmark onto the new coordinates before returning. See
`docs/position-sync-contract.md` §Re-transcription — the coordinate system
changes, the position must not.

Flushes but does not commit; the caller owns the transaction.
"""

import asyncio
import json
import logging
import zipfile
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from models.book import BookPair, PairStatus
from models.sync_map import SyncMap, SyncPoint
from models.transcript import AudioTranscript
from services.alignment import align_texts_with_diagnostics
from services.audio_change import (
    TRANSCRIPT_COVERAGE_FAIL_BELOW,
    coverage_detail,
    record_transcript_coverage,
    transcript_coverage,
)
from services.ebook_integrity import format_is_alignable
from services.epub_parser import extract_book_sentences
from services.sync_engine import save_sync_map_with_result
from services.transcription import TranscribedSentence
from utils import utcnow

logger = logging.getLogger(__name__)


class RealignError(RuntimeError):
    """A pair could not be re-aligned. `detail` is user-facing.

    `status_code` is what the HTTP layer should return; carrying it here keeps
    the router a thin caller instead of re-deriving the mapping from the
    message.
    """

    status_code = 422

    def __init__(self, detail: str, status_code: int | None = None):
        super().__init__(detail)
        self.detail = detail
        if status_code is not None:
            self.status_code = status_code


class NoCachedTranscript(RealignError):
    """There is no transcript to rebuild from — only full transcription can fix it.

    Distinct from the other failures because it is a *state*, not a fault: the
    Convert flow reacts to it by marking the pair unsynced rather than by
    reporting an error against the conversion.
    """

    status_code = 404


class TranscriptRejected(RealignError):
    """The cached transcript is known to be wrong (issue #794): Library verify
    measured it out of step with the audio file, or an admin asked for a fresh
    transcription. Re-aligning would rebuild the same error into a map that
    looks new; only a re-transcription fixes it.
    """

    status_code = 409


class TranscriptTooShort(RealignError):
    """The cached transcript stops far short of the audio file (issue #814):
    the same coverage floor the queue applies since #796. The queue keeps such a
    transcript cached when it refuses to sync it, so without this check one
    Re-align would sync exactly what the queue refused.
    """

    status_code = 409


@dataclass(frozen=True)
class RealignResult:
    points: int
    matched: int
    #: What the replaced map looked like, for the bulk rebuild's report (issue
    #: #774). None when the pair had no map before.
    old_points: Optional[int] = None
    #: Old points whose stored preview spans a line break — the symptom the
    #: splitter change fixes.
    old_multiline_points: Optional[int] = None
    bookmarks: int = 0
    bookmarks_remapped: int = 0

    @property
    def interpolated(self) -> int:
        return self.points - self.matched


async def realign_pair_from_cached_transcript(
    db: AsyncSession, pair_id: int, *, allow_rejected: bool = False
) -> RealignResult:
    """Rebuild the sync map for `pair_id` from its cached transcript.

    Raises `RealignError` (or `NoCachedTranscript`, or `TranscriptRejected`)
    with a user-facing `detail`. `allow_rejected` is for the Convert flow: a
    converted ebook needs a map in its own coordinates even when the
    transcript's timing is off, since the old map names the old file's text.
    """
    pair = (await db.execute(
        select(BookPair)
        .options(selectinload(BookPair.ebook), selectinload(BookPair.audiobook))
        .where(BookPair.id == pair_id)
    )).scalar_one_or_none()
    if not pair or not pair.ebook:
        raise RealignError("Book pair not found", status_code=404)

    ok_format, detail = format_is_alignable(pair.ebook.file_path)
    if not ok_format:
        raise RealignError(detail)

    transcript = (await db.execute(
        select(AudioTranscript).where(AudioTranscript.pair_id == pair_id)
    )).scalar_one_or_none()
    if not transcript:
        raise NoCachedTranscript(
            "No cached transcript for this pair — run full transcription instead."
        )
    if not allow_rejected:
        from services.transcript_timing import current_rejection

        rejection = await current_rejection(db, pair_id, transcript)
        if rejection:
            raise TranscriptRejected(
                f"{rejection} Re-aligning would rebuild the same error; re-queue the "
                f"pair to transcribe it afresh."
            )

    whisper_sentences = [
        TranscribedSentence(**s) for s in json.loads(transcript.sentences_json)
    ]
    # The coverage floor the queue applies (issues #796, #814). Convert skips it
    # for the same reason it skips the rejection above: a converted ebook needs
    # a map in its own coordinates whatever the transcript's shortcomings.
    duration_seconds = pair.audiobook.duration_seconds if pair.audiobook else None
    coverage = transcript_coverage(whisper_sentences, duration_seconds)
    if (not allow_rejected and coverage is not None
            and coverage < TRANSCRIPT_COVERAGE_FAIL_BELOW):
        raise TranscriptTooShort(
            f"{coverage_detail(coverage, duration_seconds)}, possibly padded audio. "
            f"Re-aligning would sync a map with no sync points for the rest of the "
            f"file; check the file, then use Re-transcribe from scratch."
        )
    try:
        epub_sentences = await asyncio.to_thread(
            extract_book_sentences, pair.ebook.file_path
        )
    except (zipfile.BadZipFile, FileNotFoundError) as e:
        raise RealignError(f"Could not read ebook file: {e}") from e

    aligned, diagnostics = await asyncio.to_thread(
        align_texts_with_diagnostics, epub_sentences, whisper_sentences
    )
    if not aligned:
        raise RealignError(
            "Alignment produced no points (empty transcript or ebook text)."
        )

    # Counted before the save: the save deletes the outgoing map's points.
    old_map_id = (await db.execute(
        select(SyncMap.id).where(SyncMap.book_pair_id == pair_id)
    )).scalar_one_or_none()
    old_points = old_multiline = None
    if old_map_id is not None:
        old_points = (await db.execute(
            select(func.count()).select_from(SyncPoint)
            .where(SyncPoint.sync_map_id == old_map_id)
        )).scalar_one()
        old_multiline = (await db.execute(
            select(func.count()).select_from(SyncPoint)
            .where(SyncPoint.sync_map_id == old_map_id,
                   SyncPoint.epub_text_preview.contains("\n"))
        )).scalar_one()

    _map, saved = await save_sync_map_with_result(db, pair_id, aligned, diagnostics)
    await record_transcript_coverage(db, pair_id, coverage, duration_seconds)
    pair.status = PairStatus.SYNCED
    pair.synced_at = utcnow()

    matched = sum(1 for p in aligned if p.confidence > 0)
    logger.info(
        f"[realign] pair {pair_id}: {len(aligned)} points, {matched} matched, "
        f"from {pair.ebook.file_path}"
    )
    return RealignResult(
        points=len(aligned),
        matched=matched,
        old_points=old_points,
        old_multiline_points=old_multiline,
        bookmarks=saved.bookmarks,
        bookmarks_remapped=saved.bookmarks_remapped,
    )
