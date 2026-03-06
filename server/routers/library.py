"""
Library router: manage ebooks, audiobooks, and book pairs.
Includes scanning directories, uploading files, and auto-matching.
"""

import os
import re
import hashlib
import logging
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple, Dict, Any

import asyncio
import shutil
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, status, Query
from pydantic import BaseModel
from sqlalchemy import select, or_, func
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
from schemas import (
    EBookResponse, AudioBookResponse, BookPairResponse,
    BookPairCreate, LibraryScanResponse, SearchResponse,
    EBookDetailResponse, AudioBookDetailResponse,
    MetadataDiscrepancy, ResolveDiscrepancyRequest, DiscrepantField
)
from routers.auth import get_current_user

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


def normalize_author(author: str) -> str:
    """
    Normalize author name to 'First Last' format.
    Handles:
    - 'Last, First' -> 'First Last'
    - 'Jim Butcher' -> 'Jim Butcher' (no change)
    - 'Butcher, Jim' -> 'Jim Butcher'
    - Extra whitespace is stripped.
    """
    if not author: return None
    author = author.strip()
    if not author: return None
    
    if "," in author:
        parts = author.split(",", 1)
        first = parts[1].strip()
        last = parts[0].strip()
        if first and last:
            return f"{first} {last}"
        return last or first
    return author


def normalize_series(series: str) -> str:
    """
    Normalize series name.
    - 'Dresden Files, The' -> 'The Dresden Files'
    - 'The Dresden Files' -> 'The Dresden Files' (preserved)
    - 'Dresden Files' -> 'Dresden Files' (no change, don't guess)
    - Trailing articles (A, An, The) are moved to the front.
    """
    if not series: return None
    clean = series.strip()
    if not clean: return None
    
    # Handle trailing article: 'Series Name, The' -> 'The Series Name'
    trailing_articles = [', The', ', A', ', An']
    for article in trailing_articles:
        if clean.endswith(article) or clean.lower().endswith(article.lower()):
            # Extract the article text (e.g., 'The', 'A', 'An')
            art = clean[-(len(article) - 2):].strip()  # skip the ', '
            base = clean[:-(len(article))].strip()
            # Capitalize the article properly
            return f"{art} {base}"
    
    return clean



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
            
            logger.info(f"[extract_metadata]   EPUB embedded: {file_meta}")
        
        elif file_type == "audiobook":
            audio = mutagen.File(filepath)
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
                        if '\xa9alb' in audio: file_meta["series"] = normalize_series(str(audio['\xa9alb'][0]))
                        
                        if '\xa9des' in audio: file_meta["description"] = md(str(audio['\xa9des'][0])).strip()
                        elif 'desc' in audio: file_meta["description"] = md(str(audio['desc'][0])).strip()
                        elif '\xa9cmt' in audio: file_meta["description"] = md(str(audio['\xa9cmt'][0])).strip()
                        
                        if '\xa9day' in audio:
                            match = re.search(r'\d{4}', str(audio['\xa9day'][0]))
                            if match: file_meta["publish_year"] = int(match.group(0))
                            
                        if '\xa9gen' in audio: file_meta["genres"] = str(audio['\xa9gen'][0])
                        
                        if '\xa9wrt' in audio: file_meta["narrators"] = str(audio['\xa9wrt'][0])
                        elif '\xa9com' in audio: file_meta["narrators"] = str(audio['\xa9com'][0])
                        
                        if '\xa9pub' in audio: file_meta["publisher"] = str(audio['\xa9pub'][0])
                        elif '----:com.apple.iTunes:publisher' in audio:
                            file_meta["publisher"] = str(audio['----:com.apple.iTunes:publisher'][0], 'utf-8')
                        
                        # Track number as series index
                        if 'trkn' in audio:
                            try:
                                track_info = audio['trkn'][0]  # Tuple: (track_number, total_tracks)
                                if isinstance(track_info, tuple):
                                    file_meta["series_index"] = float(track_info[0])
                                else:
                                    file_meta["series_index"] = float(track_info)
                            except (ValueError, TypeError, IndexError):
                                pass
                    elif audio_type in ('MP3', 'FLAC', 'OggVorbis', 'OggOpus'):
                        # ID3 tags (MP3)
                        if 'TIT2' in audio: file_meta["title"] = str(audio['TIT2'])
                        elif 'title' in audio: file_meta["title"] = str(audio['title'][0])
                        
                        if 'TPE1' in audio: file_meta["author"] = normalize_author(str(audio['TPE1']))
                        elif 'artist' in audio: file_meta["author"] = normalize_author(str(audio['artist'][0]))
                        
                        if 'TALB' in audio: file_meta["series"] = normalize_series(str(audio['TALB']))
                        elif 'album' in audio: file_meta["series"] = normalize_series(str(audio['album'][0]))
                        
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
    _: User = Depends(get_current_user),
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

