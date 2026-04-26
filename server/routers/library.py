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
import shutil
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, status, Query
from pydantic import BaseModel
from sqlalchemy import select, or_, func, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from rapidfuzz import fuzz

import ebooklib
from ebooklib import epub
import mutagen
from markdownify import markdownify as md
import zipfile
import xml.etree.ElementTree as ET
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
    NewItemsResponse, AcknowledgeItemsRequest, AcknowledgePairsRequest,
)
from routers.auth import get_current_user, get_editor_user
from services.metadata_utils import normalize_author, normalize_series, extract_series_and_index
from services.abs_metadata import fetch_abs_index, enrich_from_abs, write_metadata_to_file

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/library", tags=["library"])

# Supported file extensions
EBOOK_EXTENSIONS = {".epub", ".pdf", ".mobi", ".azw3"}
AUDIOBOOK_EXTENSIONS = {".mp3", ".m4a", ".m4b", ".flac", ".ogg", ".wav", ".aac", ".wma"}

# Regex patterns for filename parsing
# Pattern 1: Author - [Series Num] - Title
REGEX_AUTHOR_SERIES_TITLE = re.compile(r"^(.+?) - \[(.+?) (\d+(?:\.\d+)?)\] - (.+)$")
# Pattern 2: [Series Num] Title (often found in Author folders)
REGEX_SERIES_TITLE = re.compile(r"^\[(.+?) (\d+(?:\.\d+)?)\] (.+)$")


def regex_from_pattern(pattern: str) -> re.Pattern:
    """
    Convert a user-friendly pattern to a regex.
    Tags: <Author>, <Series>, <Book Number>, <Title>, <Series Index>
    Supports '/' in patterns to match against directory paths.
    """
    # Escape special regex chars in the pattern (except < > which we use for tags)
    parts = re.split(r'(<[^>]+>)', pattern)
    regex_parts = ["^"]
    
    tag_map = {
        "<Author>": r"(?P<author>[^/]+?)",
        "<Series>": r"(?P<series>[^/]+?)",
        "<Book Number>": r"(?P<series_index>\d+(?:\.\d+)?)",
        "<Series Index>": r"(?P<series_index>\d+(?:\.\d+)?)",
        "<Title>": r"(?P<title>[^/]+?)",
        "<Book Title>": r"(?P<title>[^/]+?)",
    }
    
    for part in parts:
        if part in tag_map:
            regex_parts.append(tag_map[part])
        else:
            regex_parts.append(re.escape(part))
            
    regex_parts.append("$")
    return re.compile("".join(regex_parts))


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
    """Compute SHA-256 hash of a file (first 10MB for speed on large files)."""
    sha256 = hashlib.sha256()
    with open(filepath, "rb") as f:
        # Read first 10MB for large audiobook files
        data = f.read(10 * 1024 * 1024)
        sha256.update(data)
    return sha256.hexdigest()

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
    
    logger.info(f"[metadata] Parsing '{filename}' (type={file_type})")
    logger.info(f"[metadata]   clean_name='{clean_name}', relative_path='{relative_path}', rel_path_stem='{rel_path_stem}'")
    
    for pattern_str in patterns:
        try:
            regex = regex_from_pattern(pattern_str)
            
            # Decide what to match against based on whether pattern uses directories
            if is_path_pattern(pattern_str):
                match_target = rel_path_stem
            else:
                match_target = clean_name
            
            match = regex.match(match_target)
            logger.debug(f"[metadata]   Pattern '{pattern_str}' vs '{match_target}' -> {'MATCH' if match else 'no match'}")
            
            if match:
                groups = match.groupdict()
                logger.info(f"[metadata]   Matched pattern '{pattern_str}' -> groups={groups}")
                
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
                    
                    logger.info(f"[metadata]   Final (pattern): {meta}")
                    return meta
        except Exception as e:
             logger.warning(f"[metadata]   Pattern '{pattern_str}' threw error: {e}")
             continue # Skip invalid patterns

    # Fallback to simple filename cleaning
    meta["title"] = extract_title_from_filename(filename)
    meta["_metadata_source"] = "filename"
    meta["_metadata_pattern"] = None
    if parent_dir_name:
        meta["author"] = normalize_author(parent_dir_name)
    
    logger.info(f"[metadata]   Final (fallback): {meta}")
    return meta


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
    
    logger.info(f"[extract_metadata] Processing '{filepath}' (type={file_type})")
    logger.info(f"[extract_metadata]   library_root='{library_root}', relative_path='{relative_path}'")
    
    # 1. Filename/path metadata (Regex/Settings) - Default priority as requested:
    filename_meta = await parse_filename_metadata_with_settings(
        filename, db, parent_dir, file_type, relative_path=relative_path
    )
    
    # 2. Try embedded metadata
    file_meta = {}
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

            logger.info(f"[extract_metadata]   EPUB embedded: {file_meta}")

        elif file_type == "audiobook":
            try:
                audio = mutagen.File(filepath)
            except mutagen.mp4.MP4MetadataError:
                audio = None
                logger.warning(f"[extract_metadata] MP4 chapter parse failed for {filepath}, skipping embedded tags")
            if audio:
                logger.info(f"[extract_metadata]   Mutagen type: {type(audio).__name__}")
                logger.info(f"[extract_metadata]   Available tags: {list(audio.keys())[:30]}")
                
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
                
                logger.info(f"[extract_metadata]   Audio embedded: {file_meta}")
                
    except Exception as e:
        logger.warning(f"[extract_metadata]   Embedded metadata read failed: {e}")
        
    # Merge: Prefer embedded if exists, but keep filename/path data as fallback
    meta = filename_meta.copy()

    has_embedded = False
    for field in ["title", "author", "series", "series_index", "description", "publisher", "publish_year", "language", "genres", "tags", "isbn", "asin"]:
        if file_meta.get(field) is not None:
            meta[field] = file_meta[field]
            has_embedded = True
    
    # Track the source: if embedded data overrode anything, note it
    if has_embedded:
        if meta.get("_metadata_source") == "pattern":
            meta["_metadata_source"] = "embedded+pattern"
        else:
            meta["_metadata_source"] = "embedded"
    
    logger.info(f"[extract_metadata]   Merged result: {meta}")
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


