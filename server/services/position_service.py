"""
The canonical reading position: one record, one staleness verdict, one
transaction.

A client save used to be two independent PUTs — the bookmark and the progress
row — each with its own staleness check. Either could be accepted while the
other was rejected, leaving two records describing different positions for the
same book with nothing to reconcile them; and the two readers each restored
from a different one. This module is the single write path. The legacy
endpoints are adapters over it, so an old app build and a new one converge on
the same row instead of fighting over two.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from models.book import AudioBook, BookPair, EBook
from models.bookmark import Bookmark, BookmarkLog, BookmarkSource, HintKind, PositionHint
from models.progress import ProgressType, UserProgress
from schemas import PositionScope


class PositionScopeError(ValueError):
    """The scope/id doesn't identify anything."""


# Hints are keyed by device. A client old enough not to send `device_id` still
# has a position worth keeping, so its hints land here rather than being
# dropped. Anonymous writers share this key — no worse than the single shared
# locator column they were written against, and they stop sharing the moment
# the client starts identifying itself.
UNATTRIBUTED_DEVICE_ID = "unattributed"


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
    db: AsyncSession, user_id: int, ref: ScopeRef
) -> Optional[Bookmark]:
    """The canonical record, or None.

    Deliberately never creates. The old `GET /bookmark/{pair}` invented a
    chapter-0 row on a miss, which made "does this user have a position?"
    unanswerable — and a fabricated chapter 0 is indistinguishable from a real
    one at the start of a book.
    """
    query = _scope_filter(
        select(Bookmark).options(selectinload(Bookmark.hints)), user_id, ref)
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
    other's — they used to share a single column, and whoever wrote last
    destroyed the others' precision.
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
            updated_at=datetime.utcnow(),
        ))
    else:
        existing.hint_value = value
        existing.anchor_revision = bookmark.anchor_revision
        existing.audio_position_ms = audio_position_ms
        existing.captured_at = captured_at
        existing.updated_at = datetime.utcnow()


async def latest_progress_row(
    db: AsyncSession, user_id: int, media_type: ProgressType, media_id: int
) -> Optional[UserProgress]:
    """The newest `user_progress` row for a media item, tolerating duplicates.

    `user_progress` has no unique constraint, and the old `GET /progress`
    created rows with flush-but-no-commit — two concurrent reads could leave
    two rows for the same media. `scalar_one_or_none()` raises
    `MultipleResultsFound` on those, so a book that hit the race became
    unwritable. Ordering and taking the newest keeps it working; the extra
    rows are inert.
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
            row = UserProgress(user_id=user_id, media_type=media_type,
                               is_completed=False)
            if media_type == ProgressType.EBOOK:
                row.ebook_id = media_id
            else:
                row.audiobook_id = media_id
            db.add(row)

        row.book_pair_id = ref.book_pair_id
        if media_type == ProgressType.EBOOK:
            row.epub_chapter = bookmark.epub_chapter
            row.epub_progress_percent = bookmark.epub_progress_percent
            # epub_cfi stays a mirror of the web hint; see _mirror_legacy_columns.
        else:
            row.audio_position_ms = bookmark.audio_position_ms
        row.is_completed = bookmark.is_completed
        row.captured_at = bookmark.captured_at
        row.device_id = bookmark.device_id
        row.device_name = bookmark.device_name
        row.updated_at = datetime.utcnow()


async def _mirror_legacy_columns(db: AsyncSession, bookmark: Bookmark, ref: ScopeRef,
                                 user_id: int) -> None:
    """Keep `bookmarks.epub_locator` / `user_progress.epub_cfi` in step with the
    current-anchor hints, so app builds that predate `position_hints` still
    resume. Dropped once no old build is in the field.
    """
    hints = (await db.execute(
        select(PositionHint).where(PositionHint.bookmark_id == bookmark.id)
    )).scalars().all()
    current = [h for h in hints if h.anchor_revision == bookmark.anchor_revision]

    locator = next((h for h in current if h.hint_kind == HintKind.READIUM_LOCATOR), None)
    if locator is not None:
        bookmark.epub_locator = locator.hint_value
        bookmark.locator_audio_ms = locator.audio_position_ms

    cfi = next((h for h in current if h.hint_kind == HintKind.EPUBJS_CFI), None)
    if cfi is not None and ref.ebook_id is not None:
        row = await latest_progress_row(
            db, user_id, ProgressType.EBOOK, ref.ebook_id)
        if row is not None:
            row.epub_cfi = cfi.hint_value


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

    stamped = update.captured_at or datetime.utcnow()

    if bookmark is None:
        bookmark = Bookmark(
            user_id=user_id,
            book_pair_id=ref.book_pair_id,
            ebook_id=ref.ebook_id if ref.scope != PositionScope.PAIR else None,
            audiobook_id=ref.audiobook_id if ref.scope != PositionScope.PAIR else None,
            source=update.source,
            anchor_revision=1,
            is_completed=False,
        )
        db.add(bookmark)
        await db.flush()

    before_anchor = _anchor_of(bookmark)
    prev = (bookmark.epub_chapter, bookmark.epub_sentence_index,
            bookmark.audio_position_ms)

    # Omission means "leave alone". A write carrying no anchor — a completion
    # toggle, say — must never blank one out.
    bookmark.source = update.source
    if update.epub_chapter is not None:
        bookmark.epub_chapter = update.epub_chapter
    if update.epub_sentence_index is not None:
        bookmark.epub_sentence_index = update.epub_sentence_index
    if update.epub_text_preview is not None:
        bookmark.epub_text_preview = update.epub_text_preview
    if update.epub_progress_percent is not None:
        bookmark.epub_progress_percent = update.epub_progress_percent
    if update.audio_position_ms is not None:
        bookmark.audio_position_ms = update.audio_position_ms
    if update.is_completed is not None:
        bookmark.is_completed = update.is_completed

    if update.device_id is not None:
        bookmark.device_id = update.device_id
    if update.device_name is not None:
        bookmark.device_name = update.device_name
    bookmark.captured_at = stamped
    bookmark.updated_at = datetime.utcnow()
    bookmark.synced_at = datetime.utcnow()

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
        db.add(BookmarkLog(
            bookmark_id=bookmark.id, source=update.source,
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
    await _mirror_legacy_columns(db, bookmark, ref, user_id)

    if commit:
        await db.commit()
        # Re-read rather than refresh: `hints` must come back eagerly loaded,
        # or serialising the response lazy-loads outside the async context.
        reloaded = await read_position(db, user_id, ref)
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
