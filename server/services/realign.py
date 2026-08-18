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

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from models.book import BookPair, PairStatus
from models.transcript import AudioTranscript
from services.alignment import align_texts
from services.ebook_integrity import format_is_alignable
from services.epub_parser import extract_book_sentences
from services.sync_engine import save_sync_map
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


@dataclass(frozen=True)
class RealignResult:
    points: int
    matched: int

    @property
    def interpolated(self) -> int:
        return self.points - self.matched


async def realign_pair_from_cached_transcript(
    db: AsyncSession, pair_id: int
) -> RealignResult:
    """Rebuild the sync map for `pair_id` from its cached transcript.

    Raises `RealignError` (or `NoCachedTranscript`) with a user-facing `detail`.
    """
    pair = (await db.execute(
        select(BookPair)
        .options(selectinload(BookPair.ebook))
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

    whisper_sentences = [
        TranscribedSentence(**s) for s in json.loads(transcript.sentences_json)
    ]
    try:
        epub_sentences = await asyncio.to_thread(
            extract_book_sentences, pair.ebook.file_path
        )
    except (zipfile.BadZipFile, FileNotFoundError) as e:
        raise RealignError(f"Could not read ebook file: {e}") from e

    aligned = await asyncio.to_thread(align_texts, epub_sentences, whisper_sentences)
    if not aligned:
        raise RealignError(
            "Alignment produced no points (empty transcript or ebook text)."
        )

    await save_sync_map(db, pair_id, aligned)
    pair.status = PairStatus.SYNCED
    pair.synced_at = utcnow()

    matched = sum(1 for p in aligned if p.confidence > 0)
    logger.info(
        f"[realign] pair {pair_id}: {len(aligned)} points, {matched} matched, "
        f"from {pair.ebook.file_path}"
    )
    return RealignResult(points=len(aligned), matched=matched)
