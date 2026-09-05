"""
Chapter router: reading and writing chapters to audiobook files via ffprobe/ffmpeg.
"""

import os
import json
import logging
import asyncio
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models.user import User
from models.book import AudioBook
from routers.auth import get_current_user, get_editor_user
from schemas import Chapter
from services import chapter_repair

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/audiobooks",
    tags=["chapters"],
)


@router.get("/{book_id}/chapters", response_model=List[Chapter])
async def get_audiobook_chapters(
    book_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """
    Extract chapters from an audiobook file using ffprobe.
    """
    result = await db.execute(select(AudioBook).where(AudioBook.id == book_id))
    book = result.scalar_one_or_none()
    if not book:
        raise HTTPException(status_code=404, detail="Audiobook not found")
        
    filepath = book.file_path
    if not os.path.exists(filepath):
        logger.warning(f"File not found: {filepath}")
        return []

    try:
        # Run ffprobe to get chapters as JSON
        cmd = [
            "ffprobe", 
            "-v", "quiet", 
            "-print_format", "json", 
            "-show_chapters", 
            filepath
        ]
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()
        
        if process.returncode != 0:
            logger.error(f"ffprobe failed: {stderr.decode()}")
            return []
            
        data = json.loads(stdout.decode())
        chapters_data = data.get("chapters", [])
        
        chapters = []
        for i, ch in enumerate(chapters_data):
            start = float(ch.get("start_time", 0))
            end = float(ch.get("end_time", 0))
            tags = ch.get("tags", {})
            title = tags.get("title", f"Chapter {i+1}")
            
            chapters.append(Chapter(
                id=i,
                start_time=start,
                end_time=end,
                title=title
            ))
            
        return chapters
        
    except Exception as e:
        logger.error(f"Error reading chapters: {e}")
        return []


@router.put("/{book_id}/chapters", response_model=List[Chapter])
async def update_audiobook_chapters(
    book_id: int,
    chapters: List[Chapter],
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_editor_user),
):
    """
    Update chapters in an audiobook file.

    The remux itself lives in `services.chapter_repair.remux_with_chapters`,
    shared with the chapter-encoding repair (issue #192): this is the user's
    only copy of a purchased audiobook, and the two paths that rewrite it must
    not be able to drift apart on how carefully they do it — preserving the
    iTunes freeform atoms ffmpeg cannot write, staging next to the target so
    the install is an atomic rename, and checking the output before it replaces
    the source. It is synchronous and shells out to ffmpeg, so it runs in a
    worker thread rather than on the event loop, exactly as the repair endpoint
    calls it.
    """
    result = await db.execute(select(AudioBook).where(AudioBook.id == book_id))
    book = result.scalar_one_or_none()
    if not book:
        raise HTTPException(status_code=404, detail="Audiobook not found")

    filepath = book.file_path
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="Audiobook file not found on disk")

    ok, error = await asyncio.to_thread(
        chapter_repair.remux_with_chapters,
        filepath,
        [{"start": ch.start_time, "end": ch.end_time, "title": ch.title}
         for ch in chapters],
    )
    if not ok:
        logger.error(f"Error updating chapters on {filepath}: {error}")
        raise HTTPException(status_code=500, detail=error)

    logger.info(f"Successfully wrote {len(chapters)} chapters to {filepath}")
    return chapters
