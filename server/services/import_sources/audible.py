"""
Audible source adapter.

Auth (one-time): browser-paste OAuth flow via `audible.Authenticator`. The
encrypted auth blob (refresh token + activation bytes) is stored in
import_source_credentials under source_key='audible'.

Sync: list the user's library via the audible.Client API, dedupe against
audiobooks.asin, and shell out to `audible-cli download` for any new ASINs.
audible-cli handles the AAXC license fetch, download, and ffmpeg decryption.
The resulting .m4b is then handed to library_writer.place_file().
"""

import asyncio
import concurrent.futures
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import audible  # mkb79's library
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.book import AudioBook
from services import credentials
from services.import_sources.base import (
    ImportedItem,
    ProgressFn,
    SourceAdapter,
    SyncError,
    SyncResult,
    _noop_progress,
)
from services.library_writer import place_file

logger = logging.getLogger(__name__)


# --- in-memory pending-login state -------------------------------------------
# audible.Authenticator.from_login_external() drives the whole OAuth flow
# itself: it generates a code_verifier, builds the login URL, calls a
# user-supplied callback with that URL, and expects the callback to return
# the post-login response URL. That blocks, which doesn't fit a stateless
# two-request browser flow.
#
# Bridge: run from_login_external in a background thread, hand the URL out
# of the thread via one Future, hand the response URL back into the thread
# via another Future. Single-process is fine for a home server.

DEFAULT_LOCALE = "us"  # Audible marketplace; could be configurable later.
_LOGIN_TIMEOUT_SECONDS = 600  # how long the user has to complete sign-in


@dataclass
class _PendingLogin:
    url_future: concurrent.futures.Future = field(default_factory=concurrent.futures.Future)
    response_future: concurrent.futures.Future = field(default_factory=concurrent.futures.Future)
    auth_future: concurrent.futures.Future = field(default_factory=concurrent.futures.Future)
    thread: Optional[threading.Thread] = None


_PENDING: dict[str, _PendingLogin] = {}


def _build_login_state(locale: str = DEFAULT_LOCALE) -> tuple[str, str]:
    """
    Start a login flow. Spins up a background thread that calls
    Authenticator.from_login_external; once that hits the URL callback we
    capture the URL and return it. Caller pairs (login_url, state_token)
    and passes state_token back when completing.
    """
    state_token = os.urandom(16).hex()
    pending = _PendingLogin()
    _PENDING[state_token] = pending

    def _login_url_callback(url: str) -> str:
        pending.url_future.set_result(url)
        return pending.response_future.result(timeout=_LOGIN_TIMEOUT_SECONDS)

    def _runner():
        try:
            auth = audible.Authenticator.from_login_external(
                locale=locale,
                login_url_callback=_login_url_callback,
            )
            pending.auth_future.set_result(auth)
        except Exception as e:
            pending.auth_future.set_exception(e)
            if not pending.url_future.done():
                pending.url_future.set_exception(e)
            if not pending.response_future.done():
                # Unblock any hanging callback if the failure happened pre-callback.
                pending.response_future.cancel()

    pending.thread = threading.Thread(target=_runner, daemon=True, name=f"audible-login-{state_token[:6]}")
    pending.thread.start()

    # Wait briefly for the URL — should be ~instant since the audible lib
    # does basically no I/O before invoking the callback.
    try:
        login_url = pending.url_future.result(timeout=15)
    except concurrent.futures.TimeoutError:
        _PENDING.pop(state_token, None)
        raise RuntimeError("Audible library did not produce a login URL in time.")
    except Exception as e:
        _PENDING.pop(state_token, None)
        raise RuntimeError(f"Audible login could not start: {e}")

    return login_url, state_token


async def _complete_login(state_token: str, response_url: str) -> audible.Authenticator:
    pending = _PENDING.pop(state_token, None)
    if not pending:
        raise ValueError("Unknown or expired state token — start the login over.")

    pending.response_future.set_result(response_url)

    def _wait_for_auth() -> audible.Authenticator:
        return pending.auth_future.result(timeout=60)

    auth = await asyncio.to_thread(_wait_for_auth)

    # Pre-fetch activation bytes so they end up persisted in the auth blob.
    try:
        await asyncio.to_thread(auth.get_activation_bytes)
    except Exception as e:
        logger.warning(f"[audible] could not fetch activation_bytes during login: {e}")
    return auth


# audible.Authenticator has built-in to_file/from_file (JSON). Round-trip
# through a temp file rather than reaching into private attributes — that
# keeps us compatible across audible-lib versions.

