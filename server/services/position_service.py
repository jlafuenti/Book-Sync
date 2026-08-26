"""
The canonical reading position: one record, one staleness verdict, one
transaction.

A client save used to be two independent PUTs — the bookmark and the progress
row — each with its own staleness check. Either could be accepted while the
other was rejected, leaving two records describing different positions for the
same book with nothing to reconcile them; and the two readers each restored
from a different one. This module is the single write path — every client
writes through `PUT /position/{scope}/{ident}`, and `user_progress` is only a
projection this module maintains.
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Tuple

from sqlalchemy import and_, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from config import settings
from models.book import AudioBook, BookPair, EBook
from models.bookmark import Bookmark, BookmarkLog, BookmarkSource, HintKind, PositionHint
from models.progress import ProgressType, UserProgress
from models.sync_map import SyncMap
from schemas import PositionScope
from utils import utcnow

logger = logging.getLogger(__name__)


class PositionScopeError(ValueError):
    """The scope/id doesn't identify anything."""


# Hints are keyed by device. A client that doesn't send `device_id` still has a
# position worth keeping, so its hints land here rather than being dropped.
# Anonymous writers share this key, and stop sharing the moment the client
# starts identifying itself.
UNATTRIBUTED_DEVICE_ID = "unattributed"


async def _insert_or_reread(db: AsyncSession, row, reread):
    """Insert [row], or hand back whatever a racing transaction inserted first.

    Both position tables now carry partial unique indexes (issue #64), which is
    what makes the duplicate impossible — but it also turns the losing side of a
    concurrent *first write* into an `IntegrityError`. Read-then-insert cannot be
    made race-free in the application, so the insert is attempted inside a
    savepoint and the loser simply re-reads the winner's row and applies on top.
    Without the savepoint the failed flush would poison the whole transaction.

    Returns the row that is actually in the database.
    """
    savepoint = await db.begin_nested()
    try:
        db.add(row)
        await db.flush()
    except IntegrityError:
        # The rollback detaches [row] from the session for us.
        await savepoint.rollback()
        won = await reread()
        if won is None:
            # The conflict wasn't the one we're recovering from; don't swallow it.
            raise
        return won
    return row


@dataclass
class ScopeRef:
    """Resolved target of a position write."""
    scope: PositionScope
    book_pair_id: Optional[int] = None
    ebook_id: Optional[int] = None
    audiobook_id: Optional[int] = None


async def resolve_scope(db: AsyncSession, scope: PositionScope, ident: int) -> ScopeRef:
    """Map a (scope, id) pair onto the media it addresses, 404-ing early.

    A pair's ebook/audiobook ids are carried too, so the derived progress rows
    can be written without a second lookup.
    """
    if scope == PositionScope.PAIR:
        pair = (await db.execute(
            select(BookPair).where(BookPair.id == ident)
        )).scalar_one_or_none()
        if not pair:
            raise PositionScopeError(f"Book pair {ident} not found")
        return ScopeRef(scope, book_pair_id=pair.id,
                        ebook_id=pair.ebook_id, audiobook_id=pair.audiobook_id)

    if scope == PositionScope.EBOOK:
        found = (await db.execute(
            select(EBook.id).where(EBook.id == ident)
        )).scalar_one_or_none()
        if not found:
            raise PositionScopeError(f"EBook {ident} not found")
        return ScopeRef(scope, ebook_id=ident)

    found = (await db.execute(
        select(AudioBook.id).where(AudioBook.id == ident)
    )).scalar_one_or_none()
    if not found:
        raise PositionScopeError(f"AudioBook {ident} not found")
    return ScopeRef(scope, audiobook_id=ident)


def _scope_filter(query, user_id: int, ref: ScopeRef):
    query = query.where(Bookmark.user_id == user_id)
    if ref.scope == PositionScope.PAIR:
        return query.where(Bookmark.book_pair_id == ref.book_pair_id)
    if ref.scope == PositionScope.EBOOK:
        return query.where(Bookmark.book_pair_id.is_(None),
                           Bookmark.ebook_id == ref.ebook_id)
    return query.where(Bookmark.book_pair_id.is_(None),
                       Bookmark.audiobook_id == ref.audiobook_id)


