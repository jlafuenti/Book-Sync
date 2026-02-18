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

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, status, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from rapidfuzz import fuzz

import ebooklib
from ebooklib import epub
import mutagen

from database import get_db
from config import settings
from models.settings import SystemSetting
from models.user import User
from routers.settings import DEFAULT_SETTINGS
from models.book import EBook, AudioBook, BookPair, PairStatus
from schemas import (
    EBookResponse, AudioBookResponse, BookPairResponse,
    BookPairCreate, LibraryScanResponse,
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
    Normalize 'Last, First' to 'First Last'.
    """
    if not author: return None
    if "," in author:
        parts = author.split(",", 1)
        return f"{parts[1].strip()} {parts[0].strip()}"
    return author.strip()


def normalize_series(series: str) -> str:
    """
    Normalize series name.
    1. Remove leading 'The ' for consistency.
    """
    if not series: return None
    clean = series.strip()
    if clean.lower().startswith("the "):
        return clean[4:].strip()
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
        "series_index": None
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

    if file_meta.get("title"): meta["title"] = file_meta["title"]
    if file_meta.get("author"): meta["author"] = file_meta["author"]
    if file_meta.get("series"): meta["series"] = file_meta["series"]
    if file_meta.get("series_index") is not None: meta["series_index"] = file_meta["series_index"]
    
    logger.info(f"[extract_metadata]   Merged result: {meta}")
    return meta


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
                    # If exists but missing series info, try to update metadata
                    if existing_ebook.series is None:
                         # Pass db session and await
                         meta = await extract_metadata(filepath, "ebook", db, library_root=ebook_dir)
                         if meta["series"] or meta["series_index"] is not None:
                             existing_ebook.series = meta["series"]
                             existing_ebook.series_index = meta["series_index"]
                             existing_ebook.title = meta["title"] or existing_ebook.title
                             existing_ebook.author = meta["author"] or existing_ebook.author
                             db.add(existing_ebook)
                    continue

                try:
                    file_hash = compute_file_hash(filepath)
                    file_size = os.path.getsize(filepath)
                except OSError:
                    continue
                
                meta = await extract_metadata(filepath, "ebook", db, library_root=ebook_dir)

                ebook = EBook(
                    title=meta["title"] or filename,
                    author=meta["author"],
                    series=meta["series"],
                    series_index=meta["series_index"],
                    filename=filename,
                    file_path=filepath,
                    file_hash=file_hash,
                    file_size=file_size,
                    format=ext.lstrip("."),
                )
                db.add(ebook)
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
                    if existing_audiobook.series is None:
                         meta = await extract_metadata(filepath, "audiobook", db, library_root=audiobook_dir)
                         if meta["series"] or meta["series_index"] is not None:
                             existing_audiobook.series = meta["series"]
                             existing_audiobook.series_index = meta["series_index"]
                             existing_audiobook.title = meta["title"] or existing_audiobook.title
                             existing_audiobook.author = meta["author"] or existing_audiobook.author
                             db.add(existing_audiobook)
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
                    filename=filename,
                    file_path=filepath,
                    file_hash=file_hash,
                    file_size=file_size,
                    format=ext.lstrip("."),
                )
                db.add(audiobook)
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

        for audiobook in unpaired_audiobooks:
            if audiobook.id in matched_audiobook_ids:
                continue

            # Compare titles using fuzzy matching (token sort handles word order differences)
            score = fuzz.token_sort_ratio(ebook.title.lower(), audiobook.title.lower())

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

@router.patch("/ebooks/{book_id}", response_model=EBookResponse)
async def update_ebook_metadata(
    book_id: int,
    meta: MetadataUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Manually update ebook metadata."""
    result = await db.execute(select(EBook).where(EBook.id == book_id))
    book = result.scalar_one_or_none()
    if not book:
        raise HTTPException(status_code=404, detail="Book not found")
        
    if meta.title is not None: book.title = meta.title
    if meta.author is not None: book.author = meta.author
    if meta.series is not None: book.series = meta.series
    if meta.series_index is not None: book.series_index = meta.series_index
    
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
    """Manually update audiobook metadata."""
    result = await db.execute(select(AudioBook).where(AudioBook.id == book_id))
    book = result.scalar_one_or_none()
    if not book:
        raise HTTPException(status_code=404, detail="Book not found")
        
    if meta.title is not None: book.title = meta.title
    if meta.author is not None: book.author = meta.author
    if meta.series is not None: book.series = meta.series
    if meta.series_index is not None: book.series_index = meta.series_index
    
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
