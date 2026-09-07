"""
Library ingest: the full scan, the targeted scan, and the per-file ingest they share.

Split out of `routers/library.py` (issue #255). `POST /api/library/scan`, both
upload endpoints, the import sources and the import scheduler all end up here:

* `scan_library_impl` walks both library roots and ingests every supported
  file, committing every `SCAN_COMMIT_BATCH` files (issue #202);
* `scan_files_impl` ingests exactly the paths it is given — what an upload or
  an import source calls after placing one file;
* `_ingest_one_ebook` / `_ingest_one_audiobook` enrich an existing row or
  insert a new one, racing safely against a concurrent insert on the same
  path (`_insert_or_reread`, issue #256);
* `_Batch` is the commit cadence the other long library jobs (rescan-all,
  enrich-abs) reuse.

Everything that touches the disk — hashing, tag reading, cover extraction,
the directory walk, the multi-file folder classifier — is a sync helper the
async functions reach through `asyncio.to_thread`, because one uvicorn worker
serves every request (issue #203). The scan path is tracked separately for
redesign; this module is a behaviour-preserving move of it.
"""

import asyncio
import logging
import os
from pathlib import Path
from typing import List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from models.book import AudioBook, EBook
from schemas import LibraryScanResponse
from services import multi_file_audiobooks
from services.abs_metadata import (
    enrich_from_abs,
    fetch_abs_index,
    load_abs_settings as _load_abs_settings,
    write_metadata_to_file,
)
from services.auto_match import auto_match_books
from services.metadata_extract import (
    _extract_and_save_cover,
    compute_file_hash,
    extract_metadata,
)
from services.metadata_utils import MEDIA_FILL_IF_NULL_FIELDS

logger = logging.getLogger(__name__)

# Supported file extensions
EBOOK_EXTENSIONS = {".epub", ".pdf", ".mobi", ".azw3"}
AUDIOBOOK_EXTENSIONS = {".mp3", ".m4a", ".m4b", ".flac", ".ogg", ".wav", ".aac", ".wma"}


def _hash_and_size(filepath: str) -> Tuple[str, int]:
    """Composite hash and byte size of one file. Blocking; call it on a thread.

    Both read the file, so they ride in one helper and cross to a worker thread
    together (issue #203) rather than blocking the loop per book — the same shape
    `hash_file` already had at the rehash endpoint.
    """
    return compute_file_hash(filepath), os.path.getsize(filepath)


async def _find_by_path(db: AsyncSession, model, filepath: str):
    """The row for `filepath`, or None.

    `file_path` is the library's identity key and is unique per table (issue
    #256), so `scalar_one_or_none` is safe. Factored out because both ingest
    helpers use it twice — once to decide insert-vs-enrich, and once more to
    recover the losing side of a race (see :func:`_insert_or_reread`).
    """
    result = await db.execute(select(model).where(model.file_path == filepath))
    return result.scalar_one_or_none()


async def _insert_or_reread(db: AsyncSession, row, model, filepath: str):
    """Insert `row`, or hand back whatever a racing transaction inserted first.

    Ingestion is check-then-insert, which cannot be made race-free in the
    application: two scans (or a scan and an upload) both look the path up, both
    miss, and both insert. The unique index on `file_path` means only one lands
    — and without this the loser's `IntegrityError` would poison the whole
    transaction, taking every book already ingested in this batch with it.

    So the insert happens inside a savepoint; the loser rolls back just that far
    and re-reads the winner's row. Same pattern, and the same reasoning, as
    `position_service._insert_or_reread` (issue #64).

    Returns `(row_now_in_the_database, created)`.
    """
    savepoint = await db.begin_nested()
    try:
        db.add(row)
        await db.flush()
    except IntegrityError:
        # The rollback detaches `row` from the session for us.
        await savepoint.rollback()
        won = await _find_by_path(db, model, filepath)
        if won is None:
            # The conflict wasn't the one we're recovering from; don't swallow it.
            raise
        logger.info(f"[scan] Lost the insert race for {filepath}; using row {won.id}")
        return won, False
    return row, True


