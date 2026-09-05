"""
Library router: manage ebooks, audiobooks, and book pairs.
Includes scanning directories, uploading files, and auto-matching.
"""

import os
import re
import hashlib
import logging
import subprocess
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple, Dict, Any

import asyncio
import contextlib
import shutil
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, status, Query
from pydantic import BaseModel
from sqlalchemy import select, or_, func, delete, literal, null, cast, Integer, String, exists, union_all
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from rapidfuzz import fuzz

import ebooklib
from ebooklib import epub
import mutagen
from markdownify import markdownify as md
import zipfile
# defusedxml rather than the stdlib parser (issue #265). Python 3.12's expat
# already refuses external entities and caps amplification, so this changes no
# behaviour -- it makes the choice explicit for input that arrives as an
# attacker-supplied archive, and keeps bandit quiet on a public repo.
import defusedxml.ElementTree as ET
import tempfile

from database import get_db
from config import settings
from models.settings import SystemSetting
from models.user import User
from routers.settings import DEFAULT_SETTINGS
from models.book import EBook, AudioBook, BookPair, PairStatus
from models.transcription_queue import TranscriptionQueueItem
from models.progress import UserProgress
from models.transcript import AudioTranscript
from schemas import (
    EBookResponse, AudioBookResponse, BookPairResponse,
    BookPairCreate, LibraryScanResponse, SearchResponse,
    EBookDetailResponse, AudioBookDetailResponse,
    MetadataDiscrepancy, ResolveDiscrepancyRequest, IgnoreDiscrepancyRequest, DiscrepantField,
    NewItemsResponse, AcknowledgeItemsRequest, AcknowledgePairsRequest, Page,
    LibraryItem, LibraryItemKind, LibraryTab, LibrarySort, SortDir,
    LibraryFacets, LibraryCounts, FacetCount,
)
from rate_limit import expensive_reads, search_reads
from routers.auth import get_current_user, get_editor_user, rate_limited
from services.cache import TTLValue
from services.filename_patterns import compile_pattern, regex_from_pattern
from services.metadata_utils import normalize_author, normalize_series, extract_series_and_index
from services.abs_metadata import fetch_abs_index, enrich_from_abs, write_metadata_to_file
from services.audio_duration import probe_duration_seconds
from services.position_service import (
    demote_pair_positions,
    release_standalone_positions,
    repoint_standalone_positions_to_ebook,
)
from services import multi_file_audiobooks
from services import library_jobs
from services.library_jobs import LibraryJobBusy
from services.uploads import stream_upload_to_path
from utils import resolve_cover_url, safe_join, utcnow

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/library", tags=["library"])

# Supported file extensions
EBOOK_EXTENSIONS = {".epub", ".pdf", ".mobi", ".azw3"}
AUDIOBOOK_EXTENSIONS = {".mp3", ".m4a", ".m4b", ".flac", ".ogg", ".wav", ".aac", ".wma"}
COVER_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}

# Regex patterns for filename parsing
# Pattern 1: Author - [Series Num] - Title
REGEX_AUTHOR_SERIES_TITLE = re.compile(r"^(.+?) - \[(.+?) (\d+(?:\.\d+)?)\] - (.+)$")
# Pattern 2: [Series Num] Title (often found in Author folders)
REGEX_SERIES_TITLE = re.compile(r"^\[(.+?) (\d+(?:\.\d+)?)\] (.+)$")


# The pattern compiler moved to services/filename_patterns.py (issue #354) so
# the settings PUT can validate a pattern without importing this router — which
# it cannot, because this module already imports DEFAULT_SETTINGS from it. The
# name stays exported here: it is what the rest of the router and its tests call.


def is_path_pattern(pattern: str) -> bool:
    """Check whether a pattern contains '/' indicating it matches directory structure."""
    return "/" in pattern


async def get_filename_patterns(db: AsyncSession, pattern_type: str = "ebook") -> List[str]:
    """
    Fetch filename patterns from settings. 
    pattern_type: 'ebook' or 'audiobook'
    """
    key = "ebook_filename_patterns" if pattern_type == "ebook" else "audiobook_filename_patterns"
    
    result = await db.execute(select(SystemSetting).where(SystemSetting.key == key))
    setting = result.scalar_one_or_none()
    
    if setting and setting.value:
        return setting.value.split("\n")
        
    # Fallback to old key if new ones missing (backward compat)
    if not setting:
         result = await db.execute(select(SystemSetting).where(SystemSetting.key == "filename_patterns"))
         setting = result.scalar_one_or_none()
         if setting and setting.value:
             return setting.value.split("\n")
             
    return DEFAULT_SETTINGS.get(key, DEFAULT_SETTINGS["ebook_filename_patterns"])


def compute_file_hash(filepath: str) -> str:
    """Composite content hash (size + head + tail) — see services/file_hash.py.

    Used by scan/upload ingest and Calibre registration; troubleshoot's
    replace_file uses the byte-level twin. Keep them in lockstep (issue #45).
    """
    from services.file_hash import hash_file
    return hash_file(filepath)

def extract_title_from_filename(filename: str) -> str:
    """
    Extract a clean title from a filename by removing extension and
    common audiobook/ebook suffixes.
    """
    name = Path(filename).stem
    # Remove common suffixes like " - Audiobook", " (Unabridged)", etc.
    for suffix in [" - Audiobook", " - Audio", " (Unabridged)", " (Abridged)", " [Audiobook]"]:
        name = name.replace(suffix, "")
    return name.strip()


async def parse_filename_metadata_with_settings(
    filename: str, db: AsyncSession, parent_dir_name: str = None,
    file_type: str = "ebook", relative_path: str = None
) -> Dict[str, Any]:
    """
    Attempt to extract metadata using configured patterns.
    Supports both filename-only and directory-path patterns.
    
    Args:
        filename: Just the filename (e.g. 'Battle Ground.m4b')
        db: Database session for loading settings
        parent_dir_name: The immediate parent directory name
        file_type: 'ebook' or 'audiobook'
        relative_path: Full path relative to the library root
                       (e.g. 'Jim Butcher/Battle Ground/Battle Ground.m4b')
    """
    clean_name = Path(filename).stem
    # Build a clean relative path without extension for path-based matching
    if relative_path:
        rel_path_stem = str(Path(relative_path).with_suffix(''))
        # Normalize to forward slashes
        rel_path_stem = rel_path_stem.replace('\\', '/')
    else:
        rel_path_stem = clean_name
    
    meta = {
        "title": None,
        "author": None,
        "series": None,
        "series_index": None,
        "_metadata_source": "filename",
        "_metadata_pattern": None,
    }
    
    patterns = await get_filename_patterns(db, file_type)
    
    logger.debug(f"[metadata] Parsing '{filename}' (type={file_type})")
    logger.debug(f"[metadata]   clean_name='{clean_name}', relative_path='{relative_path}', rel_path_stem='{rel_path_stem}'")
    
    for pattern_str in patterns:
        # `compile_pattern` returns None rather than raising, and warns at most
        # once per pattern for the whole process (issue #354). The old code
        # compiled inside the try below, so a pattern that could not compile —
        # `<Author>/<Title>/<Title>` was enough — logged a warning for every
        # single file in the library, every scan, and matched nothing.
        regex = compile_pattern(pattern_str)
        if regex is None:
            continue

        try:
            # Decide what to match against based on whether pattern uses directories
            if is_path_pattern(pattern_str):
                match_target = rel_path_stem
            else:
                match_target = clean_name
            
            match = regex.match(match_target)
            logger.debug(f"[metadata]   Pattern '{pattern_str}' vs '{match_target}' -> {'MATCH' if match else 'no match'}")
            
            if match:
                groups = match.groupdict()
                logger.debug(f"[metadata]   Matched pattern '{pattern_str}' -> groups={groups}")
                
                if "author" in groups: meta["author"] = normalize_author(groups["author"])
                if "series" in groups: meta["series"] = normalize_series(groups["series"])
                if "title" in groups: meta["title"] = groups["title"].strip()
                if "series_index" in groups:
                    try:
                        meta["series_index"] = float(groups["series_index"])
                    except ValueError:
                        pass
                
                # If we have at least a title, we consider it a match
                if meta["title"]:
                    meta["_metadata_source"] = "pattern"
                    meta["_metadata_pattern"] = pattern_str
                    
                    # Fallbacks from context if missing in pattern
                    if not meta["author"] and parent_dir_name:
                         meta["author"] = normalize_author(parent_dir_name)
                    
                    logger.debug(f"[metadata]   Final (pattern): {meta}")
                    return meta
        except Exception as e:
             # Applying a compiled pattern to one path, not compiling it: this
             # is now a per-file event about a specific file, which is what a
             # per-file log line should be about.
             logger.warning(f"[metadata]   Pattern '{pattern_str}' failed on '{filename}': {e}")
             continue

    # Fallback to simple filename cleaning
    meta["title"] = extract_title_from_filename(filename)
    meta["_metadata_source"] = "filename"
    meta["_metadata_pattern"] = None
    if parent_dir_name:
        meta["author"] = normalize_author(parent_dir_name)
    
    logger.debug(f"[metadata]   Final (fallback): {meta}")
    return meta


