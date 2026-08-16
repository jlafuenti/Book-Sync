"""
Troubleshoot router — library health issues and fixes.

Surfaces all detected problems (corrupt audio, DRM/unreadable ebooks, missing
files, zero-byte/tiny files, unsupported formats, failed transcriptions, failed
ACSM imports) and provides fixes: delete (single + bulk), replace-file, convert,
and re-queue. Backed by the `library_verify` background scan for the expensive
integrity checks.
"""

import logging
import os
from collections import defaultdict
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel
from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database import get_db
from models.book import AudioBook, BookPair, EBook, PairStatus
from models.library_issue import LibraryCheckResult, MultiFileAudiobookFolder
from models.progress import UserProgress
from models.sync_map import SyncMap
from models.transcript import AudioTranscript
from services.file_hash import hash_bytes
from utils import safe_join
from models.transcription_queue import TranscriptionQueueItem
from models.user import User
from routers.auth import get_current_user, get_editor_user
from services import chapter_repair, library_verify

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/troubleshoot", tags=["troubleshoot"])

# A real audiobook is always well over this; an ebook over this. Anything
# smaller (but non-zero) is almost certainly a truncated/empty download.
_TINY_AUDIO_BYTES = 1 * 1024 * 1024     # 1 MB
_TINY_EBOOK_BYTES = 1 * 1024            # 1 KB
EBOOK_EXTENSIONS = {".epub", ".pdf", ".mobi", ".azw3"}
AUDIOBOOK_EXTENSIONS = {".mp3", ".m4a", ".m4b", ".flac", ".ogg", ".wav", ".aac", ".wma"}


def _is_inside(file_path: Optional[str], folder: str) -> bool:
    """Whether `file_path` sits directly in `folder` (not in a same-prefix sibling)."""
    if not file_path:
        return False
    return os.path.normpath(os.path.dirname(file_path)) == os.path.normpath(folder)


def _item_dict(item, item_type: str, detail: str = "", pair_id: Optional[int] = None) -> dict:
    return {
        "item_type": item_type,
        "item_id": item.id,
        "title": item.title,
        "author": item.author,
        "filename": item.filename,
        "file_path": item.file_path,
        "format": item.format,
        "file_size": item.file_size,
        "detail": detail,
        "pair_id": pair_id,
    }


# ---------------------------------------------------------------------------
# Issues
# ---------------------------------------------------------------------------

