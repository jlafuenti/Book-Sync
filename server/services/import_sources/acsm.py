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
# defusedxml rather than the stdlib parser (issue #265). Python 3.12's expat
# already refuses external entities and caps amplification, so this changes no
# behaviour -- it makes the choice explicit for input that arrives as an
# attacker-supplied archive, and keeps bandit quiet on a public repo.
import defusedxml.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Optional

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


# Where Calibre keeps its configuration, and with it the DeACSM plugin's
# device keys. This used to be hardcoded to /root/.config/calibre; the server
# container no longer runs as root (issue #180), so that path is neither
# writable nor where `calibre-customize` installed the plugin at build time —
# the startup restore of the stored Adobe authorization would fail with EACCES
# and `is_adobe_id_authorized()` would report "not authorized" forever.
#
# Resolve it the way Calibre itself does: CALIBRE_CONFIG_DIRECTORY if set,
# otherwise $HOME/.config/calibre. The old root path is kept as a last resort so
# a container built before this change still finds its existing files.
_LEGACY_CALIBRE_CONFIG_DIR = Path("/root/.config/calibre")


def _calibre_config_dir() -> Path:
    override = os.environ.get("CALIBRE_CONFIG_DIRECTORY")
    if override:
        return Path(override)
    candidate = Path(os.path.expanduser("~")) / ".config" / "calibre"
    if not _dir_exists(candidate) and _dir_exists(_LEGACY_CALIBRE_CONFIG_DIR):
        return _LEGACY_CALIBRE_CONFIG_DIR
    return candidate


def _dir_exists(path: Path) -> bool:
    """`path.exists()` that treats "not allowed to look" as "not there".

    The legacy candidate lives under /root; when the app runs as an
    unprivileged user (issue #180) or in CI, /root is mode 0700 and the stat
    raises PermissionError instead of answering False.
    """
    try:
        return path.exists()
    except OSError:
        return False


def _adobe_id_path() -> Path:
    """The DeACSM plugin's account directory (devicesalt + the two XML files)."""
    return _calibre_config_dir() / "plugins" / "DeACSM" / "account"


_AUTHORIZE_SCRIPT = Path(__file__).parent / "_acsm_authorize.py"
_FULFILL_SCRIPT = Path(__file__).parent / "_acsm_fulfill.py"

# Credential-store key under which we serialize the DeACSM plugin's
# device authorization (devicesalt + device.xml + activation.xml). This
# rides the same encrypted import_source_credentials table used for
# Audible / ABS, so the auth survives container rebuilds without
# needing a dedicated docker volume.
_ACSM_CREDENTIAL_KEY = "acsm_adobe_account"
_ACSM_REQUIRED_FILES = ("devicesalt", "device.xml", "activation.xml")


# --- Adobe authorization persistence ---------------------------------------
# The DeACSM plugin keeps its device cert / keys in three small files under
# `_adobe_id_path()`. That path lives inside the container fs, so a
# `docker compose up --build` wipes it. We serialize the
# three files into one JSON blob and store it in the encrypted
# import_source_credentials table (same store Audible's auth blob uses) so
# the authorization survives rebuilds. Files are written back to disk at
# server lifespan startup before the plugin gets used.

def _read_account_files_from_disk() -> Optional[dict]:
    """Read the three account files from the plugin's on-disk dir.
    Returns None if any file is missing."""
    import base64

    if not _adobe_id_path().exists():
        return None
    out: dict = {}
    for name in _ACSM_REQUIRED_FILES:
        path = _adobe_id_path() / name
        if not path.is_file():
            return None
        data = path.read_bytes()
        # devicesalt is binary; the .xml files are text. Encode all as
        # base64 to keep the blob format uniform and survive any encoding
        # weirdness round-tripping through JSON / Postgres TEXT.
        out[name] = base64.b64encode(data).decode("ascii")
    return out


def _write_account_files_to_disk(blob: dict) -> None:
    """Write the three account files back to the plugin's on-disk dir."""
    import base64

    _adobe_id_path().mkdir(parents=True, exist_ok=True)
    for name in _ACSM_REQUIRED_FILES:
        if name not in blob:
            raise RuntimeError(f"acsm credential blob missing '{name}'")
        (_adobe_id_path() / name).write_bytes(base64.b64decode(blob[name]))


async def persist_adobe_account_to_credentials(db) -> None:
    """Serialize the on-disk Adobe account into the encrypted credential
    store. Call after a successful authorize_adobe_id() so the auth
    survives container rebuilds."""
    import json
    from services import credentials

    files = _read_account_files_from_disk()
    if files is None:
        # Nothing to save — authorize must have failed half-way through.
        return
    await credentials.set_credential(
        db, _ACSM_CREDENTIAL_KEY, json.dumps(files)
    )


