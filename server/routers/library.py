"""
Library router: manage ebooks, audiobooks, and book pairs.
Includes scanning directories, uploading files, and auto-matching.

**Transactions.** `get_db` commits once, after the handler returns; handlers do
not need to. The `await db.commit()` calls that remain here are of two kinds,
and only one of them is load-bearing -- every deliberate one carries a comment
saying what it is protecting. Read docs/request-transactions.md before adding
another, and before deleting one that looks redundant (issue #259).
"""

import os
import hashlib
import logging
import subprocess
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple, Any

import asyncio
import contextlib
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, status, Query
from pydantic import BaseModel
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ebooklib import epub
from markdownify import markdownify as md

from database import get_db
from config import settings
from models.settings import SystemSetting
from models.user import User
from models.book import EBook, AudioBook, BookPair, PairStatus
from models.transcription_queue import TranscriptionQueueItem
from models.progress import UserProgress
from models.transcript import AudioTranscript
from schemas import (
    EBookResponse, AudioBookResponse, BookPairResponse,
    BookPairCreate, LibraryScanResponse, SearchResponse,
    EBookDetailResponse, AudioBookDetailResponse,
    MetadataDiscrepancy, ResolveDiscrepancyRequest, IgnoreDiscrepancyRequest,
    NewItemsResponse, AcknowledgeItemsRequest, AcknowledgePairsRequest, Page,
    LibraryItem, LibraryItemKind, LibraryTab, LibrarySort, SortDir,
    LibraryFacets, LibraryCounts, FacetCount,
    CalibreStatusResponse, CleanupResponse, MessageResponse, VerifyFilesResponse,
)
from rate_limit import expensive_reads, search_reads
from routers.auth import get_current_user, get_editor_user, rate_limited
from services.cache import TTLValue
from services.metadata_utils import (
    MEDIA_DESCRIPTIVE_FIELDS,
    MEDIA_METADATA_FIELDS,
    normalize_author,
    normalize_series,
)
from services.abs_metadata import fetch_abs_index, enrich_from_abs, write_metadata_to_file
# Metadata extraction lives in services/metadata_extract.py (issue #255). The
# names are re-exported here because other routers and the tests import them
# from this module, and the handlers below call them as module globals.
from services.metadata_extract import (  # noqa: F401 -- re-exports
    REGEX_AUTHOR_SERIES_TITLE, REGEX_SERIES_TITLE,
    is_path_pattern, get_filename_patterns, compute_file_hash,
    extract_title_from_filename, parse_filename_metadata_with_settings,
    _read_embedded_metadata, extract_metadata, sanitize_filename,
    _extract_and_save_cover,
)
# Auto-pairing lives in services/auto_match.py (issue #255); same re-export
# rule as above (tests stub `auto_match_books` and read the thresholds here).
from services.auto_match import (  # noqa: F401 -- re-exports
    AUTO_MATCH_TITLE_THRESHOLD, AUTO_MATCH_TITLE_THRESHOLD_NO_AUTHOR,
    AUTO_MATCH_AUTHOR_GATE, AUTO_MATCH_AUTHOR_BOOST, AUTO_MATCH_AUTHOR_BOOST_POINTS,
    AUTO_MATCH_SERIES_THRESHOLD, AUTO_MATCH_SERIES_INDEX_TOLERANCE,
    _normalize_for_comparison, _normalize_author, _parse_series_index,
    _series_compatible, _auto_pair_excluded, _score_candidate, auto_match_books,
)
# Ingest lives in services/library_scan.py (issue #255). `_load_abs_settings`
# moved to services/abs_metadata.py because the ABS enrichment endpoints below
# share it with the scan. Same re-export rule as above.
from services.abs_metadata import load_abs_settings as _load_abs_settings
from services.library_scan import (  # noqa: F401 -- re-exports
    EBOOK_EXTENSIONS, AUDIOBOOK_EXTENSIONS, SCAN_COMMIT_BATCH, _Batch,
    _hash_and_size, _find_by_path, _insert_or_reread,
    _ingest_one_ebook, _ingest_one_audiobook, _maybe_load_abs_index,
    _multi_file_groups, _walk_tree, _classify_tree,
    scan_files_impl, scan_library_impl,
)
# Browse -- the paginated lists, `/search`, `/items` and `/facets` -- lives in
# services/library_browse.py (issue #255). Same re-export rule as above; the
# `new-items`/`new-pairs` handlers below still call `_pairs_base`/`_paginate`.
from services.library_browse import (  # noqa: F401 -- re-exports
    PAGE_DEFAULT_LIMIT, PAGE_MAX_LIMIT, SEARCH_MAX_RESULTS, _LIKE_ESCAPE,
    _like_term, _search_clause, _library_order, _paginate, _list_media,
    _pairs_base, _PAIR_LOADS, _int_null, _paired_ebook, _paired_audiobook,
    _pair_arm, _media_arm, _browse_arms, _browse_subquery, _browse_order,
    _hydrate_items, _count, search_impl, list_items_impl, facets_impl,
)
# Pair metadata discrepancies live in services/discrepancies.py (issue #255).
# `test_media_column_parity` reads `FIELDS_TO_COMPARE` off this module.
from services.discrepancies import (  # noqa: F401 -- re-exports
    FIELDS_TO_COMPARE, _pair_has_discrepancies,
    find_discrepancies_impl, apply_resolution, ignore_fields_impl,
)
# Aliased under the old private names: the handlers call these module globals,
# and the existing tests stub them on this module (issue #255).
from services.tag_writer import (
    write_ebook_metadata as _write_ebook_metadata,
    write_audiobook_metadata as _write_audiobook_metadata,
)
from services.position_service import (
    demote_pair_positions,
    release_standalone_positions,
    repoint_standalone_positions_to_ebook,
)
from services import library_jobs
from services.library_jobs import LibraryJobBusy
from services.uploads import stream_upload_to_path
from utils import resolve_cover_url, safe_join, utcnow

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/library", tags=["library"])

# Supported cover-image extensions; the media extension sets live with the
# ingest in services/library_scan.py and are re-exported above.
COVER_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}

# Filename-pattern parsing, embedded-tag reading, `extract_metadata` and cover
# extraction live in services/metadata_extract.py (issue #255); see the
# re-export block in the imports above.


@router.post("/normalize")
async def normalize_library_metadata(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_editor_user),
):
    """
    Re-normalize all author and series fields in the database.
    Fixes entries like 'Butcher, Jim' → 'Jim Butcher' and
    'Dresden Files, The' → 'The Dresden Files'.
    Returns counts of updated records.
    """
    updated_ebooks = 0
    updated_audiobooks = 0
    
    # Normalize ebooks
    result = await db.execute(select(EBook))
    for book in result.scalars().all():
        changed = False
        if book.author:
            new_author = normalize_author(book.author)
            if new_author != book.author:
                logger.info(f"[normalize] Ebook '{book.title}': author '{book.author}' → '{new_author}'")
                book.author = new_author
                changed = True
        if book.series:
            new_series = normalize_series(book.series)
            if new_series != book.series:
                logger.info(f"[normalize] Ebook '{book.title}': series '{book.series}' → '{new_series}'")
                book.series = new_series
                changed = True
        
        if book.description:
            new_desc = md(book.description).strip()
            if new_desc != book.description:
                logger.info(f"[normalize] Ebook '{book.title}': description converted to markdown")
                book.description = new_desc
                changed = True
                
        if changed:
            db.add(book)
            updated_ebooks += 1
    
    # Normalize audiobooks
    result = await db.execute(select(AudioBook))
    for book in result.scalars().all():
        changed = False
        if book.author:
            new_author = normalize_author(book.author)
            if new_author != book.author:
                logger.info(f"[normalize] Audiobook '{book.title}': author '{book.author}' → '{new_author}'")
                book.author = new_author
                changed = True
        if book.series:
            new_series = normalize_series(book.series)
            if new_series != book.series:
                logger.info(f"[normalize] Audiobook '{book.title}': series '{book.series}' → '{new_series}'")
                book.series = new_series
                changed = True
                
        if book.description:
            new_desc = md(book.description).strip()
            if new_desc != book.description:
                logger.info(f"[normalize] Audiobook '{book.title}': description converted to markdown")
                book.description = new_desc
                changed = True
                
        if changed:
            db.add(book)
            updated_audiobooks += 1
    
    await db.commit()
    
    total = updated_ebooks + updated_audiobooks
    return {
        "message": f"Normalized {total} records ({updated_ebooks} ebooks, {updated_audiobooks} audiobooks)",
        "updated_ebooks": updated_ebooks,
        "updated_audiobooks": updated_audiobooks,
    }


# Ingest -- the full scan, the targeted scan, the per-file ingest helpers and
# the `_Batch` commit cadence -- lives in services/library_scan.py (issue
# #255); see the re-export block in the imports above.


@contextlib.asynccontextmanager
async def _library_job(name: str):
    """Hold the library-job guard for the length of this request, or 409.

    `/scan`, `/rescan-all`, `/rehash` and `/enrich-abs` all walk the whole
    library, rewrite the same rows, and (for the audiobook paths) rewrite the
    same embedded tags on disk. Two of them at once interleave: both read "this
    path is not in the database yet" for every file the other has not committed,
    which the unique index on `file_path` then turns into an `IntegrityError` on
    every second insert (issue #202).

    That happens by accident, not by malice — nginx times the request out after a
    minute while the scan keeps running, so the operator sees an error on a live
    scan and clicks again. Refusing the second one with 409 is the honest answer;
    queueing it would just park it behind the same timeout.

    See `services/library_jobs.py` for why a flag rather than a lock.
    """
    busy = library_jobs.running_job()
    if busy is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A library {busy} is already running. Wait for it to finish "
                   f"before starting another one.",
        )
    async with library_jobs.exclusive(name):
        yield