async def read_position(
    db: AsyncSession, user_id: int, ref: ScopeRef, *, refresh: bool = False
) -> Optional[Bookmark]:
    """The canonical record, or None.

    Deliberately never creates. The since-removed `GET /bookmark/{pair}`
    invented a chapter-0 row on a miss, which made "does this user have a
    position?" unanswerable — and a fabricated chapter 0 is indistinguishable
    from a real one at the start of a book.

    [refresh] forces the loaded state to be overwritten from the database. The
    session runs `expire_on_commit=False`, so re-reading an already-loaded
    record otherwise returns it with the `hints` collection as it was *before*
    the write — the PUT response then omitted the hint it had just stored.
    """
    query = _scope_filter(
        select(Bookmark).options(selectinload(Bookmark.hints)), user_id, ref)
    if refresh:
        query = query.execution_options(populate_existing=True)
    return (await db.execute(query)).scalar_one_or_none()


def is_stale(incoming: Optional[datetime], stored: Optional[datetime]) -> bool:
    """Whether [incoming] describes an older moment than what is stored.

    Unknown on either side means no comparison is possible, so the write wins
    — that is what keeps legacy clients (which send no `captured_at`) on
    last-write-wins instead of silently failing.
    """
    if incoming is None or stored is None:
        return False
    return incoming < stored


def _anchor_of(bookmark: Bookmark) -> Tuple:
    """The fields whose movement invalidates a precise hint.

    `audio_position_ms` is deliberately absent: audio drifting on doesn't move
    the page, and a hint carries its own audio anchor for judging that.
    """
    percent = bookmark.epub_progress_percent
    return (
        bookmark.epub_chapter,
        bookmark.epub_sentence_index,
        None if percent is None else round(percent, 2),
    )


async def _upsert_hint(
    db: AsyncSession, bookmark: Bookmark, kind: HintKind, value: str,
    audio_position_ms: Optional[int], device_id: str, captured_at: Optional[datetime],
) -> None:
    """Store a device's precise position against the anchor it belongs to.

    One row per (bookmark, device, kind), so devices never overwrite each
    other's — they used to share a single `bookmarks.epub_locator` column, and
    whoever wrote last destroyed the others' precision.
    """
    existing = (await db.execute(
        select(PositionHint).where(
            PositionHint.bookmark_id == bookmark.id,
            PositionHint.device_id == device_id,
            PositionHint.hint_kind == kind,
        )
    )).scalar_one_or_none()

    if existing is None:
        db.add(PositionHint(
            bookmark_id=bookmark.id, device_id=device_id, hint_kind=kind,
            hint_value=value, anchor_revision=bookmark.anchor_revision,
            audio_position_ms=audio_position_ms, captured_at=captured_at,
            updated_at=utcnow(),
        ))
    else:
        existing.hint_value = value
        existing.anchor_revision = bookmark.anchor_revision
        existing.audio_position_ms = audio_position_ms
        existing.captured_at = captured_at
        existing.updated_at = utcnow()


async def latest_progress_row(
    db: AsyncSession, user_id: int, media_type: ProgressType, media_id: int
) -> Optional[UserProgress]:
    """The newest `user_progress` row for a media item, tolerating duplicates.

    Duplicates are now prevented at the schema level — `ux_user_progress_user_ebook`
    / `_user_audiobook` (issue #64) — and migration 0006 dedupes any a database
    already carries. This stays as the safety net for a database that has not
    been migrated yet: `scalar_one_or_none()` raises `MultipleResultsFound` on
    two rows, which made a book that hit the old race permanently unreadable and
    unwritable. Ordering and taking the newest keeps it working; the extra rows
    are inert.
    """
    id_col = (UserProgress.ebook_id if media_type == ProgressType.EBOOK
              else UserProgress.audiobook_id)
    rows = (await db.execute(
        select(UserProgress)
        .where(
            UserProgress.user_id == user_id,
            UserProgress.media_type == media_type,
            id_col == media_id,
        )
        .order_by(UserProgress.updated_at.desc().nullslast(), UserProgress.id.desc())
    )).scalars().all()
    return rows[0] if rows else None


async def _sync_derived_progress(
    db: AsyncSession, user_id: int, ref: ScopeRef, bookmark: Bookmark
) -> None:
    """Project the canonical record onto `user_progress`.

    `user_progress` still backs the Home/Continue lists, but it is no longer
    written independently by clients — that independence is exactly how the two
    tables came to disagree.
    """
    targets = []
    if ref.ebook_id is not None:
        targets.append((ProgressType.EBOOK, ref.ebook_id))
    if ref.audiobook_id is not None:
        targets.append((ProgressType.AUDIOBOOK, ref.audiobook_id))

    for media_type, media_id in targets:
        row = await latest_progress_row(db, user_id, media_type, media_id)

        if row is None:
            fresh = UserProgress(user_id=user_id, media_type=media_type,
                                 is_completed=False)
            if media_type == ProgressType.EBOOK:
                fresh.ebook_id = media_id
            else:
                fresh.audiobook_id = media_id
            # Same race as the canonical row above, one layer down.
            row = await _insert_or_reread(
                db, fresh,
                lambda mt=media_type, mid=media_id: latest_progress_row(
                    db, user_id, mt, mid),
            )

        row.book_pair_id = ref.book_pair_id
        if media_type == ProgressType.EBOOK:
            row.epub_chapter = bookmark.epub_chapter
            row.epub_progress_percent = bookmark.epub_progress_percent
        else:
            row.audio_position_ms = bookmark.audio_position_ms
        row.is_completed = bookmark.is_completed
        row.captured_at = bookmark.captured_at
        row.device_id = bookmark.device_id
        row.device_name = bookmark.device_name
        row.updated_at = utcnow()