async def restore_adobe_account_from_credentials(db) -> bool:
    """Re-hydrate the plugin's account dir from the encrypted credential
    store. Called at server startup. Returns True if it wrote files,
    False if there was nothing in the store to restore."""
    import json
    from services import credentials

    raw = await credentials.get_credential(db, _ACSM_CREDENTIAL_KEY)
    if not raw:
        return False
    try:
        blob = json.loads(raw)
        _write_account_files_to_disk(blob)
        logger.info("[acsm] restored Adobe authorization from credential store")
        return True
    except Exception as e:
        logger.exception(f"[acsm] could not restore Adobe authorization: {e}")
        return False


async def forget_adobe_account_in_credentials(db) -> None:
    """Drop the stored Adobe blob (called when the user revokes auth)."""
    from services import credentials
    await credentials.delete_credential(db, _ACSM_CREDENTIAL_KEY)


def is_adobe_id_authorized() -> bool:
    """
    The DeACSM plugin needs three artifacts on disk to decrypt an ACSM:
    a device key, a device file, and an activation file. Anything less
    than all three is a half-finished authorization that will fail at
    fulfillment time — which is much more confusing than just saying
    "not authorized" up front.
    """
    if not _adobe_id_path().exists():
        return False
    required = ("devicesalt", "device.xml", "activation.xml")
    return all((_adobe_id_path() / name).is_file() for name in required)


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
    if not _adobe_id_path().exists():
        return
    for child in _adobe_id_path().iterdir():
        try:
            if child.is_file() or child.is_symlink():
                child.unlink()
            elif child.is_dir():
                shutil.rmtree(child)
        except OSError as e:
            logger.warning(f"[acsm] could not remove {child}: {e}")


def _fulfill_acsm(acsm_path: Path, out_dir: Path) -> Path:
    """
    Fulfill an .acsm using DeACSM's libadobeFulfill directly, bypassing
    Calibre's `ebook-convert` pipeline, and decrypt the result. Returns the
    path to the resulting DRM-free .epub or .pdf.

    Adobe's fulfillment download is ADEPT-ENCRYPTED. The fulfill script
    (_acsm_fulfill.py) injects the license token as META-INF/rights.xml and
    then runs DeDRM's ineptepub with the DeACSM account key to produce a
    genuinely DRM-free EPUB (no encryption.xml). We drive fulfill() ourselves
    rather than using `ebook-convert` because Calibre's EPUB Input plugin trips
    on rights.xml with a DRMError before DeDRM can act.

    Requires the plugin to be linked to an authorized Adobe identity
    (anonymous registration is enough for most Google Play / Nook books).
    """
    if not is_adobe_id_authorized():
        raise RuntimeError(
            "ACSM source is not authorized with Adobe yet. Open the Import "
            "Sources page and click 'Authorize Adobe' on the Google Play / "
            "Nook card — anonymous authorization is enough for most books."
        )

    # Output extension is unknown until we see the downloaded bytes.
    # Reserve a base path; the script picks .epub or .pdf and writes there.
    # We pre-create as .epub since that's the overwhelmingly common case
    # and rename after the script returns if it produced a PDF.
    base = out_dir / (acsm_path.stem + ".epub")
    cmd = [
        "calibre-debug", "-e", str(_FULFILL_SCRIPT), "--",
        str(acsm_path), str(base),
    ]
    logger.info(f"[acsm] running: {' '.join(cmd)}")
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=180
        )
    except FileNotFoundError as e:
        raise RuntimeError("calibre-debug not found on PATH") from e

    if result.returncode != 0:
        diag = (result.stdout + "\n" + result.stderr).strip()
        logger.error(
            f"[acsm] fulfill failed for {acsm_path.name} (rc={result.returncode}):\n{diag}"
        )
        raise RuntimeError(_friendly_acsm_error(diag, result.returncode))

    if not base.exists() or base.stat().st_size == 0:
        # Either the script wrote a PDF at the same stem, or it wrote
        # nothing at all. Probe both.
        alt = base.with_suffix(".pdf")
        if alt.exists() and alt.stat().st_size > 0:
            return alt
        diag = (result.stdout + "\n" + result.stderr).strip()
        logger.error(f"[acsm] fulfill produced no output for {acsm_path.name}:\n{diag}")
        raise RuntimeError("Fulfillment did not produce a readable file.")

    return base