@router.get("/issues")
async def get_issues(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Return all current issues grouped by category. Cheap categories are
    computed live; audio/ebook integrity come from the last scan."""
    ebooks = (await db.execute(select(EBook))).scalars().all()
    audiobooks = (await db.execute(select(AudioBook))).scalars().all()

    missing, zero_byte, unsupported = [], [], []
    for eb in ebooks:
        if not eb.file_path or not os.path.isfile(eb.file_path):
            missing.append(_item_dict(eb, "ebook", "File not found on storage"))
        else:
            sz = os.path.getsize(eb.file_path)
            if sz < _TINY_EBOOK_BYTES:
                zero_byte.append(_item_dict(eb, "ebook", f"File is only {sz} bytes"))
        if (eb.format or "").lower() in ("mobi", "azw3"):
            converted = Path(eb.file_path).with_suffix(".epub").exists() if eb.file_path else False
            if not converted:
                unsupported.append(_item_dict(eb, "ebook", f"{(eb.format or '').upper()} needs conversion to EPUB"))

    chapter_encoding_bad = []
    for ab in audiobooks:
        if not ab.file_path or not os.path.isfile(ab.file_path):
            missing.append(_item_dict(ab, "audiobook", "File not found on storage"))
        else:
            sz = os.path.getsize(ab.file_path)
            if sz < _TINY_AUDIO_BYTES:
                zero_byte.append(_item_dict(ab, "audiobook", f"File is only {sz} bytes — likely truncated"))
            ok, detail = chapter_repair.check_chapter_encoding(ab.file_path)
            if not ok:
                chapter_encoding_bad.append(_item_dict(ab, "audiobook", detail or "Non-UTF-8 chapter title"))

    # Expensive integrity results from the last scan.
    audio_corrupt, ebook_drm, ebook_unreadable = [], [], []
    ab_by_id = {a.id: a for a in audiobooks}
    eb_by_id = {e.id: e for e in ebooks}
    rows = (await db.execute(
        select(LibraryCheckResult).where(LibraryCheckResult.ok == False)  # noqa: E712
    )).scalars().all()
    for r in rows:
        if r.check_type == "audio_integrity" and r.item_id in ab_by_id:
            audio_corrupt.append(_item_dict(ab_by_id[r.item_id], "audiobook", r.detail or "Corrupt audio"))
        elif r.check_type == "ebook_integrity" and r.item_id in eb_by_id:
            d = (r.detail or "").lower()
            bucket = ebook_drm if "drm" in d or "encrypt" in d else ebook_unreadable
            bucket.append(_item_dict(eb_by_id[r.item_id], "ebook", r.detail or "Unreadable ebook"))

    # Failed transcriptions: pairs in ERROR + latest queue error.
    failed_transcription = []
    err_pairs = (await db.execute(
        select(BookPair).where(BookPair.status == PairStatus.ERROR)
    )).scalars().all()
    for pair in err_pairs:
        q = (await db.execute(
            select(TranscriptionQueueItem)
            .where(TranscriptionQueueItem.book_pair_id == pair.id)
            .order_by(TranscriptionQueueItem.id.desc())
        )).scalars().first()
        eb = eb_by_id.get(pair.ebook_id)
        ab = ab_by_id.get(pair.audiobook_id)
        failed_transcription.append({
            "pair_id": pair.id,
            "ebook_id": pair.ebook_id,
            "audiobook_id": pair.audiobook_id,
            "title": (ab.title if ab else None) or (eb.title if eb else f"Pair {pair.id}"),
            "author": (ab.author if ab else None) or (eb.author if eb else None),
            "detail": (q.error_message if q and q.error_message else "Transcription failed"),
        })

    # Failed ACSM imports (quarantined files).
    failed_acsm = []
    try:
        from services.import_sources.acsm import _failed_dir
        fdir = _failed_dir()
        if fdir.exists():
            for p in sorted(fdir.iterdir()):
                if p.is_file():
                    failed_acsm.append({
                        "filename": p.name,
                        "file_path": str(p),
                        "file_size": p.stat().st_size,
                        "detail": "Import failed — see import logs (expired ACSM, device limit, or decryption error)",
                    })
    except Exception as e:
        logger.debug(f"failed_acsm listing skipped: {e}")

    # Synced pairs that are missing their sync map.
    sync_map_missing = []
    all_pairs = (await db.execute(select(BookPair))).scalars().all()
    syncmap_pair_ids = set(
        (await db.execute(select(SyncMap.book_pair_id))).scalars().all()
    )
    for pair in all_pairs:
        if pair.status == PairStatus.SYNCED and pair.id not in syncmap_pair_ids:
            eb = eb_by_id.get(pair.ebook_id)
            ab = ab_by_id.get(pair.audiobook_id)
            sync_map_missing.append({
                "pair_id": pair.id,
                "ebook_id": pair.ebook_id,
                "audiobook_id": pair.audiobook_id,
                "title": (eb.title if eb else None) or (ab.title if ab else f"Pair {pair.id}"),
                "author": (eb.author if eb else None) or (ab.author if ab else None),
                "detail": "Marked synced but has no sync map — re-queue to rebuild it",
            })

    # Duplicate files (same content hash) within each media type.
    duplicate = []
    by_hash = defaultdict(list)
    for eb in ebooks:
        if eb.file_hash:
            by_hash[("ebook", eb.file_hash)].append(eb)
    for ab in audiobooks:
        if ab.file_hash:
            by_hash[("audiobook", ab.file_hash)].append(ab)
    for (itype, h), group in by_hash.items():
        if len(group) > 1:
            for it in group:
                duplicate.append(_item_dict(it, itype, f"{len(group)} copies share hash {h[:12]}…"))

    # Covers: missing (broken/absent) and orphaned (file with no owner).
    covers_dir = settings.covers_dir
    missing_cover = []
    referenced = set()

    def _cover_filename(cover_path: Optional[str]) -> Optional[str]:
        if not cover_path:
            return None
        return os.path.basename(cover_path.split("?")[0])

    for item, itype in [(e, "ebook") for e in ebooks] + [(a, "audiobook") for a in audiobooks]:
        fn = _cover_filename(item.cover_path)
        if fn:
            referenced.add(fn)
            if not os.path.isfile(os.path.join(covers_dir, fn)):
                missing_cover.append(_item_dict(item, itype, "Cover reference set but file is missing"))
        else:
            missing_cover.append(_item_dict(item, itype, "No cover image"))

    orphaned_cover = []
    if os.path.isdir(covers_dir):
        for f in sorted(os.listdir(covers_dir)):
            fp = os.path.join(covers_dir, f)
            if os.path.isfile(fp) and f not in referenced:
                orphaned_cover.append({
                    "filename": f,
                    "file_path": fp,
                    "file_size": os.path.getsize(fp),
                    "detail": "Cover file not referenced by any book",
                })

    # Multi-file audiobook folders the scanner refused to import (issue #63).
    # Persisted by the scan; dismissed rows stay hidden until the folder's
    # contents change. `imported_track_count` is live: rows that predate the
    # detection (one AudioBook per track) can be removed from here.
    multi_file = []
    folder_rows = (await db.execute(
        select(MultiFileAudiobookFolder)
        .where(MultiFileAudiobookFolder.dismissed == False)
        .order_by(MultiFileAudiobookFolder.folder_path)
    )).scalars().all()
    for row in folder_rows:
        multi_file.append({
            "item_type": "folder",
            "item_id": row.id,
            "title": row.guessed_title or os.path.basename(row.folder_path),
            "author": row.guessed_author,
            "filename": None,
            "file_path": row.folder_path,
            "format": row.extension.lstrip("."),
            "file_size": row.total_size,
            "file_count": row.file_count,
            "extension": row.extension,
            "imported_track_count": sum(
                1 for ab in audiobooks if _is_inside(ab.file_path, row.folder_path)),
            "detail": (f"{row.file_count} {row.extension} files — multi-file audiobooks "
                       f"aren't supported; merge to one .m4b in Audiobookshelf and rescan"),
            "pair_id": None,
        })

    categories = {
        "missing": missing,
        "zero_byte": zero_byte,
        "chapter_encoding_bad": chapter_encoding_bad,
        "audio_corrupt": audio_corrupt,
        "ebook_drm": ebook_drm,
        "ebook_unreadable": ebook_unreadable,
        "unsupported_format": unsupported,
        "multi_file_audiobook": multi_file,
        "sync_map_missing": sync_map_missing,
        "duplicate": duplicate,
        "missing_cover": missing_cover,
        "orphaned_cover": orphaned_cover,
        "failed_transcription": failed_transcription,
        "failed_acsm": failed_acsm,
    }
    return {
        "categories": categories,
        "counts": {k: len(v) for k, v in categories.items()},
        "total": sum(len(v) for v in categories.values()),
    }


# ---------------------------------------------------------------------------
# Scan
# ---------------------------------------------------------------------------

@router.post("/scan", status_code=202)
async def start_scan(_: User = Depends(get_editor_user)):
    started = await library_verify.start_scan()
    if not started:
        raise HTTPException(status_code=409, detail="A verification scan is already running")
    return {"status": "started"}


@router.get("/scan/progress")
async def scan_progress(_: User = Depends(get_current_user)):
    return library_verify.get_progress()


@router.post("/scan/cancel")
async def scan_cancel(_: User = Depends(get_editor_user)):
    library_verify.request_cancel()
    return {"status": "cancel_requested"}


# ---------------------------------------------------------------------------
# Fixes: delete (single + bulk), replace, requeue, acsm-dismiss
# ---------------------------------------------------------------------------

async def _delete_item_core(db: AsyncSession, item_type: str, item_id: int, delete_file: bool) -> None:
    model = EBook if item_type == "ebook" else AudioBook
    item = (await db.execute(select(model).where(model.id == item_id))).scalar_one_or_none()
    if not item:
        return
    file_path = item.file_path

    pair_col = BookPair.ebook_id if item_type == "ebook" else BookPair.audiobook_id
    pairs = (await db.execute(select(BookPair).where(pair_col == item_id))).scalars().all()
    for pair in pairs:
        await db.execute(sa_delete(TranscriptionQueueItem).where(TranscriptionQueueItem.book_pair_id == pair.id))
        await db.execute(sa_delete(UserProgress).where(UserProgress.book_pair_id == pair.id))
        await db.execute(sa_delete(AudioTranscript).where(AudioTranscript.pair_id == pair.id))

    if item_type == "ebook":
        await db.execute(sa_delete(UserProgress).where(UserProgress.ebook_id == item_id))
    else:
        await db.execute(sa_delete(UserProgress).where(UserProgress.audiobook_id == item_id))

    await db.execute(sa_delete(LibraryCheckResult).where(
        LibraryCheckResult.item_type == item_type, LibraryCheckResult.item_id == item_id))

    await db.delete(item)
    await db.commit()

    if delete_file and file_path:
        try:
            os.unlink(file_path)
        except OSError as e:
            logger.warning(f"Could not delete source file {file_path}: {e}")


class BulkDeleteItem(BaseModel):
    item_type: str
    item_id: int


class BulkDeleteRequest(BaseModel):
    items: List[BulkDeleteItem]


@router.post("/bulk-delete")
async def bulk_delete(
    req: BulkDeleteRequest,
    delete_file: bool = Query(True),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_editor_user),
):
    deleted = 0
    for entry in req.items:
        if entry.item_type not in ("ebook", "audiobook"):
            continue
        await _delete_item_core(db, entry.item_type, entry.item_id, delete_file)
        deleted += 1
    return {"deleted": deleted}


@router.post("/replace/{item_type}/{item_id}")
async def replace_file(
    item_type: str,
    item_id: int,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_editor_user),
):
    """Replace an item's underlying file in place (keeps the DB row and its
    pairing). Metadata from the new file wins; gaps are filled from the old row."""
    if item_type not in ("ebook", "audiobook"):
        raise HTTPException(status_code=400, detail="item_type must be 'ebook' or 'audiobook'")
    model = EBook if item_type == "ebook" else AudioBook
    allowed = EBOOK_EXTENSIONS if item_type == "ebook" else AUDIOBOOK_EXTENSIONS

    item = (await db.execute(select(model).where(model.id == item_id))).scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail=f"{item_type} not found")

    new_ext = Path(file.filename).suffix.lower()
    if new_ext not in allowed:
        raise HTTPException(status_code=400, detail=f"Unsupported {item_type} format: {new_ext}")

    old_path = item.file_path
    default_dir = settings.ebook_dir if item_type == "ebook" else settings.audiobook_dir
    dest_dir = os.path.dirname(old_path) if old_path else default_dir
    os.makedirs(dest_dir, exist_ok=True)
    base = Path(old_path).stem if old_path else Path(safe_join(dest_dir, file.filename).name).stem
    dest_path = str(safe_join(dest_dir, base + new_ext))

    content = await file.read()
    with open(dest_path, "wb") as f:
        f.write(content)

    # Remove the old file if its path differs from the new one.
    if old_path and os.path.abspath(old_path) != os.path.abspath(dest_path) and os.path.isfile(old_path):
        try:
            os.unlink(old_path)
        except OSError as e:
            logger.warning(f"Could not remove old file {old_path}: {e}")

    # Merge metadata: new file's values win; otherwise keep what the row has.
    from routers.library import extract_metadata
    try:
        new_meta = await extract_metadata(dest_path, item_type, db, library_root=default_dir)
    except Exception as e:
        logger.warning(f"replace: metadata extract failed for {dest_path}: {e}")
        new_meta = {}

    item.file_path = dest_path
    item.filename = base + new_ext
    item.file_size = len(content)
    item.file_hash = hash_bytes(content)
    item.format = new_ext.lstrip(".")
    for field in ("title", "author", "series", "series_index"):
        val = new_meta.get(field)
        if val:
            setattr(item, field, val)
    if not item.title:
        item.title = base

    # Replacing an audiobook invalidates the cached transcript & sync.
    if item_type == "audiobook":
        pairs = (await db.execute(select(BookPair).where(BookPair.audiobook_id == item_id))).scalars().all()
        for pair in pairs:
            await db.execute(sa_delete(AudioTranscript).where(AudioTranscript.pair_id == pair.id))
            if pair.status in (PairStatus.SYNCED, PairStatus.ERROR, PairStatus.TRANSCRIBING):
                pair.status = PairStatus.MANUAL_MATCHED

    # Re-run the integrity check so the issue clears (or re-flags) immediately.
    import asyncio as _asyncio
    if item_type == "audiobook":
        from services.audio_integrity import check_audio_integrity
        ok, det = await _asyncio.to_thread(check_audio_integrity, dest_path)
        check_type = "audio_integrity"
    else:
        from services.ebook_integrity import check_ebook_integrity
        ok, det = await _asyncio.to_thread(check_ebook_integrity, dest_path)
        check_type = "ebook_integrity"

    row = (await db.execute(select(LibraryCheckResult).where(
        LibraryCheckResult.item_type == item_type,
        LibraryCheckResult.item_id == item_id,
        LibraryCheckResult.check_type == check_type,
    ))).scalar_one_or_none()
    if row is None:
        row = LibraryCheckResult(item_type=item_type, item_id=item_id, check_type=check_type)
        db.add(row)
    st = os.stat(dest_path)
    row.file_path = dest_path
    row.file_size = st.st_size
    row.file_mtime = st.st_mtime
    row.ok = ok
    row.detail = det

    await db.commit()
    return {"status": "replaced", "integrity_ok": ok, "detail": det, "item_id": item_id}


@router.post("/repair-chapter-encoding/{item_id}")
async def repair_chapter_encoding_endpoint(
    item_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_editor_user),
):
    """Repair a single audiobook's non-UTF-8 chapter titles in place."""
    ab = (await db.execute(select(AudioBook).where(AudioBook.id == item_id))).scalar_one_or_none()
    if not ab:
        raise HTTPException(status_code=404, detail="Audiobook not found")
    if not ab.file_path or not os.path.isfile(ab.file_path):
        raise HTTPException(status_code=404, detail="Audiobook file not found on disk")

    import asyncio
    ok, error = await asyncio.to_thread(chapter_repair.repair_chapter_encoding, ab.file_path)
    if ok:
        return {"status": "repaired", "detail": None, "item_id": item_id}
    _, recheck_detail = await asyncio.to_thread(chapter_repair.check_chapter_encoding, ab.file_path)
    return {"status": "failed", "detail": error or recheck_detail, "item_id": item_id}