def _read_embedded_metadata(filepath: str, file_type: str) -> Dict[str, Any]:
    """Read the tags embedded in the file itself. Blocking; call it on a thread.

    Split out of :func:`extract_metadata` so the whole of it runs off the event
    loop (issue #203). Everything here is synchronous file work — unzipping and
    XML-parsing the EPUB, opening the audio container with mutagen, and shelling
    out to ffprobe for the runtime — and one uvicorn worker serves every request,
    so doing it inline stalled position sync, audio streaming, login and
    ``/api/health`` for the length of a library scan.

    Returns whatever it could read; a file with no readable tags returns ``{}``.
    Failures are logged and swallowed on purpose: an unreadable EPUB or a
    truncated container must fall back to the filename, not fail the scan.
    """
    file_meta: Dict[str, Any] = {}
    try:
        if file_type == "ebook" and filepath.lower().endswith(".epub"):
            book = epub.read_epub(filepath, options={'ignore_ncx': True})
            
            # Title
            t = book.get_metadata('DC', 'title')
            if t: file_meta["title"] = t[0][0]
            
            # Author
            c = book.get_metadata('DC', 'creator')
            if c: file_meta["author"] = normalize_author(c[0][0])
            
            # Extended EPUB metadata
            desc = book.get_metadata('DC', 'description')
            if desc: file_meta["description"] = md(desc[0][0]).strip()
            
            pub = book.get_metadata('DC', 'publisher')
            if pub: file_meta["publisher"] = pub[0][0]
            
            lang = book.get_metadata('DC', 'language')
            if lang: file_meta["language"] = lang[0][0]
            
            date = book.get_metadata('DC', 'date')
            if date:
                match = re.search(r'\d{4}', date[0][0])
                if match: file_meta["publish_year"] = int(match.group(0))
                
            subjects = book.get_metadata('DC', 'subject')
            if subjects: file_meta["genres"] = ",".join([s[0] for s in subjects if s[0]])
                
            identifiers = book.get_metadata('DC', 'identifier')
            if identifiers:
                for id_tuple in identifiers:
                    val = id_tuple[0].lower()
                    if 'isbn' in val or (len(id_tuple)>1 and isinstance(id_tuple[1], dict) and 'isbn' in str(id_tuple[1]).lower()):
                        file_meta["isbn"] = id_tuple[0].replace('urn:isbn:', '')
                    elif 'asin' in val or (len(id_tuple)>1 and isinstance(id_tuple[1], dict) and 'asin' in str(id_tuple[1]).lower()):
                        file_meta["asin"] = id_tuple[0].replace('urn:asin:', '')
            
            # Series (Calibre stores in <meta name="calibre:series" content="..."/>)
            all_meta = book.get_metadata('OPF', 'meta')
            if all_meta:
                for m in all_meta:
                    attrs = m[1] if len(m) > 1 else {}
                    if attrs.get('name') == 'calibre:series':
                        file_meta["series"] = normalize_series(attrs.get('content', ''))
                    elif attrs.get('name') == 'calibre:series_index':
                        try:
                            file_meta["series_index"] = float(attrs.get('content', '0'))
                        except ValueError:
                            pass
            
            # If no series found in Calibre meta, try extracting from title
            if not file_meta.get('series'):
                title_str = file_meta.get('title', '')
                s_name, s_idx = extract_series_and_index(title_str)
                if s_name and s_idx is not None:
                    file_meta['series'] = s_name
                    file_meta['series_index'] = s_idx

            logger.debug(f"[extract_metadata]   EPUB embedded: {file_meta}")

        elif file_type == "audiobook":
            # Runtime, off the container header (issue #127). ffprobe is the
            # authority and mutagen's info.length only the fallback for hosts
            # without ffmpeg — see services/audio_duration.py for the library-wide
            # measurement behind that ordering. Left *absent* when neither can
            # supply one; callers take "no key" as "leave whatever is stored
            # alone", and an unknown length cleanly disables the audio end zone
            # while a wrong one would silently finish the book.
            duration = probe_duration_seconds(filepath)

            try:
                audio = mutagen.File(filepath)
            except mutagen.mp4.MP4MetadataError:
                audio = None
                logger.warning(f"[extract_metadata] MP4 chapter parse failed for {filepath}, skipping embedded tags")

            # The mutagen fallback is guarded on `is not None`, not on the
            # `if audio:` below: mutagen's FileType defines __len__ as its tag
            # count and no __bool__, so a file with *no tags at all* is falsy.
            # Those are exactly the files where the length is the only metadata
            # worth recovering.
            if duration is None and audio is not None:
                duration = getattr(getattr(audio, 'info', None), 'length', None)
            if duration and duration > 0:
                file_meta["duration_seconds"] = int(duration)

            if audio:
                logger.debug(f"[extract_metadata]   Mutagen type: {type(audio).__name__}")
                logger.debug(f"[extract_metadata]   Available tags: {list(audio.keys())[:30]}")

                # MP4/M4B/M4A files (mutagen.mp4.MP4)
                if hasattr(audio, 'tags') and hasattr(audio, 'info'):
                    audio_type = type(audio).__name__
                    
                    if audio_type in ('MP4', 'M4A'):
                        # MP4/M4B uses iTunes-style atoms
                        if '\xa9nam' in audio: file_meta["title"] = str(audio['\xa9nam'][0])
                        if '\xa9ART' in audio: file_meta["author"] = normalize_author(str(audio['\xa9ART'][0]))
                        
                        series_str = None
                        custom_series = audio.get('----:com.apple.iTunes:SERIES')
                        if custom_series:
                            series_str = bytes(custom_series[0]).decode('utf-8', 'replace').strip()
                        elif '\xa9grp' in audio:
                            series_str = str(audio['\xa9grp'][0])

                        if series_str:
                            s_name, s_idx = extract_series_and_index(series_str)
                            if s_name: file_meta["series"] = s_name
                            if s_idx is not None: file_meta["series_index"] = s_idx

                        custom_series_part = audio.get('----:com.apple.iTunes:SERIES-PART')
                        if custom_series_part and not file_meta.get('series_index'):
                            try:
                                file_meta['series_index'] = float(bytes(custom_series_part[0]).decode('utf-8', 'replace').strip())
                            except ValueError:
                                pass
                        
                        if '\xa9des' in audio: file_meta["description"] = md(str(audio['\xa9des'][0])).strip()
                        elif 'desc' in audio: file_meta["description"] = md(str(audio['desc'][0])).strip()
                        elif '\xa9cmt' in audio: file_meta["description"] = md(str(audio['\xa9cmt'][0])).strip()
                        
                        if '\xa9day' in audio:
                            match = re.search(r'\d{4}', str(audio['\xa9day'][0]))
                            if match: file_meta["publish_year"] = int(match.group(0))
                            
                        if '\xa9gen' in audio: file_meta["genres"] = str(audio['\xa9gen'][0])
                        
                        custom_narrator = audio.get('----:com.apple.iTunes:NARRATOR')
                        if custom_narrator:
                            file_meta["narrators"] = bytes(custom_narrator[0]).decode('utf-8', 'replace').strip()
                        elif '\xa9wrt' in audio:
                            file_meta["narrators"] = str(audio['\xa9wrt'][0])
                        elif '\xa9com' in audio:
                            file_meta["narrators"] = str(audio['\xa9com'][0])

                        if '\xa9pub' in audio: file_meta["publisher"] = str(audio['\xa9pub'][0])
                        elif '----:com.apple.iTunes:publisher' in audio:
                            file_meta["publisher"] = str(audio['----:com.apple.iTunes:publisher'][0], 'utf-8')
                    elif audio_type in ('MP3', 'FLAC', 'OggVorbis', 'OggOpus'):
                        # ID3 tags (MP3)
                        if 'TIT2' in audio: file_meta["title"] = str(audio['TIT2'])
                        elif 'title' in audio: file_meta["title"] = str(audio['title'][0])
                        
                        if 'TPE1' in audio: file_meta["author"] = normalize_author(str(audio['TPE1']))
                        elif 'artist' in audio: file_meta["author"] = normalize_author(str(audio['artist'][0]))
                        
                        series_str = None
                        if 'TIT3' in audio: series_str = str(audio['TIT3'])
                        elif 'subtitle' in audio: series_str = str(audio['subtitle'][0])
                        elif 'TIT1' in audio: series_str = str(audio['TIT1'])
                        elif 'GRP1' in audio: series_str = str(audio['GRP1'])
                        elif 'grouping' in audio: series_str = str(audio['grouping'][0])
                        elif 'TALB' in audio: series_str = str(audio['TALB'])
                        elif 'album' in audio: series_str = str(audio['album'][0])
                        
                        if series_str:
                            s_name, s_idx = extract_series_and_index(series_str)
                            if s_name: file_meta["series"] = s_name
                            if s_idx is not None: file_meta["series_index"] = s_idx
                        
                        # Extended fields
                        for kv in audio.keys():
                            if kv.startswith('COMM'):
                                file_meta["description"] = md(str(audio[kv].text[0])).strip()
                                break
                        if 'description' not in file_meta:
                            for dk in ['description', 'summary']:
                                if dk in audio: 
                                    file_meta["description"] = md(str(audio[dk][0])).strip()
                                    break
                                    
                        if 'TCON' in audio: file_meta["genres"] = str(audio['TCON'])
                        elif 'genre' in audio: file_meta["genres"] = str(audio['genre'][0])
                        
                        year_val = None
                        if 'TDRC' in audio: year_val = str(audio['TDRC'])
                        elif 'TYER' in audio: year_val = str(audio['TYER'])
                        elif 'date' in audio: year_val = str(audio['date'][0])
                        elif 'year' in audio: year_val = str(audio['year'][0])
                        if year_val:
                            match = re.search(r'\d{4}', year_val)
                            if match: file_meta["publish_year"] = int(match.group(0))
                            
                        if 'TPUB' in audio: file_meta["publisher"] = str(audio['TPUB'])
                        elif 'organization' in audio: file_meta["publisher"] = str(audio['organization'][0])
                        elif 'publisher' in audio: file_meta["publisher"] = str(audio['publisher'][0])
                        
                        if 'TCOM' in audio: file_meta["narrators"] = str(audio['TCOM'])
                        elif 'composer' in audio: file_meta["narrators"] = str(audio['composer'][0])
                    else:
                        # Generic fallback — try common keys
                        for title_key in ['\xa9nam', 'TIT2', 'title', 'TITLE']:
                            if title_key in audio:
                                val = audio[title_key]
                                file_meta["title"] = str(val[0]) if isinstance(val, list) else str(val)
                                break
                        for author_key in ['\xa9ART', 'TPE1', 'artist', 'ARTIST']:
                            if author_key in audio:
                                val = audio[author_key]
                                file_meta["author"] = normalize_author(str(val[0]) if isinstance(val, list) else str(val))
                                break
                        for album_key in ['\xa9alb', 'TALB', 'album', 'ALBUM']:
                            if album_key in audio:
                                val = audio[album_key]
                                file_meta["series"] = normalize_series(str(val[0]) if isinstance(val, list) else str(val))
                                break
                
                logger.debug(f"[extract_metadata]   Audio embedded: {file_meta}")
                
    except Exception as e:
        logger.warning(f"[extract_metadata]   Embedded metadata read failed: {e}")

    return file_meta


