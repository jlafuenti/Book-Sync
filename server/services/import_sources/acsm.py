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


_ADOBE_ID_PATH = Path("/root/.config/calibre/plugins/DeACSM/account")
_AUTHORIZE_SCRIPT = Path(__file__).parent / "_acsm_authorize.py"


def is_adobe_id_authorized() -> bool:
    """
    The DeACSM plugin needs three artifacts on disk to decrypt an ACSM:
    a device key, a device file, and an activation file. Anything less
    than all three is a half-finished authorization that will fail at
    fulfillment time — which is much more confusing than just saying
    "not authorized" up front.
    """
    if not _ADOBE_ID_PATH.exists():
        return False
    required = ("devicesalt", "device.xml", "activation.xml")
    return all((_ADOBE_ID_PATH / name).is_file() for name in required)


def authorize_adobe_id(mode: str = "anonymous", email: str = "", password: str = "") -> None:
    """
    Register the DeACSM plugin with an Adobe ID so it can decrypt ACSMs.
    Drives the plugin's bundled libadobeAccount via `calibre-debug -e`.

    mode = "anonymous" — works for most Google Play / Nook content; no
        email / password needed. Recommended.
    mode = "adobeid"   — register with a specific Adobe account; email
        and password required.

    Raises RuntimeError on failure (with the underlying tool's message).
    """
    if mode not in ("anonymous", "adobeid"):
        raise ValueError(f"unknown auth mode: {mode}")
    if mode == "adobeid" and not (email and password):
        raise ValueError("Adobe ID mode requires email and password")

    cmd = ["calibre-debug", "-e", str(_AUTHORIZE_SCRIPT), "--", mode]
    if mode == "adobeid":
        cmd.extend([email, password])
    logger.info(f"[acsm] authorizing DeACSM (mode={mode})")
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        # Log the full output so we can diagnose later. Pull the most
        # informative error line for the user — usually the line right
        # before the traceback ("Login failed: …", "Could not create…").
        full = (proc.stdout + "\n" + proc.stderr).strip()
        logger.error(
            f"[acsm] authorize subprocess exit {proc.returncode}:\n{full}"
        )
        lines = [
            ln.strip() for ln in full.splitlines()
            if ln.strip() and not ln.startswith(("Traceback", "  File ", "    "))
        ]
        # Drop noisy "TypeError: …" frames in favor of an upstream message
        # if there is one; otherwise show the last real line.
        msg = next(
            (ln for ln in lines if ln.startswith(("Login", "Could", "Authorization", "Device"))),
            lines[-1] if lines else f"exit {proc.returncode}",
        )
        # If state got partially written (the bug fails mid-flight), wipe
        # it so is_authorized() doesn't return True for a broken setup.
        try:
            deauthorize_adobe_id()
        except Exception:
            pass
        raise RuntimeError(msg[:400])


def deauthorize_adobe_id() -> None:
    """Wipe the plugin's account dir so the user can re-authorize with a
    different account (or anonymously)."""
    if not _ADOBE_ID_PATH.exists():
        return
    for child in _ADOBE_ID_PATH.iterdir():
        try:
            if child.is_file() or child.is_symlink():
                child.unlink()
            elif child.is_dir():
                shutil.rmtree(child)
        except OSError as e:
            logger.warning(f"[acsm] could not remove {child}: {e}")


def _convert_acsm_to_epub(acsm_path: Path, out_dir: Path) -> Path:
    """
    Convert .acsm to a DRM-free EPUB through Calibre's standard conversion
    pipeline. The DeACSM plugin is registered as a file-type plugin, so
    `ebook-convert input.acsm output.epub` automatically routes through it.

    Requires the plugin to be linked to an authorized Adobe ID (ADEPT DRM
    is keyed to an Adobe account). We surface a clear error if it isn't.
    """
    if not is_adobe_id_authorized():
        raise RuntimeError(
            "ACSM source is not authorized with Adobe yet. Open the Import "
            "Sources page and click 'Authorize Adobe' on the Google Play / "
            "Nook card — anonymous authorization is enough for most books."
        )

    out_path = out_dir / (acsm_path.stem + ".epub")
    cmd = ["ebook-convert", str(acsm_path), str(out_path)]
    logger.info(f"[acsm] running: {' '.join(cmd)}")
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=180
        )
    except FileNotFoundError as e:
        raise RuntimeError("ebook-convert not found on PATH") from e

    # Calibre always exits 0 on success and writes to stderr on info; check
    # the output file existence as the real success signal.
    if not out_path.exists() or out_path.stat().st_size == 0:
        diag = (result.stdout + "\n" + result.stderr).strip()
        diag = " ".join(diag.split())[:600] or f"ebook-convert exit {result.returncode}"
        raise RuntimeError(f"ACSM conversion failed: {diag}")

    return out_path


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
        # ACSM source is "connected" once the DeACSM plugin has an Adobe
        # device key on disk. Without one it can't decrypt anything, so
        # the UI hides the upload box and prompts the user to authorize.
        return is_adobe_id_authorized()

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