async def _resolve_against_live_map(
    db: AsyncSession, ref: ScopeRef, update, bookmark: Bookmark,
    epub_chapter: Optional[int], epub_sentence_index: int,
    audio_position_ms: Optional[int],
) -> Tuple[Optional[int], Optional[int], Optional[int], Optional[int]]:
    """Decide which version a sentence-index-carrying write is recorded against,
    re-anchoring it when its attested version trails the live map (issue #116).

    Returns `(version_to_stamp, epub_chapter, epub_sentence_index, audio_position_ms)`.

    * No attested version → stamp NULL ("unknown"). Claiming the live version
      for a coordinate nobody vouched for is the false claim that hid the drift.
    * Attested == live → stamp it; the coordinates are taken as sent.
    * Mismatch → resolve the position on the live map from the write's own
      evidence (audio position for an audiobook-sourced write, text preview
      otherwise — the same rule the re-map uses). Success re-expresses the
      coordinates and stamps the live version. Failure keeps the coordinates as
      sent and stamps the *attested* version, so the row visibly trails.

    The map's points are only loaded on the mismatch path; the ordinary write
    costs one `version` lookup, as before.
    """
    # Local import: sync_engine imports this module.
    from services.sync_engine import resolve_on_map

    live_version = (await db.execute(
        select(SyncMap.version).where(SyncMap.book_pair_id == ref.book_pair_id)
    )).scalar_one_or_none()
    attested = update.sync_map_version

    if attested is None:
        return None, epub_chapter, epub_sentence_index, audio_position_ms
    if live_version is None or attested == live_version:
        # No map to check against, or in agreement with it: record the claim.
        return attested, epub_chapter, epub_sentence_index, audio_position_ms

    sync_map = (await db.execute(
        select(SyncMap)
        .options(selectinload(SyncMap.sync_points))
        .where(SyncMap.book_pair_id == ref.book_pair_id)
    )).scalar_one_or_none()
    points = list(sync_map.sync_points) if sync_map else []
    resolved = None
    if points:
        by_audio = sorted(points, key=lambda p: p.audio_start_ms)
        resolved = resolve_on_map(
            update.source or bookmark.source, audio_position_ms,
            update.epub_text_preview, epub_chapter, points, by_audio,
        )

    if resolved is None:
        logger.warning(
            "Position write for pair %s (user %s) attests sync map v%s but the "
            "live map is v%s and it carries no usable anchor — keeping its "
            "coordinates (ch=%s, s=%s) and stamping v%s.",
            ref.book_pair_id, bookmark.user_id, attested, live_version,
            epub_chapter, epub_sentence_index, attested,
        )
        return attested, epub_chapter, epub_sentence_index, audio_position_ms

    new_chapter, new_sentence, new_audio_ms = resolved
    logger.info(
        "Re-anchored a v%s position write for pair %s (user %s) onto sync map "
        "v%s: (ch=%s, s=%s) -> (ch=%s, s=%s).",
        attested, ref.book_pair_id, bookmark.user_id, live_version,
        epub_chapter, epub_sentence_index, new_chapter, new_sentence,
    )
    return (
        live_version, new_chapter, new_sentence,
        new_audio_ms if new_audio_ms is not None else audio_position_ms,
    )


def _in_epub_end_zone(percent: Optional[float]) -> bool:
    return percent is not None and percent >= settings.auto_complete_epub_percent


def _in_audio_end_zone(position_ms: Optional[int], duration_seconds: Optional[int]) -> bool:
    if position_ms is None or duration_seconds is None:
        return False
    tail_ms = settings.auto_complete_audio_tail_seconds * 1000
    return duration_seconds * 1000 - position_ms <= tail_ms