async def extract_metadata(
    filepath: str, file_type: str, db: AsyncSession,
    library_root: str = None
) -> Dict[str, Any]:
    """
    Extract metadata with priority:
    1. Embedded Metadata (EPUB/ID3/M4B tags)
    2. Regex on Filename (including directory-path patterns)
    3. Fallback to simple filename parsing
    
    Args:
        filepath: Absolute path to the file
        file_type: 'ebook' or 'audiobook'
        db: Database session
        library_root: Root directory of the library for computing relative paths
    """
    filename = os.path.basename(filepath)
    parent_dir = os.path.basename(os.path.dirname(filepath))
    
    # Compute relative path from the library root for directory-based patterns
    relative_path = None
    if library_root:
        try:
            relative_path = os.path.relpath(filepath, library_root)
            relative_path = relative_path.replace('\\', '/')
        except ValueError:
            pass  # Different drives on Windows, etc.
    
    logger.debug(f"[extract_metadata] Processing '{filepath}' (type={file_type})")
    logger.debug(f"[extract_metadata]   library_root='{library_root}', relative_path='{relative_path}'")
    
    # 1. Filename/path metadata (Regex/Settings) - Default priority as requested:
    filename_meta = await parse_filename_metadata_with_settings(
        filename, db, parent_dir, file_type, relative_path=relative_path
    )
    
    # 2. Embedded metadata, off the event loop (issue #203). This unzips and
    #    XML-parses the EPUB, or opens the audio container and probes its
    #    runtime — seconds of blocking file work per book on a single worker.
    file_meta = await asyncio.to_thread(_read_embedded_metadata, filepath, file_type)
        
    # Merge: Prefer embedded if exists, but keep filename/path data as fallback
    meta = filename_meta.copy()

    has_embedded = False
    for field in ["title", "author", "series", "series_index", "description", "publisher", "publish_year", "language", "genres", "tags", "isbn", "asin", "narrators"]:
        if file_meta.get(field) is not None:
            meta[field] = file_meta[field]
            has_embedded = True

    # Duration is a physical property of the file rather than something a
    # tagger wrote, so it merges outside the loop above: a readable length must
    # not by itself flip `_metadata_source` to "embedded".
    if file_meta.get("duration_seconds") is not None:
        meta["duration_seconds"] = file_meta["duration_seconds"]

    # Track the source: if embedded data overrode anything, note it
    if has_embedded:
        if meta.get("_metadata_source") == "pattern":
            meta["_metadata_source"] = "embedded+pattern"
        else:
            meta["_metadata_source"] = "embedded"
    
    logger.debug(f"[extract_metadata]   Merged result: {meta}")
    return meta


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


def sanitize_filename(name: str) -> str:
    if not name: return ""
    name = re.sub(r'[\\/*?:"<>|]', "", name)
    name = name.replace(" ", "_")
    return name.strip()

def _extract_and_save_cover(filepath: str, book_type: str, book_id: int, book_title: str = None) -> Optional[str]:
    covers_path = Path(settings.covers_dir)
    covers_path.mkdir(parents=True, exist_ok=True)
    
    cover_bytes = None
    ext = ".jpg"
    
    try:
        if book_type == "ebook" and filepath.lower().endswith(".epub"):
            book = epub.read_epub(filepath, options={'ignore_ncx': True})
            for item in book.get_items():
                if item.get_type() == ebooklib.ITEM_COVER:
                    cover_bytes = item.get_content()
                    ext = Path(item.file_name).suffix if item.file_name else ".jpg"
                    break
                elif item.get_type() == ebooklib.ITEM_IMAGE:
                    if "cover" in (item.file_name or "").lower():
                        cover_bytes = item.get_content()
                        ext = Path(item.file_name).suffix if item.file_name else ".jpg"
        
        elif book_type == "audiobook":
            audio = mutagen.File(filepath)
            if audio:
                if type(audio).__name__ in ('MP4', 'M4A') and 'covr' in audio:
                    covers = audio['covr']
                    if covers:
                        cover_bytes = bytes(covers[0])
                        ext = ".png" if getattr(covers[0], 'imageformat', None) == mutagen.mp4.MP4Cover.FORMAT_PNG else ".jpg"
                elif type(audio).__name__ == 'MP3':
                    from mutagen.id3 import APIC
                    if hasattr(audio, 'tags') and audio.tags:
                        for tag in audio.tags.values():
                            if isinstance(tag, APIC):
                                cover_bytes = tag.data
                                ext = ".png" if "png" in tag.mime.lower() else ".jpg"
                                break
                elif type(audio).__name__ in ('FLAC', 'OggVorbis', 'OggOpus'):
                    if getattr(audio, 'pictures', None) and audio.pictures:
                        cover_bytes = audio.pictures[0].data
                        ext = ".png" if "png" in audio.pictures[0].mime.lower() else ".jpg"
                        
        if cover_bytes:
            safe_title = sanitize_filename(book_title)
            base_name = safe_title if safe_title else book_type
            filename = f"{base_name}_{book_id}{ext}"
            dest_path = covers_path / filename
            with open(dest_path, "wb") as f:
                f.write(cover_bytes)
            return f"/api/files/covers/{filename}"
            
    except Exception as e:
        logger.warning(f"Failed to extract cover for {filepath}: {e}")
        
    return None


