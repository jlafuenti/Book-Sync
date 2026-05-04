"""
Audiobookshelf (ABS) metadata enrichment service.

Fetches library metadata from the ABS API and merges it into Book Sync
audiobook records, filling in fields that embedded tag extraction left empty.
After enrichment, writes the combined metadata back into the audio file's
embedded tags so that future rescans don't need to call ABS again.

The ABS API token is stored in the encrypted import_source_credentials table
(source_key='abs') alongside Audible and other source credentials. A one-shot
startup migration (services/abs_token_migration.py, called from main.py
lifespan) moves any token that still lives plaintext in system_settings.
"""

import os
import logging
from typing import Optional

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.settings import SystemSetting
from services import credentials
from services.metadata_utils import extract_series_and_index

logger = logging.getLogger(__name__)


async def get_abs_token(db: AsyncSession) -> Optional[str]:
    """
    Read the ABS API token from the encrypted credential store. Falls back to
    legacy plaintext system_settings.abs_api_token in case the startup
    migration hasn't run yet (e.g. unit tests that bypass lifespan).
    """
    token = await credentials.get_credential(db, "abs")
    if token:
        return token
    legacy = await db.execute(
        select(SystemSetting).where(SystemSetting.key == "abs_api_token")
    )
    row = legacy.scalar_one_or_none()
    return (row.value if row and row.value else None)


async def migrate_abs_token_to_credentials() -> None:
    """
    One-shot: move the plaintext abs_api_token in system_settings into the
    encrypted credential store. Idempotent — does nothing once the row in
    system_settings has been blanked. Safe to call on every startup.
    """
    from database import async_session

    async with async_session() as db:
        result = await db.execute(
            select(SystemSetting).where(SystemSetting.key == "abs_api_token")
        )
        row = result.scalar_one_or_none()
        if not row or not row.value or row.value.strip() == "":
            return

        existing = await credentials.get_credential(db, "abs")
        if existing == row.value:
            row.value = ""
            await db.commit()
            return

        await credentials.set_credential(db, "abs", row.value)
        row.value = ""  # blank it out in plaintext
        await db.commit()
        logger.info("[abs_metadata] migrated abs_api_token to encrypted credential store")


def _normalize_path(path: str, prefix: str) -> str:
    """Strip a mount-point prefix and normalize slashes/case for comparison."""
    p = path.replace("\\", "/")
    if prefix:
        pfx = prefix.rstrip("/")
        if p.startswith(pfx):
            p = p[len(pfx):]
    return p.strip("/").lower()


def fetch_abs_index(abs_url: str, api_token: str, abs_prefix: str) -> dict[str, dict]:
    """
    Fetch all audiobook library items from ABS and return a dict keyed by
    normalized relative path (e.g. 'jim butcher/dresden files/blood rites').
    Returns an empty dict on any error so callers degrade gracefully.
    """
    headers = {"Authorization": f"Bearer {api_token}"}
    index: dict[str, dict] = {}
    try:
        # Find the audiobook library
        r = httpx.get(f"{abs_url}/api/libraries", headers=headers, timeout=15)
        r.raise_for_status()
        libraries = r.json().get("libraries", [])
        lib_id = next(
            (lib["id"] for lib in libraries if lib.get("mediaType") == "book"), None
        )
        if not lib_id:
            logger.warning("[abs_metadata] No audiobook library found in ABS")
            return index

        # Paginate through all items
        page, limit = 0, 500
        while True:
            r = httpx.get(
                f"{abs_url}/api/libraries/{lib_id}/items",
                headers=headers,
                params={"limit": limit, "page": page},
                timeout=30,
            )
            r.raise_for_status()
            data = r.json()
            items = data.get("results", [])
            for item in items:
                path = item.get("path", "")
                rel = _normalize_path(path, abs_prefix)
                if rel:
                    index[rel] = item
            if len(items) < limit:
                break
            page += 1

        logger.info(f"[abs_metadata] Loaded {len(index)} items from ABS")
    except Exception as e:
        logger.warning(f"[abs_metadata] Failed to fetch ABS index: {e}")
    return index