async def _auto_complete(
    db: AsyncSession, ref: ScopeRef, bookmark: Bookmark, update,
    prev_percent: Optional[float], prev_audio_ms: Optional[int],
    wrote_percent: bool, wrote_audio: bool,
) -> None:
    """Mark the book finished when this write *crosses into* the end zone.

    docs/position-sync-contract.md § Completion (issue #56). The rule lives
    here so every client inherits it — the web player, the Android player and
    both readers used to each decide for themselves (or not at all).

    - An explicit `is_completed` on the write always wins; the rule only runs
      when the client said nothing.
    - It never clears the flag: re-reading a chapter is not un-finishing.
    - It fires on the *transition* into the zone, not on being in it, so a
      manual un-finish sticks while the position sits at the end — the next
      heartbeat doesn't undo the user's choice. Leaving and re-entering the
      zone completes the book again.
    - Ebook: the write carried `epub_progress_percent` and the stored value
      went from below `auto_complete_epub_percent` (or unset) to at/above it.
    - Audio: the write carried `audio_position_ms`, the scope has an
      audiobook whose `duration_seconds` is known, and the position went from
      outside the last `auto_complete_audio_tail_seconds` to inside them.
      Unknown length means no end zone; the client's own end-of-stream write
      still completes the book because it sends `is_completed` explicitly.
    """
    if update.is_completed is not None or bookmark.is_completed:
        return

    if wrote_percent and _in_epub_end_zone(bookmark.epub_progress_percent) \
            and not _in_epub_end_zone(prev_percent):
        bookmark.is_completed = True
        return

    if wrote_audio and ref.audiobook_id is not None:
        duration = (await db.execute(
            select(AudioBook.duration_seconds).where(AudioBook.id == ref.audiobook_id)
        )).scalar_one_or_none()
        if _in_audio_end_zone(bookmark.audio_position_ms, duration) \
                and not _in_audio_end_zone(prev_audio_ms, duration):
            bookmark.is_completed = True