class BulkRepairChapterEncodingRequest(BaseModel):
    item_ids: List[int]


@router.post("/bulk-repair-chapter-encoding")
async def bulk_repair_chapter_encoding(
    req: BulkRepairChapterEncodingRequest,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_editor_user),
):
    import asyncio
    repaired = 0
    failures = []
    for item_id in req.item_ids:
        ab = (await db.execute(select(AudioBook).where(AudioBook.id == item_id))).scalar_one_or_none()
        if not ab or not ab.file_path or not os.path.isfile(ab.file_path):
            failures.append({"item_id": item_id, "title": ab.title if ab else None, "error": "File not found on disk"})
            continue
        ok, error = await asyncio.to_thread(chapter_repair.repair_chapter_encoding, ab.file_path)
        if ok:
            repaired += 1
        else:
            failures.append({"item_id": item_id, "title": ab.title, "error": error})
    return {"repaired": repaired, "failures": failures}


@router.post("/requeue/{pair_id}")
async def requeue_pair(
    pair_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_editor_user),
):
    pair = (await db.execute(select(BookPair).where(BookPair.id == pair_id))).scalar_one_or_none()
    if not pair:
        raise HTTPException(status_code=404, detail="Pair not found")
    from services.queue_manager import add_to_queue
    await add_to_queue([pair_id])
    return {"status": "queued", "pair_id": pair_id}


