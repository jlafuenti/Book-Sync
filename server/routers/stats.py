"""
System statistics router.
"""

import asyncio
import os
import re
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from config import settings
from models.user import User
from routers.auth import get_current_user, get_admin_user, get_superadmin_user

router = APIRouter(prefix="/api/stats", tags=["stats"])

# ── Backup artifact naming (must match scripts/backup.sh) ──────────────────
_DB_PREFIX = "booksync-db-"
_DB_SUFFIX = ".dump"
# Covers are stored as per-date hardlink-snapshot directories: covers/<date>/.
_COVERS_SUBDIR = "covers"
# Backup ids are a plain calendar date; the strict pattern also blocks path
# traversal since a valid id can never contain a separator.
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# A daily backup older than this means at least one nightly run was missed.
_BACKUP_STALE_AFTER_SECONDS = 36 * 3600


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
# Backups (issue #60) — status, listing, and a guarded restore.
#
# The nightly `backup` compose sidecar writes custom-format pg_dump files and
# covers archives into `settings.backups_dir` (mounted read-only into the
# server). The server reads that directory to report status / list backups, and
# a superadmin can restore a chosen one over the live database. See
# docs/backup-restore.md and scripts/backup.sh.
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
    id: str  # YYYY-MM-DD
    db_file: str
    db_size_bytes: int
    has_covers: bool


class BackupListResponse(BaseModel):
    location: str
    items: list[BackupItem]


class RestoreRequest(BaseModel):
    backup_id: str
    confirm: bool = False


def _list_db_dumps(backups_dir: str):
    """Return [(date, Path)] for DB dumps in backups_dir, oldest→newest by date."""
    out = []
    try:
        for entry in Path(backups_dir).iterdir():
            name = entry.name
            if name.startswith(_DB_PREFIX) and name.endswith(_DB_SUFFIX) and entry.is_file():
                date = name[len(_DB_PREFIX):-len(_DB_SUFFIX)]
                if _DATE_RE.match(date):
                    out.append((date, entry))
    except OSError:
        return []
    out.sort(key=lambda t: t[0])
    return out


def _covers_snapshot(backups_dir: str, date: str) -> Path:
    """Path to the per-date covers snapshot directory (covers/<date>/)."""
    return Path(backups_dir) / _COVERS_SUBDIR / date


def _has_covers(snapshot: Path) -> bool:
    """True if the snapshot directory exists and holds at least one entry."""
    try:
        return snapshot.is_dir() and any(snapshot.iterdir())
    except OSError:
        return False


@router.get("/backup", response_model=BackupStatus)
async def get_backup_status(_: User = Depends(get_current_user)):
    """Lightweight backup health: last successful run + staleness flag."""
    location = settings.backups_dir
    dumps = _list_db_dumps(location)
    if not dumps:
        return BackupStatus(
            configured=False, location=location, last_backup_utc=None,
            age_seconds=None, stale=True, latest_db_file=None, latest_db_size_bytes=None,
        )
    _, path = dumps[-1]
    st = path.stat()
    age = int(time.time() - st.st_mtime)
    last_utc = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return BackupStatus(
        configured=True, location=location, last_backup_utc=last_utc,
        age_seconds=max(age, 0), stale=age > _BACKUP_STALE_AFTER_SECONDS,
        latest_db_file=path.name, latest_db_size_bytes=st.st_size,
    )


@router.get("/backups", response_model=BackupListResponse)
async def list_backups(_: User = Depends(get_admin_user)):
    """List available backups (newest first) for the restore picker."""
    location = settings.backups_dir
    items = []
    for date, path in reversed(_list_db_dumps(location)):
        items.append(BackupItem(
            id=date,
            db_file=path.name,
            db_size_bytes=path.stat().st_size,
            has_covers=_has_covers(_covers_snapshot(location, date)),
        ))
    return BackupListResponse(location=location, items=items)


async def _run_pg_restore(dump_path: str) -> None:  # pragma: no cover — integration-only (needs real Postgres + pg_restore); exercised by the restore drill
    """Restore a custom-format pg_dump over the live database.

    Terminates other backends and disposes the app's connection pool so the
    ``--clean`` DROPs aren't blocked by held locks, then shells out to
    ``pg_restore``. Isolated behind this seam so tests can stub it.
    """
    from sqlalchemy import text
    from sqlalchemy.engine.url import make_url

    from database import engine

    url = make_url(settings.database_url)
    try:
        async with engine.connect() as conn:
            await conn.execute(text(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = current_database() AND pid <> pg_backend_pid()"
            ))
    finally:
        await engine.dispose()

    env = dict(os.environ)
    if url.password:
        env["PGPASSWORD"] = url.password
    cmd = [
        "pg_restore", "--clean", "--if-exists", "--no-owner", "--no-privileges",
        "-h", url.host or "localhost",
        "-p", str(url.port or 5432),
        "-U", url.username or "booksync",
        "-d", url.database or "booksync",
        dump_path,
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd, env=env,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(
            f"pg_restore exited {proc.returncode}: {stderr.decode(errors='replace')[:500]}"
        )


def _restore_covers(snapshot_dir: str) -> None:  # pragma: no cover — integration-only (filesystem copy); exercised by the restore drill
    """Copy a covers snapshot directory into the live covers dir (merge/overwrite)."""
    shutil.copytree(snapshot_dir, settings.covers_dir, dirs_exist_ok=True)


@router.post("/restore")
async def restore_backup(body: RestoreRequest, _: User = Depends(get_superadmin_user)):
    """Restore a selected backup's DB (and covers) over the live deployment."""
    if not body.confirm:
        raise HTTPException(status_code=400, detail="Restore requires confirm=true")
    if not _DATE_RE.match(body.backup_id):
        raise HTTPException(status_code=400, detail="Invalid backup_id")

    backups = Path(settings.backups_dir)
    dump_path = backups / f"{_DB_PREFIX}{body.backup_id}{_DB_SUFFIX}"
    if not dump_path.is_file():
        raise HTTPException(status_code=404, detail="Backup not found")
    covers_path = _covers_snapshot(settings.backups_dir, body.backup_id)

    try:
        await _run_pg_restore(str(dump_path))
        covers_restored = False
        if _has_covers(covers_path):
            _restore_covers(str(covers_path))
            covers_restored = True
    except Exception as e:  # noqa: BLE001 — surface any restore failure to the caller
        raise HTTPException(status_code=500, detail=f"Restore failed: {e}")

    return {"restored": True, "backup_id": body.backup_id, "covers_restored": covers_restored}
