"""
ACSM source adapter — covers Google Play Books and Nook.

Both vendors give you a `.acsm` file when you click "Download EPUB" on a DRM
title. We convert each `.acsm` to a DRM-free EPUB using the
`acsm-calibre-plugin` (Leseratte10) which can be invoked through Calibre's
`calibre-debug -r DeACSM` entry point. The user already has Calibre installed
on the docker server (see memory/reference_calibre.md).

Two intake paths, both handled here:
  1. Web upload — `routers/import_sources.py` writes the .acsm to a temp file
     and calls `process_file()`.
  2. Watched folder — the scheduler calls `process_inbox()` every minute; any
     .acsm in /data/imports/acsm/inbox/ is processed, then moved to
     /data/imports/acsm/processed/ (or /failed/ on error).

If the user drops a plain `.epub` (no DRM) into the inbox, we skip the
conversion step and place it directly — handy for Project Gutenberg etc.
"""

import asyncio
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

import ebooklib
from ebooklib import epub
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from models.book import EBook
from services.import_sources.base import (
    ProgressFn,
    SourceAdapter,
    SyncError,
    SyncResult,
    _noop_progress,
)
from services.library_writer import place_file

logger = logging.getLogger(__name__)


def _inbox_dir() -> Path:
    return Path(settings.imports_dir) / "acsm" / "inbox"


def _processed_dir() -> Path:
    return Path(settings.imports_dir) / "acsm" / "processed"


def _failed_dir() -> Path:
    return Path(settings.imports_dir) / "acsm" / "failed"


def _ensure_dirs() -> None:
    for d in (_inbox_dir(), _processed_dir(), _failed_dir()):
        d.mkdir(parents=True, exist_ok=True)


def _convert_acsm_to_epub(acsm_path: Path, out_dir: Path) -> Path:
    """
    Run the acsm-calibre-plugin via `calibre-debug -r DeACSM`. Returns the
    path to the output EPUB. Raises RuntimeError on failure.
    """
    cmd = ["calibre-debug", "-r", "DeACSM", "--", str(acsm_path)]
    logger.info(f"[acsm] running: {' '.join(cmd)} (cwd={out_dir})")
    try:
        result = subprocess.run(
            cmd, cwd=out_dir, capture_output=True, text=True, timeout=120
        )
    except FileNotFoundError as e:
        raise RuntimeError(
            "calibre-debug not found on PATH — install Calibre and the "
            "acsm-calibre-plugin in the server image."
        ) from e

    if result.returncode != 0:
        raise RuntimeError(
            f"acsm-calibre-plugin failed (rc={result.returncode}): "
            f"{result.stderr.strip() or result.stdout.strip()}"
        )

    epubs = sorted(out_dir.glob("*.epub"))
    if not epubs:
        raise RuntimeError(
            f"acsm-calibre-plugin produced no EPUB. stdout={result.stdout!r}"
        )
    return epubs[0]


def _read_epub_meta(epub_path: Path) -> dict:
    """Pull title/author/series from an EPUB's metadata."""
    book = epub.read_epub(str(epub_path))
    meta: dict = {}

    titles = book.get_metadata("DC", "title")
    if titles:
        meta["title"] = titles[0][0]

    creators = book.get_metadata("DC", "creator")
    if creators:
        meta["author"] = creators[0][0]

    publishers = book.get_metadata("DC", "publisher")
    if publishers:
        meta["publisher"] = publishers[0][0]

    # Identifier: Google Play has 'urn:gpb:id:XXXX'; Nook uses BN-format URNs.
    identifiers = book.get_metadata("DC", "identifier") or []
    for ident, attrs in identifiers:
        meta.setdefault("identifiers", []).append(ident)

    # Calibre series metadata is in the OPF as <meta name="calibre:series">.
    for ns in ("OPF", None):
        try:
            series = book.get_metadata(ns, "calibre:series") if ns else []
            if series:
                meta["series"] = series[0][0] if isinstance(series[0], tuple) else series[0]
                break
        except Exception:
            pass

    return meta


