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
import json
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

import audible  # mkb79's library
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.book import AudioBook
from services import credentials
from services.import_sources.base import SourceAdapter, SyncResult
from services.library_writer import place_file

logger = logging.getLogger(__name__)


# --- in-memory pending-login state -------------------------------------------
# Holds half-completed logins between "start" (server prints OAuth URL) and
# "complete" (user pastes the redirect URL back). Single-process is fine for
# a home server — Tandem doesn't run replicas.

_PENDING: dict[str, dict] = {}
DEFAULT_LOCALE = "us"  # Audible marketplace; could be configurable later.


def _build_login_state(locale: str = DEFAULT_LOCALE) -> tuple[str, str]:
    """
    Start a login flow. Returns (login_url, state_token). Caller stores
    state_token client-side and passes it back with the post-login redirect URL.
    """
    code_verifier = audible.login.create_code_verifier()
    locale_obj = audible.localization.Locale(locale)
    login_url = audible.login.build_oauth_url(
        country_code=locale_obj.country_code,
        domain=locale_obj.domain,
        market_place_id=locale_obj.market_place_id,
        code_verifier=code_verifier,
    )[0]
    state_token = os.urandom(16).hex()
    _PENDING[state_token] = {
        "code_verifier": code_verifier,
        "locale": locale,
    }
    return login_url, state_token


async def _complete_login(state_token: str, response_url: str) -> audible.Authenticator:
    state = _PENDING.pop(state_token, None)
    if not state:
        raise ValueError("Unknown or expired state token — start the login over.")

    def _do_login() -> audible.Authenticator:
        return audible.Authenticator.from_login_external(
            locale=state["locale"],
            code_verifier=state["code_verifier"],
            login_url_callback=lambda u: response_url,
        )

    auth = await asyncio.to_thread(_do_login)
    # Pre-fetch activation bytes so they end up persisted in the auth blob.
    try:
        await asyncio.to_thread(auth.get_activation_bytes)
    except Exception as e:
        logger.warning(f"[audible] could not fetch activation_bytes during login: {e}")
    return auth


def _auth_to_blob(auth: audible.Authenticator) -> str:
    """Serialize an Authenticator to a JSON-able plaintext blob."""
    data = {
        "adp_token": auth.adp_token,
        "device_private_key": auth.device_private_key,
        "access_token": auth.access_token,
        "refresh_token": auth.refresh_token,
        "device_info": auth.device_info,
        "customer_info": auth.customer_info,
        "expires": auth.expires,
        "locale_code": auth.locale.locale_code,
        "with_username": auth.with_username,
        "activation_bytes": getattr(auth, "activation_bytes", None),
        "website_cookies": getattr(auth, "website_cookies", None),
    }
    return json.dumps(data)


def _blob_to_auth(blob: str) -> audible.Authenticator:
    data = json.loads(blob)
    auth = audible.Authenticator()
    auth.adp_token = data["adp_token"]
    auth.device_private_key = data["device_private_key"]
    auth.access_token = data["access_token"]
    auth.refresh_token = data["refresh_token"]
    auth.device_info = data["device_info"]
    auth.customer_info = data["customer_info"]
    auth.expires = data["expires"]
    auth.locale = audible.localization.Locale(data["locale_code"])
    auth.with_username = data["with_username"]
    if data.get("activation_bytes"):
        auth.activation_bytes = data["activation_bytes"]
    if data.get("website_cookies"):
        auth.website_cookies = data["website_cookies"]
    return auth


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


async def _existing_asins(db: AsyncSession) -> set[str]:
    result = await db.execute(select(AudioBook.asin).where(AudioBook.asin.isnot(None)))
    return {row[0] for row in result.all() if row[0]}


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
    """
    _ensure_audible_cli_config(auth_blob_path.parent, auth_blob_path.name)
    cmd = [
        "audible",
        "--config-dir", str(auth_blob_path.parent),
        "download",
        "--asin", asin,
        "--aaxc",
        "--output-dir", str(out_dir),
    ]
    logger.info(f"[audible] running: {' '.join(cmd)}")
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
    if proc.returncode != 0:
        raise RuntimeError(
            f"audible-cli download failed for {asin}: "
            f"{proc.stderr.strip() or proc.stdout.strip()}"
        )
    # audible-cli writes a .aaxc plus a .voucher; we then need to decrypt.
    aaxc = next(out_dir.glob("*.aaxc"), None)
    if aaxc is None:
        # Maybe it gave us a legacy .aax
        aax = next(out_dir.glob("*.aax"), None)
        if aax is None:
            raise RuntimeError(f"audible-cli produced no output file for {asin}")
        return aax
    return aaxc


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

    async def sync(self, db: AsyncSession) -> SyncResult:
        auth = await _load_auth(db)
        if not auth:
            return SyncResult(error="Audible is not connected.")

        try:
            items = await asyncio.to_thread(_list_library, auth)
        except Exception as e:
            return SyncResult(error=f"Could not list Audible library: {e}")

        existing = await _existing_asins(db)
        new_items = [i for i in items if i.get("asin") and i["asin"] not in existing]

        added = 0
        skipped = len(items) - len(new_items)
        added_titles: list[str] = []
        errors: list[str] = []

        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            # Persist a fresh on-disk auth file for audible-cli to use.
            auth_file = tmpdir / "audible.json"
            auth_file.write_text(_auth_to_blob(auth))

            for raw in new_items:
                try:
                    meta = _normalize_item(raw)
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
                    added += 1
                    if meta.get("title"):
                        added_titles.append(meta["title"])
                    logger.info(f"[audible] imported {asin} -> {target}")
                except Exception as e:
                    logger.exception(f"[audible] failed to import {raw.get('asin')}: {e}")
                    errors.append(f"{raw.get('title') or raw.get('asin')}: {e}")

        detail = ""
        if added_titles:
            detail = "Added: " + "; ".join(added_titles)
        if errors:
            detail += ("\n" if detail else "") + "Errors: " + "; ".join(errors)

        return SyncResult(
            items_added=added,
            items_skipped=skipped,
            detail=detail,
            error="; ".join(errors) if errors and added == 0 else None,
        )
