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


# The fields a pair's two halves are compared on. Deliberately *not*
# `MEDIA_METADATA_FIELDS`: the identifiers are dropped (an ebook and its
# audiobook legitimately carry different ISBNs/ASINs, and the narrator is
# audiobook-only), and `cover_path` is added. Derived from the shared groups
# rather than retyped, so a new metadata field lands here too (issue #257).
FIELDS_TO_COMPARE = list(
    MEDIA_CORE_FIELDS + MEDIA_DESCRIPTIVE_FIELDS + MEDIA_FLAG_FIELDS + ("cover_path",)
)


def _pair_has_discrepancies(pair: BookPair) -> bool:
    """Return True if a pair still has un-ignored metadata mismatches."""
    if not pair.ebook or not pair.audiobook:
        return False
    ignored = set(pair.ignored_fields or [])
    for field in FIELDS_TO_COMPARE:
        if field in ignored:
            continue
        ev = getattr(pair.ebook, field)
        av = getattr(pair.audiobook, field)
        if ev == "":
            ev = None
        if av == "":
            av = None
        if field == "series_index":
            if ev is not None:
                ev = float(ev)
            if av is not None:
                av = float(av)
        if ev != av:
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
            ebook_val = getattr(pair.ebook, field)
            audio_val = getattr(pair.audiobook, field)
            
            # Normalize empty strings to None for comparison
            if ebook_val == "": ebook_val = None
            if audio_val == "": audio_val = None
            
            # Format numbers to avoid float vs int mismatches
            if field == "series_index":
                if ebook_val is not None: ebook_val = float(ebook_val)
                if audio_val is not None: audio_val = float(audio_val)
                
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


def apply_resolution(pair: BookPair, req: ResolveDiscrepancyRequest) -> tuple[bool, bool]:
    """Apply `req` to the pair's two halves in memory; returns (ebook_changed, audio_changed).

    Coerces `series_index`, `publish_year` and the flag fields from the
    request's strings, ignores fields outside `FIELDS_TO_COMPARE`, and
    auto-acknowledges the pair once nothing is left to resolve. The caller
    commits and writes the files back.
    """
    ebook = pair.ebook
    audiobook = pair.audiobook

    # Process EBook updates
    ebook_changed = False
    for field, value in req.ebook_updates.items():
        if field in FIELDS_TO_COMPARE:
            # Handle type conversions
            if field == "series_index" and value is not None:
                value = float(value)
            elif field == "publish_year" and value is not None:
                value = int(value)
            elif field in ["is_explicit", "is_abridged"] and value is not None:
                value = str(value).lower() in ("true", "1")
                
            setattr(ebook, field, value)
            ebook_changed = True
            
    # Process AudioBook updates
    audio_changed = False
    for field, value in req.audiobook_updates.items():
        if field in FIELDS_TO_COMPARE:
            # Handle type conversions
            if field == "series_index" and value is not None:
                value = float(value)
            elif field == "publish_year" and value is not None:
                value = int(value)
            elif field in ["is_explicit", "is_abridged"] and value is not None:
                value = str(value).lower() in ("true", "1")
                
            setattr(audiobook, field, value)
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