async def _ingest_one_ebook(db: AsyncSession, filepath: str, ebook_dir: str) -> bool:
    """
    Process a single ebook file: enrich an existing row, or create a new
    one. Returns True if a new row was created.
    """
    filename = os.path.basename(filepath)
    ext = Path(filename).suffix.lower()

    existing_ebook = await _find_by_path(db, EBook, filepath)

    if existing_ebook:
        meta = await extract_metadata(filepath, "ebook", db, library_root=ebook_dir)
        updated = False

        if not existing_ebook.metadata_source:
            existing_ebook.title = meta.get("title") or existing_ebook.title
            existing_ebook.author = meta.get("author") or existing_ebook.author
            existing_ebook.metadata_source = meta.get("_metadata_source")
            existing_ebook.metadata_pattern = meta.get("_metadata_pattern")
            updated = True

        if meta.get("series") and not existing_ebook.series:
            existing_ebook.series = meta["series"]
            existing_ebook.series_index = meta.get("series_index")
            updated = True

        # Shared with the audiobook ingest below (issue #257). This list used to
        # be retyped here without `narrators`, so an ebook whose file carried a
        # narrator tag never got one — even though `EBook.narrators` exists and
        # `extract_metadata` had already put the value in `meta`.
        for f in MEDIA_FILL_IF_NULL_FIELDS:
            if meta.get(f) is not None and getattr(existing_ebook, f) is None:
                setattr(existing_ebook, f, meta.get(f))
                updated = True

        if updated:
            db.add(existing_ebook)

        if not existing_ebook.cover_path:
            try:
                cover_path = await asyncio.to_thread(
                    _extract_and_save_cover, filepath, "ebook", existing_ebook.id, existing_ebook.title
                )
                if cover_path:
                    existing_ebook.cover_path = cover_path
                    db.add(existing_ebook)
            except Exception as e:
                logger.error(f"Error extracting cover after scan for ebook {existing_ebook.id}: {e}")
        return False

    try:
        file_hash, file_size = await asyncio.to_thread(_hash_and_size, filepath)
    except OSError:
        return False

    meta = await extract_metadata(filepath, "ebook", db, library_root=ebook_dir)

    ebook = EBook(
        title=meta.get("title") or filename,
        author=meta.get("author"),
        series=meta.get("series"),
        series_index=meta.get("series_index"),
        description=meta.get("description"),
        publisher=meta.get("publisher"),
        publish_year=meta.get("publish_year"),
        language=meta.get("language"),
        genres=meta.get("genres"),
        tags=meta.get("tags"),
        isbn=meta.get("isbn"),
        asin=meta.get("asin"),
        metadata_source=meta.get("_metadata_source"),
        metadata_pattern=meta.get("_metadata_pattern"),
        filename=filename,
        file_path=filepath,
        file_hash=file_hash,
        file_size=file_size,
        format=ext.lstrip("."),
    )
    ebook, created = await _insert_or_reread(db, ebook, EBook, filepath)

    if not ebook.cover_path:
        try:
            cover_path = await asyncio.to_thread(
                _extract_and_save_cover, filepath, "ebook", ebook.id, ebook.title
            )
            if cover_path:
                ebook.cover_path = cover_path
                db.add(ebook)
        except Exception as e:
            logger.error(f"Error extracting cover for new ebook {ebook.id}: {e}")

    return created