async def _load_abs_settings(db: AsyncSession) -> tuple[bool, str, str, str]:
    """Load ABS config from DB settings. Returns (enabled, url, api_token, prefix).

    The token is read from the encrypted credential store (source_key='abs');
    the other fields stay in system_settings since they're non-secret.
    """
    from services.abs_metadata import get_abs_token

    result = await db.execute(
        select(SystemSetting).where(
            SystemSetting.key.in_(["abs_enabled", "abs_url", "abs_audiobooks_prefix"])
        )
    )
    conf = {s.key: s.value for s in result.scalars().all()}
    enabled = conf.get("abs_enabled", "false").lower() == "true"
    url = conf.get("abs_url") or ""
    token = (await get_abs_token(db)) or ""
    prefix = conf.get("abs_audiobooks_prefix") or ""
    return enabled, url, token, prefix


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

        for f in ["description", "publisher", "publish_year", "language", "genres", "tags", "isbn", "asin"]:
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

        for f in ["description", "publisher", "publish_year", "language", "genres", "tags", "narrators", "isbn", "asin"]:
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


@router.post("/rescan-all")
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
            book.description = meta.get("description") or book.description
            book.publisher = meta.get("publisher") or book.publisher
            book.publish_year = meta.get("publish_year") or book.publish_year
            book.language = meta.get("language") or book.language
            book.genres = meta.get("genres") or book.genres
            book.tags = meta.get("tags") or book.tags

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
            book.description = meta.get("description") or book.description
            book.publisher = meta.get("publisher") or book.publisher
            book.publish_year = meta.get("publish_year") or book.publish_year
            book.language = meta.get("language") or book.language
            book.genres = meta.get("genres") or book.genres
            book.tags = meta.get("tags") or book.tags

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
    book.description = meta.get("description") or book.description
    book.publisher = meta.get("publisher") or book.publisher
    book.publish_year = meta.get("publish_year") or book.publish_year
    book.language = meta.get("language") or book.language
    book.genres = meta.get("genres") or book.genres
    book.tags = meta.get("tags") or book.tags
    
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


# ---------------------------------------------------------------------------
# Auto-pairing rules (issue #253)
#
# These four helpers are the whole decision an automatic pairing makes. They
# live at module level, take plain objects and touch no session, so the rules
# can be pinned by golden vectors (`tests/fixtures/auto_match_cases.json`)
# without a database — `auto_match_books` below is only the DB-driving shell
# that walks the unpaired rows and applies `_score_candidate`.
#
# Four rules were tightened in issue #253 and their golden vectors flipped in
# the same commit: series indexes compare as floats (1 is not the #1.5
# novella), a missing author on either side raises the title bar instead of
# silently skipping the author gate, the unpair exclusion is keyed by row id as
# well as file hash, and an empty normalized title never pairs. Greedy
# scan-order assignment was reviewed and deliberately kept.
# ---------------------------------------------------------------------------

# A title similarity at or above this wins the pairing when nothing rejects it.
AUTO_MATCH_TITLE_THRESHOLD = 75
# ...but with no author on one side there is no second signal to corroborate a
# near miss, so the title alone has to be this good ("The Way of Kings" vs
# "The Way of Kings Prime" scores 80 — two different books).
AUTO_MATCH_TITLE_THRESHOLD_NO_AUTHOR = 90
# Below this the authors are "different people" and no title score can rescue it.
AUTO_MATCH_AUTHOR_GATE = 70
# At or above this the authors agree well enough to boost the title score.
AUTO_MATCH_AUTHOR_BOOST = 80
AUTO_MATCH_AUTHOR_BOOST_POINTS = 10
# Below this the two series names are different series.
AUTO_MATCH_SERIES_THRESHOLD = 85
# Two series indexes closer than this are the same book. Only float noise is
# meant to fit under it — 1 and 1.5 are different books, and so are 1 and 1.1.
AUTO_MATCH_SERIES_INDEX_TOLERANCE = 0.001


def _normalize_for_comparison(text: str) -> str:
    """Normalize text for fuzzy comparison: strip articles, lowercase."""
    if not text:
        return ""
    t = text.lower().strip()
    # Strip leading articles for comparison
    for article in ['the ', 'a ', 'an ']:
        if t.startswith(article):
            t = t[len(article):]
            break
    return t


def _normalize_author(text: str) -> str:
    """Normalize author name for comparison.
    Strips punctuation used in initials/suffixes (L.E. → le, Jr. → jr)
    so that 'L.E. Modesitt Jr.' and 'L. E. Modesitt, Jr.' compare as equal.
    """
    if not text:
        return ""
    t = text.lower()
    t = re.sub(r'[.,]', '', t)       # remove periods and commas
    t = re.sub(r'\s+', ' ', t).strip()
    return t


def _parse_series_index(value) -> Optional[float]:
    """Best-effort float for a series index, or None when it is not a number.

    The column is a Float, but indexes reach the matcher from EPUB metadata and
    filename patterns before anything coerces them, so "1", "1.0" and "01" all
    turn up and all mean book one. An index that is not a number at all ("II",
    "") is treated as *missing* metadata rather than as a mismatch — see
    `_series_compatible`, which only rejects when both sides parse.
    """
    if value is None:
        return None
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _series_compatible(eb_series, eb_idx, ab_series, ab_idx) -> bool:
    """Return False if series metadata indicates these are different books."""
    if not eb_series and not ab_series:
        return True
    if not eb_series or not ab_series:
        return True  # Only one has series — missing metadata is fine
    series_score = fuzz.token_sort_ratio(
        _normalize_for_comparison(eb_series),
        _normalize_for_comparison(ab_series),
    )
    if series_score < AUTO_MATCH_SERIES_THRESHOLD:
        return False  # Clearly different series
    # Same series — if both have a readable index they must be the same number.
    # Compared as floats, not truncated to whole numbers: a #1.5 novella is a
    # different book from #1, and pairing it with #1's ebook builds a sync map
    # between two different texts (issue #253).
    eb_num = _parse_series_index(eb_idx)
    ab_num = _parse_series_index(ab_idx)
    if eb_num is not None and ab_num is not None:
        if abs(eb_num - ab_num) > AUTO_MATCH_SERIES_INDEX_TOLERANCE:
            return False
    return True


def _auto_pair_excluded(ebook, audiobook) -> bool:
    """True if a manual unpair recorded these two as "never re-pair".

    Two independent keys, either of which blocks the pair:

    * the *row ids* — always recordable, so a row without a file hash (legacy;
      every ingest and upload path computes one now) keeps its memory of the
      unpair instead of being re-paired by the next scan (issue #253);
    * the *file hashes* — kept because they survive a row being deleted and
      re-ingested at a new id, and because `POST /rehash` remaps them.

    Each is checked against the other side's identity, so an exclusion naming
    audiobook 22 does not block audiobook 33.
    """
    eb_excluded_ids = ebook.auto_pair_excluded_ids or []
    ab_excluded_ids = audiobook.auto_pair_excluded_ids or []
    if (audiobook.id is not None and audiobook.id in eb_excluded_ids) or (
        ebook.id is not None and ebook.id in ab_excluded_ids
    ):
        return True

    eb_excluded = ebook.auto_pair_excluded_hashes or []
    ab_excluded = audiobook.auto_pair_excluded_hashes or []
    return bool(
        (audiobook.file_hash and audiobook.file_hash in eb_excluded)
        or (ebook.file_hash and ebook.file_hash in ab_excluded)
    )


def _score_candidate(ebook, audiobook) -> Optional[float]:
    """Score this ebook/audiobook as a pairing, or None if it is not viable.

    None means "rejected": an unpair exclusion, incompatible series metadata,
    authors too far apart, or a title score under the threshold. Otherwise the
    number returned is the title score plus the author-agreement boost, and the
    highest score across an ebook's candidates wins.
    """
    if _auto_pair_excluded(ebook, audiobook):
        return None

    # Reject if series metadata indicates these are different books
    if not _series_compatible(ebook.series, ebook.series_index,
                              audiobook.series, audiobook.series_index):
        return None

    eb_title = _normalize_for_comparison(ebook.title)
    ab_title = _normalize_for_comparison(audiobook.title)

    # An empty title is not a match, it is absent metadata: two of them score a
    # perfect 100 against each other, which used to pair every untitled row in
    # the library with the first other one the scan reached (issue #253).
    if not eb_title or not ab_title:
        return None

    eb_author_norm = _normalize_author(ebook.author)
    ab_author_norm = _normalize_author(audiobook.author)

    # Author gate: if both books have authors they must be similar.
    # token_set_ratio handles initials/punctuation variants well
    # (e.g. "L.E. Modesitt Jr." matches "L. E. Modesitt, Jr.").
    if eb_author_norm and ab_author_norm:
        author_score = fuzz.token_set_ratio(eb_author_norm, ab_author_norm)
        if author_score < AUTO_MATCH_AUTHOR_GATE:
            return None  # Authors too different — don't pair regardless of title
        threshold = AUTO_MATCH_TITLE_THRESHOLD
    else:
        # No author on one side, so nothing corroborates the title. Rather than
        # skip the gate silently, demand a much better title match (issue #253).
        author_score = 0
        threshold = AUTO_MATCH_TITLE_THRESHOLD_NO_AUTHOR

    # Compare titles using fuzzy matching (token sort handles word order)
    score = fuzz.token_sort_ratio(eb_title, ab_title)

    # Boost score if authors also match well
    if author_score >= AUTO_MATCH_AUTHOR_BOOST:
        score = min(100, score + AUTO_MATCH_AUTHOR_BOOST_POINTS)

    if score < threshold:
        return None
    return score


