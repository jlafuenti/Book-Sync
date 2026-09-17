"""
Detecting and reacting to an audiobook file replaced in place (issue #588).

A downloader "upgrading" a file, a re-rip, or Audiobookshelf's merge/re-encode
tools can all replace the bytes behind an `AudioBook.file_path` without
touching the row. Nothing on the scan side used to notice: `file_size` and
`file_hash` kept describing the old recording, `services.queue_manager`'s
transcript cache kept handing the old transcript back for re-transcription,
and a synced pair stayed `synced` against a sync map aligned to audio that no
longer exists at that path.

`invalidate_audiobook_transcripts` is the reaction: drop the cached
transcript and demote an active pair's status, exactly what
`POST /api/troubleshoot/replace/audiobook/{id}` already did for an
operator-uploaded replacement. Extracted here so scan/rescan -- which detects
a replacement without an upload -- reuses it instead of drifting from it.

`refresh_if_audiobook_file_changed` is the detector: scan and rescan call it
for a *known* path (an existing row) before trusting the file's metadata.
Cheap signals (a `stat()` size, and the duration `extract_metadata` already
read for this pass) decide whether the bounded-cost composite hash
(`services.file_hash`, at most 20 MiB read) is worth computing at all. A hash
mismatch on its own is *not* the verdict, though: the owner edits tags
outside Tandem routinely (Audiobookshelf's own metadata tools, a tag editor),
and that changes the whole-file hash without touching a frame of audio.
**Duration** is what separates the two -- moved by more than the tolerance
means a different recording (invalidate); unchanged means an edit (refresh
the hash, keep the transcript). See the function's own docstring for the
reasoning and what backstops the case this can't distinguish (a same-length
swap to a different recording).

A metadata-only tag write-back by Tandem itself (`services.tag_writer`,
`services.abs_metadata.write_metadata_to_file`) changes the file's bytes --
and therefore its hash -- too. `refresh_after_write_back` is the other half
of that: called right after *this* server's own write, it moves
`AudioBook.file_hash` (and every pair's cached transcript fingerprint)
forward in the same request/scan pass, so a write-back never looks like a
replacement to the detector above -- by the time anything reads `file_hash`
again, it already matches what's on disk.
"""

import asyncio
import logging
import os
from typing import Optional

from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.book import AudioBook, BookPair, PairStatus
from models.transcript import AudioTranscript
from services.file_hash import hash_file

logger = logging.getLogger(__name__)

# ffprobe/mutagen occasionally disagree by a fraction of a second, and
# encoders round differently -- this must not read that noise as a different
# recording. Matches the issue's "moves by more than ~2 s" wording.
AUDIO_DURATION_FINGERPRINT_TOLERANCE_SEC = 2

# `services.queue_manager._transcript_covers_duration`'s threshold for a
# NULL-fingerprint transcript's own content: its last segment must reach at
# least this fraction of the audiobook's current duration to be trusted.
# Below it, the transcript looks like it describes a shorter recording at the
# same path -- the exact shape a same-path replacement predating this fix
# would leave behind. 0.95 rather than 1.0 because trailing
# silence/credits/outros are routinely left untranscribed by Whisper even on
# an untouched file.
TRANSCRIPT_COVERAGE_MIN_FRACTION = 0.95


async def invalidate_audiobook_transcripts(db: AsyncSession, audiobook_id: int) -> int:
    """Drop the cached transcript and demote an active pair's status for
    every pair on this audiobook -- the same reaction
    `POST /api/troubleshoot/replace/audiobook/{id}` has always had to an
    operator-uploaded replacement, shared so scan/rescan (which detects a
    replacement without an upload) doesn't reimplement it and drift.

    Reading positions are untouched; only the transcript cache and the pair's
    sync status move. Returns how many pairs were touched.
    """
    pairs = (await db.execute(
        select(BookPair).where(BookPair.audiobook_id == audiobook_id)
    )).scalars().all()
    for pair in pairs:
        await db.execute(
            sa_delete(AudioTranscript).where(AudioTranscript.pair_id == pair.id)
        )
        if pair.status in (PairStatus.SYNCED, PairStatus.ERROR, PairStatus.TRANSCRIBING):
            pair.status = PairStatus.MANUAL_MATCHED
    return len(pairs)


def _duration_changed(
    *, old_duration_seconds: Optional[int], new_duration_seconds: Optional[int]
) -> bool:
    """Has the file's runtime moved by more than the fingerprint tolerance?
    False (not "changed") when either side is unknown -- there is nothing to
    compare, so this is not evidence of anything."""
    if old_duration_seconds is None or new_duration_seconds is None:
        return False
    return (
        abs(new_duration_seconds - old_duration_seconds)
        > AUDIO_DURATION_FINGERPRINT_TOLERANCE_SEC
    )


def _looks_changed(
    *, old_size, new_size, old_duration_seconds, new_duration_seconds
) -> bool:
    """Cheap pre-check with no I/O of its own: is it worth paying for the
    hash at all? Both signals are already in hand by the time a scan calls
    this -- `new_size` from a `stat()` it just did, `new_duration_seconds`
    from the `extract_metadata` pass it's already running. This only decides
    whether to *look closer*; `_duration_changed` alone decides whether a
    hash mismatch found by looking closer means "replaced" or "edited"."""
    if old_size is not None and new_size is not None and old_size != new_size:
        return True
    return _duration_changed(
        old_duration_seconds=old_duration_seconds,
        new_duration_seconds=new_duration_seconds,
    )