async def scan_library_impl(db: AsyncSession) -> LibraryScanResponse:
    """
    Library-scan implementation, callable from internal code paths
    (e.g. import sources after they place new files into the library).

    This is the body of POST /api/library/scan minus the auth dependency.
    """
    new_ebooks = 0
    new_audiobooks = 0
    auto_matched = 0

    # Scan ebook directory
    ebook_dir = settings.ebook_dir
    if os.path.isdir(ebook_dir):
        for root, _, files in os.walk(ebook_dir):
            for filename in files:
                ext = Path(filename).suffix.lower()
                if ext not in EBOOK_EXTENSIONS:
                    continue

                filepath = os.path.join(root, filename)
                rel_path = os.path.relpath(filepath, ebook_dir)
                
                # Check if already in database (by file_path or filename)
                result = await db.execute(
                    select(EBook).where(EBook.file_path == filepath)
                )
                existing_ebook = result.scalar_one_or_none()
                
                if existing_ebook:
                    # If exists, see if we can enrich it with embedded metadata
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

                    # Try to extract cover if missing
                    if not existing_ebook.cover_path:
                        try:
                            cover_path = await asyncio.to_thread(_extract_and_save_cover, filepath, "ebook", existing_ebook.id, existing_ebook.title)
                            if cover_path:
                                existing_ebook.cover_path = cover_path
                                db.add(existing_ebook)
                        except Exception as e:
                            logger.error(f"Error extracting cover after scan for ebook {existing_ebook.id}: {e}")
                    
                    continue

                try:
                    file_hash = compute_file_hash(filepath)
                    file_size = os.path.getsize(filepath)
                except OSError:
                    continue
                
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
                db.add(ebook)
                await db.flush()  # flush to get ID
                
                # Try to extract cover
                try:
                    cover_path = await asyncio.to_thread(_extract_and_save_cover, filepath, "ebook", ebook.id, ebook.title)
                    if cover_path:
                        ebook.cover_path = cover_path
                        db.add(ebook)
                except Exception as e:
                    logger.error(f"Error extracting cover for new ebook {ebook.id}: {e}")
                    
                new_ebooks += 1

    # Build ABS metadata index once before scanning audiobooks
    abs_index = {}
    _abs_enabled, _abs_url, _abs_token, _abs_prefix = await _load_abs_settings(db)
    if _abs_enabled and _abs_url and _abs_token:
        abs_index = await asyncio.to_thread(fetch_abs_index, _abs_url, _abs_token, _abs_prefix)

    # Scan audiobook directory
    audiobook_dir = settings.audiobook_dir
    if os.path.isdir(audiobook_dir):
        for root, _, files in os.walk(audiobook_dir):
            for filename in files:
                ext = Path(filename).suffix.lower()
                if ext not in AUDIOBOOK_EXTENSIONS:
                    continue

                filepath = os.path.join(root, filename)
                
                result = await db.execute(
                    select(AudioBook).where(AudioBook.file_path == filepath)
                )
                existing_audiobook = result.scalar_one_or_none()
                
                if existing_audiobook:
                    # Update metadata if missing
                    meta = await extract_metadata(filepath, "audiobook", db, library_root=audiobook_dir)

                    # Enrich from ABS for any still-missing fields
                    if abs_index:
                        meta, abs_changed, _ = enrich_from_abs(
                            meta, filepath, abs_index, audiobook_dir
                        )
                        # Respect user-cleared fields: empty string means user explicitly
                        # cleared the value — don't let ABS restore it or write it to file.
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

                    # Only set series if it has never been set (None) — empty string means
                    # the user explicitly cleared it and we must not overwrite that.
                    if meta.get("series") and existing_audiobook.series is None:
                         existing_audiobook.series = meta["series"]
                         existing_audiobook.series_index = meta.get("series_index")
                         updated = True

                    for f in ["description", "publisher", "publish_year", "language", "genres", "tags", "narrators", "isbn", "asin"]:
                         if meta.get(f) is not None and getattr(existing_audiobook, f) is None:
                             setattr(existing_audiobook, f, meta.get(f))
                             updated = True

                    if updated:
                        db.add(existing_audiobook)
                    # Try to extract cover if missing
                    if not existing_audiobook.cover_path:
                        try:
                            cover_path = await asyncio.to_thread(_extract_and_save_cover, filepath, "audiobook", existing_audiobook.id)
                            if cover_path:
                                existing_audiobook.cover_path = cover_path
                                db.add(existing_audiobook)
                        except Exception as e:
                            logger.error(f"Error extracting cover after scan for audiobook {existing_audiobook.id}: {e}")
                            
                    continue

                try:
                    file_hash = compute_file_hash(filepath)
                    file_size = os.path.getsize(filepath)
                except OSError:
                    continue
                
                meta = await extract_metadata(filepath, "audiobook", db, library_root=audiobook_dir)

                # Enrich from ABS before creating the record
                if abs_index:
                    meta, abs_changed, _ = enrich_from_abs(
                        meta, filepath, abs_index, audiobook_dir
                    )
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
                db.add(audiobook)
                await db.flush() # flush to get ID
                
                # Try to extract cover
                try:
                    cover_path = await asyncio.to_thread(_extract_and_save_cover, filepath, "audiobook", audiobook.id)
                    if cover_path:
                        audiobook.cover_path = cover_path
                        db.add(audiobook)
                except Exception as e:
                    logger.error(f"Error extracting cover for new audiobook {audiobook.id}: {e}")

                new_audiobooks += 1

    await db.flush()

    # Auto-match by filename similarity
    auto_matched = await auto_match_books(db)

    return LibraryScanResponse(
        new_ebooks=new_ebooks,
        new_audiobooks=new_audiobooks,
        auto_matched_pairs=auto_matched,
        message=f"Found {new_ebooks} new ebooks, {new_audiobooks} new audiobooks, "
                f"auto-matched {auto_matched} pairs.",
    )


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
    """
    return await scan_library_impl(db)


@router.post("/rescan-all")
async def rescan_all_files(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_editor_user)
):
    """
    Force a rescan of EVERY file in the library to extract metadata.
    Overrides all DB metadata fields with whatever is extracted from the files.
    """
    updated_ebooks = 0
    updated_audiobooks = 0
    
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

    await db.commit()
    
    return {
        "message": f"Force-rescanned {updated_ebooks} ebooks and {updated_audiobooks} audiobooks."
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


async def auto_match_books(db: AsyncSession) -> int:
    """
    Attempt to auto-match unmatched ebooks and audiobooks by title similarity.
    Uses fuzzy string matching on the extracted titles.
    Returns the number of new pairs created.
    """
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
        import re
        t = text.lower()
        t = re.sub(r'[.,]', '', t)       # remove periods and commas
        t = re.sub(r'\s+', ' ', t).strip()
        return t

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
        eb_title = _normalize_for_comparison(ebook.title)
        eb_author_norm = _normalize_author(ebook.author)

        for audiobook in unpaired_audiobooks:
            if audiobook.id in matched_audiobook_ids:
                continue

            ab_title = _normalize_for_comparison(audiobook.title)

            # Author gate: if both books have authors they must be similar.
            # token_set_ratio handles initials/punctuation variants well
            # (e.g. "L.E. Modesitt Jr." matches "L. E. Modesitt, Jr.").
            if eb_author_norm and audiobook.author:
                ab_author_norm = _normalize_author(audiobook.author)
                author_score = fuzz.token_set_ratio(eb_author_norm, ab_author_norm)
                if author_score < 70:
                    continue  # Authors too different — don't pair regardless of title
            else:
                author_score = 0

            # Compare titles using fuzzy matching (token sort handles word order)
            score = fuzz.token_sort_ratio(eb_title, ab_title)

            # Boost score if authors also match well
            if author_score >= 80:
                score = min(100, score + 10)

            if score > best_score and score >= 75:  # 75% similarity threshold
                best_score = score
                best_match = audiobook

        if best_match:
            pair = BookPair(
                ebook_id=ebook.id,
                audiobook_id=best_match.id,
                status=PairStatus.AUTO_MATCHED,
                matched_at=datetime.utcnow(),
            )
            db.add(pair)
            matched_audiobook_ids.add(best_match.id)
            matched += 1
            await db.flush() # Flush to get the ID
            new_pair_ids.append(pair.id)

    if auto_transcribe and new_pair_ids:
        from services.queue_manager import add_to_queue
        await add_to_queue(new_pair_ids)

    return matched


@router.get("/ebooks", response_model=List[EBookResponse])
async def list_ebooks(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """List all ebooks in the library."""
    result = await db.execute(
        select(EBook)
        .order_by(EBook.author.nulls_last(), EBook.series.nulls_last(), EBook.series_index.nulls_last(), EBook.title)
    )
    return result.scalars().all()


@router.get("/audiobooks", response_model=List[AudioBookResponse])
async def list_audiobooks(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """List all audiobooks in the library."""
    result = await db.execute(
        select(AudioBook)
        .order_by(AudioBook.author.nulls_last(), AudioBook.series.nulls_last(), AudioBook.series_index.nulls_last(), AudioBook.title)
    )
    return result.scalars().all()


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
    _: User = Depends(get_current_user),
):
    """Global search across ebooks, audiobooks, and book pairs."""
    search_term = f"%{q.lower()}%"
    
    # Search EBooks
    ebook_query = select(EBook).where(
        or_(
            func.lower(EBook.title).like(search_term),
            func.lower(EBook.author).like(search_term),
            func.lower(EBook.series).like(search_term)
        )
    )
    ebooks_result = await db.execute(ebook_query)
    ebooks = ebooks_result.scalars().all()
    
    # Search Audiobooks
    audiobook_query = select(AudioBook).where(
        or_(
            func.lower(AudioBook.title).like(search_term),
            func.lower(AudioBook.author).like(search_term),
            func.lower(AudioBook.series).like(search_term)
        )
    )
    audiobooks_result = await db.execute(audiobook_query)
    audiobooks = audiobooks_result.scalars().all()
    
    # Search BookPairs (matching either the ebook or audiobook)
    pair_query = (
        select(BookPair)
        .options(selectinload(BookPair.ebook), selectinload(BookPair.audiobook))
        .join(BookPair.ebook)
        .join(BookPair.audiobook)
        .where(
            or_(
                func.lower(EBook.title).like(search_term),
                func.lower(EBook.author).like(search_term),
                func.lower(EBook.series).like(search_term),
                func.lower(AudioBook.title).like(search_term),
                func.lower(AudioBook.author).like(search_term),
                func.lower(AudioBook.series).like(search_term)
            )
        )
    )
    pairs_result = await db.execute(pair_query)
    pairs = pairs_result.scalars().all()
    
    return SearchResponse(
        query=q,
        ebooks=ebooks,
        audiobooks=audiobooks,
        book_pairs=pairs
    )


@router.get("/pairs", response_model=List[BookPairResponse])
async def list_pairs(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """List all book pairs (matched ebook + audiobook)."""
    result = await db.execute(
        select(BookPair)
        .options(selectinload(BookPair.ebook), selectinload(BookPair.audiobook))
        .join(BookPair.ebook)
        .order_by(EBook.author.nulls_last(), EBook.series.nulls_last(), EBook.series_index.nulls_last(), EBook.title)
    )
    return result.scalars().all()


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
        matched_at=datetime.utcnow(),
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
        await add_to_queue([pair.id])

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
    result = await db.execute(select(BookPair).where(BookPair.id == pair_id))
    pair = result.scalar_one_or_none()
    if not pair:
        raise HTTPException(status_code=404, detail="Book pair not found")
    await db.execute(delete(TranscriptionQueueItem).where(TranscriptionQueueItem.book_pair_id == pair_id))
    await db.execute(delete(UserProgress).where(UserProgress.book_pair_id == pair_id))
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

    # Save file
    filepath = os.path.join(settings.ebook_dir, file.filename)
    content = await file.read()
    with open(filepath, "wb") as f:
        f.write(content)

    file_hash = hashlib.sha256(content[:10 * 1024 * 1024]).hexdigest()
    
    # Metadata extraction
    meta = extract_metadata(filepath, "ebook")

    ebook = EBook(
        title=meta["title"] or file.filename,
        author=meta["author"],
        series=meta["series"],
        series_index=meta["series_index"],
        filename=file.filename,
        file_path=filepath,
        file_hash=file_hash,
        file_size=len(content),
        format=ext.lstrip("."),
    )
    db.add(ebook)
    await db.flush()
    await db.refresh(ebook)
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

    # Save file
    filepath = os.path.join(settings.audiobook_dir, file.filename)
    content = await file.read()
    with open(filepath, "wb") as f:
        f.write(content)

    file_hash = hashlib.sha256(content[:10 * 1024 * 1024]).hexdigest()
    
    # Metadata extraction
    meta = extract_metadata(filepath, "audiobook")

    audiobook = AudioBook(
        title=meta["title"] or file.filename,
        author=meta["author"],
        series=meta["series"],
        series_index=meta["series_index"],
        filename=file.filename,
        file_path=filepath,
        file_hash=file_hash,
        file_size=len(content),
        format=ext.lstrip("."),
    )
    db.add(audiobook)
    await db.flush()
    await db.refresh(audiobook)

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
                
        set_dc_tag("title", book.title)
        set_dc_tag("creator", book.author)
        set_dc_tag("description", getattr(book, 'description', None))
        set_dc_tag("publisher", getattr(book, 'publisher', None))
        set_dc_tag("language", getattr(book, 'language', None))
        set_dc_tag("date", getattr(book, 'publish_year', None))
        
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
            if getattr(book, 'description', None) is not None: audio['desc'] = [book.description]
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
            if getattr(book, 'description', None) is not None:
                audio.tags['COMM'] = COMM(encoding=3, lang='eng', desc='', text=book.description)
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
            if getattr(book, 'description', None) is not None: audio['description'] = [book.description]
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
    
    ext = Path(file.filename).suffix
    safe_title = sanitize_filename(book.title) if book.title else "ebook"
    new_filename = f"{safe_title}_{book.id}{ext}"
    dest_path = covers_path / new_filename
    
    with open(dest_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
        
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
    
    ext = Path(file.filename).suffix
    safe_title = sanitize_filename(book.title) if book.title else "audiobook"
    new_filename = f"{safe_title}_{book.id}{ext}"
    dest_path = covers_path / new_filename
    
    with open(dest_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
        
    book.cover_path = f"/api/files/covers/{new_filename}"
    await db.commit()
    await db.refresh(book)
    return book


@router.get("/debug-metadata/{book_type}/{book_id}")
async def debug_metadata(
    book_type: str,
    book_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """
    Debug endpoint: re-extract metadata from a book's file and return all
    the intermediate data (pattern matches, embedded tags, merged result)
    without modifying the database.
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
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Return ebooks and audiobooks that haven't been acknowledged yet."""
    ebook_result = await db.execute(
        select(EBook).where(EBook.acknowledged == False).order_by(EBook.uploaded_at.desc())
    )
    audio_result = await db.execute(
        select(AudioBook).where(AudioBook.acknowledged == False).order_by(AudioBook.uploaded_at.desc())
    )
    return NewItemsResponse(
        ebooks=[EBookResponse.model_validate(e) for e in ebook_result.scalars().all()],
        audiobooks=[AudioBookResponse.model_validate(a) for a in audio_result.scalars().all()],
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


@router.get("/new-pairs", response_model=List[BookPairResponse])
async def get_new_pairs(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Return book pairs that haven't been acknowledged yet."""
    result = await db.execute(
        select(BookPair)
        .options(selectinload(BookPair.ebook), selectinload(BookPair.audiobook))
        .where(BookPair.acknowledged == False)
        .order_by(BookPair.matched_at.desc())
    )
    return result.scalars().all()


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

    # Remove associated transcription queue items and user progress for any pairs this ebook is in
    pairs_result = await db.execute(select(BookPair).where(BookPair.ebook_id == ebook_id))
    pairs = pairs_result.scalars().all()
    for pair in pairs:
        await db.execute(delete(TranscriptionQueueItem).where(TranscriptionQueueItem.book_pair_id == pair.id))
        await db.execute(delete(UserProgress).where(UserProgress.book_pair_id == pair.id))
        await db.execute(delete(AudioTranscript).where(AudioTranscript.pair_id == pair.id))

    # Remove any user progress referencing this ebook directly
    await db.execute(delete(UserProgress).where(UserProgress.ebook_id == ebook_id))

    # Delete the ebook (cascades to BookPair → SyncMap, Bookmarks)
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

    # Remove associated transcription queue items and user progress for any pairs this audiobook is in
    pairs_result = await db.execute(select(BookPair).where(BookPair.audiobook_id == audiobook_id))
    pairs = pairs_result.scalars().all()
    for pair in pairs:
        await db.execute(delete(TranscriptionQueueItem).where(TranscriptionQueueItem.book_pair_id == pair.id))
        await db.execute(delete(UserProgress).where(UserProgress.book_pair_id == pair.id))
        await db.execute(delete(AudioTranscript).where(AudioTranscript.pair_id == pair.id))

    # Remove any user progress referencing this audiobook directly
    await db.execute(delete(UserProgress).where(UserProgress.audiobook_id == audiobook_id))

    # Delete the audiobook (cascades to BookPair → SyncMap, Bookmarks)
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

@router.get("/verify")
async def verify_files(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """
    Check every ebook and audiobook file_path against the filesystem.
    Returns lists of entries whose source files no longer exist.
    """
    orphaned_ebooks = []
    orphaned_audiobooks = []

    ebooks_result = await db.execute(select(EBook))
    for ebook in ebooks_result.scalars().all():
        if ebook.file_path and not os.path.isfile(ebook.file_path):
            orphaned_ebooks.append({
                "id": ebook.id,
                "title": ebook.title,
                "author": ebook.author,
                "filename": ebook.filename,
                "file_path": ebook.file_path,
                "format": ebook.format,
            })

    audiobooks_result = await db.execute(select(AudioBook))
    for ab in audiobooks_result.scalars().all():
        if ab.file_path and not os.path.isfile(ab.file_path):
            orphaned_audiobooks.append({
                "id": ab.id,
                "title": ab.title,
                "author": ab.author,
                "filename": ab.filename,
                "file_path": ab.file_path,
                "format": ab.format,
            })

    return {
        "orphaned_ebooks": orphaned_ebooks,
        "orphaned_audiobooks": orphaned_audiobooks,
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
                await db.execute(delete(TranscriptionQueueItem).where(TranscriptionQueueItem.book_pair_id == pair.id))
                await db.execute(delete(UserProgress).where(UserProgress.book_pair_id == pair.id))
                await db.execute(delete(AudioTranscript).where(AudioTranscript.pair_id == pair.id))
            await db.execute(delete(UserProgress).where(UserProgress.ebook_id == eid))
            await db.delete(ebook)
            deleted_ebooks += 1

    for aid in req.audiobook_ids:
        result = await db.execute(select(AudioBook).where(AudioBook.id == aid))
        audiobook = result.scalar_one_or_none()
        if audiobook:
            pairs_result = await db.execute(select(BookPair).where(BookPair.audiobook_id == aid))
            for pair in pairs_result.scalars().all():
                await db.execute(delete(TranscriptionQueueItem).where(TranscriptionQueueItem.book_pair_id == pair.id))
                await db.execute(delete(UserProgress).where(UserProgress.book_pair_id == pair.id))
                await db.execute(delete(AudioTranscript).where(AudioTranscript.pair_id == pair.id))
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
    """
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
            await asyncio.to_thread(write_metadata_to_file, ab.file_path, enriched)
            updated_count += 1

    await db.commit()
    return {"message": f"Enriched {updated_count} audiobook(s) from Audiobookshelf", "updated": updated_count}


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
        await asyncio.to_thread(write_metadata_to_file, ab.file_path, enriched)
        await db.commit()
        status_key = "enriched"
        message = "Metadata enriched from Audiobookshelf and written back to file."
    else:
        status_key = "already_current"
        message = "Metadata is already up to date — no changes needed."

    await db.refresh(ab)
    from schemas import AudioBookResponse
    return {"status": status_key, "message": message, "book": AudioBookResponse.model_validate(ab)}


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

@router.get("/calibre-status")
async def get_calibre_status(current_user: User = Depends(get_current_user)):
    """Check whether calibre's ebook-convert is available in the server container."""
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
    existing = await db.execute(select(EBook).where(EBook.file_path == epub_path))
    existing_eb = existing.scalar_one_or_none()
    if existing_eb:
        return existing_eb

    file_size = Path(epub_path).stat().st_size
    file_hash = compute_file_hash(epub_path)

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
    db.add(epub_eb)
    await db.flush()
    return epub_eb


async def _relink_or_cleanup_pairs(eb_id: int, epub_eb: Optional[EBook], db: AsyncSession) -> None:
    """
    For every BookPair whose ebook_id == eb_id:
      - If epub_eb is given: re-point pair.ebook_id to the new EPUB (keeps pair + all data intact).
      - If epub_eb is None: delete the pair and all its dependent rows.
    Also removes UserProgress rows that reference eb_id directly.
    """
    pairs_result = await db.execute(select(BookPair).where(BookPair.ebook_id == eb_id))
    for pair in pairs_result.scalars().all():
        if epub_eb is not None:
            pair.ebook_id = epub_eb.id
            db.add(pair)
        else:
            await db.execute(delete(TranscriptionQueueItem).where(TranscriptionQueueItem.book_pair_id == pair.id))
            await db.execute(delete(UserProgress).where(UserProgress.book_pair_id == pair.id))
            await db.execute(delete(AudioTranscript).where(AudioTranscript.pair_id == pair.id))
            await db.delete(pair)
    await db.execute(delete(UserProgress).where(UserProgress.ebook_id == eb_id))


@router.get("/unsupported", response_model=List[UnsupportedFileResponse])
async def list_unsupported_files(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
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

    for eb, epub_eb in to_delete:
        await _relink_or_cleanup_pairs(eb.id, epub_eb, db)
        try:
            os.remove(eb.file_path)
        except OSError as e:
            logger.warning(f"[convert] could not delete {eb.file_path}: {e}")
        await db.delete(eb)

    await db.commit()

    return {
        "succeeded": succeeded,
        "failed": failed,
        "total": len(ebooks),
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
    if delete_source:
        await _relink_or_cleanup_pairs(eb.id, epub_eb, db)
        try:
            os.remove(eb.file_path)
        except OSError as e:
            logger.warning(f"[convert] could not delete source {eb.file_path}: {e}")
        await db.delete(eb)

    await db.commit()

    return {
        "status": "converted",
        "epub_ebook_id": epub_ebook_id,
        "epub_path": epub_path,
        "source_deleted": delete_source,
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
    await _relink_or_cleanup_pairs(ebook_id, epub_eb, db)

    await db.delete(eb)
    await db.commit()
    return {"status": "deleted", "filename": eb.filename}
