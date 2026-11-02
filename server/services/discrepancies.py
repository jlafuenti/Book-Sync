"""
Pair metadata discrepancies: which fields an ebook and its audiobook disagree
on, and how a resolution or an ignore list is applied.

Split out of `routers/library.py` (issue #255). The router keeps the three
endpoints -- `GET /pairs-discrepancies`, `POST /pairs/{id}/resolve-discrepancies`,
`POST /pairs/{id}/ignore-discrepancies` -- with their lookups, 404s, the
commit-before-write-back ordering of resolve (issue #259) and the on-disk tag
write-back itself; this module holds the comparison and the coercions:

* `FIELDS_TO_COMPARE`: the pair-comparison field list (issue #257);
* `_pair_has_discrepancies`: the auto-acknowledge test;
* `find_discrepancies_impl`: the body of the listing;
* `apply_resolution`: resolve's per-field coercion and assignment, returning
  which halves changed so the router knows what to commit and write back;
* `ignore_fields_impl`: the ignore list update and its auto-acknowledge.

Behaviour-preserving move: `_pair_has_discrepancies` and the listing still
compare the same way in two copies, as they did in the router.
"""

from typing import List

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from models.book import BookPair
from schemas import DiscrepantField, MetadataDiscrepancy, ResolveDiscrepancyRequest
from services.metadata_utils import (
    MEDIA_CORE_FIELDS,
    MEDIA_DESCRIPTIVE_FIELDS,
    MEDIA_FLAG_FIELDS,
)
from services.series import entries_of, format_series, parse_series, set_series


# The fields a pair's two halves are compared on. Deliberately *not*
# `MEDIA_METADATA_FIELDS`: the identifiers are dropped (an ebook and its
# audiobook legitimately carry different ISBNs/ASINs, and the narrator is
# audiobook-only), and `cover_path` is added. Derived from the shared groups
# rather than retyped, so a new metadata field lands here too (issue #257).
#
# `series` is a virtual field (issue #905): a book can belong to several series,
# so the two halves are compared as whole lists, through their canonical string
# (`Name #4; Other #2.5`), and a resolution writes the chosen string back as a
# list. Order counts — entry 0 is the primary series. There is no separate
# `series_index` any more; an index is part of its entry.
FIELDS_TO_COMPARE = list(
    MEDIA_CORE_FIELDS + ("series",) + MEDIA_DESCRIPTIVE_FIELDS + MEDIA_FLAG_FIELDS
    + ("cover_path",)
)


def _compared_value(media, field: str):
    """One half's value for `field`, normalised for comparison.

    The empty string reads as None. `series` is the canonical string of the
    half's list, `""` (no series) again None.
    """
    if field == "series":
        return format_series(entries_of(media)) or None
    value = getattr(media, field)
    return None if value == "" else value


def _pair_has_discrepancies(pair: BookPair) -> bool:
    """Return True if a pair still has un-ignored metadata mismatches."""
    if not pair.ebook or not pair.audiobook:
        return False
    ignored = set(pair.ignored_fields or [])
    for field in FIELDS_TO_COMPARE:
        if field in ignored:
            continue
        if _compared_value(pair.ebook, field) != _compared_value(pair.audiobook, field):
            return True
    return False


async def find_discrepancies_impl(db: AsyncSession) -> List[MetadataDiscrepancy]:
    """Body of `GET /pairs-discrepancies`: every pair whose halves still disagree."""
    result = await db.execute(
        select(BookPair)
        .options(
            selectinload(BookPair.ebook),
            selectinload(BookPair.audiobook)
        )
    )
    pairs = result.scalars().all()
    
    discrepancies: List[MetadataDiscrepancy] = []
    
    for pair in pairs:
        if not pair.ebook or not pair.audiobook:
            continue
            
        diffs = []
        ignored = set(pair.ignored_fields or [])
        for field in FIELDS_TO_COMPARE:
            if field in ignored:
                continue
            # Empty strings read as None; series compare as canonical strings.
            ebook_val = _compared_value(pair.ebook, field)
            audio_val = _compared_value(pair.audiobook, field)

            if ebook_val != audio_val:
                diffs.append(DiscrepantField(
                    field=field,
                    ebook_value=str(ebook_val) if ebook_val is not None else None,
                    audiobook_value=str(audio_val) if audio_val is not None else None
                ))
                
        if diffs:
            discrepancies.append(MetadataDiscrepancy(
                pair_id=pair.id,
                ebook_id=pair.ebook.id,
                audiobook_id=pair.audiobook.id,
                title=pair.ebook.title or pair.audiobook.title or "Unknown",
                discrepancies=diffs
            ))
            
    return discrepancies


def _assign(media, field: str, value) -> None:
    """Set one compared `field` on a half from the request's loosely-typed value."""
    if field == "series":
        set_series(media, parse_series(value) if value else [], user_edit=True)
        return
    if field == "publish_year" and value is not None:
        value = int(value)
    elif field in ["is_explicit", "is_abridged"] and value is not None:
        value = str(value).lower() in ("true", "1")
    setattr(media, field, value)


def apply_resolution(pair: BookPair, req: ResolveDiscrepancyRequest) -> tuple[bool, bool]:
    """Apply `req` to the pair's two halves in memory; returns (ebook_changed, audio_changed).

    Coerces `publish_year` and the flag fields from the request's strings,
    turns a `series` string back into entries (`None` clears them, as a user
    edit), ignores fields outside `FIELDS_TO_COMPARE`, and auto-acknowledges the
    pair once nothing is left to resolve. The caller commits and writes the
    files back.
    """
    ebook = pair.ebook
    audiobook = pair.audiobook

    # Process EBook updates
    ebook_changed = False
    for field, value in req.ebook_updates.items():
        if field in FIELDS_TO_COMPARE:
            _assign(ebook, field, value)
            ebook_changed = True
            
    # Process AudioBook updates
    audio_changed = False
    for field, value in req.audiobook_updates.items():
        if field in FIELDS_TO_COMPARE:
            _assign(audiobook, field, value)
            audio_changed = True
            
    if ebook_changed or audio_changed:
        # Auto-acknowledge pair if all discrepancies are now resolved
        if not _pair_has_discrepancies(pair):
            pair.acknowledged = True

    return ebook_changed, audio_changed


async def ignore_fields_impl(db: AsyncSession, pair: BookPair, fields: List[str]) -> None:
    """Body of `POST /pairs/{id}/ignore-discrepancies` after the lookup: extend the
    ignore list, auto-acknowledge if nothing is left, commit."""
    existing = set(pair.ignored_fields or [])
    existing.update(fields)
    pair.ignored_fields = list(existing)

    # Need ebook/audiobook loaded to check discrepancies
    result = await db.execute(
        select(BookPair)
        .options(selectinload(BookPair.ebook), selectinload(BookPair.audiobook))
        .where(BookPair.id == pair.id)
    )
    loaded_pair = result.scalar_one_or_none()
    if loaded_pair and not _pair_has_discrepancies(loaded_pair):
        loaded_pair.acknowledged = True

    await db.commit()