async def apply_position(
    db: AsyncSession, user_id: int, ref: ScopeRef, update, *, commit: bool = True
) -> Tuple[Bookmark, bool]:
    """Apply a whole position in one transaction.

    Returns `(record, accepted)`. When `accepted` is False the write was stale
    and **nothing at all** was changed — not the bookmark, not the hints, not
    the derived progress rows. Partial application is the failure mode this
    whole module exists to remove.
    """
    bookmark = await read_position(db, user_id, ref)

    if bookmark is not None and is_stale(update.captured_at, bookmark.captured_at):
        return bookmark, False

    stamped = update.captured_at or utcnow()

    if bookmark is None:
        new_fields = dict(
            user_id=user_id,
            book_pair_id=ref.book_pair_id,
            ebook_id=ref.ebook_id if ref.scope != PositionScope.PAIR else None,
            audiobook_id=ref.audiobook_id if ref.scope != PositionScope.PAIR else None,
            anchor_revision=1,
            is_completed=False,
        )
        # Omitting the kwarg (rather than passing None) lets the column's own
        # default apply — `source` is NOT NULL, so a source-less first write
        # must not hand SQLAlchemy an explicit None to insert.
        if update.source is not None:
            new_fields["source"] = update.source
        # A concurrent first write may already have inserted this row; take
        # theirs and apply on top rather than 500ing on the unique index.
        bookmark = await _insert_or_reread(
            db, Bookmark(**new_fields),
            lambda: read_position(db, user_id, ref, refresh=True),
        )
        # If we lost the race, the row now in hand is the winner's and has never
        # been staleness-checked. A newly inserted row has captured_at=None, so
        # this is a no-op on the ordinary path.
        if is_stale(update.captured_at, bookmark.captured_at):
            return bookmark, False

    before_anchor = _anchor_of(bookmark)
    prev = (bookmark.epub_chapter, bookmark.epub_sentence_index,
            bookmark.audio_position_ms)
    prev_percent = bookmark.epub_progress_percent

    # Omission means "leave alone". A write carrying no anchor — a completion
    # toggle, say — must never blank one out. `source` follows the same rule:
    # only a foreground, user-initiated write claims the format, so a
    # background save (service teardown, Android Auto heartbeat) that omits
    # it must not re-stamp the stored value.
    if update.source is not None:
        bookmark.source = update.source

    # The coordinates this write lands with. A sentence index is a sync-map
    # coordinate, so a write whose attested `sync_map_version` trails the live
    # map is re-expressed in the live map's terms first (issue #116) — the
    # remaining fields below are then applied from these resolved values.
    epub_chapter = update.epub_chapter
    epub_sentence_index = update.epub_sentence_index
    audio_position_ms = update.audio_position_ms
    stamp_version = False
    version_to_stamp: Optional[int] = None
    if update.epub_sentence_index is not None and ref.scope == PositionScope.PAIR:
        # Writes carrying no sentence index — a completion toggle, an audio
        # heartbeat — establish no coordinate and must not claim a version.
        stamp_version = True
        version_to_stamp, epub_chapter, epub_sentence_index, audio_position_ms = (
            await _resolve_against_live_map(
                db, ref, update, bookmark,
                epub_chapter, epub_sentence_index, audio_position_ms,
            )
        )

    if epub_chapter is not None:
        bookmark.epub_chapter = epub_chapter
    if epub_sentence_index is not None:
        bookmark.epub_sentence_index = epub_sentence_index
    if stamp_version:
        bookmark.sync_map_version = version_to_stamp
    if update.epub_text_preview is not None:
        bookmark.epub_text_preview = update.epub_text_preview
    if update.epub_progress_percent is not None:
        bookmark.epub_progress_percent = update.epub_progress_percent
    if audio_position_ms is not None:
        bookmark.audio_position_ms = audio_position_ms
    if update.is_completed is not None:
        bookmark.is_completed = update.is_completed
    await _auto_complete(
        db, ref, bookmark, update, prev_percent, prev[2],
        wrote_percent=update.epub_progress_percent is not None,
        wrote_audio=audio_position_ms is not None,
    )

    if update.device_id is not None:
        bookmark.device_id = update.device_id
    if update.device_name is not None:
        bookmark.device_name = update.device_name
    bookmark.captured_at = stamped
    bookmark.updated_at = utcnow()
    bookmark.synced_at = utcnow()

    # Bump before storing the hint, so the hint records the anchor it actually
    # belongs to. That tag is the entire staleness mechanism.
    if _anchor_of(bookmark) != before_anchor:
        bookmark.anchor_revision = (bookmark.anchor_revision or 0) + 1

    if update.hint is not None:
        await _upsert_hint(
            db, bookmark, update.hint.kind, update.hint.value,
            update.hint.audio_position_ms,
            bookmark.device_id or UNATTRIBUTED_DEVICE_ID, stamped,
        )

    position_changed = prev != (bookmark.epub_chapter, bookmark.epub_sentence_index,
                                bookmark.audio_position_ms)
    if position_changed and update.append_to_log:
        # `bookmark.source`, not `update.source`: the update's source is
        # optional and may be None on a background write (the very write a
        # session-boundary log entry is likely to accompany), but the log's
        # `source` column is NOT NULL. The bookmark's resolved value is never
        # None once any write has landed.
        db.add(BookmarkLog(
            bookmark_id=bookmark.id, source=bookmark.source,
            prev_epub_chapter=prev[0], prev_epub_sentence_index=prev[1],
            prev_audio_position_ms=prev[2],
            new_epub_chapter=bookmark.epub_chapter,
            new_epub_sentence_index=bookmark.epub_sentence_index,
            new_audio_position_ms=bookmark.audio_position_ms,
            device_id=bookmark.device_id, device_name=bookmark.device_name,
            captured_at=stamped,
        ))

    await db.flush()
    await _sync_derived_progress(db, user_id, ref, bookmark)

    if commit:
        await db.commit()
        # Re-read rather than refresh: `hints` must come back eagerly loaded,
        # or serialising the response lazy-loads outside the async context.
        # populate_existing, or the identity map returns the pre-write state.
        reloaded = await read_position(db, user_id, ref, refresh=True)
        if reloaded is not None:
            return reloaded, True
    return bookmark, True


def _beats(challenger: Optional[datetime], champion: Optional[datetime]) -> bool:
    """Whether [challenger]'s `captured_at` is strictly newer than [champion]'s.

    None counts as oldest: a row nobody ever stamped loses to any stamped row,
    and a tie (including None vs None) goes to the champion.
    """
    if challenger is None:
        return False
    return champion is None or challenger > champion


