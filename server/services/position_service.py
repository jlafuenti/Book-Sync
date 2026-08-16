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

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

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
