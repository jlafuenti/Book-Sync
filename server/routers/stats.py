"""
System statistics router.
"""

import os
import shutil
from pathlib import Path
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from config import settings
from models.user import User
from routers.auth import get_current_user

router = APIRouter(prefix="/api/stats", tags=["stats"])


class DiskUsageStats(BaseModel):
    ebook_dir_bytes: int
    audiobook_dir_bytes: int
    app_data_dir_bytes: int
    ebook_dir_human: str
    audiobook_dir_human: str
    app_data_dir_human: str


def get_dir_size(path: str) -> int:
    """Calculate the total size of a directory in bytes."""
    total = 0
    try:
        for entry in os.scandir(path):
            if entry.is_file():
                total += entry.stat().st_size
            elif entry.is_dir():
                total += get_dir_size(entry.path)
    except Exception:
        pass  # Ignore permission errors, etc.
    return total


def format_bytes(size: int) -> str:
    """Format bytes into human readable string."""
    power = 2**10
    n = size
    power_labels = {0: '', 1: 'K', 2: 'M', 3: 'G', 4: 'T'}
    count = 0
    while n >= power and count < 4:
        n /= power
        count += 1
    return f"{n:.2f} {power_labels[count]}B"


@router.get("/disk_usage", response_model=DiskUsageStats)
async def get_disk_usage(
    _: User = Depends(get_current_user),
):
    """Get disk usage statistics for configured directories."""
    ebook_size = get_dir_size(settings.ebook_dir)
    audiobook_size = get_dir_size(settings.audiobook_dir)
    app_data_size = get_dir_size(settings.app_data_dir)

    return DiskUsageStats(
        ebook_dir_bytes=ebook_size,
        audiobook_dir_bytes=audiobook_size,
        app_data_dir_bytes=app_data_size,
        ebook_dir_human=format_bytes(ebook_size),
        audiobook_dir_human=format_bytes(audiobook_size),
        app_data_dir_human=format_bytes(app_data_size),
    )