async def _claim_standalone_scope(
    db: AsyncSession, bookmark: Bookmark,
    ebook_id: Optional[int], audiobook_id: Optional[int],
    *, clear_sync_map_version: bool = True,
    extra_values: Optional[dict] = None,
) -> None:
    """Move [bookmark] onto the standalone media scopes.

    Sets `book_pair_id = NULL` and the given media ids. A pre-existing
    standalone row for the same (user, medium) collides on the partial unique
    indexes (`ux_bookmarks_user_ebook` / `_user_audiobook`); read-then-write
    cannot be made race-free, so — like `_insert_or_reread` — the write is
    attempted inside a savepoint and the conflict is resolved only when the
    index actually reports it. Each contested medium goes to whichever row has
    the newer `captured_at` (None counts as oldest); the loser is deleted, and
    the moving row is deleted outright when it ends up claiming no medium.

    [extra_values] are further column values to set on the moving row. They go
    through the same path as the scope itself because a savepoint rollback
    reverts *every* pending change on the instance, so anything set outside it
    would silently vanish on the conflict path. Pass finished values, never
    ones derived from reading the row back: after a rollback the instance is
    expired, and reading it lazy-loads outside the async context.
    """
    if ebook_id is None and audiobook_id is None:
        await db.delete(bookmark)
        await db.flush()
        return

    # Snapshot before the savepoint attempt: a rollback expires the instance,
    # and reading an expired attribute lazy-loads outside the async context.
    bookmark_id = bookmark.id
    user_id = bookmark.user_id
    captured_at = bookmark.captured_at

    def _apply(eb_id: Optional[int], ab_id: Optional[int]) -> None:
        bookmark.book_pair_id = None
        bookmark.ebook_id = eb_id
        bookmark.audiobook_id = ab_id
        if clear_sync_map_version:
            bookmark.sync_map_version = None
        for field, value in (extra_values or {}).items():
            setattr(bookmark, field, value)

    savepoint = await db.begin_nested()
    try:
        _apply(ebook_id, audiobook_id)
        await db.flush()
        return
    except IntegrityError:
        # The rollback restores the row's stored scope for us.
        await savepoint.rollback()

    async def _incumbent(id_col, media_id: int) -> Optional[Bookmark]:
        return (await db.execute(
            select(Bookmark)
            .options(selectinload(Bookmark.hints))
            .where(
                Bookmark.user_id == user_id,
                Bookmark.book_pair_id.is_(None),
                id_col == media_id,
                Bookmark.id != bookmark_id,
            )
        )).scalar_one_or_none()

    keep_ebook_id, keep_audiobook_id = ebook_id, audiobook_id
    if ebook_id is not None:
        other = await _incumbent(Bookmark.ebook_id, ebook_id)
        if other is not None:
            if _beats(other.captured_at, captured_at):
                keep_ebook_id = None
            else:
                await db.delete(other)
    if audiobook_id is not None:
        other = await _incumbent(Bookmark.audiobook_id, audiobook_id)
        if other is not None:
            if _beats(other.captured_at, captured_at):
                keep_audiobook_id = None
            else:
                await db.delete(other)
    await db.flush()

    if keep_ebook_id is None and keep_audiobook_id is None:
        # A newer standalone row exists for every medium this row could have
        # claimed — those rows *are* the position now.
        await db.delete(bookmark)
    else:
        _apply(keep_ebook_id, keep_audiobook_id)
    await db.flush()


async def demote_pair_positions(
    db: AsyncSession, pair_id: int,
    *, keep_ebook: bool = True, keep_audiobook: bool = True,
) -> None:
    """Re-scope every user's pair-scoped position to the standalone media
    before the pair row is deleted (issue #155).

    Unpairing is the normal way to correct a mis-matched pair; the canonical
    `Bookmark` rows used to die with it via the ORM cascade, wiping every
    user's position in *both* books. Instead, each pair-scoped bookmark is
    demoted (`book_pair_id = NULL`, media ids set to the surviving rows) so
    `GET /position/ebook/...` and `/audiobook/...` keep answering. The caller
    that is also deleting one medium passes `keep_ebook=False` /
    `keep_audiobook=False` so the demoted row never references the dead id.

    `sync_map_version` is cleared: the sentence index is a sync-map coordinate
    and the map dies with the pair. Chapter, percent, audio position, hints and
    logs are all kept — same media, still valid. `user_progress` rows are keyed
    by media and remain the projection of the demoted bookmark, so they are
    unlinked from the pair rather than deleted.

    Runs in the caller's transaction (flushes, never commits). Call it before
    deleting the pair — the demoted rows must be flushed out of the pair's
    cascade before the ORM collects it.
    """
    pair = (await db.execute(
        select(BookPair).where(BookPair.id == pair_id)
    )).scalar_one_or_none()
    if pair is None:
        return
    target_ebook_id = pair.ebook_id if keep_ebook else None
    target_audiobook_id = pair.audiobook_id if keep_audiobook else None

    rows = (await db.execute(
        select(Bookmark)
        .options(selectinload(Bookmark.hints))
        .where(Bookmark.book_pair_id == pair_id)
    )).scalars().all()
    for bookmark in rows:
        await _claim_standalone_scope(
            db, bookmark, target_ebook_id, target_audiobook_id)

    await db.execute(
        update(UserProgress)
        .where(UserProgress.book_pair_id == pair_id)
        .values(book_pair_id=None)
    )
    await db.flush()


