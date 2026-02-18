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
    # Ebooks
    ebook_used_bytes: int
    ebook_total_bytes: int
    ebook_free_bytes: int
    ebook_used_human: str
    ebook_total_human: str
    ebook_free_human: str
    
    # Audiobooks
    audiobook_used_bytes: int
    audiobook_total_bytes: int
    audiobook_free_bytes: int
    audiobook_used_human: str
    audiobook_total_human: str
    audiobook_free_human: str
    
    # App Data
    app_data_used_bytes: int
    app_data_total_bytes: int
    app_data_free_bytes: int
    app_data_used_human: str
    app_data_total_human: str
    app_data_free_human: str


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
    
    # Heloer for filesystem stats (total, free) + directory size (used)
    def get_partition_stats(path: str):
        # Recursive size of the directory itself
        used_size = get_dir_size(path)
        
        # Filesystem stats (capacity of the drive)
        try:
            total, _, free = shutil.disk_usage(path)
        except Exception:
            total, free = 0, 0
            
        return used_size, total, free

    # Ebooks
    eb_used, eb_total, eb_free = get_partition_stats(settings.ebook_dir)
    
    # Audiobooks
    ab_used, ab_total, ab_free = get_partition_stats(settings.audiobook_dir)
    
    # App Data
    ad_used, ad_total, ad_free = get_partition_stats(settings.app_data_dir)

    return DiskUsageStats(
        # Ebooks
        ebook_used_bytes=eb_used,
        ebook_total_bytes=eb_total,
        ebook_free_bytes=eb_free,
        ebook_used_human=format_bytes(eb_used),
        ebook_total_human=format_bytes(eb_total),
        ebook_free_human=format_bytes(eb_free),
        
        # Audiobooks
        audiobook_used_bytes=ab_used,
        audiobook_total_bytes=ab_total,
        audiobook_free_bytes=ab_free,
        audiobook_used_human=format_bytes(ab_used),
        audiobook_total_human=format_bytes(ab_total),
        audiobook_free_human=format_bytes(ab_free),
        
        # App Data
        app_data_used_bytes=ad_used,
        app_data_total_bytes=ad_total,
        app_data_free_bytes=ad_free,
        app_data_used_human=format_bytes(ad_used),
        app_data_total_human=format_bytes(ad_total),
        app_data_free_human=format_bytes(ad_free),
    )