class DeleteCoversRequest(BaseModel):
    filenames: List[str]


@router.post("/delete-orphan-covers")
async def delete_orphan_covers(req: DeleteCoversRequest, _: User = Depends(get_editor_user)):
    """Delete orphaned cover files. Paths are constrained to the covers dir."""
    covers_dir = os.path.abspath(settings.covers_dir)
    deleted = 0
    for name in req.filenames:
        fp = os.path.abspath(os.path.join(covers_dir, os.path.basename(name)))
        if os.path.dirname(fp) != covers_dir:
            continue
        if os.path.isfile(fp):
            try:
                os.unlink(fp)
                deleted += 1
            except OSError as e:
                logger.warning(f"Could not delete cover {fp}: {e}")
    return {"deleted": deleted}


class AcsmDismissRequest(BaseModel):
    filename: str


@router.post("/acsm-dismiss")
async def acsm_dismiss(req: AcsmDismissRequest, _: User = Depends(get_editor_user)):
    from services.import_sources.acsm import _failed_dir
    target = _failed_dir() / Path(req.filename).name
    if not target.is_file():
        raise HTTPException(status_code=404, detail="File not found in failed imports")
    try:
        target.unlink()
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Could not delete: {e}")
    return {"status": "dismissed"}


# ---------------------------------------------------------------------------
# Multi-file audiobook folders (issue #63)
# ---------------------------------------------------------------------------