async def invalidate_parse_coordinates_for_ebook(db: AsyncSession, ebook_id: int) -> int:
    """Drop the coordinates that only meant something in an ebook's *old* file
    (issue #303).

    `POST /troubleshoot/replace/ebook/{id}` swaps the file behind an existing
    `EBook` row and keeps the row id, so every position still points at it —
    but the new file is a different parse. This is the same treatment a
    conversion gives a position (`repoint_standalone_positions_to_ebook`,
    issue #298), minus the change of id: the book is the same, the artifact
    describing it is not.

    Every position referencing the ebook — standalone rows, and the
    pair-scoped rows of every pair containing it — has:

    * `epub_sentence_index` and `sync_map_version` **cleared**: both are
      coordinates of the outgoing parse and the map built from it, and on the
      new file the same index silently names different text.
    * `anchor_revision` **bumped**, which marks every hint of the row stale
      without deleting one. A Readium locator or an epub.js CFI addresses the
      DOM of the file it was captured in, and that file is gone. Deleting them
      is the failure the contract warns about — a client that finds no hint
      reads it as "no position" and writes chapter 0 over a real one.

    What survives: `epub_chapter`, `epub_progress_percent`,
    `epub_text_preview`, the audio side, and `captured_at`. A chapter/percent
    anchor is roughly right across a re-parse, and the reader's whole-spine
    text search lands the preview precisely. `captured_at` is untouched for the
    same reason the re-map leaves it alone — this is a server-side
    invalidation, not a device capture, and stamping it would let it beat a
    genuinely newer write from a phone.

    `user_progress` is not re-projected: it carries chapter, percent and the
    completion flag, none of which this touches.

    Runs in the caller's transaction (flushes, never commits). Returns the
    number of rows invalidated.
    """
    pair_ids = (await db.execute(
        select(BookPair.id).where(BookPair.ebook_id == ebook_id)
    )).scalars().all()

    scopes = [and_(Bookmark.book_pair_id.is_(None), Bookmark.ebook_id == ebook_id)]
    if pair_ids:
        scopes.append(Bookmark.book_pair_id.in_(pair_ids))

    rows = (await db.execute(
        select(Bookmark).where(or_(*scopes))
    )).scalars().all()

    for bookmark in rows:
        bookmark.epub_sentence_index = None
        bookmark.sync_map_version = None
        bookmark.anchor_revision = (bookmark.anchor_revision or 0) + 1
        bookmark.updated_at = utcnow()

    await db.flush()
    if rows:
        logger.info(
            "Ebook %s got a new file: invalidated the parse coordinates of %s "
            "position(s) and marked their hints stale.", ebook_id, len(rows),
        )
    return len(rows)


async def _repoint_ebook_progress(
    db: AsyncSession, old_ebook_id: int, new_ebook_id: int
) -> None:
    """Move the `user_progress` ebook projection onto [new_ebook_id].

    Same collision rule as the canonical row it projects — `user_progress`
    carries its own partial unique index (`ux_user_progress_user_ebook`), and
    the winner is the newer `captured_at`, which is copied from the bookmark
    and so agrees with the decision made there.

    Every row naming the ebook moves, not just the `EBOOK`-typed ones: the
    source row is about to be deleted, and a stray row of another type still
    holding the id would dangle its foreign key. Only an `EBOOK` row can
    collide — the index is partial on that type.
    """
    rows = (await db.execute(
        select(UserProgress).where(UserProgress.ebook_id == old_ebook_id)
    )).scalars().all()

    for row in rows:
        # Snapshot before the savepoint: a rollback expires the instance.
        user_id, captured_at = row.user_id, row.captured_at

        savepoint = await db.begin_nested()
        try:
            row.ebook_id = new_ebook_id
            await db.flush()
            continue
        except IntegrityError:
            # The rollback restores the row's stored ebook id for us.
            await savepoint.rollback()

        incumbent = await latest_progress_row(
            db, user_id, ProgressType.EBOOK, new_ebook_id)
        if incumbent is not None and _beats(incumbent.captured_at, captured_at):
            await db.delete(row)
        else:
            if incumbent is not None:
                await db.delete(incumbent)
                await db.flush()
            row.ebook_id = new_ebook_id
        await db.flush()