def _auth_to_blob(auth: audible.Authenticator) -> str:
    with tempfile.NamedTemporaryFile(mode="r", suffix=".json", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        auth.to_file(filename=str(tmp_path), encryption=False)
        return tmp_path.read_text()
    finally:
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass


def _blob_to_auth(blob: str) -> audible.Authenticator:
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as tmp:
        tmp.write(blob)
        tmp_path = Path(tmp.name)
    try:
        return audible.Authenticator.from_file(filename=str(tmp_path))
    finally:
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass


async def _load_auth(db: AsyncSession) -> Optional[audible.Authenticator]:
    blob = await credentials.get_credential(db, "audible")
    if not blob:
        return None
    try:
        return _blob_to_auth(blob)
    except Exception as e:
        logger.error(f"[audible] could not deserialize auth blob: {e}")
        return None


def _list_library(auth: audible.Authenticator) -> list[dict]:
    """
    Page through the user's full Audible library. Returns a list of dicts
    with at least: asin, title, authors, narrators, series, series_index.
    """
    items: list[dict] = []
    page = 1
    page_size = 1000
    response_groups = (
        "contributors,product_attrs,product_extended_attrs,product_desc,"
        "media,series,relationships"
    )
    with audible.Client(auth=auth) as client:
        while True:
            resp = client.get(
                "1.0/library",
                num_results=page_size,
                page=page,
                response_groups=response_groups,
            )
            chunk = resp.get("items", []) or []
            items.extend(chunk)
            if len(chunk) < page_size:
                break
            page += 1
    return items


def _normalize_item(item: dict) -> dict:
    """Squash an Audible library item into the meta shape library_writer wants."""
    series_name = None
    series_index = None
    for s in item.get("series") or []:
        series_name = s.get("title")
        try:
            series_index = float(s.get("sequence")) if s.get("sequence") else None
        except (TypeError, ValueError):
            series_index = None
        break

    authors = ", ".join(a.get("name") for a in (item.get("authors") or []) if a.get("name"))
    narrators = ", ".join(
        n.get("name") for n in (item.get("narrators") or []) if n.get("name")
    )

    return {
        "asin": item.get("asin"),
        "title": item.get("title"),
        "author": authors or None,
        "narrators": narrators or None,
        "series": series_name,
        "series_index": series_index,
        "publisher": item.get("publisher_name"),
        "language": item.get("language"),
        "publish_year": (
            int(item["release_date"][:4])
            if item.get("release_date") and item["release_date"][:4].isdigit()
            else None
        ),
    }


_TITLE_PAREN = re.compile(r"\s*\([^)]*\)\s*$")


def _normalize_title(t: str) -> str:
    """Strip trailing parenthetical suffixes ("(Unabridged)") and lowercase
    so audiobook titles from disk can be matched against Audible API titles."""
    if not t:
        return ""
    t = t.lower().strip()
    # Repeatedly strip trailing parentheticals — "Title (Book 3) (Unabridged)"
    while True:
        new = _TITLE_PAREN.sub("", t).strip()
        if new == t:
            break
        t = new
    return t


async def _existing_asins(db: AsyncSession) -> set[str]:
    result = await db.execute(select(AudioBook.asin).where(AudioBook.asin.isnot(None)))
    return {row[0] for row in result.all() if row[0]}


async def _untagged_audiobooks(db: AsyncSession) -> list[tuple[int, str, str]]:
    """
    Return audiobook rows that don't yet have an ASIN — these are candidates
    for fuzzy-title backfill against the Audible library. Tuple shape:
    (id, normalized_title, author_lower).
    """
    result = await db.execute(
        select(AudioBook.id, AudioBook.title, AudioBook.author)
        .where(AudioBook.asin.is_(None))
    )
    return [
        (row_id, _normalize_title(title or ""), (author or "").lower())
        for (row_id, title, author) in result.all()
    ]


def _match_existing(meta: dict, candidates: list[tuple[int, str, str]]) -> int | None:
    """
    Find an existing audiobook row that almost certainly is the same book as
    `meta` from the Audible library. Returns the audiobook id, or None.

    Uses rapidfuzz (already a project dep) for token-set matching, plus a
    soft author-agreement check to avoid cross-author title collisions.
    """
    from rapidfuzz import fuzz

    target_title = _normalize_title(meta.get("title") or "")
    if not target_title:
        return None
    target_author = (meta.get("author") or "").lower()

    best_id = None
    best_score = 0
    for ab_id, norm_title, author in candidates:
        if not norm_title:
            continue
        title_score = fuzz.token_set_ratio(target_title, norm_title)
        # Require titles to be very close. Author agreement adds confidence
        # but isn't strictly required (audible's author field is occasionally
        # "John Doe et al.").
        if title_score >= 92:
            score = title_score
            if target_author and author:
                if target_author in author or author in target_author:
                    score += 5
                elif fuzz.token_set_ratio(target_author, author) < 60:
                    # Strong author disagreement — this is almost certainly
                    # a different book that happens to share a title.
                    continue
            if score > best_score:
                best_score = score
                best_id = ab_id
    return best_id


def _ensure_audible_cli_config(config_dir: Path, auth_filename: str, locale: str = DEFAULT_LOCALE) -> None:
    """
    audible-cli reads a config.toml that names the active profile and points
    at the auth file. We synthesize a minimal one alongside the auth blob so
    the CLI can find it via --config-dir.
    """
    config_path = config_dir / "config.toml"
    if config_path.exists():
        return
    config_path.write_text(
        f'[APP]\n'
        f'primary_profile = "default"\n\n'
        f'[profile.default]\n'
        f'auth_file = "{auth_filename}"\n'
        f'country_code = "{locale}"\n'
    )


def _download_via_cli(asin: str, out_dir: Path, auth_blob_path: Path) -> Path:
    """
    Shell out to audible-cli to download a single ASIN, decrypt to .m4b, and
    return the resulting file. Requires audible-cli + ffmpeg on PATH in the
    server image.

    audible-cli locates its config via the AUDIBLE_CONFIG_DIR env var; there
    is no equivalent CLI flag (verified by reading audible_cli.constants).

    We pass --aax-fallback rather than --aaxc so older titles that exist
    only in the legacy .aax format come through too — passing --aaxc against
    an aax-only title makes audible-cli exit 0 without writing anything.
    """
    _ensure_audible_cli_config(auth_blob_path.parent, auth_blob_path.name)
    cmd = [
        "audible",
        "download",
        "--asin", asin,
        "--aax-fallback",
        "--output-dir", str(out_dir),
    ]
    env = {**os.environ, "AUDIBLE_CONFIG_DIR": str(auth_blob_path.parent)}
    logger.info(f"[audible] running: AUDIBLE_CONFIG_DIR={auth_blob_path.parent} {' '.join(cmd)}")
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=3600, env=env)
    if proc.returncode != 0:
        raise RuntimeError(
            f"audible-cli download failed for {asin}: "
            f"{proc.stderr.strip() or proc.stdout.strip()}"
        )

    # Look for any audio file the CLI might have produced. Single-file
    # downloads land as .aax / .aaxc; the rare multi-part audiobook can
    # come down as .m4b / .m4a directly.
    for ext in ("*.aaxc", "*.aax", "*.m4b", "*.m4a"):
        match = next(out_dir.glob(ext), None)
        if match:
            return match

    # No output file produced. audible-cli sometimes succeeds (rc=0) but
    # silently skips a title — surface its stdout/stderr so we get a real
    # reason, not just "no output file".
    cli_out = (proc.stdout + "\n" + proc.stderr).strip()
    cli_msg = " ".join(cli_out.split())[:300] or "audible-cli wrote nothing and said nothing"
    raise RuntimeError(f"No output file for {asin}: {cli_msg}")