async def _ingest_one_audiobook(
    db: AsyncSession, filepath: str, audiobook_dir: str, abs_index: dict
) -> bool:
    """
    Process a single audiobook file: enrich an existing row, or create a
    new one. Returns True if a new row was created.
    """
    filename = os.path.basename(filepath)
    ext = Path(filename).suffix.lower()

    existing_audiobook = await _find_by_path(db, AudioBook, filepath)

    if existing_audiobook:
        meta = await extract_metadata(filepath, "audiobook", db, library_root=audiobook_dir)

        if abs_index:
            meta, abs_changed, _ = enrich_from_abs(meta, filepath, abs_index, audiobook_dir)
            # Respect user-cleared fields: empty string means user explicitly cleared it.
            if existing_audiobook.series == "":
                meta.pop("series", None)
                meta.pop("series_index", None)
            if abs_changed:
                await asyncio.to_thread(write_metadata_to_file, filepath, meta)

        updated = False
        if not existing_audiobook.metadata_source:
            existing_audiobook.title = meta.get("title") or existing_audiobook.title
            existing_audiobook.author = meta.get("author") or existing_audiobook.author
            existing_audiobook.metadata_source = meta.get("_metadata_source")
            existing_audiobook.metadata_pattern = meta.get("_metadata_pattern")
            updated = True

        if meta.get("series") and existing_audiobook.series is None:
            existing_audiobook.series = meta["series"]
            existing_audiobook.series_index = meta.get("series_index")
            updated = True

        for f in MEDIA_FILL_IF_NULL_FIELDS:
            if meta.get(f) is not None and getattr(existing_audiobook, f) is None:
                setattr(existing_audiobook, f, meta.get(f))
                updated = True

        # Duration is deliberately *not* in that fill-if-null list: the file is
        # the authority, so a length we can read wins over whatever is stored
        # (a replaced or re-encoded file must not keep a stale end zone). This
        # branch is also the backfill — a library scanned before #127 shipped
        # gets every row populated by one ordinary scan.
        if meta.get("duration_seconds") and existing_audiobook.duration_seconds != meta["duration_seconds"]:
            existing_audiobook.duration_seconds = meta["duration_seconds"]
            updated = True

        if updated:
            db.add(existing_audiobook)

        if not existing_audiobook.cover_path:
            try:
                cover_path = await asyncio.to_thread(
                    _extract_and_save_cover, filepath, "audiobook", existing_audiobook.id
                )
                if cover_path:
                    existing_audiobook.cover_path = cover_path
                    db.add(existing_audiobook)
            except Exception as e:
                logger.error(f"Error extracting cover after scan for audiobook {existing_audiobook.id}: {e}")
        return False

    try:
        file_hash, file_size = await asyncio.to_thread(_hash_and_size, filepath)
    except OSError:
        return False

    meta = await extract_metadata(filepath, "audiobook", db, library_root=audiobook_dir)

    if abs_index:
        meta, abs_changed, _ = enrich_from_abs(meta, filepath, abs_index, audiobook_dir)
        if abs_changed:
            await asyncio.to_thread(write_metadata_to_file, filepath, meta)

    audiobook = AudioBook(
        title=meta["title"] or filename,
        author=meta["author"],
        series=meta["series"],
        series_index=meta["series_index"],
        description=meta.get("description"),
        publisher=meta.get("publisher"),
        publish_year=meta.get("publish_year"),
        language=meta.get("language"),
        genres=meta.get("genres"),
        tags=meta.get("tags"),
        isbn=meta.get("isbn"),
        asin=meta.get("asin"),
        narrators=meta.get("narrators"),
        duration_seconds=meta.get("duration_seconds"),
        is_explicit=meta.get("is_explicit", False),
        is_abridged=meta.get("is_abridged", False),
        metadata_source=meta.get("_metadata_source"),
        metadata_pattern=meta.get("_metadata_pattern"),
        filename=filename,
        file_path=filepath,
        file_hash=file_hash,
        file_size=file_size,
        format=ext.lstrip("."),
    )
    audiobook, created = await _insert_or_reread(db, audiobook, AudioBook, filepath)

    if not audiobook.cover_path:
        try:
            cover_path = await asyncio.to_thread(
                _extract_and_save_cover, filepath, "audiobook", audiobook.id
            )
            if cover_path:
                audiobook.cover_path = cover_path
                db.add(audiobook)
        except Exception as e:
            logger.error(f"Error extracting cover for new audiobook {audiobook.id}: {e}")

    return created


async def _maybe_load_abs_index(db: AsyncSession) -> dict:
    """Build the ABS metadata index once when enabled, else return {}."""
    abs_enabled, abs_url, abs_token, abs_prefix = await _load_abs_settings(db)
    if abs_enabled and abs_url and abs_token:
        return await asyncio.to_thread(fetch_abs_index, abs_url, abs_token, abs_prefix)
    return {}