async def repoint_standalone_positions_to_ebook(
    db: AsyncSession, *, old_ebook_id: int, new_ebook_id: int
) -> None:
    """Move standalone positions on one ebook onto its replacement (issue #298).

    Converting an unsupported ebook registers the converted EPUB as a *new*
    `EBook` row and deletes the source. A pair is re-pointed at the new row, so
    pair-scoped positions travel with it — but standalone rows referencing the
    source used to go to `release_standalone_positions`, which drops the id and
    deletes any row left describing nothing. That treats a conversion like a
    deletion, when in fact the converted EPUB *is* the same book.

    What survives, and why:

    * `epub_chapter`, `epub_progress_percent`, `epub_text_preview`, hints,
      `captured_at`, the audiobook side — a chapter/percent anchor is roughly
      right across a conversion, and the reader's restore ladder does the fine
      landing from the preview.
    * `epub_sentence_index` and `sync_map_version` are cleared: both are
      coordinates of the *old* parse and its map, and would silently name
      different text on the new one.
    * `anchor_revision` is bumped, which marks every hint of the row stale
      without deleting one. A Readium locator or epub.js CFI addresses the DOM
      of the file it was captured in; the converted EPUB is a different file.
      Deleting them instead is the failure the contract warns about — a client
      that finds no hint reads it as "no position" and writes chapter 0 over a
      real one.

    Collisions with a position already held on the replacement resolve exactly
    as a demotion does: newer `captured_at` wins (None counts as oldest), the
    loser is deleted. `user_progress` follows the same way.

    Runs in the caller's transaction (flushes, never commits).
    """
    if old_ebook_id == new_ebook_id:
        return

    rows = (await db.execute(
        select(Bookmark)
        .options(selectinload(Bookmark.hints))
        .where(Bookmark.book_pair_id.is_(None), Bookmark.ebook_id == old_ebook_id)
    )).scalars().all()
    for bookmark in rows:
        await _claim_standalone_scope(
            db, bookmark, new_ebook_id, bookmark.audiobook_id,
            extra_values={
                "epub_sentence_index": None,
                "anchor_revision": (bookmark.anchor_revision or 0) + 1,
            },
        )

    await _repoint_ebook_progress(db, old_ebook_id, new_ebook_id)
    await db.flush()


async def release_standalone_positions(
    db: AsyncSession, *,
    ebook_id: Optional[int] = None, audiobook_id: Optional[int] = None,
) -> None:
    """Detach standalone bookmark rows from a medium that is being deleted.

    A demoted row can reference both media at once; when one of them is
    deleted the row must let go of that id (the FK would otherwise block the
    delete on Postgres) while keeping the surviving medium's position. A row
    that references *only* the dying medium has nothing left to describe and
    is removed with its hints and logs.

    Runs in the caller's transaction (flushes, never commits).
    """
    for id_col, dying_id, other_col in (
        (Bookmark.ebook_id, ebook_id, "audiobook_id"),
        (Bookmark.audiobook_id, audiobook_id, "ebook_id"),
    ):
        if dying_id is None:
            continue
        rows = (await db.execute(
            select(Bookmark)
            .options(selectinload(Bookmark.hints))
            .where(Bookmark.book_pair_id.is_(None), id_col == dying_id)
        )).scalars().all()
        for row in rows:
            if getattr(row, other_col) is not None:
                setattr(row, id_col.key, None)
            else:
                await db.delete(row)
    await db.flush()


def to_response_dict(bookmark: Bookmark, ref: ScopeRef) -> dict:
    """Serialise the canonical record, marking each hint current or stale."""
    return {
        "scope": ref.scope,
        "book_pair_id": bookmark.book_pair_id,
        "ebook_id": ref.ebook_id,
        "audiobook_id": ref.audiobook_id,
        "source": bookmark.source,
        "anchor_revision": bookmark.anchor_revision,
        "epub_chapter": bookmark.epub_chapter,
        "epub_sentence_index": bookmark.epub_sentence_index,
        "sync_map_version": bookmark.sync_map_version,
        "epub_text_preview": bookmark.epub_text_preview,
        "epub_progress_percent": bookmark.epub_progress_percent,
        "audio_position_ms": bookmark.audio_position_ms,
        "is_completed": bookmark.is_completed,
        "captured_at": bookmark.captured_at,
        "updated_at": bookmark.updated_at,
        "device_id": bookmark.device_id,
        "device_name": bookmark.device_name,
        "hints": [
            {
                "kind": h.hint_kind,
                "device_id": h.device_id,
                "value": h.hint_value,
                "anchor_revision": h.anchor_revision,
                "audio_position_ms": h.audio_position_ms,
                "current": h.anchor_revision == bookmark.anchor_revision,
            }
            for h in sorted(bookmark.hints, key=lambda h: h.id)
        ],
    }