def enrich_from_abs(
    file_meta: dict,
    file_path: str,
    abs_index: dict,
    audiobooks_prefix: str,
    force: bool = False,
) -> tuple[dict, bool, bool]:
    """
    Merge ABS metadata into file_meta.

    - Normal mode (force=False): fills only missing fields.
    - Force mode (force=True): overwrites all enrichable fields.

    Returns (file_meta, changed, matched) where:
      - changed=True means at least one field was updated
      - matched=True means an ABS entry was found for this book
    file_meta is mutated in place and also returned.
    """
    rel = _normalize_path(os.path.dirname(file_path), audiobooks_prefix)
    item = abs_index.get(rel)

    if not item:
        # Fuzzy fallback by title
        try:
            from rapidfuzz import fuzz
            title = (file_meta.get("title") or "").lower()
            best, best_score = None, 0
            for it in abs_index.values():
                abs_title = (
                    it.get("media", {}).get("metadata", {}).get("title") or ""
                ).lower()
                score = fuzz.token_sort_ratio(title, abs_title)
                if score > best_score:
                    best_score, best = score, it
            if best_score >= 85:
                item = best
                logger.debug(
                    f"[abs_metadata] Fuzzy matched '{file_meta.get('title')}' → "
                    f"'{item['media']['metadata'].get('title')}' ({best_score}%)"
                )
        except Exception:
            pass

    if not item:
        return file_meta, False, False

    meta = item.get("media", {}).get("metadata", {})
    changed = False

    def _fill(key: str, val) -> None:
        nonlocal changed
        if val and (force or not file_meta.get(key)):
            file_meta[key] = val
            changed = True

    _fill("description", meta.get("description"))
    _fill("publisher", meta.get("publisher"))
    _fill("language", meta.get("language"))
    _fill("isbn", meta.get("isbn"))
    _fill("asin", meta.get("asin"))
    _fill("author", meta.get("authorName"))
    _fill("narrators", meta.get("narratorName"))

    published_year = meta.get("publishedYear")
    if published_year:
        try:
            _fill("publish_year", int(published_year))
        except (ValueError, TypeError):
            pass

    genres = meta.get("genres")
    if genres:
        _fill("genres", ",".join(genres) if isinstance(genres, list) else genres)

    tags = item.get("media", {}).get("tags") or item.get("tags")
    if tags:
        _fill("tags", ",".join(tags) if isinstance(tags, list) else tags)

    if meta.get("explicit") and (force or not file_meta.get("is_explicit")):
        file_meta["is_explicit"] = True
        changed = True
    if meta.get("abridged") and (force or not file_meta.get("is_abridged")):
        file_meta["is_abridged"] = True
        changed = True

    if force or not file_meta.get("series"):
        series_str = meta.get("seriesName", "")
        if series_str:
            s_name, s_idx = extract_series_and_index(series_str)
            if s_name:
                file_meta["series"] = s_name
                changed = True
                if s_idx is not None:
                    file_meta["series_index"] = s_idx

    if changed:
        logger.debug(f"[abs_metadata] Enriched '{file_meta.get('title')}' from ABS")

    return file_meta, changed, True


def write_metadata_to_file(filepath: str, file_meta: dict) -> bool:
    """
    Write enriched metadata back into the audio file's embedded tags.
    Currently handles M4B/MP4 only (MP3/FLAC support can be added later).
    Returns True on success, False on failure.
    """
    ext = os.path.splitext(filepath)[1].lower()
    if ext not in (".m4b", ".m4a", ".mp4"):
        logger.debug(f"[abs_metadata] Skipping write-back for non-M4B file: {filepath}")
        return False

    try:
        import mutagen.mp4

        audio = mutagen.mp4.MP4(filepath)

        def _set(tag: str, val) -> None:
            if val:
                audio[tag] = [str(val)]
            elif tag in audio:
                del audio[tag]

        _set("\xa9nam", file_meta.get("title"))
        _set("\xa9ART", file_meta.get("author"))
        _set("\xa9des", file_meta.get("description"))
        _set(
            "\xa9day",
            str(file_meta["publish_year"]) if file_meta.get("publish_year") else None,
        )
        _set("\xa9gen", file_meta.get("genres"))
        _set("\xa9pub", file_meta.get("publisher"))
        _set("\xa9wrt", file_meta.get("narrators"))

        # Series → ©grp as "Name #N" (the format extract_series_and_index reads)
        series = file_meta.get("series")
        series_index = file_meta.get("series_index")
        if series:
            grp = (
                f"{series} #{int(series_index)}"
                if series_index is not None
                else series
            )
            audio["\xa9grp"] = [grp]
        elif "\xa9grp" in audio:
            del audio["\xa9grp"]

        # Also write custom iTunes freeform atoms for compatibility with other players
        def _set_freeform(atom: str, val: Optional[str]) -> None:
            key = f"----:com.apple.iTunes:{atom}"
            if val:
                audio[key] = [val.encode("utf-8")]
            elif key in audio:
                del audio[key]

        _set_freeform("SERIES", series)
        if series and series_index is not None:
            _set_freeform("SERIES-PART", str(series_index))
        elif series is None:
            _set_freeform("SERIES-PART", None)
        _set_freeform("NARRATOR", file_meta.get("narrators"))

        audio.save()
        logger.debug(f"[abs_metadata] Wrote tags back to {filepath}")
        return True
    except Exception as e:
        logger.warning(f"[abs_metadata] Failed to write tags to {filepath}: {e}")
        return False