@router.post("/scan", response_model=LibraryScanResponse)
async def scan_library(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_editor_user),
):
    """
    Scan the ebook and audiobook directories for new files.
    Recursively searches subdirectories.
    Extracts metadata from filenames and tags.
    Adds any undiscovered files to the database and attempts auto-matching.

    Refuses with 409 while another library job is running (issue #202).
    """
    async with _library_job("scan"):
        return await scan_library_impl(db)


@router.post("/rescan-all", response_model=MessageResponse)
async def rescan_all_files(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_editor_user)
):
    """
    Force a rescan of EVERY file in the library to extract metadata.
    Overrides all DB metadata fields with whatever is extracted from the files.

    Refuses with 409 while another library job is running (issue #202).
    """
    async with _library_job("rescan-all"):
        return await _rescan_all_impl(db)


async def _rescan_all_impl(db: AsyncSession) -> dict:
    """The body of POST /rescan-all minus auth and the job guard.

    Commits in batches like the scan does: this re-reads and rewrites every row
    in the library, and losing all of it to one unreadable file at the far end is
    the same bug in a different endpoint (issue #202).
    """
    updated_ebooks = 0
    updated_audiobooks = 0
    batch = _Batch(db)
    
    # Ebooks
    result = await db.execute(select(EBook))
    for book in result.scalars().all():
        if not os.path.exists(book.file_path):
            continue
            
        logger.info(f"[global-rescan] Forcing rescan of ebook {book.id}: {book.file_path}")
        try:
            meta = await extract_metadata(book.file_path, "ebook", db, library_root=settings.ebook_dir)
            
            book.title = meta.get("title") or book.title
            book.author = meta.get("author") or book.author
            # Respect user-cleared series: empty string = user cleared it, don't overwrite
            if book.series is None:
                book.series = meta.get("series") or book.series
                book.series_index = meta.get("series_index") or book.series_index
            for f in MEDIA_DESCRIPTIVE_FIELDS:
                setattr(book, f, meta.get(f) or getattr(book, f))

            if meta.get("isbn"): book.isbn = meta["isbn"]
            if meta.get("asin"): book.asin = meta["asin"]

            book.metadata_source = meta.get("_metadata_source")
            book.metadata_pattern = meta.get("_metadata_pattern")

            if not book.cover_path:
                try:
                    cover_path = await asyncio.to_thread(_extract_and_save_cover, book.file_path, "ebook", book.id, book.title)
                    if cover_path:
                        book.cover_path = cover_path
                except Exception as e:
                    pass

            db.add(book)
            updated_ebooks += 1
        except Exception as e:
            logger.error(f"[global-rescan] Error on ebook {book.id}: {e}")
        await batch.tick()

    # Audiobooks
    result = await db.execute(select(AudioBook))
    for book in result.scalars().all():
        if not os.path.exists(book.file_path):
            continue
            
        logger.info(f"[global-rescan] Forcing rescan of audiobook {book.id}: {book.file_path}")
        try:
            meta = await extract_metadata(book.file_path, "audiobook", db, library_root=settings.audiobook_dir)
            
            book.title = meta.get("title") or book.title
            book.author = meta.get("author") or book.author
            # Respect user-cleared series: empty string = user cleared it, don't overwrite
            if book.series is None:
                book.series = meta.get("series") or book.series
                book.series_index = meta.get("series_index") or book.series_index
            for f in MEDIA_DESCRIPTIVE_FIELDS:
                setattr(book, f, meta.get(f) or getattr(book, f))

            if meta.get("narrators"): book.narrators = meta["narrators"]
            book.duration_seconds = meta.get("duration_seconds") or book.duration_seconds

            book.metadata_source = meta.get("_metadata_source")
            book.metadata_pattern = meta.get("_metadata_pattern")

            if not book.cover_path:
                try:
                    cover_path = await asyncio.to_thread(_extract_and_save_cover, book.file_path, "audiobook", book.id)
                    if cover_path:
                        book.cover_path = cover_path
                except Exception as e:
                    pass

            db.add(book)
            updated_audiobooks += 1
        except Exception as e:
            logger.error(f"[global-rescan] Error on audiobook {book.id}: {e}")
        await batch.tick()

    await db.commit()
    
    return {
        "message": f"Force-rescanned {updated_ebooks} ebooks and {updated_audiobooks} audiobooks."
    }


@router.post("/rehash")
async def rehash_library(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_editor_user),
):
    """Rehash the library; refuses with 409 while another job runs (issue #202)."""
    async with _library_job("rehash"):
        return await _rehash_impl(db)


async def _rehash_impl(db: AsyncSession) -> dict:
    """One-time migration to the composite hash scheme (issue #45).

    Recomputes file_hash for every row whose file exists on disk, then remaps
    auto_pair_excluded_hashes through the old→new mapping — unpair memory
    stores raw hash values, so without the remap every existing exclusion
    would silently stop matching. Rows whose file is missing keep their stale
    hash (harmless: it can no longer collide with a composite hash).

    Deliberately *not* batched, unlike the other three library jobs (issue
    #202): the second pass remaps `auto_pair_excluded_hashes` through the
    old→new mapping the first pass builds, so committing part-way would leave
    rows rehashed with their exclusions still pointing at the old values — every
    unpair the user has ever recorded silently stops matching, and the next scan
    re-pairs what they broke apart. The two passes have to land together. It
    takes the job guard like the others, which is what stops it overlapping a
    scan; a crash mid-rehash costs a re-run, not correctness.
    """
    from services.file_hash import hash_file

    hash_map: dict[str, str] = {}
    rehashed = 0
    skipped_missing = 0

    for model in (EBook, AudioBook):
        result = await db.execute(select(model))
        for book in result.scalars().all():
            if not book.file_path or not os.path.exists(book.file_path):
                skipped_missing += 1
                continue
            try:
                new_hash = await asyncio.to_thread(hash_file, book.file_path)
            except OSError as e:
                logger.error(f"[rehash] Could not read {book.file_path}: {e}")
                skipped_missing += 1
                continue
            if book.file_hash and book.file_hash != new_hash:
                hash_map[book.file_hash] = new_hash
            if book.file_hash != new_hash:
                book.file_hash = new_hash
                rehashed += 1

    exclusions_remapped = 0
    if hash_map:
        for model in (EBook, AudioBook):
            result = await db.execute(select(model))
            for book in result.scalars().all():
                excluded = book.auto_pair_excluded_hashes or []
                if any(h in hash_map for h in excluded):
                    book.auto_pair_excluded_hashes = [
                        hash_map.get(h, h) for h in excluded
                    ]
                    exclusions_remapped += 1

    await db.commit()
    logger.info(
        f"[rehash] Rehashed {rehashed} rows, skipped {skipped_missing} missing, "
        f"remapped exclusions on {exclusions_remapped} rows."
    )
    return {
        "rehashed": rehashed,
        "skipped_missing": skipped_missing,
        "exclusions_remapped": exclusions_remapped,
    }


@router.post("/{book_type}s/{book_id}/rescan")
async def rescan_book_file(
    book_type: str,
    book_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_editor_user)
):
    """
    Force a rescan of a single file to extract metadata from its tags/filename.
    Overrides all DB metadata fields with whatever is extracted.
    """
    if book_type not in ["ebook", "audiobook"]:
        raise HTTPException(status_code=400, detail="Invalid book type")
        
    model = EBook if book_type == "ebook" else AudioBook
    library_root = settings.ebook_dir if book_type == "ebook" else settings.audiobook_dir
    
    result = await db.execute(select(model).where(model.id == book_id))
    book = result.scalar_one_or_none()
    
    if not book:
        raise HTTPException(status_code=404, detail=f"{book_type} not found")
        
    if not os.path.exists(book.file_path):
        raise HTTPException(status_code=404, detail="File not found on disk")
        
    logger.info(f"Forcing rescan of {book_type} {book.id}: {book.file_path}")
    
    meta = await extract_metadata(book.file_path, book_type, db, library_root=library_root)
    
    # Overwrite DB with extracted data
    book.title = meta.get("title") or book.title
    book.author = meta.get("author") or book.author
    # Respect user-cleared series: empty string = user cleared it, don't overwrite
    if book.series is None:
        book.series = meta.get("series") or book.series
        book.series_index = meta.get("series_index") or book.series_index
    for f in MEDIA_DESCRIPTIVE_FIELDS:
        setattr(book, f, meta.get(f) or getattr(book, f))

    if book_type == "audiobook":
        if meta.get("narrators"): book.narrators = meta["narrators"]
        book.duration_seconds = meta.get("duration_seconds") or book.duration_seconds
    if book_type == "ebook":
        if meta.get("isbn"): book.isbn = meta["isbn"]
        if meta.get("asin"): book.asin = meta["asin"]
        
    book.metadata_source = meta.get("_metadata_source")
    book.metadata_pattern = meta.get("_metadata_pattern")
    
    # Try to extract cover if missing
    if not book.cover_path:
        try:
            cover_path = await asyncio.to_thread(_extract_and_save_cover, book.file_path, book_type, book.id, book.title)
            if cover_path:
                book.cover_path = cover_path
        except Exception as e:
            logger.error(f"Error extracting cover after scan for {book_type} {book.id}: {e}")
            
    db.add(book)
    await db.commit()
    await db.refresh(book)
    
    return book


# Auto-pairing (the rule helpers, their thresholds and `auto_match_books`)
# lives in services/auto_match.py (issue #255); see the re-export block in the
# imports above.


# ---------------------------------------------------------------------------
# Paginated list endpoints (issue #48)
#
# `page`/`limit` follow GET /api/users/audit-log: 1-based page, limit 1..500,
# default 100. `q` is a case-insensitive substring match on title/author/series
# — the same match `/search` does — so clients stop fetching everything to
# filter locally. Every ordering ends in `id` so pages are disjoint and stable.
# ---------------------------------------------------------------------------