async def auto_match_books(db: AsyncSession) -> int:
    """
    Attempt to auto-match unmatched ebooks and audiobooks by title similarity.
    Uses fuzzy string matching on the extracted titles (`_score_candidate`).
    Returns the number of new pairs created.

    Assignment is greedy in scan order: the first ebook to claim an audiobook
    keeps it, and `matched_audiobook_ids` stops a second ebook taking it in the
    same run.
    """
    # Check if auto-transcribe is enabled
    result = await db.execute(select(SystemSetting).where(SystemSetting.key == "auto_transcribe_enabled"))
    setting = result.scalar_one_or_none()
    auto_transcribe = False
    if setting and setting.value:
        auto_transcribe = setting.value.lower() == "true"

    # Get all ebooks that aren't already paired
    paired_ebook_ids = select(BookPair.ebook_id)
    result = await db.execute(
        select(EBook).where(EBook.id.notin_(paired_ebook_ids))
    )
    unpaired_ebooks = result.scalars().all()

    # Get all audiobooks that aren't already paired
    paired_audiobook_ids = select(BookPair.audiobook_id)
    result = await db.execute(
        select(AudioBook).where(AudioBook.id.notin_(paired_audiobook_ids))
    )
    unpaired_audiobooks = result.scalars().all()

    matched = 0
    matched_audiobook_ids = set()
    new_pair_ids = []

    for ebook in unpaired_ebooks:
        best_match = None
        best_score = 0

        for audiobook in unpaired_audiobooks:
            if audiobook.id in matched_audiobook_ids:
                continue

            score = _score_candidate(ebook, audiobook)
            if score is None:
                continue

            if score > best_score:
                best_score = score
                best_match = audiobook

        if best_match:
            pair = BookPair(
                ebook_id=ebook.id,
                audiobook_id=best_match.id,
                status=PairStatus.AUTO_MATCHED,
                matched_at=utcnow(),
            )
            db.add(pair)
            matched_audiobook_ids.add(best_match.id)
            matched += 1
            await db.flush() # Flush to get the ID
            new_pair_ids.append(pair.id)

    if auto_transcribe and new_pair_ids:
        from services.queue_manager import add_to_queue
        # Share this transaction: the pairs above are flushed, not committed,
        # so a second session would not see them (issue #199).
        await add_to_queue(new_pair_ids, db)

    return matched


# ---------------------------------------------------------------------------
# Paginated list endpoints (issue #48)
#
# `page`/`limit` follow GET /api/users/audit-log: 1-based page, limit 1..500,
# default 100. `q` is a case-insensitive substring match on title/author/series
# — the same match `/search` does — so clients stop fetching everything to
# filter locally. Every ordering ends in `id` so pages are disjoint and stable.
# ---------------------------------------------------------------------------

PAGE_DEFAULT_LIMIT = 100
PAGE_MAX_LIMIT = 500


def _page_param() -> int:
    return Query(1, ge=1, description="1-based page number")


def _limit_param() -> int:
    return Query(PAGE_DEFAULT_LIMIT, ge=1, le=PAGE_MAX_LIMIT, description="Page size")


def _q_param() -> Optional[str]:
    return Query(None, min_length=1, description="Substring match on title, author, or series")


# Hard cap on `/api/library/search`, which is not paginated (issue #208). Three
# unbounded queries, and — before the escaping below — `q=%` returned the whole
# library three times over in one request. 200 is well above any result a person
# scans by eye and well below anything that hurts.
SEARCH_MAX_RESULTS = 200

# LIKE's own wildcards. Someone typing `%` into a search box means the character,
# not "everything"; `_` means the character, not "any character". Escaped with a
# backslash, declared to the DB with `escape="\\"` so the same term behaves
# identically on SQLite and Postgres.
_LIKE_ESCAPE = "\\"


def _like_term(q: str) -> str:
    """`q` as a LIKE substring pattern with its wildcards neutralised.

    The backslash is escaped first, or escaping the wildcards afterwards would
    re-escape the escapes.
    """
    escaped = q.lower()
    for ch in (_LIKE_ESCAPE, "%", "_"):
        escaped = escaped.replace(ch, _LIKE_ESCAPE + ch)
    return f"%{escaped}%"


def _search_clause(model, q: Optional[str]):
    """`WHERE lower(title|author|series) LIKE %q%` for one model, or None."""
    if not q:
        return None
    term = _like_term(q)
    return or_(
        func.lower(model.title).like(term, escape=_LIKE_ESCAPE),
        func.lower(model.author).like(term, escape=_LIKE_ESCAPE),
        func.lower(model.series).like(term, escape=_LIKE_ESCAPE),
    )


def _library_order(model):
    """The library's browse order: author → series → series index → title, then id."""
    return (
        model.author.nulls_last(), model.series.nulls_last(),
        model.series_index.nulls_last(), model.title, model.id,
    )


async def _paginate(db: AsyncSession, query, count_from, page: int, limit: int) -> Page:
    """Run `query` for one page and a matching count.

    `count_from` is the FROM/JOIN/WHERE part of the same statement with no
    ORDER BY or eager-load options, so the total reflects exactly the filtered
    set. Returns a `Page` whose `items` are ORM rows — the endpoint's
    `response_model` serialises them.
    """
    total = (await db.execute(
        select(func.count()).select_from(count_from.subquery())
    )).scalar_one()
    rows = (await db.execute(
        query.offset((page - 1) * limit).limit(limit)
    )).scalars().all()
    return Page(items=rows, total=total, page=page, limit=limit)


async def _list_media(db, model, *, page, limit, q, where=None, order=None):
    """Shared body of the ebook/audiobook list endpoints."""
    base = select(model)
    if where is not None:
        base = base.where(where)
    clause = _search_clause(model, q)
    if clause is not None:
        base = base.where(clause)
    ordered = base.order_by(*(order if order is not None else _library_order(model)))
    return await _paginate(db, ordered, base, page, limit)


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
    # Ask for one more than the cap: if it comes back, there was more to find.
    probe = SEARCH_MAX_RESULTS + 1

    ebook_query = select(EBook).where(_search_clause(EBook, q)).limit(probe)
    ebooks = (await db.execute(ebook_query)).scalars().all()

    audiobook_query = select(AudioBook).where(_search_clause(AudioBook, q)).limit(probe)
    audiobooks = (await db.execute(audiobook_query)).scalars().all()

    # Pairs match on either side.
    pair_query = (
        select(BookPair)
        .options(selectinload(BookPair.ebook), selectinload(BookPair.audiobook))
        .join(BookPair.ebook)
        .join(BookPair.audiobook)
        .where(or_(_search_clause(EBook, q), _search_clause(AudioBook, q)))
        .limit(probe)
    )
    pairs = (await db.execute(pair_query)).scalars().all()

    truncated = any(len(rows) > SEARCH_MAX_RESULTS
                    for rows in (ebooks, audiobooks, pairs))

    return SearchResponse(
        query=q,
        ebooks=ebooks[:SEARCH_MAX_RESULTS],
        audiobooks=audiobooks[:SEARCH_MAX_RESULTS],
        book_pairs=pairs[:SEARCH_MAX_RESULTS],
        truncated=truncated,
    )


def _pairs_base(q: Optional[str], where=None):
    """FROM/JOIN/WHERE for a pairs listing; `q` matches either side of the pair."""
    base = select(BookPair).join(BookPair.ebook).join(BookPair.audiobook)
    if where is not None:
        base = base.where(where)
    if q:
        base = base.where(or_(_search_clause(EBook, q), _search_clause(AudioBook, q)))
    return base


_PAIR_LOADS = (
    # `sync_map` is eager-loaded purely so `sync_map_version` can be
    # reported here — this is the listing Android's `refreshPairs` polls,
    # and it is how a client learns its cached sync points went stale.
    selectinload(BookPair.ebook), selectinload(BookPair.audiobook),
    selectinload(BookPair.sync_map),
)


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