def _decrypt_to_m4b(input_file: Path, out_dir: Path, activation_bytes: Optional[str]) -> Path:
    """
    Decrypt an AAXC/AAX file to plain .m4b using ffmpeg.
    For .aaxc, audible-cli writes a .voucher next to it containing the per-file key.
    For .aax, we use the account-wide activation_bytes.
    """
    out = out_dir / (input_file.stem + ".m4b")

    if input_file.suffix.lower() == ".aaxc":
        voucher = input_file.with_suffix(".voucher")
        if not voucher.exists():
            raise RuntimeError(f"missing voucher for {input_file}")
        v = json.loads(voucher.read_text())
        key = v["content_license"]["license_response"]["key"]
        iv = v["content_license"]["license_response"]["iv"]
        cmd = [
            "ffmpeg", "-y",
            "-audible_key", key,
            "-audible_iv", iv,
            "-i", str(input_file),
            "-c", "copy",
            str(out),
        ]
    else:  # .aax
        if not activation_bytes:
            raise RuntimeError("activation_bytes missing — cannot decrypt .aax")
        cmd = [
            "ffmpeg", "-y",
            "-activation_bytes", activation_bytes,
            "-i", str(input_file),
            "-c", "copy",
            str(out),
        ]
    logger.info(f"[audible] running: {' '.join(cmd)}")
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg decrypt failed: {proc.stderr.strip()[:500]}")
    return out


