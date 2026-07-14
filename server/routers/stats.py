"""
System statistics router.
"""

import os
import shutil
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database import get_db
from models.user import User
from routers.auth import get_current_user, get_admin_user, get_superadmin_user
from services import backup_service

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


# ═══════════════════════════════════════════════════════════════════════════
# Backups (issue #60) — thin HTTP layer over services/backup_service.py, which
# owns all backup logic (create/list/prune/delete/restore + the scheduler).
# Retention/schedule config lives in SystemSetting and is edited via the
# settings API. See docs/backup-restore.md.
# ═══════════════════════════════════════════════════════════════════════════


class BackupStatus(BaseModel):
    configured: bool
    location: str
    last_backup_utc: Optional[str]
    age_seconds: Optional[int]
    stale: bool
    latest_db_file: Optional[str]
    latest_db_size_bytes: Optional[int]


class BackupItem(BaseModel):
    id: str
    db_file: str
    db_size_bytes: int
    has_covers: bool
    is_manual: bool
    label: Optional[str]
    created_utc: str


class BackupListResponse(BaseModel):
    location: str
    items: list[BackupItem]


class CreateBackupRequest(BaseModel):
    label: Optional[str] = None


class RestoreRequest(BaseModel):
    backup_id: str
    confirm: bool = False


@router.get("/backup", response_model=BackupStatus)
async def get_backup_status(_: User = Depends(get_current_user)):
    """Lightweight backup health: last successful run + staleness flag."""
    return BackupStatus(**backup_service.get_status())


@router.get("/backups", response_model=BackupListResponse)
async def list_backups(_: User = Depends(get_admin_user)):
    """List available backups (newest first) for the restore/delete/download UI."""
    return BackupListResponse(
        location=settings.backups_dir,
        items=[BackupItem(**i) for i in backup_service.list_backups()],
    )


@router.post("/backups", response_model=BackupItem)
async def create_backup(body: CreateBackupRequest, _: User = Depends(get_superadmin_user)):
    """Create a manual backup now (kept indefinitely until deleted)."""
    label = (body.label or "").strip() or None
    try:
        item = await backup_service.create_backup(manual=True, label=label)
    except Exception as e:  # noqa: BLE001 — surface any backup failure to the caller
        raise HTTPException(status_code=500, detail=f"Backup failed: {e}")
    return BackupItem(**item)


@router.delete("/backups/{backup_id}")
async def delete_backup(backup_id: str, _: User = Depends(get_superadmin_user)):
    """Delete a backup (dump + label + covers snapshot)."""
    try:
        backup_service.delete_backup(backup_id)
    except backup_service.InvalidBackupId:
        raise HTTPException(status_code=400, detail="Invalid backup_id")
    except backup_service.BackupNotFound:
        raise HTTPException(status_code=404, detail="Backup not found")
    return {"deleted": True, "backup_id": backup_id}


@router.get("/backups/{backup_id}/download")
async def download_backup(backup_id: str, _: User = Depends(get_superadmin_user)):
    """Download a backup's .dump file (for off-site safekeeping)."""
    try:
        path = backup_service.dump_path_for_download(backup_id)
    except backup_service.InvalidBackupId:
        raise HTTPException(status_code=400, detail="Invalid backup_id")
    except backup_service.BackupNotFound:
        raise HTTPException(status_code=404, detail="Backup not found")
    return FileResponse(path, media_type="application/octet-stream", filename=path.name)


@router.post("/restore")
async def restore_backup(
    body: RestoreRequest,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_superadmin_user),
):
    """Restore a selected backup's DB (and covers) over the live deployment."""
    if not body.confirm:
        raise HTTPException(status_code=400, detail="Restore requires confirm=true")
    # Release this request's own DB connection before the restore terminates all
    # other backends and swaps the database out. Otherwise get_db's teardown
    # commit runs on a killed connection and 500s even though the restore ran.
    await db.close()
    try:
        return await backup_service.restore(body.backup_id)
    except backup_service.InvalidBackupId:
        raise HTTPException(status_code=400, detail="Invalid backup_id")
    except backup_service.BackupNotFound:
        raise HTTPException(status_code=404, detail="Backup not found")
    except Exception as e:  # noqa: BLE001 — surface any restore failure to the caller
        raise HTTPException(status_code=500, detail=f"Restore failed: {e}")