# Back-compat alias — the rest of the module still calls this name.
_convert_acsm_to_epub = _fulfill_acsm


# Human-readable translations for the most common Adobe ADEPT error codes
# that DeACSM surfaces inside its tracebacks. The codes are the canonical
# names Adobe documents — we match on substring so the error wrapping
# from various ADE versions all funnel to the same message.
_ADEPT_ERROR_HINTS: list[tuple[str, str]] = [
    (
        "E_ADEPT_REQUEST_EXPIRED",
        "This ACSM file has expired. Download a fresh copy from Google Play "
        "(or the original retailer) and upload it right away — the link is "
        "only valid for about 24 hours.",
    ),
    (
        "E_GOOGLE_DEVICE_LIMIT_REACHED",
        "Google Play has refused this download — your Google account has "
        "fulfilled this book to its device limit (typically 6). This is "
        "Google's limit, not Adobe's, so re-authorizing the server won't "
        "help. Options: (a) manage devices on play.google.com/books and "
        "remove old ones, then re-download the ACSM; (b) wait for Google's "
        "counter to relax (no published timeline); or (c) buy / borrow on "
        "a different platform (Kobo, library via Libby, etc.).",
    ),
    (
        "E_LIC_ALREADY_FULFILLED_BY_ANOTHER_USER",
        "This book has already been downloaded with a different Adobe ID. "
        "Re-authorize the server with that same Adobe ID (the 'Adobe ID' "
        "option on the authorize panel), or get a fresh ACSM.",
    ),
    (
        "E_ACT_NOT_READY",
        "The Adobe authorization on the server isn't fully active yet. "
        "Click 'Revoke Adobe authorization' and authorize again.",
    ),
    (
        "E_AUTH_BAD_DEVICE_KEY",
        "Adobe rejected the device key. Revoke the current authorization "
        "and authorize again to refresh the device registration.",
    ),
    (
        "E_LIC_LICENSE_SIGN_ERROR",
        "Adobe could not verify the license signature for this file. "
        "The ACSM may be corrupted — re-download it.",
    ),
    (
        "ADE auth is missing or broken",
        "The server isn't authorized with Adobe. Click 'Authorize Adobe' "
        "above to set it up.",
    ),
]


def _friendly_acsm_error(diag: str, returncode: int) -> str:
    """Pick a readable message from a long Calibre/DeACSM traceback."""
    for needle, message in _ADEPT_ERROR_HINTS:
        if needle in diag:
            return message
    # Fall back to the most informative-looking single line, trimmed.
    # The fulfill script prefixes useful lines with "Fulfillment refused:"
    # or "Download failed". Older code paths put a "DeACSM" prefix on info
    # lines. Probe both shapes.
    for prefix in ("Fulfillment refused", "Download failed", "DeACSM"):
        candidates = [
            ln.strip() for ln in diag.splitlines()
            if ln.strip() and prefix in ln and "Try" not in ln
        ]
        if candidates:
            return candidates[-1][:300]
    return f"ACSM conversion failed (exit {returncode}); see server logs."


_NS_DC = "http://purl.org/dc/elements/1.1/"
_NS_OPF = "http://www.idpf.org/2007/opf"
_NS_CONTAINER = "urn:oasis:names:tc:opendocument:xmlns:container"