async def scan_files_impl(
    db: AsyncSession, filepaths: list[str]
) -> LibraryScanResponse:
    """
    Targeted-scan: process exactly the files in `filepaths` instead of
    walking the whole library. Used by import sources after they place
    new files, so a single ACSM upload doesn't spend seconds re-checking
    every existing audiobook on disk.

    Each path is classified by extension into ebook vs audiobook and
    routed to the appropriate per-file ingest helper. ABS metadata
    enrichment runs once per call (not per file) when there's at least
    one audiobook in the list. Auto-pairing runs once at the end.
    """
    new_ebooks = 0
    new_audiobooks = 0
    ebook_dir = settings.ebook_dir
    audiobook_dir = settings.audiobook_dir

    # Partition by extension so we know whether to bother fetching the
    # ABS index (which is a network call we want to skip if no audiobooks).
    audiobook_paths: list[str] = []
    ebook_paths: list[str] = []
    for p in filepaths:
        if not os.path.isfile(p):
            continue
        ext = Path(p).suffix.lower()
        if ext in EBOOK_EXTENSIONS:
            ebook_paths.append(p)
        elif ext in AUDIOBOOK_EXTENSIONS:
            audiobook_paths.append(p)

    abs_index: dict = {}
    if audiobook_paths:
        abs_index = await _maybe_load_abs_index(db)

    for path in ebook_paths:
        if await _ingest_one_ebook(db, path, ebook_dir):
            new_ebooks += 1

    # A file inside a multi-file folder is judged with its siblings (issue
    # #63): if the folder is one book split into tracks, none of it imports.
    # Only the folders touched are re-flagged; a targeted scan says nothing
    # about the rest of the library, so it never prunes.
    flagged_groups = {}
    for folder in {os.path.dirname(p) for p in audiobook_paths}:
        # Lists the folder and reads every sibling's tags — off the loop (#203).
        for group in await asyncio.to_thread(_multi_file_groups, folder, audiobook_dir):
            flagged_groups[(group.folder_path, group.extension)] = group
    skip = {p for g in flagged_groups.values() for p in g.paths}
    if flagged_groups:
        await multi_file_audiobooks.sync_folder_flags(db, flagged_groups.values(), prune=False)

    for path in audiobook_paths:
        if path in skip:
            continue
        if await _ingest_one_audiobook(db, path, audiobook_dir, abs_index):
            new_audiobooks += 1

    await db.flush()
    auto_matched = await auto_match_books(db)

    return LibraryScanResponse(
        new_ebooks=new_ebooks,
        new_audiobooks=new_audiobooks,
        auto_matched_pairs=auto_matched,
        multi_file_folders=len(flagged_groups),
        message=(
            f"Scanned {len(filepaths)} file(s): {new_ebooks} new ebooks, "
            f"{new_audiobooks} new audiobooks, auto-matched {auto_matched} pairs."
            + (f" Skipped {len(flagged_groups)} multi-file audiobook folder(s)."
               if flagged_groups else "")
        ),
    )


def _multi_file_groups(folder: str, audiobook_dir: str):
    """The multi-file audiobook groups in one folder (issue #63).

    Blocking: it lists the folder and reads every file's tags. Callers inside an
    `async def` must reach it through `asyncio.to_thread` (issue #203).
    """
    try:
        names = [n for n in os.listdir(folder)
                 if os.path.isfile(os.path.join(folder, n))]
    except OSError:
        return []
    return multi_file_audiobooks.classify_folder(
        folder, names, multi_file_audiobooks.read_audio_tags, library_root=audiobook_dir,
    )


def _walk_tree(root: str) -> List[Tuple[str, List[str]]]:
    """`(directory, filenames)` for every directory under `root`; `[]` if absent.

    `os.walk` stats every entry it returns, so on a library of a few thousand
    files the walk alone is seconds of blocking work. Materialising it in one
    sync helper lets the caller cross to a worker thread once (issue #203) —
    iterating the generator from the event loop would run those stat calls back
    on the loop, one directory at a time, which is the same bug in disguise.
    """
    if not os.path.isdir(root):
        return []
    return [(directory, files) for directory, _dirs, files in os.walk(root)]


def _classify_tree(tree: List[Tuple[str, List[str]]], audiobook_dir: str) -> list:
    """Multi-file audiobook groups across a whole walked tree (issue #63).

    Blocking — it reads the embedded tags of every audio file it considers — so
    the whole tree is classified in one hop to a worker thread rather than one
    hop per folder.
    """
    groups = []
    for directory, files in tree:
        groups.extend(multi_file_audiobooks.classify_folder(
            directory, files, multi_file_audiobooks.read_audio_tags,
            library_root=audiobook_dir,
        ))
    return groups


