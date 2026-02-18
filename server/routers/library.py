"""
Library router: manage ebooks, audiobooks, and book pairs.
Includes scanning directories, uploading files, and auto-matching.
"""

import os
import hashlib
from datetime import datetime
from pathlib import Path
from typing import List

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from rapidfuzz import fuzz

from database import get_db
from config import settings
from models.user import User
from models.book import EBook, AudioBook, BookPair, PairStatus
from schemas import (
    EBookResponse, AudioBookResponse, BookPairResponse,
    BookPairCreate, LibraryScanResponse,
)
from routers.auth import get_current_user, get_admin_user

router = APIRouter(prefix="/api/library", tags=["library"])

# Supported file extensions
EBOOK_EXTENSIONS = {".epub", ".pdf", ".mobi", ".azw3"}
AUDIOBOOK_EXTENSIONS = {".mp3", ".m4a", ".m4b", ".flac", ".ogg", ".wav", ".aac", ".wma"}


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


@router.post("/scan", response_model=LibraryScanResponse)
async def scan_library(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """
    Scan the ebook and audiobook directories for new files.
    Adds any undiscovered files to the database and attempts auto-matching.
    """
    new_ebooks = 0
    new_audiobooks = 0
    auto_matched = 0

    # Scan ebook directory
    ebook_dir = settings.ebook_dir
    if os.path.isdir(ebook_dir):
        for filename in os.listdir(ebook_dir):
            ext = Path(filename).suffix.lower()
            if ext not in EBOOK_EXTENSIONS:
                continue

            filepath = os.path.join(ebook_dir, filename)
            if not os.path.isfile(filepath):
                continue

            # Check if already in database
            result = await db.execute(
                select(EBook).where(EBook.filename == filename)
            )
            if result.scalar_one_or_none():
                continue

            file_hash = compute_file_hash(filepath)
            file_size = os.path.getsize(filepath)
            title = extract_title_from_filename(filename)

            ebook = EBook(
                title=title,
                author=None,  # Could parse from EPUB metadata later
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
        for filename in os.listdir(audiobook_dir):
            ext = Path(filename).suffix.lower()
            if ext not in AUDIOBOOK_EXTENSIONS:
                continue

            filepath = os.path.join(audiobook_dir, filename)
            if not os.path.isfile(filepath):
                continue

            # Check if already in database
            result = await db.execute(
                select(AudioBook).where(AudioBook.filename == filename)
            )
            if result.scalar_one_or_none():
                continue

            file_hash = compute_file_hash(filepath)
            file_size = os.path.getsize(filepath)
            title = extract_title_from_filename(filename)

            audiobook = AudioBook(
                title=title,
                author=None,
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
    result = await db.execute(select(EBook).order_by(EBook.title))
    return result.scalars().all()


@router.get("/audiobooks", response_model=List[AudioBookResponse])
async def list_audiobooks(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """List all audiobooks in the library."""
    result = await db.execute(select(AudioBook).order_by(AudioBook.title))
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
        .order_by(BookPair.id)
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
    _: User = Depends(get_admin_user),
):
    """Delete a book pair."""
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
    title = extract_title_from_filename(file.filename)

    ebook = EBook(
        title=title,
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
    title = extract_title_from_filename(file.filename)

    audiobook = AudioBook(
        title=title,
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