# ---------------------------------------------------------------------------
# Mixed library browse + facets (issue #120)
#
# The web LibraryPage renders one list: each pair once (ebook-primary for its
# display fields) plus every unpaired ebook and audiobook, filtered by tab /
# search / author / series, sorted, and paged. It used to download the whole
# library and do all of that in memory. Here the three shapes are normalised
# into one UNION ALL subquery so the database can filter, sort, count and page
# it in a single pass; the page's rows are then hydrated into the ordinary
# response models.
# ---------------------------------------------------------------------------

def _int_null():
    # A typed NULL, so the union's column types agree on Postgres.
    return cast(null(), Integer)


def _paired_ebook(): return exists(select(BookPair.id).where(BookPair.ebook_id == EBook.id))
def _paired_audiobook(): return exists(select(BookPair.id).where(BookPair.audiobook_id == AudioBook.id))


def _pair_arm(where=None):
    """Pairs, ebook-primary: title/author/series/... come from the ebook and
    fall back to the audiobook — the same rule the web used in memory."""
    q = select(
        literal("pair").label("kind"),
        BookPair.id.label("item_id"),
        BookPair.id.label("pair_id"),
        EBook.id.label("ebook_id"),
        AudioBook.id.label("audiobook_id"),
        func.coalesce(EBook.title, AudioBook.title).label("title"),
        func.coalesce(EBook.author, AudioBook.author).label("author"),
        func.coalesce(EBook.series, AudioBook.series).label("series"),
        func.coalesce(EBook.series_index, AudioBook.series_index).label("series_index"),
        func.coalesce(EBook.uploaded_at, AudioBook.uploaded_at).label("uploaded_at"),
        func.coalesce(EBook.file_size, AudioBook.file_size).label("file_size"),
        BookPair.acknowledged.label("acknowledged"),
        # The audiobook side, so `q` matches either half of a pair — the
        # display columns above are ebook-primary.
        AudioBook.title.label("alt_title"),
        AudioBook.author.label("alt_author"),
        AudioBook.series.label("alt_series"),
    ).select_from(BookPair).join(EBook, BookPair.ebook_id == EBook.id).join(
        AudioBook, BookPair.audiobook_id == AudioBook.id)
    return q.where(where) if where is not None else q


def _media_arm(model, kind: str, where=None):
    ebook_id = model.id if model is EBook else _int_null()
    audiobook_id = model.id if model is AudioBook else _int_null()
    q = select(
        literal(kind).label("kind"),
        model.id.label("item_id"),
        _int_null().label("pair_id"),
        ebook_id.label("ebook_id"),
        audiobook_id.label("audiobook_id"),
        model.title.label("title"),
        model.author.label("author"),
        model.series.label("series"),
        model.series_index.label("series_index"),
        model.uploaded_at.label("uploaded_at"),
        model.file_size.label("file_size"),
        model.acknowledged.label("acknowledged"),
        cast(null(), String).label("alt_title"),
        cast(null(), String).label("alt_author"),
        cast(null(), String).label("alt_series"),
    )
    return q.where(where) if where is not None else q


def _browse_arms(tab: LibraryTab, kind: Optional[LibraryItemKind]):
    """Which of the three shapes a tab (and its optional sub-kind) shows.

    Mirrors the web's tab semantics exactly: `all` is pairs + unpaired media;
    `ebooks`/`audiobooks` are every ebook/audiobook (paired ones carry their
    pair); `new` is unacknowledged media (or, with kind=pair, unacknowledged
    pairs).
    """
    want = lambda k: kind is None or kind == k  # noqa: E731
    arms = []
    if tab == LibraryTab.ALL:
        arms = [_pair_arm(), _media_arm(EBook, "ebook", ~_paired_ebook()),
                _media_arm(AudioBook, "audiobook", ~_paired_audiobook())]
    elif tab == LibraryTab.EBOOKS:
        arms = [_media_arm(EBook, "ebook")]
    elif tab == LibraryTab.AUDIOBOOKS:
        arms = [_media_arm(AudioBook, "audiobook")]
    elif tab == LibraryTab.PAIRED:
        arms = [_pair_arm()]
    elif tab == LibraryTab.UNPAIRED:
        if want(LibraryItemKind.EBOOK):
            arms.append(_media_arm(EBook, "ebook", ~_paired_ebook()))
        if want(LibraryItemKind.AUDIOBOOK):
            arms.append(_media_arm(AudioBook, "audiobook", ~_paired_audiobook()))
    elif tab == LibraryTab.NEW:
        if kind == LibraryItemKind.PAIR:
            arms = [_pair_arm(BookPair.acknowledged == False)]  # noqa: E712
        else:
            if want(LibraryItemKind.EBOOK):
                arms.append(_media_arm(EBook, "ebook", EBook.acknowledged == False))  # noqa: E712
            if want(LibraryItemKind.AUDIOBOOK):
                arms.append(_media_arm(AudioBook, "audiobook", AudioBook.acknowledged == False))  # noqa: E712
    return arms


def _browse_subquery(tab: LibraryTab, kind: Optional[LibraryItemKind]):
    arms = _browse_arms(tab, kind)
    if not arms:
        # An impossible tab/kind combination (e.g. paired + kind=ebook):
        # an empty set, expressed as a query so the callers stay uniform.
        return _media_arm(EBook, "ebook", literal(False)).subquery("browse")
    return union_all(*arms).subquery("browse")


def _browse_order(u, sort: LibrarySort, direction: SortDir):
    """ORDER BY for the browse list; every key ends in (kind, item_id) so pages
    are disjoint and stable, like every other list here."""
    desc = direction == SortDir.DESC

    def key(col):
        return col.desc().nulls_last() if desc else col.asc().nulls_last()

    if sort == LibrarySort.AUTHOR:
        keys = [key(u.c.author), key(u.c.series), key(u.c.series_index), key(u.c.title)]
    elif sort == LibrarySort.SERIES:
        keys = [key(u.c.series), key(u.c.series_index), key(u.c.title)]
    elif sort == LibrarySort.DATE:
        keys = [key(u.c.uploaded_at), key(u.c.title)]
    elif sort == LibrarySort.SIZE:
        keys = [key(u.c.file_size), key(u.c.title)]
    else:
        keys = [key(u.c.title)]
    return keys + [u.c.kind, u.c.item_id]


async def _hydrate_items(db: AsyncSession, rows) -> List[LibraryItem]:
    """Turn the page's union rows into LibraryItems, in the same order."""
    pair_ids = [r.pair_id for r in rows if r.kind == "pair"]
    ebook_ids = [r.ebook_id for r in rows if r.kind == "ebook"]
    audiobook_ids = [r.audiobook_id for r in rows if r.kind == "audiobook"]

    pairs: Dict[int, BookPair] = {}
    if pair_ids:
        for p in (await db.execute(
            select(BookPair).options(*_PAIR_LOADS).where(BookPair.id.in_(pair_ids))
        )).scalars():
            pairs[p.id] = p

    ebooks: Dict[int, EBook] = {}
    pair_by_ebook: Dict[int, BookPair] = {}
    if ebook_ids:
        for e in (await db.execute(select(EBook).where(EBook.id.in_(ebook_ids)))).scalars():
            ebooks[e.id] = e
        for p in (await db.execute(
            select(BookPair).options(*_PAIR_LOADS).where(BookPair.ebook_id.in_(ebook_ids))
        )).scalars():
            pair_by_ebook[p.ebook_id] = p

    audiobooks: Dict[int, AudioBook] = {}
    pair_by_audiobook: Dict[int, BookPair] = {}
    if audiobook_ids:
        for a in (await db.execute(select(AudioBook).where(AudioBook.id.in_(audiobook_ids)))).scalars():
            audiobooks[a.id] = a
        for p in (await db.execute(
            select(BookPair).options(*_PAIR_LOADS).where(BookPair.audiobook_id.in_(audiobook_ids))
        )).scalars():
            pair_by_audiobook[p.audiobook_id] = p

    def pair_model(p):
        return BookPairResponse.model_validate(p) if p is not None else None

    items: List[LibraryItem] = []
    for r in rows:
        if r.kind == "pair":
            p = pairs.get(r.pair_id)
            if p is None:
                continue  # deleted between the count and the hydrate
            items.append(LibraryItem(kind=LibraryItemKind.PAIR, pair=pair_model(p)))
        elif r.kind == "ebook":
            e = ebooks.get(r.ebook_id)
            if e is None:
                continue
            items.append(LibraryItem(
                kind=LibraryItemKind.EBOOK, ebook=EBookResponse.model_validate(e),
                pair=pair_model(pair_by_ebook.get(e.id)),
            ))
        else:
            a = audiobooks.get(r.audiobook_id)
            if a is None:
                continue
            items.append(LibraryItem(
                kind=LibraryItemKind.AUDIOBOOK, audiobook=AudioBookResponse.model_validate(a),
                pair=pair_model(pair_by_audiobook.get(a.id)),
            ))
    return items


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
    u = _browse_subquery(tab, kind)
    base = select(u)
    if q:
        term = f"%{q.lower()}%"
        base = base.where(or_(
            func.lower(u.c.title).like(term),
            func.lower(u.c.author).like(term),
            func.lower(u.c.series).like(term),
            func.lower(u.c.alt_title).like(term),
            func.lower(u.c.alt_author).like(term),
            func.lower(u.c.alt_series).like(term),
        ))
    if author:
        base = base.where(u.c.author == author)
    if series:
        base = base.where(u.c.series == series)

    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    rows = (await db.execute(
        base.order_by(*_browse_order(u, sort, dir)).offset((page - 1) * limit).limit(limit)
    )).all()
    items = await _hydrate_items(db, rows)
    return Page(items=items, total=total, page=page, limit=limit)


