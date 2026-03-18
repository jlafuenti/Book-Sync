"""
Chapter router: reading and writing chapters to audiobook files via ffprobe/ffmpeg.
"""

import os
import json
import logging
import asyncio
import tempfile
import shutil
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models.user import User
from models.book import AudioBook
from routers.auth import get_current_user, get_editor_user
from schemas import Chapter

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
    Update chapters in an audiobook file using ffmpeg meta injection.
    """
    result = await db.execute(select(AudioBook).where(AudioBook.id == book_id))
    book = result.scalar_one_or_none()
    if not book:
        raise HTTPException(status_code=404, detail="Audiobook not found")
        
    filepath = book.file_path
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="Audiobook file not found on disk")

    try:
        # 1. Export existing metadata
        fd_meta, temp_meta_path = tempfile.mkstemp(suffix=".txt")
        os.close(fd_meta)
        
        cmd_export = [
            "ffmpeg", "-y", "-i", filepath, "-f", "ffmetadata", temp_meta_path
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd_export, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        await proc.communicate()
        if proc.returncode != 0:
            raise Exception("Failed to export metadata from file")

        # 2. Parse metadata text to remove existing [CHAPTER] blocks but keep everything else
        with open(temp_meta_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
            
        new_lines = []
        in_chapter_block = False
        for line in lines:
            if line.strip() == "[CHAPTER]":
                in_chapter_block = True
                continue
            elif in_chapter_block and line.startswith("["):
                # End of chapter block, start of a new section (e.g. [STREAM])
                in_chapter_block = False
                
            if not in_chapter_block:
                new_lines.append(line)
                
        # 3. Append new chapters
        timebase = 1000  # ms precision
        for ch in chapters:
            start_pts = int(ch.start_time * timebase)
            end_pts = int(ch.end_time * timebase)
            
            new_lines.append("[CHAPTER]\n")
            new_lines.append(f"TIMEBASE=1/{timebase}\n")
            new_lines.append(f"START={start_pts}\n")
            new_lines.append(f"END={end_pts}\n")
            new_lines.append(f"title={ch.title}\n")
            
        with open(temp_meta_path, 'w', encoding='utf-8') as f:
            f.writelines(new_lines)
            
        # 4. Write metadata back to a new file using codec copy
        fd_out, temp_out_path = tempfile.mkstemp(suffix=os.path.splitext(filepath)[1])
        os.close(fd_out)
        
        cmd_inject = [
            "ffmpeg", "-y", 
            "-i", filepath, 
            "-i", temp_meta_path, 
            "-map_metadata", "1", 
            "-codec", "copy", 
            temp_out_path
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd_inject, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        _, err = await proc.communicate()
        if proc.returncode != 0:
            raise Exception(f"Failed to inject metadata: {err.decode()}")
            
        # 5. Move new file over old file
        shutil.move(temp_out_path, filepath)
        logger.info(f"Successfully wrote {len(chapters)} chapters to {filepath}")
        
    except Exception as e:
        logger.error(f"Error updating chapters: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        # Cleanup
        if 'temp_meta_path' in locals() and os.path.exists(temp_meta_path):
            os.remove(temp_meta_path)
        if 'temp_out_path' in locals() and os.path.exists(temp_out_path):
            os.remove(temp_out_path)
            
    return chapters