# Files ingested between commits during a full scan (issue #202). The scan used
# to be one transaction for the whole library: every write was a `flush()` and
# the only `commit()` was `get_db`'s at the end of the request, so a crash, a
# container restart or one unreadable file threw away everything the walk had
# done — minutes of work on the production library — while Postgres held a write
# transaction open for the duration.
#
# 50 is chosen for the crash window, not for throughput: small enough that a scan
# that dies has lost at most a handful of books (the next scan re-ingests them),
# large enough that the commit is a rounding error next to the per-file hashing
# and tag parsing around it.
SCAN_COMMIT_BATCH = 50


class _Batch:
    """Commit every `size` items, so a long job leaves its progress behind.

    Shared by the four library jobs (issue #202). All of them used to run as one
    request-long transaction and commit — if at all — only at the very end, which
    meant a crash discarded every row they had touched and Postgres held a write
    transaction open for the whole walk.

    `tick()` is called once per item, whether or not that item changed anything,
    so the commit interval tracks work done rather than rows written.
    """

    def __init__(self, db: AsyncSession, size: Optional[int] = None):
        self._db = db
        self._size = size if size is not None else SCAN_COMMIT_BATCH
        self._pending = 0

    async def tick(self) -> None:
        self._pending += 1
        if self._pending >= self._size:
            await self._db.commit()
            self._pending = 0


async def scan_library_impl(db: AsyncSession) -> LibraryScanResponse:
    """
    Full library scan: walk both library directories and ingest every
    supported file. The body of POST /api/library/scan minus auth.

    Commits every `SCAN_COMMIT_BATCH` files, so partial progress survives a crash
    and no single transaction stays open for the length of the walk. The response
    shape is unchanged; only the durability is.
    """
    new_ebooks = 0
    new_audiobooks = 0
    batch = _Batch(db)

    for root, files in await asyncio.to_thread(_walk_tree, settings.ebook_dir):
        for filename in files:
            if Path(filename).suffix.lower() not in EBOOK_EXTENSIONS:
                continue
            filepath = os.path.join(root, filename)
            if await _ingest_one_ebook(db, filepath, settings.ebook_dir):
                new_ebooks += 1
            await batch.tick()

    abs_index = await _maybe_load_abs_index(db)

    # Multi-file audiobook folders (issue #63): a folder whose same-extension
    # audio files are one book split into tracks is flagged, not imported —
    # `AudioBook` has one `file_path`, and one row per track polluted the
    # library and auto-pairing. The flags are reconciled after the walk so a
    # folder that stops qualifying (merged .m4b, tracks removed) clears.
    audio_tree = await asyncio.to_thread(_walk_tree, settings.audiobook_dir)
    flagged_groups = []
    if audio_tree:
        flagged_groups = await asyncio.to_thread(
            _classify_tree, audio_tree, settings.audiobook_dir
        )
        skip = {p for g in flagged_groups for p in g.paths}
        for root, files in audio_tree:
            for filename in files:
                if Path(filename).suffix.lower() not in AUDIOBOOK_EXTENSIONS:
                    continue
                filepath = os.path.join(root, filename)
                if filepath in skip:
                    continue
                if await _ingest_one_audiobook(db, filepath, settings.audiobook_dir, abs_index):
                    new_audiobooks += 1
                await batch.tick()
        await multi_file_audiobooks.sync_folder_flags(db, flagged_groups, prune=True)

    await db.flush()
    auto_matched = await auto_match_books(db)

    # The tail of the walk, the folder flags and the auto-matched pairs are all
    # still uncommitted here. `get_db` would commit them at the end of the
    # request, but the short final batch has to land the same way every other
    # batch did, and the import-scheduler caller has no request around it.
    await db.commit()

    return LibraryScanResponse(
        new_ebooks=new_ebooks,
        new_audiobooks=new_audiobooks,
        auto_matched_pairs=auto_matched,
        multi_file_folders=len(flagged_groups),
        message=f"Found {new_ebooks} new ebooks, {new_audiobooks} new audiobooks, "
                f"auto-matched {auto_matched} pairs."
                + (f" Skipped {len(flagged_groups)} multi-file audiobook folder(s) — "
                   f"see Troubleshoot Library." if flagged_groups else ""),
    )

