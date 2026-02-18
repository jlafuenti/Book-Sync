"""
Library router: manage ebooks, audiobooks, and book pairs.
Includes scanning directories, uploading files, and auto-matching.
"""

import os
import re
import hashlib
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple, Dict, Any

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, status, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from rapidfuzz import fuzz

import ebooklib
from ebooklib import epub
import mutagen

from database import get_db
from config import settings
from models.user import User
from models.book import EBook, AudioBook, BookPair, PairStatus
from schemas import (
    EBookResponse, AudioBookResponse, BookPairResponse,
    BookPairCreate, LibraryScanResponse,
)
from routers.auth import get_current_user

router = APIRouter(prefix="/api/library", tags=["library"])

# Supported file extensions
EBOOK_EXTENSIONS = {".epub", ".pdf", ".mobi", ".azw3"}
AUDIOBOOK_EXTENSIONS = {".mp3", ".m4a", ".m4b", ".flac", ".ogg", ".wav", ".aac", ".wma"}

# Regex patterns for filename parsing
# Pattern 1: Author - [Series Num] - Title
REGEX_AUTHOR_SERIES_TITLE = re.compile(r"^(.+?) - \[(.+?) (\d+(?:\.\d+)?)\] - (.+)$")
# Pattern 2: [Series Num] Title (often found in Author folders)
REGEX_SERIES_TITLE = re.compile(r"^\[(.+?) (\d+(?:\.\d+)?)\] (.+)$")


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


def parse_filename_metadata(filename: str, parent_dir_name: str = None) -> Dict[str, Any]:
    """
    Attempt to extract metadata from filename using regex patterns.
    Returns a dict with 'title', 'author', 'series', 'series_index' keys (values may be None).
    """
    clean_name = Path(filename).stem
    meta = {
        "title": None,
        "author": None,
        "series": None,
        "series_index": None
    }

    # Pattern 1: Author - [Series Num] - Title
    match1 = REGEX_AUTHOR_SERIES_TITLE.match(clean_name)
    if match1:
        meta["author"] = match1.group(1).strip()
        meta["series"] = match1.group(2).strip()
        try:
            meta["series_index"] = float(match1.group(3))
        except ValueError:
            pass
        meta["title"] = match1.group(4).strip()
        return meta

    # Pattern 2: [Series Num] Title
    # If found, try to use parent directory as author
    match2 = REGEX_SERIES_TITLE.match(clean_name)
    if match2:
        meta["series"] = match2.group(1).strip()
        try:
            meta["series_index"] = float(match2.group(2))
        except ValueError:
            pass
        meta["title"] = match2.group(3).strip()
        if parent_dir_name:
             meta["author"] = parent_dir_name
        return meta
    
    # Fallback
    meta["title"] = extract_title_from_filename(filename)
    if parent_dir_name:
        meta["author"] = parent_dir_name
        
    return meta


def extract_metadata(filepath: str, file_type: str) -> Dict[str, Any]:
    """
    Extract metadata with priority:
    1. Embedded Metadata (EPUB/ID3)
    2. Regex on Filename
    3. Fallback to simple filename parsing
    """
    filename = os.path.basename(filepath)
    parent_dir = os.path.basename(os.path.dirname(filepath))
    
    # 1. Start with filename metadata as baseline fallback
    meta = parse_filename_metadata(filename, parent_dir)
    
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
            if c: file_meta["author"] = c[0][0]
            
            # Series (calibre specific usually)
            # Calibre stores series in <meta name="calibre:series" content="Series Name"/>
            # ebooklib handling, messy but possible. 
            # For MVP, we stick to standard DC metadata, or filename regex if better.
            
        elif file_type == "audiobook":
            audio = mutagen.File(filepath)
            if audio:
                # Title
                if 'TIT2' in audio: file_meta["title"] = str(audio['TIT2'])
                elif 'title' in audio: file_meta["title"] = str(audio['title'][0])
                
                # Author
                if 'TPE1' in audio: file_meta["author"] = str(audio['TPE1'])
                elif 'artist' in audio: file_meta["author"] = str(audio['artist'][0])
                
                # Album/Series?
                if 'TALB' in audio: file_meta["series"] = str(audio['TALB'])
                elif 'album' in audio: file_meta["series"] = str(audio['album'][0])
                
                # Track number as series index? Only if single file per book?
                # Probably unsafe for now unless user explicitly wants it.
                
    except Exception:
        pass # Metadata read failed, stick with filename
        
    # Merge: Prefer embedded if exists and not empty
    if file_meta.get("title"): meta["title"] = file_meta["title"]
    if file_meta.get("author"): meta["author"] = file_meta["author"]
    # For series, filename regex is often MORE reliable than tags for audiobooks
    # so we might prefer regex if embedded is missing. 
    # If embedded 'series' (album) is found, use it? Often album != series.
    # Let's trust regex for series if present, else fallback.
    
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
                
                # Check if already in database (by filename)
                result = await db.execute(
                    select(EBook).where(EBook.filename == filename)
                )
                existing_ebook = result.scalar_one_or_none()
                
                if existing_ebook:
                    # If exists but missing series info, try to update metadata
                    if existing_ebook.series is None:
                         meta = extract_metadata(filepath, "ebook")
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
                
                meta = extract_metadata(filepath, "ebook")

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
                    select(AudioBook).where(AudioBook.filename == filename)
                )
                existing_audiobook = result.scalar_one_or_none()
                
                if existing_audiobook:
                    # Update metadata if missing
                    if existing_audiobook.series is None:
                         meta = extract_metadata(filepath, "audiobook")
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
                
                meta = extract_metadata(filepath, "audiobook")

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
    return audiobook