async def _folder_or_404(db: AsyncSession, folder_id: int) -> MultiFileAudiobookFolder:
    row = (await db.execute(
        select(MultiFileAudiobookFolder).where(MultiFileAudiobookFolder.id == folder_id)
    )).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Multi-file audiobook folder not found")
    return row


@router.post("/multi-file/{folder_id}/dismiss")
async def multi_file_dismiss(
    folder_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_editor_user),
):
    """Hide a flagged folder until its contents change (the next scan clears
    the dismissal when the folder's fingerprint moves)."""
    row = await _folder_or_404(db, folder_id)
    row.dismissed = True
    await db.commit()
    return {"status": "dismissed", "id": row.id}


@router.post("/multi-file/{folder_id}/remove-tracks")
async def multi_file_remove_tracks(
    folder_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_editor_user),
):
    """Delete the AudioBook rows that were imported one-per-track from this
    folder before multi-file detection existed. DB rows only — the files stay
    on disk for merging in Audiobookshelf; the folder stays flagged."""
    row = await _folder_or_404(db, folder_id)
    audiobooks = (await db.execute(select(AudioBook))).scalars().all()
    victims = [ab.id for ab in audiobooks if _is_inside(ab.file_path, row.folder_path)]
    for audiobook_id in victims:
        await _delete_item_core(db, "audiobook", audiobook_id, delete_file=False)
    return {"deleted": len(victims), "id": row.id}