async def _count(db, stmt) -> int:
    return (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()


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
    u = _browse_subquery(tab, kind)

    async def facet(col):
        rows = (await db.execute(
            select(col, func.count()).where(col.isnot(None)).group_by(col).order_by(col)
        )).all()
        return [FacetCount(name=name, count=count) for name, count in rows]

    authors = await facet(u.c.author)
    series = await facet(u.c.series)

    unpaired_ebooks = await _count(db, select(EBook.id).where(~_paired_ebook()))
    unpaired_audiobooks = await _count(db, select(AudioBook.id).where(~_paired_audiobook()))
    counts = LibraryCounts(
        ebooks=await _count(db, select(EBook.id)),
        audiobooks=await _count(db, select(AudioBook.id)),
        pairs=await _count(db, select(BookPair.id)),
        unpaired=unpaired_ebooks + unpaired_audiobooks,
        new_ebooks=await _count(db, select(EBook.id).where(EBook.acknowledged == False)),  # noqa: E712
        new_audiobooks=await _count(db, select(AudioBook.id).where(AudioBook.acknowledged == False)),  # noqa: E712
        new_pairs=await _count(db, select(BookPair.id).where(BookPair.acknowledged == False)),  # noqa: E712
    )
    return LibraryFacets(authors=authors, series=series, counts=counts)


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


def _write_ebook_metadata(filepath: str, book) -> None:
    """
    Write metadata back to an EPUB file.
    Modifies Dublin Core fields and Calibre series metadata in-place.
    """
    if not filepath.lower().endswith(".epub"):
        logger.info(f"[write-back] Skipping non-EPUB file: {filepath}")
        return
    
    if not os.path.exists(filepath):
        logger.warning(f"[write-back] File not found: {filepath}")
        return
    
    logger.info(f"[write-back] Writing EPUB metadata to: {filepath}")
    
    try:
        # We use zipfile and ElementTree instead of ebooklib.write_epub because 
        # ebooklib is notorious for destroying complex epub structures and causing "Bad Zip File" errors.
        
        # 1. Find the OPF file
        opf_path = None
        with zipfile.ZipFile(filepath, 'r') as zin:
            # First look at container.xml
            try:
                container = zin.read("META-INF/container.xml")
                root = ET.fromstring(container)
                # usually urn:oasis:names:tc:opendocument:xmlns:container
                for rootfile in root.iter():
                    if 'rootfile' in rootfile.tag and 'full-path' in rootfile.attrib:
                        opf_path = rootfile.attrib['full-path']
                        break
            except Exception:
                pass
            
            # Fallback if container.xml parsing fails
            if not opf_path:
                for name in zin.namelist():
                    if name.lower().endswith('.opf'):
                        opf_path = name
                        break
                        
        if not opf_path:
            logger.error(f"[write-back] Could not locate OPF file in {filepath}")
            return
            
        # 2. Extract and modify the OPF
        with zipfile.ZipFile(filepath, 'r') as zin:
            opf_content = zin.read(opf_path)
            
        # Parse OPF XML
        # Register namespaces to preserve them on write
        namespaces = {
            'opf': 'http://www.idpf.org/2007/opf',
            'dc': 'http://purl.org/dc/elements/1.1/',
            'calibre': 'http://calibre.kovidgoyal.net/2009/metadata',
        }
        for prefix, uri in namespaces.items():
            ET.register_namespace(prefix, uri)
            
        root = ET.fromstring(opf_content)
        metadata = None
        for child in root.iter():
            if child.tag.endswith('metadata'):
                metadata = child
                break
                
        if metadata is None:
            logger.error(f"[write-back] No metadata block found in OPF for {filepath}")
            return
            
        # Helper to set or add a DC tag
        def set_dc_tag(tag_name, value):
            if not value: return
            found = False
            for child in list(metadata):
                if child.tag.endswith(tag_name):
                    child.text = str(value)
                    found = True
            if not found:
                el = ET.SubElement(metadata, f"{{http://purl.org/dc/elements/1.1/}}{tag_name}")
                el.text = str(value)

        def clear_dc_tag(tag_name, value):
            """Set DC tag when value is present; remove all matching tags when empty/None."""
            if value:
                set_dc_tag(tag_name, value)
            else:
                for child in list(metadata):
                    if child.tag.endswith(tag_name):
                        metadata.remove(child)

        set_dc_tag("title", book.title)
        set_dc_tag("creator", book.author)
        clear_dc_tag("description", getattr(book, 'description', None))
        clear_dc_tag("publisher", getattr(book, 'publisher', None))
        clear_dc_tag("language", getattr(book, 'language', None))
        clear_dc_tag("date", getattr(book, 'publish_year', None))
        
        # Calibre series meta tags
        if getattr(book, 'series', None):
            # Remove existing series tags
            for meta_tag in list(metadata):
                if meta_tag.tag.endswith('meta'):
                    name_attr = meta_tag.attrib.get('name')
                    if name_attr in ('calibre:series', 'calibre:series_index'):
                        metadata.remove(meta_tag)
                        
            # Add new ones
            series_meta = ET.SubElement(metadata, "{http://www.idpf.org/2007/opf}meta")
            series_meta.attrib['name'] = 'calibre:series'
            series_meta.attrib['content'] = str(book.series)
            
            if getattr(book, 'series_index', None) is not None:
                index_meta = ET.SubElement(metadata, "{http://www.idpf.org/2007/opf}meta")
                index_meta.attrib['name'] = 'calibre:series_index'
                index_meta.attrib['content'] = str(book.series_index)
                
        # Write modified OPF back to a new zip file, then replace original
        modified_opf = ET.tostring(root, encoding='utf-8', xml_declaration=True)
        
        fd, temp_path = tempfile.mkstemp(suffix=".epub")
        os.close(fd)
        
        try:
            with zipfile.ZipFile(filepath, 'r') as zin:
                with zipfile.ZipFile(temp_path, 'w', zipfile.ZIP_DEFLATED) as zout:
                    # Write all files except the OPF
                    for item in zin.infolist():
                        if item.filename != opf_path:
                            zout.writestr(item, zin.read(item.filename))
                    # Write the new OPF
                    zout.writestr(opf_path, modified_opf)
                    
            # Replace original
            shutil.move(temp_path, filepath)
            logger.info(f"[write-back] EPUB metadata written safely to {filepath}")
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        
    except Exception as e:
        logger.error(f"[write-back] Failed to write EPUB metadata: {e}")
        raise


def _write_audiobook_metadata(filepath: str, book) -> None:
    """
    Write metadata back to an audiobook file.
    Supports M4B/M4A (iTunes atoms), MP3 (ID3), FLAC, and Ogg.
    """
    if not os.path.exists(filepath):
        logger.warning(f"[write-back] File not found: {filepath}")
        return
    
    logger.info(f"[write-back] Writing audio metadata to: {filepath}")
    
    try:
        audio = mutagen.File(filepath)
        if not audio:
            logger.warning(f"[write-back] Mutagen could not open: {filepath}")
            return
        
        audio_type = type(audio).__name__
        logger.info(f"[write-back] Audio type: {audio_type}")
        
        if audio_type in ('MP4', 'M4A'):
            # iTunes-style atoms for M4B/M4A
            if book.title is not None: audio['\xa9nam'] = [book.title]
            if book.author is not None: audio['\xa9ART'] = [book.author]
            # Write series to the tags that extract_metadata reads (©grp, SERIES atom)
            # so that clearing series actually takes effect on subsequent scans.
            if book.series is not None:
                if book.series:
                    grp = (f"{book.series} #{int(book.series_index)}"
                           if book.series_index is not None else book.series)
                    audio['\xa9grp'] = [grp]
                    audio['----:com.apple.iTunes:SERIES'] = [book.series.encode('utf-8')]
                    if book.series_index is not None:
                        audio['----:com.apple.iTunes:SERIES-PART'] = [
                            str(int(book.series_index)).encode('utf-8')
                        ]
                else:
                    # Empty string = user cleared series; delete all series tags from file
                    for tag in ('\xa9grp', '\xa9alb', '----:com.apple.iTunes:SERIES',
                                '----:com.apple.iTunes:SERIES-PART'):
                        if tag in audio:
                            del audio[tag]
            if book.series_index is not None:
                audio['trkn'] = [(int(book.series_index), 0)]
            if book.description:
                audio['desc'] = [book.description]
            elif 'desc' in audio:
                del audio['desc']
            if getattr(book, 'genres', None) is not None: audio['\xa9gen'] = [book.genres]
            if getattr(book, 'publish_year', None) is not None: audio['\xa9day'] = [str(book.publish_year)]
                
        elif audio_type == 'MP3':
            from mutagen.id3 import TIT2, TPE1, TALB, TRCK, COMM, TCON, TYER, TPUB
            
            if audio.tags is None:
                audio.add_tags()
            
            if book.title is not None: audio.tags['TIT2'] = TIT2(encoding=3, text=book.title)
            if book.author is not None: audio.tags['TPE1'] = TPE1(encoding=3, text=book.author)
            if book.series is not None:
                if book.series:
                    audio.tags['TALB'] = TALB(encoding=3, text=book.series)
                elif 'TALB' in audio.tags:
                    del audio.tags['TALB']
            if book.series_index is not None:
                audio.tags['TRCK'] = TRCK(encoding=3, text=str(int(book.series_index)))
            if book.description:
                audio.tags['COMM'] = COMM(encoding=3, lang='eng', desc='', text=book.description)
            elif audio.tags and 'COMM' in audio.tags:
                del audio.tags['COMM']
            if getattr(book, 'genres', None) is not None: audio.tags['TCON'] = TCON(encoding=3, text=book.genres)
            if getattr(book, 'publish_year', None) is not None: audio.tags['TYER'] = TYER(encoding=3, text=str(book.publish_year))
            if getattr(book, 'publisher', None) is not None: audio.tags['TPUB'] = TPUB(encoding=3, text=book.publisher)
                
        elif audio_type in ('FLAC', 'OggVorbis', 'OggOpus'):
            # Vorbis comments
            if book.title is not None: audio['title'] = [book.title]
            if book.author is not None: audio['artist'] = [book.author]
            if book.series is not None:
                if book.series:
                    audio['album'] = [book.series]
                elif 'album' in audio:
                    del audio['album']
            if book.series_index is not None:
                audio['tracknumber'] = [str(int(book.series_index))]
            if book.description:
                audio['description'] = [book.description]
            elif 'description' in audio:
                del audio['description']
            if getattr(book, 'genres', None) is not None: audio['genre'] = [book.genres]
            if getattr(book, 'publish_year', None) is not None: audio['date'] = [str(book.publish_year)]
            if getattr(book, 'publisher', None) is not None: audio['organization'] = [book.publisher]
        else:
            logger.warning(f"[write-back] Unsupported audio type for write-back: {audio_type}")
            return
        
        audio.save()
        logger.info(f"[write-back] Audio metadata written successfully")
        
    except Exception as e:
        logger.error(f"[write-back] Failed to write audio metadata: {e}")

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
        
    if meta.title is not None: book.title = meta.title
    if meta.author is not None: book.author = meta.author
    if meta.series is not None: book.series = meta.series
    if meta.series_index is not None: book.series_index = meta.series_index
    if meta.description is not None: book.description = meta.description
    if meta.publisher is not None: book.publisher = meta.publisher
    if meta.publish_year is not None: book.publish_year = meta.publish_year
    if meta.language is not None: book.language = meta.language
    if meta.genres is not None: book.genres = meta.genres
    if meta.tags is not None: book.tags = meta.tags
    if meta.narrators is not None: book.narrators = meta.narrators
    if meta.isbn is not None: book.isbn = meta.isbn
    if meta.asin is not None: book.asin = meta.asin
    if meta.is_explicit is not None: book.is_explicit = meta.is_explicit
    if meta.is_abridged is not None: book.is_abridged = meta.is_abridged
    
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
        
    if meta.title is not None: book.title = meta.title
    if meta.author is not None: book.author = meta.author
    if meta.series is not None: book.series = meta.series
    if meta.series_index is not None: book.series_index = meta.series_index
    if meta.description is not None: book.description = meta.description
    if meta.publisher is not None: book.publisher = meta.publisher
    if meta.publish_year is not None: book.publish_year = meta.publish_year
    if meta.language is not None: book.language = meta.language
    if meta.genres is not None: book.genres = meta.genres
    if meta.tags is not None: book.tags = meta.tags
    if meta.narrators is not None: book.narrators = meta.narrators
    if meta.isbn is not None: book.isbn = meta.isbn
    if meta.asin is not None: book.asin = meta.asin
    if meta.is_explicit is not None: book.is_explicit = meta.is_explicit
    if meta.is_abridged is not None: book.is_abridged = meta.is_abridged
    
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

FIELDS_TO_COMPARE = [
    "title", "author", "series", "series_index", "description",
    "publisher", "publish_year", "language", "genres", "tags",
    "is_explicit", "is_abridged", "cover_path"
]


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

    existing = set(pair.ignored_fields or [])
    existing.update(req.fields)
    pair.ignored_fields = list(existing)

    # Need ebook/audiobook loaded to check discrepancies
    result = await db.execute(
        select(BookPair)
        .options(selectinload(BookPair.ebook), selectinload(BookPair.audiobook))
        .where(BookPair.id == pair_id)
    )
    loaded_pair = result.scalar_one_or_none()
    if loaded_pair and not _pair_has_discrepancies(loaded_pair):
        loaded_pair.acknowledged = True

    await db.commit()
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


@router.get("/verify")
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


@router.post("/cleanup")
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
        file_meta = {
            "title": ab.title,
            "author": ab.author,
            "series": ab.series,
            "series_index": ab.series_index,
            "description": ab.description,
            "publisher": ab.publisher,
            "publish_year": ab.publish_year,
            "language": ab.language,
            "genres": ab.genres,
            "tags": ab.tags,
            "isbn": ab.isbn,
            "asin": ab.asin,
            "narrators": ab.narrators,
            "is_explicit": ab.is_explicit,
            "is_abridged": ab.is_abridged,
        }
        enriched, changed, _ = enrich_from_abs(
            file_meta, ab.file_path, abs_index, abs_prefix, force=True
        )
        if changed:
            for field in ["title", "author", "series", "series_index", "description",
                          "publisher", "publish_year", "language", "genres", "tags",
                          "isbn", "asin", "narrators", "is_explicit", "is_abridged"]:
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

    file_meta = {
        "title": ab.title,
        "author": ab.author,
        "series": ab.series,
        "series_index": ab.series_index,
        "description": ab.description,
        "publisher": ab.publisher,
        "publish_year": ab.publish_year,
        "language": ab.language,
        "genres": ab.genres,
        "tags": ab.tags,
        "isbn": ab.isbn,
        "asin": ab.asin,
        "narrators": ab.narrators,
        "is_explicit": ab.is_explicit,
        "is_abridged": ab.is_abridged,
    }
    enriched, changed, matched = enrich_from_abs(
        file_meta, ab.file_path, abs_index, abs_prefix, force=True
    )

    tag_write_error = None
    if not matched:
        status_key = "no_match"
        message = "No matching entry found in Audiobookshelf for this book."
    elif changed:
        for field in ["title", "author", "series", "series_index", "description",
                      "publisher", "publish_year", "language", "genres", "tags",
                      "isbn", "asin", "narrators", "is_explicit", "is_abridged"]:
            if enriched.get(field) is not None:
                setattr(ab, field, enriched[field])
        db.add(ab)
        tag_write_ok, tag_write_error = await asyncio.to_thread(
            write_metadata_to_file, ab.file_path, enriched
        )
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


@router.get("/calibre-status")
async def get_calibre_status(
    current_user: User = Depends(rate_limited(expensive_reads)),
):
    """Check whether calibre's ebook-convert is available in the server container.

    `subprocess.run` with a 10 s timeout used to run inline on the event loop, so
    one wedged `ebook-convert` blocked every other request for those ten seconds
    — on a single-worker server, with a System page tile calling this on load.
    Now: worker thread, cached (issue #208).
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