async def refresh_if_audiobook_file_changed(
    db: AsyncSession,
    book: AudioBook,
    filepath: str,
    *,
    new_duration_seconds: Optional[int],
) -> bool:
    """If `filepath` no longer holds the recording `book` was last hashed
    against, refresh `file_size`/`file_hash`. Returns True only when this
    also invalidated every pair's cached transcript -- a confirmed
    *replacement*, not every hash change.

    The owner routinely edits tags outside Tandem (Audiobookshelf's
    metadata tools, a tag editor) -- that changes the whole-file hash with
    the audio itself untouched, and none of those tools go through
    `refresh_after_write_back` below to keep the hash in step the way
    Tandem's own write-back does. Treating every hash change as a
    replacement would silently discard a transcript that can take hours to
    rebuild on the Jetson, for nothing but an edited title. So the verdict
    turns on **duration**, not the hash alone:

    * duration moved by more than the tolerance -> a genuine replacement.
      Invalidate: drop the cached transcript(s), demote a synced pair.
    * hash/size differ but duration didn't move -> an edit, not a
      replacement. Refresh `file_hash`/`file_size` and move the transcript's
      own fingerprint forward (same as a Tandem write-back would), but
      *keep* the transcript. A same-length swap to a different recording is
      the case this can't tell apart from an edit -- rare, and the sync-map
      audit's timing check (issue #586) is the backstop for it, sampling the
      cached transcript's own timestamps against the sync map's.

    Call this for a *known* path a scan is already visiting (an existing
    row) before this same pass does anything else that could rewrite the
    file (ABS enrichment's write-back, in particular) -- the comparison must
    see the file as it was found, not as this pass is about to leave it. A
    brand new row has no prior hash to compare against and doesn't call this
    at all.
    """
    try:
        current_size = os.path.getsize(filepath)
    except OSError:
        return False

    duration_changed = _duration_changed(
        old_duration_seconds=book.duration_seconds,
        new_duration_seconds=new_duration_seconds,
    )
    signals_changed = _looks_changed(
        old_size=book.file_size,
        new_size=current_size,
        old_duration_seconds=book.duration_seconds,
        new_duration_seconds=new_duration_seconds,
    )
    had_hash = book.file_hash is not None
    if not signals_changed and had_hash:
        return False  # nothing suspicious, and we already have a hash to trust

    try:
        new_hash = await asyncio.to_thread(hash_file, filepath)
    except OSError:
        return False

    unchanged = had_hash and new_hash == book.file_hash
    book.file_hash = new_hash
    book.file_size = current_size
    db.add(book)

    if unchanged or not had_hash:
        # Either a size/duration blip that didn't actually move the hash, or
        # the first time this row has ever been hashed -- backfill without
        # punishing a pair for a gap in history there's no evidence changed
        # anything (issue #588's migration note).
        return False

    if not duration_changed:
        logger.info(
            f"[audio-change] audiobook {book.id} at {filepath}: hash changed "
            f"but duration is unchanged (~{book.duration_seconds}s) -- treating "
            f"as an external tag edit, not a replacement; keeping the cached "
            f"transcript(s) and re-stamping their fingerprint"
        )
        await refresh_transcript_fingerprints(db, book.id, new_hash)
        return False

    logger.info(
        f"[audio-change] audiobook {book.id} at {filepath} changed on disk "
        f"(duration moved from {book.duration_seconds}s to {new_duration_seconds}s) "
        f"-- dropping cached transcript(s) and demoting synced pairs"
    )
    await invalidate_audiobook_transcripts(db, book.id)
    return True


async def refresh_transcript_fingerprints(
    db: AsyncSession, audiobook_id: int, new_hash: str
) -> None:
    """Move every pair's cached transcript fingerprint on this audiobook
    forward to `new_hash` -- called right after *this* server rewrote the
    file's own tags. Only a transcript that already carries a fingerprint
    needs correcting; one with a NULL `audio_file_hash` predates the column,
    or has never been verified, and stays "unknown" either way
    (`AudioTranscript.audio_file_hash`'s docstring).
    """
    transcripts = (await db.execute(
        select(AudioTranscript)
        .join(BookPair, BookPair.id == AudioTranscript.pair_id)
        .where(
            BookPair.audiobook_id == audiobook_id,
            AudioTranscript.audio_file_hash.isnot(None),
        )
    )).scalars().all()
    for t in transcripts:
        t.audio_file_hash = new_hash


async def refresh_after_write_back(
    db: AsyncSession, book: AudioBook, filepath: str
) -> None:
    """After *this* server rewrites `book`'s own file (a tag write-back),
    keep `file_hash`/`file_size` and every pair's cached transcript
    fingerprint from going stale (issue #533's pattern, applied to the audio
    side of issue #588). Only ever called right after a write-back this same
    call just made, so recomputing here is provenance the server can vouch
    for.
    """
    if not os.path.exists(filepath):
        return
    new_hash = await asyncio.to_thread(hash_file, filepath)
    book.file_hash = new_hash
    book.file_size = os.path.getsize(filepath)
    db.add(book)
    await refresh_transcript_fingerprints(db, book.id, new_hash)