def _detect_source_from_meta(meta: dict, original_filename: str) -> str:
    """Best-effort guess of which retailer an EPUB came from."""
    blob = " ".join(str(v) for v in (meta.get("identifiers") or [])).lower()
    blob += " " + (meta.get("publisher") or "").lower()
    blob += " " + original_filename.lower()
    if "gpb" in blob or "google" in blob or "play.google" in blob:
        return "google_play"
    if "barnes" in blob or "nook" in blob or "bn.com" in blob:
        return "nook"
    return "acsm"


async def _is_duplicate(db: AsyncSession, external_id: Optional[str]) -> bool:
    if not external_id:
        return False
    result = await db.execute(
        select(EBook).where(EBook.external_id == external_id)
    )
    return result.scalar_one_or_none() is not None


async def process_file(db: AsyncSession, source_path: Path, original_filename: str) -> dict:
    """
    Convert (if needed) one ACSM/EPUB file and place it in the library.
    Returns a small dict describing the outcome.
    """
    _ensure_dirs()

    ext = source_path.suffix.lower()
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)

        if ext == ".epub":
            epub_path = tmpdir / source_path.name
            shutil.copy2(source_path, epub_path)
        elif ext == ".acsm":
            staged = tmpdir / source_path.name
            shutil.copy2(source_path, staged)
            epub_path = await asyncio.to_thread(_convert_acsm_to_epub, staged, tmpdir)
        else:
            raise ValueError(f"Unsupported file type: {ext}")

        meta = await asyncio.to_thread(_read_epub_meta, epub_path)
        if not meta.get("title"):
            meta["title"] = epub_path.stem

        external_id = next(
            (i for i in (meta.get("identifiers") or []) if i),
            None,
        )
        import_source = _detect_source_from_meta(meta, original_filename)

        if await _is_duplicate(db, external_id):
            return {
                "status": "skipped",
                "reason": "duplicate",
                "external_id": external_id,
                "title": meta.get("title"),
            }

        target_path = await place_file(
            db,
            book_type="ebook",
            source_path=str(epub_path),
            extension=".epub",
            meta=meta,
            move=True,
        )

        return {
            "status": "added",
            "import_source": import_source,
            "external_id": external_id,
            "title": meta.get("title"),
            "target_path": target_path,
        }


class AcsmSource(SourceAdapter):
    SOURCE_KEY = "acsm"
    DISPLAY_NAME = "Google Play / Nook (ACSM upload)"
    BOOK_TYPE = "ebook"
    SUPPORTS_AUTO_SYNC = True  # auto-sync = poll the watched folder

    async def is_connected(self, db: AsyncSession) -> bool:
        # Always "connected": this source needs no credentials.
        return True

    async def sync(self, db: AsyncSession, progress: ProgressFn = _noop_progress) -> SyncResult:
        """Process every file in the inbox folder."""
        _ensure_dirs()
        result = SyncResult()

        inbox = _inbox_dir()
        candidates = sorted(
            p for p in inbox.iterdir()
            if p.is_file() and p.suffix.lower() in (".acsm", ".epub")
        )
        total = len(candidates)

        for idx, path in enumerate(candidates, start=1):
            await progress(idx, total, path.name)
            try:
                outcome = await process_file(db, path, path.name)
                if outcome["status"] == "added":
                    result.items_added += 1
                    if outcome.get("title"):
                        result.added_titles.append(outcome["title"])
                    shutil.move(str(path), _processed_dir() / path.name)
                elif outcome["status"] == "skipped":
                    result.items_skipped += 1
                    shutil.move(str(path), _processed_dir() / path.name)
            except Exception as e:
                logger.exception(f"[acsm] failed to process {path.name}: {e}")
                result.errors.append(SyncError(
                    title=path.name,
                    error=str(e).strip().split("\n")[0][:300],
                ))
                try:
                    shutil.move(str(path), _failed_dir() / path.name)
                except Exception:
                    pass

        return result