# The limits, the search cap, the query builders and the endpoint bodies live
# in services/library_browse.py (issue #255); see the re-export block above.


def _page_param() -> int:
    return Query(1, ge=1, description="1-based page number")


def _limit_param() -> int:
    return Query(PAGE_DEFAULT_LIMIT, ge=1, le=PAGE_MAX_LIMIT, description="Page size")


def _q_param() -> Optional[str]:
    return Query(None, min_length=1, description="Substring match on title, author, or series")


@router.get("/ebooks", response_model=Page[EBookResponse])
async def list_ebooks(
    page: int = _page_param(),
    limit: int = _limit_param(),
    q: Optional[str] = _q_param(),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """One page of ebooks, in library order, optionally narrowed by `q`."""
    return await _list_media(db, EBook, page=page, limit=limit, q=q)


@router.get("/audiobooks", response_model=Page[AudioBookResponse])
async def list_audiobooks(
    page: int = _page_param(),
    limit: int = _limit_param(),
    q: Optional[str] = _q_param(),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """One page of audiobooks, in library order, optionally narrowed by `q`."""
    return await _list_media(db, AudioBook, page=page, limit=limit, q=q)


@router.get("/ebooks/{book_id}", response_model=EBookDetailResponse)
async def get_ebook_detail(
    book_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Get a single ebook and its pair status."""
    result = await db.execute(
        select(EBook)
        .options(
            selectinload(EBook.pairs).selectinload(BookPair.audiobook)
        )
        .where(EBook.id == book_id)
    )
    ebook = result.scalar_one_or_none()
    if not ebook:
        raise HTTPException(status_code=404, detail="Ebook not found")

    response_data = EBookDetailResponse.model_validate(ebook)
    if ebook.pairs:
        pair = ebook.pairs[0]
        response_data.pair_id = pair.id
        response_data.pair_status = pair.status
        response_data.paired_with = pair.audiobook

    return response_data


@router.get("/audiobooks/{book_id}", response_model=AudioBookDetailResponse)
async def get_audiobook_detail(
    book_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Get a single audiobook and its pair status."""
    result = await db.execute(
        select(AudioBook)
        .options(
            selectinload(AudioBook.pairs).selectinload(BookPair.ebook)
        )
        .where(AudioBook.id == book_id)
    )
    audiobook = result.scalar_one_or_none()
    if not audiobook:
        raise HTTPException(status_code=404, detail="Audiobook not found")

    response_data = AudioBookDetailResponse.model_validate(audiobook)
    if audiobook.pairs:
        pair = audiobook.pairs[0]
        response_data.pair_id = pair.id
        response_data.pair_status = pair.status
        response_data.paired_with = pair.ebook

    return response_data


@router.get("/search", response_model=SearchResponse)
async def search_library(
    q: str = Query(..., min_length=1, description="Search query for title, author, or series"),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(rate_limited(search_reads)),
):
    """Global search across ebooks, audiobooks, and book pairs.

    Bounded on both axes since issue #208. Each of the three queries stops at
    `SEARCH_MAX_RESULTS`, and `truncated` says when one of them did, so a client
    can tell "nothing else matched" from "narrow your query". The term's LIKE
    wildcards are escaped by `_search_clause`, which is shared with the
    paginated browse so the two cannot disagree about what `%` means — before
    that, `q=%` returned the entire library three times in a single request.
    """
    return await search_impl(db, q)


@router.get("/pairs", response_model=Page[BookPairResponse])
async def list_pairs(
    page: int = _page_param(),
    limit: int = _limit_param(),
    q: Optional[str] = _q_param(),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """One page of book pairs (matched ebook + audiobook), in library order."""
    base = _pairs_base(q)
    ordered = base.options(*_PAIR_LOADS).order_by(*_library_order(EBook), BookPair.id)
    return await _paginate(db, ordered, base, page, limit)


@router.get("/pairs/{pair_id}", response_model=BookPairResponse)
async def get_pair(
    pair_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """One book pair by id — the listing's element, fetched directly.

    Resolving a pair used to mean walking every page of `GET /pairs` and
    filtering client-side (issue #277). Same `_PAIR_LOADS` as the listing, so
    `sync_map_version` is reported here too: a client that swaps to this
    endpoint must not lose the field its sync-map cache check keys on
    (docs/position-sync-contract.md, "Re-transcription").

    Same role floor as the listing — `get_current_user`, not `get_editor_user`.
    A reader-role account has to be able to resolve a pair it is opening.
    """
    result = await db.execute(
        select(BookPair).options(*_PAIR_LOADS).where(BookPair.id == pair_id)
    )
    pair = result.scalar_one_or_none()
    if not pair:
        raise HTTPException(status_code=404, detail="Pair not found")
    return pair

@router.get("/items", response_model=Page[LibraryItem])
async def list_library_items(
    tab: LibraryTab = Query(LibraryTab.ALL, description="Which shelf: all | ebooks | audiobooks | paired | unpaired | new"),
    kind: Optional[LibraryItemKind] = Query(None, description="Sub-filter for unpaired/new: ebook | audiobook | pair"),
    q: Optional[str] = _q_param(),
    author: Optional[str] = Query(None, min_length=1, description="Exact author"),
    series: Optional[str] = Query(None, min_length=1, description="Exact series"),
    sort: LibrarySort = Query(LibrarySort.TITLE),
    dir: SortDir = Query(SortDir.ASC),
    page: int = _page_param(),
    limit: int = _limit_param(),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """One page of the mixed library list the Library page renders."""
    return await list_items_impl(
        db, tab=tab, kind=kind, q=q, author=author, series=series,
        sort=sort, direction=dir, page=page, limit=limit,
    )


@router.get("/facets", response_model=LibraryFacets)
async def library_facets(
    tab: LibraryTab = Query(LibraryTab.ALL),
    kind: Optional[LibraryItemKind] = Query(None),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Filter-pill options for a tab (distinct authors / series with counts,
    *not* narrowed by q/author/series — pills describe the whole tab) plus the
    library-wide counts the tab labels show."""
    return await facets_impl(db, tab, kind)


@router.post("/pairs", response_model=BookPairResponse, status_code=status.HTTP_201_CREATED)
async def create_pair(
    pair_data: BookPairCreate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_editor_user),
):
    """Manually create a book pair (match an ebook with an audiobook)."""
    # Verify ebook exists
    result = await db.execute(select(EBook).where(EBook.id == pair_data.ebook_id))
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="EBook not found")

    # Verify audiobook exists
    result = await db.execute(
        select(AudioBook).where(AudioBook.id == pair_data.audiobook_id)
    )
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="AudioBook not found")

    # Check if pair already exists
    result = await db.execute(
        select(BookPair).where(
            BookPair.ebook_id == pair_data.ebook_id,
            BookPair.audiobook_id == pair_data.audiobook_id,
        )
    )
    if result.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="This pair already exists")

    pair = BookPair(
        ebook_id=pair_data.ebook_id,
        audiobook_id=pair_data.audiobook_id,
        status=PairStatus.MANUAL_MATCHED,
        matched_at=utcnow(),
    )
    db.add(pair)
    await db.flush()

    # Auto-acknowledge the individual books now that they've been matched
    result = await db.execute(select(EBook).where(EBook.id == pair_data.ebook_id))
    ebook_obj = result.scalar_one_or_none()
    if ebook_obj:
        ebook_obj.acknowledged = True

    result = await db.execute(select(AudioBook).where(AudioBook.id == pair_data.audiobook_id))
    audio_obj = result.scalar_one_or_none()
    if audio_obj:
        audio_obj.acknowledged = True

    # Check auto-transcribe setting
    setting_result = await db.execute(select(SystemSetting).where(SystemSetting.key == "auto_transcribe_enabled"))
    setting = setting_result.scalar_one_or_none()
    if setting and setting.value and setting.value.lower() == "true":
        from services.queue_manager import add_to_queue
        # Share this transaction — the pair is only flushed here (issue #199).
        await add_to_queue([pair.id], db)

    # Reload with relationships
    result = await db.execute(
        select(BookPair)
        .options(selectinload(BookPair.ebook), selectinload(BookPair.audiobook))
        .where(BookPair.id == pair.id)
    )
    return result.scalar_one()


@router.delete("/pairs/{pair_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_pair(
    pair_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_editor_user),
):
    """Delete a book pair."""
    result = await db.execute(
        select(BookPair)
        .options(selectinload(BookPair.ebook), selectinload(BookPair.audiobook))
        .where(BookPair.id == pair_id)
    )
    pair = result.scalar_one_or_none()
    if not pair:
        raise HTTPException(status_code=404, detail="Book pair not found")

    # Record mutual exclusion so these two books won't auto-pair again.
    # The row ids go in unconditionally (issue #253): the hash pair below is
    # only recordable when *both* sides have a hash, which left a legacy
    # hash-less row with no memory of the unpair at all, so the very next scan
    # re-created the pair the user had just broken.
    ebook = pair.ebook
    audiobook = pair.audiobook
    if ebook and audiobook:
        eb_excluded_ids = list(ebook.auto_pair_excluded_ids or [])
        if audiobook.id not in eb_excluded_ids:
            eb_excluded_ids.append(audiobook.id)
            ebook.auto_pair_excluded_ids = eb_excluded_ids
        ab_excluded_ids = list(audiobook.auto_pair_excluded_ids or [])
        if ebook.id not in ab_excluded_ids:
            ab_excluded_ids.append(ebook.id)
            audiobook.auto_pair_excluded_ids = ab_excluded_ids
    if ebook and audiobook and ebook.file_hash and audiobook.file_hash:
        eb_excluded = list(ebook.auto_pair_excluded_hashes or [])
        if audiobook.file_hash not in eb_excluded:
            eb_excluded.append(audiobook.file_hash)
            ebook.auto_pair_excluded_hashes = eb_excluded
        ab_excluded = list(audiobook.auto_pair_excluded_hashes or [])
        if ebook.file_hash not in ab_excluded:
            ab_excluded.append(ebook.file_hash)
            audiobook.auto_pair_excluded_hashes = ab_excluded

    # Unpairing must not destroy anyone's reading position (issue #155): every
    # user's pair-scoped bookmark is demoted to the standalone media scopes
    # (and `user_progress` unlinked, not deleted) before the pair row goes.
    await demote_pair_positions(db, pair_id)
    await db.execute(delete(TranscriptionQueueItem).where(TranscriptionQueueItem.book_pair_id == pair_id))
    await db.execute(delete(AudioTranscript).where(AudioTranscript.pair_id == pair_id))
    await db.delete(pair)


@router.post("/upload/ebook", response_model=EBookResponse, status_code=status.HTTP_201_CREATED)
async def upload_ebook(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_editor_user),
):
    """Upload an ebook file to the library."""
    ext = Path(file.filename).suffix.lower()
    if ext not in EBOOK_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported ebook format: {ext}. Supported: {EBOOK_EXTENSIONS}",
        )

    filepath = safe_join(settings.ebook_dir, file.filename)
    if filepath.exists():
        raise HTTPException(
            status_code=409,
            detail=f"A file named '{filepath.name}' already exists in the library",
        )

    await stream_upload_to_path(file, filepath, settings.max_upload_file_bytes)

    # Delegate to the same ingest path the directory scanner uses, so uploads
    # get cover extraction, metadata enrichment, and auto-matching for free.
    await scan_files_impl(db, [str(filepath)])
    ebook = (
        await db.execute(select(EBook).where(EBook.file_path == str(filepath)))
    ).scalar_one()
    return ebook


@router.post("/upload/audiobook", response_model=AudioBookResponse, status_code=status.HTTP_201_CREATED)
async def upload_audiobook(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_editor_user),
):
    """Upload an audiobook file to the library."""
    ext = Path(file.filename).suffix.lower()
    if ext not in AUDIOBOOK_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported audiobook format: {ext}. Supported: {AUDIOBOOK_EXTENSIONS}",
        )

    filepath = safe_join(settings.audiobook_dir, file.filename)
    if filepath.exists():
        raise HTTPException(
            status_code=409,
            detail=f"A file named '{filepath.name}' already exists in the library",
        )

    await stream_upload_to_path(file, filepath, settings.max_upload_file_bytes)

    # Delegate to the same ingest path the directory scanner uses, so uploads
    # get cover extraction, metadata enrichment, and auto-matching for free.
    await scan_files_impl(db, [str(filepath)])
    audiobook = (
        await db.execute(select(AudioBook).where(AudioBook.file_path == str(filepath)))
    ).scalar_one()
    return audiobook


class MetadataUpdate(BaseModel):
    title: Optional[str] = None
    author: Optional[str] = None
    series: Optional[str] = None
    series_index: Optional[float] = None
    description: Optional[str] = None
    publisher: Optional[str] = None
    publish_year: Optional[int] = None
    language: Optional[str] = None
    genres: Optional[str] = None
    tags: Optional[str] = None
    narrators: Optional[str] = None
    isbn: Optional[str] = None
    asin: Optional[str] = None
    is_explicit: Optional[bool] = None
    is_abridged: Optional[bool] = None


# The on-disk writers live in services/tag_writer.py (issue #255). The
# underscored aliases imported above are what the handlers below call, so
# tests can still stub them on this module.


@router.patch("/ebooks/{book_id}", response_model=EBookResponse)
async def update_ebook_metadata(
    book_id: int,
    meta: MetadataUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_editor_user),
):
    """Manually update ebook metadata and write changes back to the file."""
    result = await db.execute(select(EBook).where(EBook.id == book_id))
    book = result.scalar_one_or_none()
    if not book:
        raise HTTPException(status_code=404, detail="Book not found")
        
    # One loop over the shared field list instead of the same fifteen `if`s on
    # each media type (issue #257). `None` still means "no opinion": a PATCH is
    # a partial update, so an omitted field is left alone rather than cleared.
    for field in MEDIA_METADATA_FIELDS:
        value = getattr(meta, field)
        if value is not None:
            setattr(book, field, value)
    
    # Write metadata back to the file
    try:
        _write_ebook_metadata(book.file_path, book)
    except Exception as e:
        logger.warning(f"Failed to write metadata to ebook file: {e}")
    
    await db.commit()
    await db.refresh(book)
    return book

@router.post("/ebooks/{book_id}/cover", response_model=EBookResponse)
async def upload_ebook_cover(
    book_id: int,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_editor_user),
):
    """Upload a new cover image for an ebook."""
    result = await db.execute(select(EBook).where(EBook.id == book_id))
    book = result.scalar_one_or_none()
    if not book:
        raise HTTPException(status_code=404, detail="Book not found")
        
    covers_path = Path(settings.covers_dir)
    covers_path.mkdir(parents=True, exist_ok=True)

    ext = Path(file.filename).suffix.lower()
    if ext not in COVER_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported cover format: {ext}. Supported: {COVER_EXTENSIONS}",
        )
    safe_title = sanitize_filename(book.title) if book.title else "ebook"
    new_filename = f"{safe_title}_{book.id}{ext}"
    dest_path = safe_join(covers_path, new_filename)

    old_path = resolve_cover_url(book.cover_path, covers_path)

    await stream_upload_to_path(file, dest_path, settings.max_cover_bytes)

    if old_path and old_path.is_file() and old_path != dest_path:
        try:
            old_path.unlink()
        except OSError as e:
            logger.warning(f"Failed to delete old cover {old_path}: {e}")

    book.cover_path = f"/api/files/covers/{new_filename}"
    await db.commit()
    await db.refresh(book)
    return book

@router.patch("/audiobooks/{book_id}", response_model=AudioBookResponse)
async def update_audiobook_metadata(
    book_id: int,
    meta: MetadataUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_editor_user),
):
    """Manually update audiobook metadata and write changes back to the file."""
    result = await db.execute(select(AudioBook).where(AudioBook.id == book_id))
    book = result.scalar_one_or_none()
    if not book:
        raise HTTPException(status_code=404, detail="Book not found")
        
    # One loop over the shared field list instead of the same fifteen `if`s on
    # each media type (issue #257). `None` still means "no opinion": a PATCH is
    # a partial update, so an omitted field is left alone rather than cleared.
    for field in MEDIA_METADATA_FIELDS:
        value = getattr(meta, field)
        if value is not None:
            setattr(book, field, value)
    
    # Write metadata back to the file
    try:
        _write_audiobook_metadata(book.file_path, book)
    except Exception as e:
        logger.warning(f"Failed to write metadata to audiobook file: {e}")
    
    await db.commit()
    await db.refresh(book)
    return book

@router.post("/audiobooks/{book_id}/cover", response_model=AudioBookResponse)
async def upload_audiobook_cover(
    book_id: int,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_editor_user),
):
    """Upload a new cover image for an audiobook."""
    result = await db.execute(select(AudioBook).where(AudioBook.id == book_id))
    book = result.scalar_one_or_none()
    if not book:
        raise HTTPException(status_code=404, detail="Book not found")
        
    covers_path = Path(settings.covers_dir)
    covers_path.mkdir(parents=True, exist_ok=True)

    ext = Path(file.filename).suffix.lower()
    if ext not in COVER_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported cover format: {ext}. Supported: {COVER_EXTENSIONS}",
        )
    safe_title = sanitize_filename(book.title) if book.title else "audiobook"
    new_filename = f"{safe_title}_{book.id}{ext}"
    dest_path = safe_join(covers_path, new_filename)

    old_path = resolve_cover_url(book.cover_path, covers_path)

    await stream_upload_to_path(file, dest_path, settings.max_cover_bytes)

    if old_path and old_path.is_file() and old_path != dest_path:
        try:
            old_path.unlink()
        except OSError as e:
            logger.warning(f"Failed to delete old cover {old_path}: {e}")

    book.cover_path = f"/api/files/covers/{new_filename}"
    await db.commit()
    await db.refresh(book)
    return book


@router.get("/debug-metadata/{book_type}/{book_id}")
async def debug_metadata(
    book_type: str,
    book_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_editor_user),
):
    """
    Debug endpoint: re-extract metadata from a book's file and return all
    the intermediate data (pattern matches, embedded tags, merged result)
    without modifying the database.

    Editor-gated (issue #263): the response carries the book's absolute
    `file_path`, the absolute `library_root` it sits under and the raw extracted
    tags — a map of the operator's filesystem, and never something a reader
    account had a use for.
    """
    import mutagen as mutagen_lib
    
    if book_type == "ebook":
        result = await db.execute(select(EBook).where(EBook.id == book_id))
        book = result.scalar_one_or_none()
        library_root = settings.ebook_dir
    elif book_type == "audiobook":
        result = await db.execute(select(AudioBook).where(AudioBook.id == book_id))
        book = result.scalar_one_or_none()
        library_root = settings.audiobook_dir
    else:
        raise HTTPException(status_code=400, detail="book_type must be 'ebook' or 'audiobook'")
    
    if not book:
        raise HTTPException(status_code=404, detail="Book not found")
    
    filepath = book.file_path
    filename = os.path.basename(filepath)
    parent_dir = os.path.basename(os.path.dirname(filepath))
    
    relative_path = None
    try:
        relative_path = os.path.relpath(filepath, library_root)
        relative_path = relative_path.replace('\\', '/')
    except ValueError:
        pass
    
    debug_info = {
        "file_path": filepath,
        "filename": filename,
        "parent_dir": parent_dir,
        "library_root": library_root,
        "relative_path": relative_path,
        "file_exists": os.path.exists(filepath),
        "current_db_metadata": {
            "title": book.title,
            "author": book.author,
            "series": getattr(book, 'series', None),
            "series_index": getattr(book, 'series_index', None),
        },
        "patterns_used": await get_filename_patterns(db, book_type),
        "filename_parse_result": None,
        "embedded_tags": {},
        "mutagen_type": None,
        "all_tag_keys": [],
        "merged_result": None,
    }
    
    # Filename/path parse
    fn_meta = await parse_filename_metadata_with_settings(
        filename, db, parent_dir, book_type, relative_path=relative_path
    )
    debug_info["filename_parse_result"] = fn_meta
    
    # Embedded metadata
    if debug_info["file_exists"]:
        try:
            if book_type == "audiobook":
                audio = mutagen_lib.File(filepath)
                if audio:
                    debug_info["mutagen_type"] = type(audio).__name__
                    debug_info["all_tag_keys"] = list(audio.keys())
                    # Dump all tag values (convert to strings for JSON)
                    for key in audio.keys():
                        try:
                            val = audio[key]
                            if isinstance(val, list):
                                debug_info["embedded_tags"][key] = [str(v) for v in val]
                            else:
                                debug_info["embedded_tags"][key] = str(val)
                        except Exception:
                            debug_info["embedded_tags"][key] = "<unreadable>"
            elif book_type == "ebook" and filepath.lower().endswith(".epub"):
                try:
                    epub_book = epub.read_epub(filepath, options={'ignore_ncx': True})
                    t = epub_book.get_metadata('DC', 'title')
                    if t: debug_info["embedded_tags"]["DC:title"] = str(t[0][0])
                    c = epub_book.get_metadata('DC', 'creator')
                    if c: debug_info["embedded_tags"]["DC:creator"] = str(c[0][0])
                    # Calibre series
                    all_meta = epub_book.get_metadata('OPF', 'meta')
                    if all_meta:
                        for m in all_meta:
                            attrs = m[1] if len(m) > 1 else {}
                            name = attrs.get('name', '')
                            if 'series' in name.lower():
                                debug_info["embedded_tags"][name] = attrs.get('content', '')
                except Exception as e:
                    debug_info["embedded_tags"]["error"] = str(e)
        except Exception as e:
            debug_info["embedded_tags"]["error"] = str(e)
    
    # Full merged result
    if debug_info["file_exists"]:
        merged = await extract_metadata(filepath, book_type, db, library_root=library_root)
        debug_info["merged_result"] = merged
    
    return debug_info

# ============================================================
# Metadata Cleanup Endpoints
# ============================================================

# `FIELDS_TO_COMPARE`, `_pair_has_discrepancies` and the bodies of the three
# discrepancy endpoints live in services/discrepancies.py (issue #255); see
# the re-export block above.


@router.get("/new-items", response_model=NewItemsResponse)
async def get_new_items(
    page: int = _page_param(),
    limit: int = _limit_param(),
    q: Optional[str] = _q_param(),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Ebooks and audiobooks that haven't been acknowledged yet, newest first.

    One page per sub-list; `page`/`limit` apply to each independently.
    """
    ebooks = await _list_media(
        db, EBook, page=page, limit=limit, q=q,
        where=EBook.acknowledged == False,
        order=(EBook.uploaded_at.desc(), EBook.id.desc()),
    )
    audiobooks = await _list_media(
        db, AudioBook, page=page, limit=limit, q=q,
        where=AudioBook.acknowledged == False,
        order=(AudioBook.uploaded_at.desc(), AudioBook.id.desc()),
    )
    return NewItemsResponse(
        ebooks=Page[EBookResponse](
            items=[EBookResponse.model_validate(e) for e in ebooks.items],
            total=ebooks.total, page=page, limit=limit),
        audiobooks=Page[AudioBookResponse](
            items=[AudioBookResponse.model_validate(a) for a in audiobooks.items],
            total=audiobooks.total, page=page, limit=limit),
    )


@router.post("/new-items/acknowledge")
async def acknowledge_new_items(
    req: AcknowledgeItemsRequest,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_editor_user),
):
    """Mark selected ebooks and/or audiobooks as acknowledged."""
    if req.ebook_ids:
        ebook_result = await db.execute(select(EBook).where(EBook.id.in_(req.ebook_ids)))
        for e in ebook_result.scalars().all():
            e.acknowledged = True
    if req.audiobook_ids:
        audio_result = await db.execute(select(AudioBook).where(AudioBook.id.in_(req.audiobook_ids)))
        for a in audio_result.scalars().all():
            a.acknowledged = True
    await db.commit()
    return {"acknowledged_ebooks": len(req.ebook_ids), "acknowledged_audiobooks": len(req.audiobook_ids)}


@router.get("/new-pairs", response_model=Page[BookPairResponse])
async def get_new_pairs(
    page: int = _page_param(),
    limit: int = _limit_param(),
    q: Optional[str] = _q_param(),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """One page of book pairs that haven't been acknowledged yet, newest first."""
    base = _pairs_base(q, where=BookPair.acknowledged == False)
    ordered = base.options(*_PAIR_LOADS).order_by(BookPair.matched_at.desc(), BookPair.id.desc())
    return await _paginate(db, ordered, base, page, limit)


@router.post("/new-pairs/acknowledge")
async def acknowledge_new_pairs(
    req: AcknowledgePairsRequest,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_editor_user),
):
    """Mark selected book pairs as acknowledged."""
    result = await db.execute(select(BookPair).where(BookPair.id.in_(req.pair_ids)))
    for pair in result.scalars().all():
        pair.acknowledged = True
    await db.commit()
    return {"acknowledged_pairs": len(req.pair_ids)}


@router.get("/pairs-discrepancies", response_model=List[MetadataDiscrepancy])
async def get_metadata_discrepancies(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Find all book pairs with discrepancies in their shared metadata fields."""
    return await find_discrepancies_impl(db)

@router.post("/pairs/{pair_id}/resolve-discrepancies")
async def resolve_metadata_discrepancy(
    pair_id: int,
    req: ResolveDiscrepancyRequest,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_editor_user),
):
    """Apply resolutions to mismatched metadata fields on a pair."""
    result = await db.execute(
        select(BookPair)
        .options(
            selectinload(BookPair.ebook),
            selectinload(BookPair.audiobook)
        )
        .where(BookPair.id == pair_id)
    )
    pair = result.scalar_one_or_none()
    
    if not pair or not pair.ebook or not pair.audiobook:
        raise HTTPException(status_code=404, detail="Pair, EBook, or AudioBook not found")
        
    ebook = pair.ebook
    audiobook = pair.audiobook
    ebook_changed, audio_changed = apply_resolution(pair, req)

    if ebook_changed or audio_changed:
        # Deliberate: persist before the file write-back (issue #259). The
        # writers below rewrite the EPUB's OPF and the m4b's tags in place and
        # are not guarded here, so an exception in either would otherwise reach
        # `get_db`, roll the transaction back, and lose a resolution the
        # operator had already made -- against a file that may already be half
        # rewritten. Committing first bounds the disagreement to the harmless
        # direction. Pinned by tests/test_request_transactions.py.
        await db.commit()

        # Write back to files
        if ebook_changed:
            _write_ebook_metadata(ebook.file_path, ebook)
        if audio_changed:
            _write_audiobook_metadata(audiobook.file_path, audiobook)

    return {"message": "Discrepancies resolved successfully"}


@router.post("/pairs/{pair_id}/ignore-discrepancies")
async def ignore_metadata_discrepancies(
    pair_id: int,
    req: IgnoreDiscrepancyRequest,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_editor_user),
):
    """Mark specific metadata fields as permanently ignored for a pair."""
    result = await db.execute(select(BookPair).where(BookPair.id == pair_id))
    pair = result.scalar_one_or_none()
    if not pair:
        raise HTTPException(status_code=404, detail="Pair not found")

    await ignore_fields_impl(db, pair, req.fields)
    return {"message": "Fields ignored successfully"}


# ---------------------------------------------------------------------------
# Delete individual books
# ---------------------------------------------------------------------------

@router.delete("/ebooks/{ebook_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_ebook(
    ebook_id: int,
    delete_file: bool = Query(False, description="Also delete the source file from disk"),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_editor_user),
):
    """Delete an ebook from the database.  Optionally delete the source file."""
    result = await db.execute(select(EBook).where(EBook.id == ebook_id))
    ebook = result.scalar_one_or_none()
    if not ebook:
        raise HTTPException(status_code=404, detail="EBook not found")

    file_path = ebook.file_path

    # Remove associated transcription queue items for any pairs this ebook is
    # in — but demote each user's pair-scoped position onto the surviving
    # audiobook first (issue #155): the pair cascade must not take the
    # canonical bookmark rows with it.
    pairs_result = await db.execute(select(BookPair).where(BookPair.ebook_id == ebook_id))
    pairs = pairs_result.scalars().all()
    for pair in pairs:
        await demote_pair_positions(db, pair.id, keep_ebook=False)
        await db.execute(delete(TranscriptionQueueItem).where(TranscriptionQueueItem.book_pair_id == pair.id))
        await db.execute(delete(AudioTranscript).where(AudioTranscript.pair_id == pair.id))

    # Standalone bookmark rows must let go of the dying ebook id (keeping any
    # audiobook side they carry); then remove progress referencing it directly.
    await release_standalone_positions(db, ebook_id=ebook_id)
    await db.execute(delete(UserProgress).where(UserProgress.ebook_id == ebook_id))

    # Delete the ebook (cascades to BookPair → SyncMap)
    await db.delete(ebook)
    # Deliberate: commit before the unlink (issue #259). Removing the file first
    # would risk the row surviving a rollback with nothing behind it -- the
    # orphan `verify` exists to find. This order can only leave the opposite,
    # which the next scan re-ingests.
    await db.commit()

    # Optionally delete source file
    if delete_file and file_path:
        try:
            os.unlink(file_path)
            logger.info(f"Deleted source file: {file_path}")
        except OSError as e:
            logger.warning(f"Could not delete source file {file_path}: {e}")


@router.delete("/audiobooks/{audiobook_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_audiobook(
    audiobook_id: int,
    delete_file: bool = Query(False, description="Also delete the source file from disk"),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_editor_user),
):
    """Delete an audiobook from the database.  Optionally delete the source file."""
    result = await db.execute(select(AudioBook).where(AudioBook.id == audiobook_id))
    audiobook = result.scalar_one_or_none()
    if not audiobook:
        raise HTTPException(status_code=404, detail="AudioBook not found")

    file_path = audiobook.file_path

    # Remove associated transcription queue items for any pairs this audiobook
    # is in — but demote each user's pair-scoped position onto the surviving
    # ebook first (issue #155).
    pairs_result = await db.execute(select(BookPair).where(BookPair.audiobook_id == audiobook_id))
    pairs = pairs_result.scalars().all()
    for pair in pairs:
        await demote_pair_positions(db, pair.id, keep_audiobook=False)
        await db.execute(delete(TranscriptionQueueItem).where(TranscriptionQueueItem.book_pair_id == pair.id))
        await db.execute(delete(AudioTranscript).where(AudioTranscript.pair_id == pair.id))

    # Standalone bookmark rows must let go of the dying audiobook id (keeping
    # any ebook side they carry); then remove progress referencing it directly.
    await release_standalone_positions(db, audiobook_id=audiobook_id)
    await db.execute(delete(UserProgress).where(UserProgress.audiobook_id == audiobook_id))

    # Delete the audiobook (cascades to BookPair → SyncMap)
    await db.delete(audiobook)
    # Deliberate: commit before the unlink, as in delete_ebook (issue #259).
    await db.commit()

    # Optionally delete source file
    if delete_file and file_path:
        try:
            os.unlink(file_path)
            logger.info(f"Deleted source file: {file_path}")
        except OSError as e:
            logger.warning(f"Could not delete source file {file_path}: {e}")


# ---------------------------------------------------------------------------
# Verify files & cleanup orphans
# ---------------------------------------------------------------------------

# Hard cap on each of `/verify`'s two lists (issue #208). A library whose mount
# has dropped is *entirely* orphaned, and the honest answer to that is not a
# response body with one entry per book — it is the first 200 plus `truncated`,
# which says as much as the operator needs to know before going to look at the
# mount. Cleaning up and re-running gets the next batch.
VERIFY_MAX_RESULTS = 200


def _find_orphans(rows: List[dict], limit: int) -> tuple[List[dict], bool]:
    """Rows whose `file_path` is gone, capped. **Blocking** — one stat per row.

    Takes plain dicts, not ORM instances: this runs in a worker thread and must
    not touch the request's session.
    """
    orphans = []
    truncated = False
    for row in rows:
        if row["file_path"] and not os.path.isfile(row["file_path"]):
            if len(orphans) >= limit:
                truncated = True
                break
            orphans.append(row)
    return orphans, truncated


def _verify_row(item) -> dict:
    return {
        "id": item.id,
        "title": item.title,
        "author": item.author,
        "filename": item.filename,
        "file_path": item.file_path,
        "format": item.format,
    }


@router.get("/verify", response_model=VerifyFilesResponse)
async def verify_files(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(rate_limited(expensive_reads, get_editor_user)),
):
    """
    Check every ebook and audiobook file_path against the filesystem.
    Returns lists of entries whose source files no longer exist.

    Editor-gated (issue #263): the report lists the absolute `file_path` of
    every orphaned row, and it drives the Maintenance menu's "Verify Files"
    action, which the web already shows only to editors.

    The stat loop used to run on the event loop, one call per library row over a
    NAS mount, on a single-worker server — so a full library made every other
    request wait, `/api/health` included. It now runs in a worker thread, and
    each list stops at `VERIFY_MAX_RESULTS` with `truncated` set (issue #208).

    Editor and up: this is a curation view. Nothing here is actionable without
    the edit and re-scan controls, which are already editor-gated, so opening it
    to every account only bought anyone a full pass over every row on demand.
    The web hides the entry point to match (`RequireRole`), but that is cosmetic
    -- this dependency is the boundary.
    """
    ebook_rows = [
        _verify_row(e) for e in (await db.execute(select(EBook))).scalars().all()
    ]
    audiobook_rows = [
        _verify_row(a) for a in (await db.execute(select(AudioBook))).scalars().all()
    ]

    def _scan():
        ebooks, eb_truncated = _find_orphans(ebook_rows, VERIFY_MAX_RESULTS)
        audiobooks, ab_truncated = _find_orphans(audiobook_rows, VERIFY_MAX_RESULTS)
        return ebooks, audiobooks, eb_truncated or ab_truncated

    orphaned_ebooks, orphaned_audiobooks, truncated = await asyncio.to_thread(_scan)

    return {
        "orphaned_ebooks": orphaned_ebooks,
        "orphaned_audiobooks": orphaned_audiobooks,
        "truncated": truncated,
    }


class CleanupRequest(BaseModel):
    ebook_ids: List[int] = []
    audiobook_ids: List[int] = []


@router.post("/cleanup", response_model=CleanupResponse)
async def cleanup_orphans(
    req: CleanupRequest,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_editor_user),
):
    """
    Bulk-delete orphaned ebook/audiobook entries from the database.
    Does NOT touch the filesystem (these files are already missing).
    """
    deleted_ebooks = 0
    deleted_audiobooks = 0

    for eid in req.ebook_ids:
        result = await db.execute(select(EBook).where(EBook.id == eid))
        ebook = result.scalar_one_or_none()
        if ebook:
            pairs_result = await db.execute(select(BookPair).where(BookPair.ebook_id == eid))
            for pair in pairs_result.scalars().all():
                # Same rule as delete_ebook: demote positions, don't cascade
                # them away with the pair (issue #155).
                await demote_pair_positions(db, pair.id, keep_ebook=False)
                await db.execute(delete(TranscriptionQueueItem).where(TranscriptionQueueItem.book_pair_id == pair.id))
                await db.execute(delete(AudioTranscript).where(AudioTranscript.pair_id == pair.id))
            await release_standalone_positions(db, ebook_id=eid)
            await db.execute(delete(UserProgress).where(UserProgress.ebook_id == eid))
            await db.delete(ebook)
            deleted_ebooks += 1

    for aid in req.audiobook_ids:
        result = await db.execute(select(AudioBook).where(AudioBook.id == aid))
        audiobook = result.scalar_one_or_none()
        if audiobook:
            pairs_result = await db.execute(select(BookPair).where(BookPair.audiobook_id == aid))
            for pair in pairs_result.scalars().all():
                await demote_pair_positions(db, pair.id, keep_audiobook=False)
                await db.execute(delete(TranscriptionQueueItem).where(TranscriptionQueueItem.book_pair_id == pair.id))
                await db.execute(delete(AudioTranscript).where(AudioTranscript.pair_id == pair.id))
            await release_standalone_positions(db, audiobook_id=aid)
            await db.execute(delete(UserProgress).where(UserProgress.audiobook_id == aid))
            await db.delete(audiobook)
            deleted_audiobooks += 1

    await db.commit()

    return {
        "message": f"Cleaned up {deleted_ebooks} ebook(s) and {deleted_audiobooks} audiobook(s)",
        "deleted_ebooks": deleted_ebooks,
        "deleted_audiobooks": deleted_audiobooks,
    }


@router.post("/enrich-abs")
async def enrich_library_from_abs(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_editor_user),
):
    """
    Force re-enrich all audiobooks from Audiobookshelf metadata.
    Overwrites existing values (unlike the normal scan which only fills gaps).
    Also writes enriched metadata back into each audio file's embedded tags.

    Refuses with 409 while another library job is running (issue #202): this one
    rewrites tags on disk as it goes, so overlapping it with a scan means two
    writers on the same files as well as the same rows.
    """
    async with _library_job("enrich-abs"):
        return await _enrich_abs_impl(db)


async def _enrich_abs_impl(db: AsyncSession) -> dict:
    """The body of POST /enrich-abs minus auth and the job guard."""
    abs_enabled, abs_url, abs_token, abs_prefix = await _load_abs_settings(db)
    if not abs_url or not abs_token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Audiobookshelf URL and API token must be configured in Settings.",
        )

    abs_index = await asyncio.to_thread(fetch_abs_index, abs_url, abs_token, abs_prefix)
    if not abs_index:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Failed to fetch metadata from Audiobookshelf. Check the URL and API token in Settings.",
        )

    result = await db.execute(select(AudioBook))
    audiobooks = result.scalars().all()
    updated_count = 0
    tag_write_failures: list[dict] = []
    batch = _Batch(db)

    for ab in audiobooks:
        file_meta = {f: getattr(ab, f) for f in MEDIA_METADATA_FIELDS}
        enriched, changed, _ = enrich_from_abs(
            file_meta, ab.file_path, abs_index, abs_prefix, force=True
        )
        if changed:
            for field in MEDIA_METADATA_FIELDS:
                if enriched.get(field) is not None:
                    setattr(ab, field, enriched[field])
            db.add(ab)
            tag_write_ok, tag_write_error = await asyncio.to_thread(
                write_metadata_to_file, ab.file_path, enriched
            )
            if not tag_write_ok and tag_write_error:
                tag_write_failures.append(
                    {"id": ab.id, "title": ab.title, "error": tag_write_error}
                )
            updated_count += 1
        # Batched *outside* the `if changed`, and deliberately close behind the
        # tag write above: this endpoint edits files on disk inside the
        # transaction, so the longer the transaction the wider the window where a
        # rollback leaves the file changed and the row not (issue #202).
        await batch.tick()

    # Deliberate: the row changes stand even for the books whose tags could not
    # be written -- that partial success is what the response reports, and a
    # failed tag write must not cost the operator the metadata (issue #259).
    await db.commit()
    message = f"Enriched {updated_count} audiobook(s) from Audiobookshelf"
    if tag_write_failures:
        message += f", but {len(tag_write_failures)} file(s) could not be tagged"
    return {
        "message": message,
        "updated": updated_count,
        "tag_write_failures": tag_write_failures,
    }


@router.post("/audiobooks/{audiobook_id}/enrich-abs")
async def enrich_audiobook_from_abs(
    audiobook_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_editor_user),
):
    """
    Force re-enrich a single audiobook from Audiobookshelf metadata.
    Overwrites existing values and writes tags back to the audio file.
    """
    abs_enabled, abs_url, abs_token, abs_prefix = await _load_abs_settings(db)
    if not abs_url or not abs_token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Audiobookshelf URL and API token must be configured in Settings.",
        )

    result = await db.execute(select(AudioBook).where(AudioBook.id == audiobook_id))
    ab = result.scalar_one_or_none()
    if not ab:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Audiobook not found")

    abs_index = await asyncio.to_thread(fetch_abs_index, abs_url, abs_token, abs_prefix)
    if not abs_index:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Failed to fetch metadata from Audiobookshelf.",
        )

    file_meta = {f: getattr(ab, f) for f in MEDIA_METADATA_FIELDS}
    enriched, changed, matched = enrich_from_abs(
        file_meta, ab.file_path, abs_index, abs_prefix, force=True
    )

    tag_write_error = None
    if not matched:
        status_key = "no_match"
        message = "No matching entry found in Audiobookshelf for this book."
    elif changed:
        for field in MEDIA_METADATA_FIELDS:
            if enriched.get(field) is not None:
                setattr(ab, field, enriched[field])
        db.add(ab)
        tag_write_ok, tag_write_error = await asyncio.to_thread(
            write_metadata_to_file, ab.file_path, enriched
        )
        # Deliberate, same contract as the bulk endpoint above: "updated in the
        # library, but the file's tags could not be written" is a real answer
        # this endpoint gives, so the row change is committed regardless of
        # `tag_write_ok` (issue #259).
        await db.commit()
        if tag_write_ok:
            status_key = "enriched"
            message = "Metadata enriched from Audiobookshelf and written back to file."
        else:
            status_key = "tag_write_failed"
            message = (
                "Metadata updated in the library, but writing tags to the file "
                f"failed: {tag_write_error}"
                if tag_write_error
                else "Metadata updated in the library, but the file's tags could not be updated."
            )
    else:
        status_key = "already_current"
        message = "Metadata is already up to date — no changes needed."

    await db.refresh(ab)
    from schemas import AudioBookResponse
    return {
        "status": status_key,
        "message": message,
        "book": AudioBookResponse.model_validate(ab),
        "tag_write_error": tag_write_error,
    }


# ---------------------------------------------------------------------------
# Unsupported file conversion helpers
# ---------------------------------------------------------------------------

UNSUPPORTED_FORMATS = {".mobi", ".azw3"}


def _convert_to_epub_sync(src_path: str) -> str:
    """
    Convert a MOBI/AZW3 file to EPUB using calibre's ebook-convert.
    Returns the output EPUB path on success. Raises RuntimeError on failure.
    """
    src = Path(src_path)
    out = src.with_suffix(".epub")

    try:
        result = subprocess.run(
            ["ebook-convert", str(src), str(out)],
            capture_output=True,
            text=True,
            timeout=300,
        )
        if result.returncode == 0 and out.exists():
            logger.info(f"[convert] calibre succeeded: {out}")
            return str(out)
        stderr = result.stderr
        logger.warning(f"[convert] calibre failed: {stderr[:300]}")
        if "MobiError" in stderr or "Unknown book type" in stderr:
            raise RuntimeError(
                "This file appears to be corrupted or in an unsupported MOBI variant "
                "and cannot be converted. The original file may need to be replaced."
            )
        if "DRMException" in stderr or "drm" in stderr.lower() and "protected" in stderr.lower():
            raise RuntimeError("This file is DRM-protected and cannot be converted.")
        raise RuntimeError(f"Conversion failed: {stderr[:200]}")
    except FileNotFoundError:
        raise RuntimeError(
            "Calibre (ebook-convert) is not installed in the server container. "
            "Rebuild the server image to enable conversions."
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError("ebook-convert timed out after 5 minutes.")


# ---------------------------------------------------------------------------
# Calibre status
# ---------------------------------------------------------------------------

def _probe_calibre() -> dict:
    """Ask `ebook-convert` for its version. **Blocking** — up to 10 s."""
    try:
        result = subprocess.run(
            ["ebook-convert", "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            version = result.stdout.strip().splitlines()[0] if result.stdout else "unknown"
            return {"available": True, "version": version}
        return {"available": False, "error": result.stderr.strip()[:200]}
    except FileNotFoundError:
        return {"available": False, "error": "ebook-convert not found — rebuild the server container to install calibre"}
    except subprocess.TimeoutExpired:
        return {"available": False, "error": "version check timed out"}


#: Whether calibre is installed changes on an image rebuild, not between two
#: page loads — so the subprocess runs at most once per
#: `CALIBRE_STATUS_CACHE_SECONDS` (issue #208).
calibre_status_cache = TTLValue(lambda: settings.calibre_status_cache_seconds)


@router.get(
    "/calibre-status",
    response_model=CalibreStatusResponse,
    # The probe returns either `version` or `error`, never both; without
    # this the absent one would be emitted as null, which the dict never did.
    response_model_exclude_none=True,
)
async def get_calibre_status(
    current_user: User = Depends(rate_limited(expensive_reads, get_editor_user)),
):
    """Check whether calibre's ebook-convert is available in the server container.

    `subprocess.run` with a 10 s timeout used to run inline on the event loop, so
    one wedged `ebook-convert` blocked every other request for those ten seconds
    — on a single-worker server, with a System page tile calling this on load.
    Now: worker thread, cached, and editor-gated (issue #208) -- whether the
    conversion binary is installed is an operator's question, and a read-only
    account can convert nothing. The role check runs before the bucket, so a
    refused caller starts no subprocess.
    """
    return await calibre_status_cache.get(_probe_calibre)


# ---------------------------------------------------------------------------
# Unsupported file endpoints
# ---------------------------------------------------------------------------


class UnsupportedFileResponse(BaseModel):
    id: int
    filename: str
    title: Optional[str]
    author: Optional[str]
    format: str
    file_size: Optional[int]
    already_converted: bool
    epub_ebook_id: Optional[int] = None


async def _register_epub_in_db(epub_path: str, source_eb: EBook, db: AsyncSession) -> EBook:
    """Add the converted EPUB as a new EBook record (inheriting source metadata). No-op if already registered."""
    existing_eb = await _find_by_path(db, EBook, epub_path)
    if existing_eb:
        return existing_eb

    file_hash, file_size = await asyncio.to_thread(_hash_and_size, epub_path)

    epub_eb = EBook(
        title=source_eb.title,
        author=source_eb.author,
        series=source_eb.series,
        series_index=source_eb.series_index,
        filename=Path(epub_path).name,
        file_path=epub_path,
        file_hash=file_hash,
        file_size=file_size,
        format="epub",
        metadata_source=source_eb.metadata_source,
    )
    epub_eb, _created = await _insert_or_reread(db, epub_eb, EBook, epub_path)
    return epub_eb


async def _relink_or_cleanup_pairs(eb_id: int, epub_eb: Optional[EBook], db: AsyncSession) -> List[int]:
    """
    For every BookPair whose ebook_id == eb_id:
      - If epub_eb is given: re-point pair.ebook_id to the new EPUB (keeps pair + all data intact).
      - If epub_eb is None: delete the pair and all its dependent rows.

    Standalone (`ebook`-scoped) positions on the source follow the same split:
    re-pointed at the replacement when there is one (issue #298), released when
    there isn't (issue #155). Either way the source ebook row is about to be
    deleted by the caller, so nothing may still reference its id.

    Returns the ids of the pairs that were re-pointed. Their sync maps were
    built from the *source* file and no longer describe the ebook the reader
    gets, so the caller must rebuild them — see `_realign_relinked_pairs`
    (issue #101).
    """
    relinked: List[int] = []
    pairs_result = await db.execute(select(BookPair).where(BookPair.ebook_id == eb_id))
    for pair in pairs_result.scalars().all():
        if epub_eb is not None:
            pair.ebook_id = epub_eb.id
            db.add(pair)
            relinked.append(pair.id)
        else:
            # No replacement EPUB: the pair dies, but each user's position is
            # demoted onto the surviving audiobook first (issue #155).
            await demote_pair_positions(db, pair.id, keep_ebook=False)
            await db.execute(delete(TranscriptionQueueItem).where(TranscriptionQueueItem.book_pair_id == pair.id))
            await db.execute(delete(AudioTranscript).where(AudioTranscript.pair_id == pair.id))
            await db.delete(pair)

    if epub_eb is not None:
        # The converted EPUB *is* the same book, so a position on the source is
        # carried over to it rather than dropped — chapter, percent, preview and
        # hints intact, map coordinates cleared, hints marked stale (issue #298).
        await repoint_standalone_positions_to_ebook(
            db, old_ebook_id=eb_id, new_ebook_id=epub_eb.id)
    else:
        # Nothing to carry the position to: standalone bookmark rows let go of
        # the dying id (keeping any audiobook side they carry), and the ebook
        # projection goes with it.
        await release_standalone_positions(db, ebook_id=eb_id)
        await db.execute(delete(UserProgress).where(UserProgress.ebook_id == eb_id))
    return relinked


async def _realign_relinked_pairs(pair_ids: List[int], db: AsyncSession) -> List[dict]:
    """
    Rebuild the sync map of every pair that was just re-pointed at a converted
    EPUB, so its coordinates describe the artifact the reader renders (#101).

    The map is *not* deleted when it cannot be rebuilt: it is the only thing a
    bookmark's (chapter, sentence) coordinate can still be translated from. A
    pair with no cached transcript is put back to `manual_matched` instead, which
    is how it shows up as needing transcription.

    Returns one entry per failure; a failure never aborts the batch, because the
    conversion itself has already happened on disk.
    """
    from services.realign import (
        NoCachedTranscript,
        RealignError,
        realign_pair_from_cached_transcript,
    )

    failures: List[dict] = []
    for pair_id in pair_ids:
        try:
            await realign_pair_from_cached_transcript(db, pair_id)
        except NoCachedTranscript as e:
            pair = await db.get(BookPair, pair_id)
            if pair is not None and pair.status == PairStatus.SYNCED:
                pair.status = PairStatus.MANUAL_MATCHED
                db.add(pair)
            failures.append({"pair_id": pair_id, "error": e.detail})
        except RealignError as e:
            failures.append({"pair_id": pair_id, "error": e.detail})
        except Exception as e:  # noqa: BLE001 — a bad ebook must not undo the conversion
            logger.warning(f"[convert] realign of pair {pair_id} failed: {e}")
            failures.append({"pair_id": pair_id, "error": str(e)})
    return failures


@router.get("/unsupported", response_model=List[UnsupportedFileResponse])
async def list_unsupported_files(
    db: AsyncSession = Depends(get_db),
    # Editor, not admin: this list drives conversion, which is library
    # maintenance rather than infrastructure (issue #283).
    current_user: User = Depends(get_editor_user),
):
    """List all ebooks in unsupported formats (MOBI, AZW3) with conversion status."""
    result = await db.execute(
        select(EBook).where(EBook.format.in_(["mobi", "azw3"]))
    )
    ebooks = result.scalars().all()

    items = []
    for eb in ebooks:
        epub_sibling = Path(eb.file_path).with_suffix(".epub")
        epub_path_str = str(epub_sibling)
        epub_result = await db.execute(select(EBook).where(EBook.file_path == epub_path_str))
        epub_eb = epub_result.scalar_one_or_none()
        items.append(
            UnsupportedFileResponse(
                id=eb.id,
                filename=eb.filename,
                title=eb.title,
                author=eb.author,
                format=eb.format or "",
                file_size=eb.file_size,
                already_converted=epub_sibling.exists(),
                epub_ebook_id=epub_eb.id if epub_eb else None,
            )
        )
    return items


# NOTE: /unsupported/convert-all must be registered BEFORE /unsupported/{ebook_id}/convert
# so FastAPI doesn't try to interpret "convert-all" as an integer ebook_id.
@router.post("/unsupported/convert-all")
async def convert_all_unsupported(
    delete_source: bool = False,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_editor_user),
):
    """Convert all unsupported ebooks (MOBI/AZW3) to EPUB."""
    result = await db.execute(
        select(EBook).where(EBook.format.in_(["mobi", "azw3"]))
    )
    ebooks = result.scalars().all()

    succeeded, failed = [], []
    # Track (source_eb, epub_eb) pairs to delete after all conversions
    to_delete: list[tuple] = []

    for eb in ebooks:
        epub_sibling = Path(eb.file_path).with_suffix(".epub")
        if epub_sibling.exists():
            epub_eb = await _register_epub_in_db(str(epub_sibling), eb, db)
            if delete_source:
                to_delete.append((eb, epub_eb))
            continue
        try:
            epub_path = await asyncio.to_thread(_convert_to_epub_sync, eb.file_path)
            epub_eb = await _register_epub_in_db(epub_path, eb, db)
            succeeded.append(eb.filename)
            if delete_source:
                to_delete.append((eb, epub_eb))
        except RuntimeError as e:
            failed.append({"filename": eb.filename, "error": str(e)})

    realign_failures: list[dict] = []
    for eb, epub_eb in to_delete:
        relinked = await _relink_or_cleanup_pairs(eb.id, epub_eb, db)
        try:
            os.remove(eb.file_path)
        except OSError as e:
            logger.warning(f"[convert] could not delete {eb.file_path}: {e}")
        await db.delete(eb)
        realign_failures.extend(await _realign_relinked_pairs(relinked, db))

    await db.commit()

    return {
        "succeeded": succeeded,
        "failed": failed,
        "total": len(ebooks),
        "realign_failures": realign_failures,
    }


@router.post("/unsupported/{ebook_id}/convert")
async def convert_unsupported_file(
    ebook_id: int,
    delete_source: bool = False,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_editor_user),
):
    """Convert a single unsupported ebook to EPUB using calibre or mobi library."""
    result = await db.execute(select(EBook).where(EBook.id == ebook_id))
    eb = result.scalar_one_or_none()
    if not eb:
        raise HTTPException(status_code=404, detail="Ebook not found")

    if eb.format not in ("mobi", "azw3"):
        raise HTTPException(status_code=400, detail="File is already in a supported format")

    try:
        epub_path = await asyncio.to_thread(_convert_to_epub_sync, eb.file_path)
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))

    # Register the new EPUB in the DB so it can be previewed
    epub_eb = await _register_epub_in_db(epub_path, eb, db)
    epub_ebook_id = epub_eb.id

    # Delete source if requested — re-link any pairs to the new EPUB first
    realign_failures: list[dict] = []
    relinked: List[int] = []
    if delete_source:
        relinked = await _relink_or_cleanup_pairs(eb.id, epub_eb, db)
        try:
            os.remove(eb.file_path)
        except OSError as e:
            logger.warning(f"[convert] could not delete source {eb.file_path}: {e}")
        await db.delete(eb)
        realign_failures = await _realign_relinked_pairs(relinked, db)

    await db.commit()

    return {
        "status": "converted",
        "epub_ebook_id": epub_ebook_id,
        "epub_path": epub_path,
        "source_deleted": delete_source,
        # A re-linked pair whose map was rebuilt now shares one axis with the
        # reader; one that couldn't be rebuilt says why (issue #101).
        "realigned": bool(relinked) and not realign_failures,
        "realign_error": realign_failures[0]["error"] if realign_failures else None,
    }


@router.delete("/unsupported/{ebook_id}/source")
async def delete_unsupported_source(
    ebook_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_editor_user),
):
    """Delete the source MOBI/AZW3 file (only if an EPUB version already exists)."""
    result = await db.execute(select(EBook).where(EBook.id == ebook_id))
    eb = result.scalar_one_or_none()
    if not eb:
        raise HTTPException(status_code=404, detail="Ebook not found")

    epub_sibling = Path(eb.file_path).with_suffix(".epub")
    if not epub_sibling.exists():
        raise HTTPException(
            status_code=400,
            detail="No converted EPUB found. Convert the file first before deleting the source.",
        )

    try:
        os.remove(eb.file_path)
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Could not delete file: {e}")

    # Ensure the EPUB is registered, then re-link any pairs to it (preserves pair + transcript)
    epub_eb = await _register_epub_in_db(str(epub_sibling), eb, db)
    relinked = await _relink_or_cleanup_pairs(ebook_id, epub_eb, db)

    await db.delete(eb)
    realign_failures = await _realign_relinked_pairs(relinked, db)
    await db.commit()
    return {
        "status": "deleted",
        "filename": eb.filename,
        "realigned": bool(relinked) and not realign_failures,
        "realign_error": realign_failures[0]["error"] if realign_failures else None,
    }


# NOTE: /unsupported/force-all must be registered BEFORE /unsupported/{ebook_id}/force
# so FastAPI doesn't try to interpret "force-all" as an integer ebook_id.
@router.delete("/unsupported/force-all")
async def force_delete_all_unsupported(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_editor_user),
):
    """Force-delete ALL unsupported ebooks (MOBI/AZW3) from the filesystem and library."""
    result = await db.execute(
        select(EBook).where(EBook.format.in_(["mobi", "azw3"]))
    )
    ebooks = result.scalars().all()

    deleted = []
    for eb in ebooks:
        await _relink_or_cleanup_pairs(eb.id, None, db)
        if eb.file_path:
            try:
                os.remove(eb.file_path)
            except OSError as e:
                logger.warning(f"[force-delete] could not delete {eb.file_path}: {e}")
        deleted.append(eb.filename)
        await db.delete(eb)

    await db.commit()
    return {"deleted": deleted, "total": len(deleted)}


@router.delete("/unsupported/{ebook_id}/force")
async def force_delete_unsupported(
    ebook_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_editor_user),
):
    """Force-delete a single unsupported ebook from the filesystem and library, even if no EPUB exists."""
    result = await db.execute(select(EBook).where(EBook.id == ebook_id))
    eb = result.scalar_one_or_none()
    if not eb:
        raise HTTPException(status_code=404, detail="Ebook not found")

    if eb.format not in ("mobi", "azw3"):
        raise HTTPException(status_code=400, detail="File is already in a supported format")

    filename = eb.filename
    await _relink_or_cleanup_pairs(eb.id, None, db)
    if eb.file_path:
        try:
            os.remove(eb.file_path)
        except OSError as e:
            logger.warning(f"[force-delete] could not delete {eb.file_path}: {e}")

    await db.delete(eb)
    await db.commit()
    return {"status": "deleted", "filename": filename}