def _read_epub_meta(epub_path: Path) -> dict:
    """
    Extract title / author / publisher / identifiers / series from an EPUB
    by parsing its OPF file directly.

    Why we don't use ebooklib here: ebooklib's read_epub() also tries to
    parse the legacy NCX navigation file and crashes with
    'NoneType' object has no attribute 'find' on EPUB 3 files that don't
    ship one (Google Play's exports, for instance). Reading just the OPF
    avoids the whole navigation-parsing path and is everything we need.
    """
    meta: dict = {}

    try:
        with zipfile.ZipFile(str(epub_path), "r") as z:
            # 1. Find the OPF path via META-INF/container.xml.
            try:
                container_bytes = z.read("META-INF/container.xml")
            except KeyError:
                logger.warning(f"[acsm] {epub_path.name}: missing META-INF/container.xml")
                return meta

            container_root = ET.fromstring(container_bytes)
            rootfile = container_root.find(
                f"{{{_NS_CONTAINER}}}rootfiles/{{{_NS_CONTAINER}}}rootfile"
            )
            if rootfile is None or "full-path" not in rootfile.attrib:
                logger.warning(f"[acsm] {epub_path.name}: container.xml has no rootfile")
                return meta
            opf_path = rootfile.attrib["full-path"]

            # 2. Parse the OPF.
            try:
                opf_bytes = z.read(opf_path)
            except KeyError:
                logger.warning(f"[acsm] {epub_path.name}: OPF not found at {opf_path}")
                return meta

        opf_root = ET.fromstring(opf_bytes)
        # Metadata block can be namespaced under OPF or unnamespaced; try both.
        metadata_elem = (
            opf_root.find(f"{{{_NS_OPF}}}metadata")
            or opf_root.find("metadata")
        )
        if metadata_elem is None:
            return meta

        def _first_text(tag: str) -> Optional[str]:
            el = metadata_elem.find(f"{{{_NS_DC}}}{tag}")
            if el is not None and el.text:
                return el.text.strip()
            return None

        title = _first_text("title")
        if title:
            meta["title"] = title
        creator = _first_text("creator")
        if creator:
            meta["author"] = creator
        publisher = _first_text("publisher")
        if publisher:
            meta["publisher"] = publisher

        # Identifiers: Google Play uses urn:gpb:id:XXX, Nook uses BN-style.
        identifiers: list[str] = []
        for ident_el in metadata_elem.findall(f"{{{_NS_DC}}}identifier"):
            if ident_el.text:
                identifiers.append(ident_el.text.strip())
        if identifiers:
            meta["identifiers"] = identifiers

        # calibre:series — written as <meta name="calibre:series" content="…"/>
        # in OPF 2.x, or as <meta property="…" refines="…"> in OPF 3.x. Cover
        # the common case (OPF 2-style) since that's what calibre + Google
        # Play actually emit.
        for meta_el in metadata_elem.findall(f"{{{_NS_OPF}}}meta") + metadata_elem.findall("meta"):
            name = meta_el.attrib.get("name", "")
            if name == "calibre:series":
                series_val = meta_el.attrib.get("content")
                if series_val:
                    meta["series"] = series_val.strip()
                    break

    except zipfile.BadZipFile:
        logger.warning(f"[acsm] {epub_path.name} is not a valid zip/EPUB")
    except ET.ParseError as e:
        logger.warning(f"[acsm] {epub_path.name}: OPF/container XML parse error: {e}")
    except Exception as e:
        logger.exception(f"[acsm] {epub_path.name}: unexpected error reading EPUB metadata: {e}")

    return meta


def _detect_source_from_meta(meta: dict, original_filename: str) -> str:
    """Best-effort guess of which retailer an EPUB came from."""
    blob = " ".join(str(v) for v in (meta.get("identifiers") or [])).lower()
    blob += " " + (meta.get("publisher") or "").lower()
    blob += " " + original_filename.lower()
    # Barnes & Noble names its ACSM files "BN_<id>.acsm"; treat that prefix as a
    # Nook signal (strict prefix to avoid false positives on stray "bn" substrings).
    fn = os.path.basename(original_filename).lower()
    if "gpb" in blob or "google" in blob or "play.google" in blob:
        return "google_play"
    if ("barnes" in blob or "nook" in blob or "bn.com" in blob
            or fn.startswith("bn_") or fn.startswith("bn-")):
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
            book_path = tmpdir / source_path.name
            shutil.copy2(source_path, book_path)
        elif ext == ".acsm":
            staged = tmpdir / source_path.name
            shutil.copy2(source_path, staged)
            book_path = await asyncio.to_thread(_convert_acsm_to_epub, staged, tmpdir)
        else:
            raise ValueError(f"Unsupported file type: {ext}")

        out_ext = book_path.suffix.lower()  # ".epub" or ".pdf"

        # Defense in depth: never let a still-encrypted / unreadable EPUB enter
        # the library. If decryption silently failed upstream, fail the import
        # here with a clear message instead of storing AES garbage. (sync()
        # wraps this into a per-item SyncError and moves the file to failed/.)
        if out_ext == ".epub":
            from services.ebook_integrity import check_ebook_integrity
            ok, detail = await asyncio.to_thread(check_ebook_integrity, str(book_path))
            if not ok:
                raise RuntimeError(f"Ebook failed integrity check after import — {detail}")

            meta = await asyncio.to_thread(_read_epub_meta, book_path)
        else:
            # PDFs don't have OPF metadata. Use the filename stem as title;
            # author / identifiers stay empty unless we want to bring in a
            # PDF metadata reader later.
            meta = {"title": book_path.stem}

        if not meta.get("title"):
            meta["title"] = book_path.stem

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
            source_path=str(book_path),
            extension=out_ext,
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