class AudibleSource(SourceAdapter):
    SOURCE_KEY = "audible"
    DISPLAY_NAME = "Audible"
    BOOK_TYPE = "audiobook"
    SUPPORTS_AUTO_SYNC = True

    # --- login flow re-exposed for the router -------------------------------
    @staticmethod
    def start_login(locale: str = DEFAULT_LOCALE) -> tuple[str, str]:
        return _build_login_state(locale)

    @staticmethod
    async def complete_login(db: AsyncSession, state_token: str, response_url: str) -> None:
        auth = await _complete_login(state_token, response_url)
        await credentials.set_credential(db, "audible", _auth_to_blob(auth))

    @staticmethod
    async def disconnect(db: AsyncSession) -> None:
        await credentials.delete_credential(db, "audible")

    # --- adapter API ---------------------------------------------------------
    async def is_connected(self, db: AsyncSession) -> bool:
        return await _load_auth(db) is not None

    async def sync(self, db: AsyncSession, progress: ProgressFn = _noop_progress) -> SyncResult:
        auth = await _load_auth(db)
        if not auth:
            return SyncResult(fatal_error="Audible is not connected.")

        try:
            items = await asyncio.to_thread(_list_library, auth)
        except Exception as e:
            return SyncResult(fatal_error=f"Could not list Audible library: {e}")

        # Two-tier dedup:
        # 1) Exact ASIN match against audiobooks.asin (fast, definitive).
        # 2) Fuzzy title+author match against existing rows that have no ASIN
        #    yet — handles books imported before provenance tracking landed.
        #    Matched rows get backfilled with asin/import_source so all future
        #    syncs hit path #1 instead.
        existing_asins = await _existing_asins(db)
        untagged = await _untagged_audiobooks(db)

        result = SyncResult()
        to_download: list[dict] = []

        for raw in items:
            asin = raw.get("asin")
            if not asin:
                continue
            if asin in existing_asins:
                result.items_skipped += 1
                continue

            meta = _normalize_item(raw)
            matched_id = _match_existing(meta, untagged)
            if matched_id is not None:
                ab = await db.get(AudioBook, matched_id)
                if ab is not None:
                    ab.asin = asin
                    ab.external_id = asin
                    ab.import_source = "audible"
                    db.add(ab)
                    logger.info(
                        f"[audible] backfilled existing row #{matched_id} "
                        f"('{ab.title}') -> {asin}"
                    )
                # Drop from candidate list so a different ASIN can't double-match.
                untagged = [c for c in untagged if c[0] != matched_id]
                existing_asins.add(asin)
                result.items_skipped += 1
                continue

            to_download.append(raw)

        # Persist any backfills before the long download phase so they're
        # not lost if a later download crashes.
        await db.flush()

        total = len(to_download)
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            auth_file = tmpdir / "audible.json"
            auth_file.write_text(_auth_to_blob(auth))

            for idx, raw in enumerate(to_download, start=1):
                meta = _normalize_item(raw)
                title = meta.get("title") or raw.get("asin") or "Unknown"
                await progress(idx, total, title)

                try:
                    asin = meta["asin"]
                    item_dir = tmpdir / asin
                    item_dir.mkdir()

                    encrypted = await asyncio.to_thread(
                        _download_via_cli, asin, item_dir, auth_file
                    )
                    decrypted = await asyncio.to_thread(
                        _decrypt_to_m4b,
                        encrypted,
                        item_dir,
                        getattr(auth, "activation_bytes", None),
                    )
                    target = await place_file(
                        db,
                        book_type="audiobook",
                        source_path=str(decrypted),
                        extension=".m4b",
                        meta=meta,
                        move=True,
                    )
                    result.items_added += 1
                    result.added_titles.append(title)
                    result.imported_items.append(ImportedItem(
                        file_path=target,
                        source_key="audible",
                        external_id=asin,
                    ))
                    logger.info(f"[audible] imported {asin} -> {target}")
                except Exception as e:
                    logger.exception(f"[audible] failed to import {raw.get('asin')}: {e}")
                    result.errors.append(SyncError(
                        title=title,
                        external_id=raw.get("asin"),
                        error=str(e).strip().split("\n")[0][:300],
                    ))

        return result