@router.post("/scan", response_model=LibraryScanResponse)
async def scan_library(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """
    Scan the ebook and audiobook directories for new files.
    Recursively searches subdirectories.
    Extracts metadata from filenames and tags.
    Adds any undiscovered files to the database and attempts auto-matching.
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
                    updated = False
                    
                    if not existing_audiobook.metadata_source:
                         existing_audiobook.title = meta.get("title") or existing_audiobook.title
                         existing_audiobook.author = meta.get("author") or existing_audiobook.author
                         existing_audiobook.metadata_source = meta.get("_metadata_source")
                         existing_audiobook.metadata_pattern = meta.get("_metadata_pattern")
                         updated = True
                         
                    if meta.get("series") and not existing_audiobook.series:
                         existing_audiobook.series = meta["series"]
                         existing_audiobook.series_index = meta.get("series_index")
                         updated = True
                         
                    for f in ["description", "publisher", "publish_year", "language", "genres", "tags", "narrators"]:
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

                audiobook = AudioBook(
                    title=meta["title"] or filename,
                    author=meta["author"],
                    series=meta["series"],
                    series_index=meta["series_index"],
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

    for ebook in unpaired_ebooks:
        best_match = None
        best_score = 0
        eb_title = _normalize_for_comparison(ebook.title)
        eb_author = _normalize_for_comparison(ebook.author)

        for audiobook in unpaired_audiobooks:
            if audiobook.id in matched_audiobook_ids:
                continue

            ab_title = _normalize_for_comparison(audiobook.title)

            # Compare titles using fuzzy matching (token sort handles word order)
            score = fuzz.token_sort_ratio(eb_title, ab_title)
            
            # Boost score if authors also match
            if eb_author and audiobook.author:
                ab_author = _normalize_for_comparison(audiobook.author)
                author_score = fuzz.token_sort_ratio(eb_author, ab_author)
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
    _: User = Depends(get_current_user),
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
    _: User = Depends(get_current_user),
):
    """Delete a book pair."""
    # Allow any authenticated user to delete pairs for now
    result = await db.execute(select(BookPair).where(BookPair.id == pair_id))
    pair = result.scalar_one_or_none()
    if not pair:
        raise HTTPException(status_code=404, detail="Book pair not found")
    await db.delete(pair)


@router.post("/upload/ebook", response_model=EBookResponse, status_code=status.HTTP_201_CREATED)
async def upload_ebook(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
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
    _: User = Depends(get_current_user),
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
            if book.series is not None: audio['\xa9alb'] = [book.series]
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
            if book.series is not None: audio.tags['TALB'] = TALB(encoding=3, text=book.series)
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
            if book.series is not None: audio['album'] = [book.series]
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
        raise

@router.patch("/ebooks/{book_id}", response_model=EBookResponse)
async def update_ebook_metadata(
    book_id: int,
    meta: MetadataUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
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
    _: User = Depends(get_current_user),
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
    _: User = Depends(get_current_user),
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
    _: User = Depends(get_current_user),
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
        for field in FIELDS_TO_COMPARE:
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
    _: User = Depends(get_current_user),
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
        await db.commit()
        
        # Write back to files
        if ebook_changed:
            _write_ebook_metadata(ebook)
        if audio_changed:
            _write_audiobook_metadata(audiobook)
            
    return {"message": "Discrepancies resolved successfully"}

