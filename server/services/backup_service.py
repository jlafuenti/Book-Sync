"""
Backup engine (issue #60, feature 2) — the single owner of database + covers
backups. Both the scheduler and the API call these functions, so there's one
implementation of "make a backup / restore / prune".

Layout under ``settings.backups_dir`` (a NAS mount, read-write in the server):

    booksync-db-<id>.dump      # custom-format pg_dump
    booksync-db-<id>.label     # optional label (manual backups)
    covers/<id>/               # hardlink cover snapshot (rsync --link-dest)

``<id>`` is a calendar date ``YYYY-MM-DD`` for scheduled backups (one/day) or
``YYYY-MM-DD_HHMMSS-manual`` for manual ones. Manual backups are kept until
deleted — retention pruning only touches scheduled ids.

The destructive shell-outs are isolated behind seams (``_run_pg_dump`` /
``_snapshot_covers`` / ``_run_pg_restore`` / ``_restore_covers``) so unit tests
can stub them; they're ``# pragma: no cover`` and exercised by the restore drill.

Scheduler mirrors ``services/import_scheduler.py``: a singleton asyncio task
started/stopped from ``main.py``'s lifespan.
"""

import asyncio
import logging
import os
import re
import shutil
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from sqlalchemy import select

from config import settings
from database import async_session
from models.settings import SystemSetting
from utils import utcnow

logger = logging.getLogger("backup-service")

DB_PREFIX = "booksync-db-"
DB_SUFFIX = ".dump"
LABEL_SUFFIX = ".label"
COVERS_SUBDIR = "covers"

# A scheduled id is a bare date; a manual id appends _HHMMSS-manual. Neither can
# contain a path separator, so a matching id can't traverse out of backups_dir.
_ID_RE = re.compile(r"^\d{4}-\d{2}-\d{2}(_\d{6}-manual)?$")
_BACKUP_STALE_AFTER_SECONDS = 36 * 3600

# Defaults mirror the backup keys in routers/settings.py DEFAULT_SETTINGS.
_DEFAULTS = {
    "backup_enabled": True,
    "backup_hour": 3,
    "backup_keep_daily": 14,
    "backup_keep_monthly": 6,
}

POLL_INTERVAL_SECONDS = 600


class InvalidBackupId(ValueError):
    """Raised when a backup id fails the strict format check."""


class BackupNotFound(Exception):
    """Raised when a referenced backup id has no dump on disk."""


@dataclass
class BackupConfig:
    enabled: bool
    hour: int
    keep_daily: int
    keep_monthly: int


# ── id / path helpers ──────────────────────────────────────────────────────

def valid_backup_id(backup_id: str) -> bool:
    return bool(_ID_RE.match(backup_id or ""))


def is_manual(backup_id: str) -> bool:
    return backup_id.endswith("-manual")


def _backups_dir() -> Path:
    return Path(settings.backups_dir)


def _covers_root() -> Path:
    return _backups_dir() / COVERS_SUBDIR


def _covers_snapshot(backup_id: str) -> Path:
    return _covers_root() / backup_id


def _dump_path(backup_id: str) -> Path:
    return _backups_dir() / f"{DB_PREFIX}{backup_id}{DB_SUFFIX}"


def _label_path(backup_id: str) -> Path:
    return _backups_dir() / f"{DB_PREFIX}{backup_id}{LABEL_SUFFIX}"


def _has_covers(snapshot: Path) -> bool:
    try:
        return snapshot.is_dir() and any(snapshot.iterdir())
    except OSError:
        return False


def _list_ids() -> list[str]:
    """Valid backup ids present on disk, oldest→newest (ids sort chronologically)."""
    out = []
    try:
        for entry in _backups_dir().iterdir():
            name = entry.name
            if name.startswith(DB_PREFIX) and name.endswith(DB_SUFFIX) and entry.is_file():
                bid = name[len(DB_PREFIX):-len(DB_SUFFIX)]
                if _ID_RE.match(bid):
                    out.append(bid)
    except OSError:
        return []
    out.sort()
    return out


# ── config ─────────────────────────────────────────────────────────────────

async def get_backup_config(db) -> BackupConfig:
    rows = (await db.execute(
        select(SystemSetting).where(SystemSetting.key.in_(list(_DEFAULTS.keys())))
    )).scalars().all()
    vals = {r.key: r.value for r in rows}

    def _bool(key: str) -> bool:
        v = vals.get(key)
        return _DEFAULTS[key] if v is None else str(v).lower() == "true"

    def _int(key: str) -> int:
        v = vals.get(key)
        if v is None:
            return _DEFAULTS[key]
        try:
            return int(v)
        except (ValueError, TypeError):
            return _DEFAULTS[key]

    return BackupConfig(
        enabled=_bool("backup_enabled"),
        hour=_int("backup_hour"),
        keep_daily=_int("backup_keep_daily"),
        keep_monthly=_int("backup_keep_monthly"),
    )


# ── list / status ──────────────────────────────────────────────────────────

def list_backups() -> list[dict]:
    """All backups, newest first, with metadata for the UI."""
    items = []
    for bid in reversed(_list_ids()):
        dump = _dump_path(bid)
        try:
            st = dump.stat()
        except OSError:
            continue
        label = None
        lp = _label_path(bid)
        if lp.is_file():
            try:
                label = lp.read_text().strip() or None
            except OSError:
                label = None
        items.append({
            "id": bid,
            "db_file": dump.name,
            "db_size_bytes": st.st_size,
            "has_covers": _has_covers(_covers_snapshot(bid)),
            "is_manual": is_manual(bid),
            "label": label,
            "created_utc": datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        })
    return items


def get_status() -> dict:
    """Lightweight health: newest backup + staleness flag."""
    location = settings.backups_dir
    ids = _list_ids()
    if not ids:
        return {
            "configured": False, "location": location, "last_backup_utc": None,
            "age_seconds": None, "stale": True, "latest_db_file": None,
            "latest_db_size_bytes": None,
        }
    bid = ids[-1]
    st = _dump_path(bid).stat()
    age = int(time.time() - st.st_mtime)
    return {
        "configured": True,
        "location": location,
        "last_backup_utc": datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "age_seconds": max(age, 0),
        "stale": age > _BACKUP_STALE_AFTER_SECONDS,
        "latest_db_file": _dump_path(bid).name,
        "latest_db_size_bytes": st.st_size,
    }


# ── delete / prune ─────────────────────────────────────────────────────────

def _remove_backup_files(backup_id: str) -> None:
    _dump_path(backup_id).unlink(missing_ok=True)
    _label_path(backup_id).unlink(missing_ok=True)
    shutil.rmtree(_covers_snapshot(backup_id), ignore_errors=True)


def delete_backup(backup_id: str) -> None:
    if not valid_backup_id(backup_id):
        raise InvalidBackupId(backup_id)
    if not _dump_path(backup_id).is_file():
        raise BackupNotFound(backup_id)
    _remove_backup_files(backup_id)


def dump_path_for_download(backup_id: str) -> Path:
    """Validated path to a backup's .dump file, for a download response."""
    if not valid_backup_id(backup_id):
        raise InvalidBackupId(backup_id)
    path = _dump_path(backup_id)
    if not path.is_file():
        raise BackupNotFound(backup_id)
    return path


def prune(config: BackupConfig) -> list[str]:
    """Delete scheduled backups beyond retention. Manual backups are never pruned."""
    scheduled = sorted((b for b in _list_ids() if not is_manual(b)), reverse=True)
    pruned = []
    i = 0
    monthly_kept = 0
    for bid in scheduled:
        i += 1
        day = bid[8:10]
        if i <= config.keep_daily:
            continue
        if day == "01" and monthly_kept < config.keep_monthly:
            monthly_kept += 1
            continue
        _remove_backup_files(bid)
        pruned.append(bid)
    return pruned


# ── create ─────────────────────────────────────────────────────────────────

async def create_backup(manual: bool, label: Optional[str] = None) -> dict:
    """Dump the DB (+ covers snapshot) into a new backup and return its list item."""
    _backups_dir().mkdir(parents=True, exist_ok=True)
    now = utcnow()
    backup_id = now.strftime("%Y-%m-%d_%H%M%S") + "-manual" if manual else now.strftime("%Y-%m-%d")

    dump = _dump_path(backup_id)
    tmp = Path(str(dump) + ".tmp")
    await _run_pg_dump(str(tmp))
    os.replace(tmp, dump)

    if Path(settings.covers_dir).is_dir():
        snap = _covers_snapshot(backup_id)
        snap.parent.mkdir(parents=True, exist_ok=True)
        await _snapshot_covers(str(snap))

    if manual and label:
        _label_path(backup_id).write_text(label)

    match = [i for i in list_backups() if i["id"] == backup_id]
    return match[0]


# ── restore ────────────────────────────────────────────────────────────────

async def restore(backup_id: str) -> dict:
    if not valid_backup_id(backup_id):
        raise InvalidBackupId(backup_id)
    dump = _dump_path(backup_id)
    if not dump.is_file():
        raise BackupNotFound(backup_id)
    await _run_pg_restore(str(dump))
    covers_restored = False
    snap = _covers_snapshot(backup_id)
    if _has_covers(snap):
        _restore_covers(str(snap))
        covers_restored = True
    return {"restored": True, "backup_id": backup_id, "covers_restored": covers_restored}


# ── integration seams (monkeypatched in tests; run only against real infra) ──

def _pg_env_and_conn():  # pragma: no cover
    from sqlalchemy.engine.url import make_url
    url = make_url(settings.database_url)
    env = dict(os.environ)
    if url.password:
        env["PGPASSWORD"] = url.password
    return url, env


async def _run_pg_dump(dump_path: str) -> None:  # pragma: no cover — needs real Postgres
    url, env = _pg_env_and_conn()
    cmd = [
        "pg_dump", "-Fc",
        "-h", url.host or "localhost", "-p", str(url.port or 5432),
        "-U", url.username or "booksync",
        "-f", dump_path, url.database or "booksync",
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd, env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"pg_dump exited {proc.returncode}: {stderr.decode(errors='replace')[:500]}")


async def _snapshot_covers(snapshot_dir: str) -> None:  # pragma: no cover — filesystem/rsync
    src = settings.covers_dir.rstrip("/") + "/"
    this = os.path.basename(snapshot_dir.rstrip("/"))
    prev = None
    try:
        others = sorted(
            (d.name for d in _covers_root().iterdir() if d.is_dir() and d.name != this),
            reverse=True,
        )
        if others:
            prev = str(_covers_root() / others[0])
    except OSError:
        pass
    cmd = ["rsync", "-a", "--delete"]
    if prev:
        cmd += ["--link-dest", prev]
    cmd += [src, snapshot_dir.rstrip("/") + "/"]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"rsync exited {proc.returncode}: {stderr.decode(errors='replace')[:500]}")


async def _run_pg_restore(dump_path: str) -> None:  # pragma: no cover — needs real Postgres
    from sqlalchemy import text
    from database import engine

    url, env = _pg_env_and_conn()
    try:
        async with engine.connect() as conn:
            await conn.execute(text(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = current_database() AND pid <> pg_backend_pid()"
            ))
    finally:
        await engine.dispose()

    cmd = [
        "pg_restore", "--clean", "--if-exists", "--no-owner", "--no-privileges",
        "-h", url.host or "localhost", "-p", str(url.port or 5432),
        "-U", url.username or "booksync", "-d", url.database or "booksync",
        dump_path,
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd, env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"pg_restore exited {proc.returncode}: {stderr.decode(errors='replace')[:500]}")


def _restore_covers(snapshot_dir: str) -> None:  # pragma: no cover — filesystem copy
    shutil.copytree(snapshot_dir, settings.covers_dir, dirs_exist_ok=True)


# ── scheduler ──────────────────────────────────────────────────────────────

_task: Optional[asyncio.Task] = None
_stop = asyncio.Event()


def _should_run_scheduled(now_hour: int, config: BackupConfig, today_exists: bool) -> bool:
    return bool(config.enabled) and now_hour == config.hour and not today_exists


async def _safe_create_and_prune(config: BackupConfig) -> None:
    try:
        item = await create_backup(manual=False)
        logger.info(f"[backup] created scheduled backup {item['id']}")
        pruned = prune(config)
        if pruned:
            logger.info(f"[backup] pruned {len(pruned)} old backup(s): {pruned}")
    except Exception as e:
        logger.exception(f"[backup] scheduled backup failed: {e}")


async def _tick() -> None:
    async with async_session() as db:
        config = await get_backup_config(db)

    # Fresh deployment: take an immediate baseline so there's something to restore.
    if not _list_ids():
        logger.info("[backup] no backups yet — taking an initial baseline")
        await _safe_create_and_prune(config)
        return

    now = utcnow()
    today_exists = _dump_path(now.strftime("%Y-%m-%d")).is_file()
    if _should_run_scheduled(now.hour, config, today_exists):
        await _safe_create_and_prune(config)


async def _run_loop() -> None:  # pragma: no cover — asyncio task plumbing (mirrors import_scheduler)
    logger.info("[backup] scheduler started")
    while not _stop.is_set():
        try:
            await _tick()
        except Exception as e:
            logger.exception(f"[backup] loop error: {e}")
        try:
            await asyncio.wait_for(_stop.wait(), timeout=POLL_INTERVAL_SECONDS)
        except asyncio.TimeoutError:
            pass
    logger.info("[backup] scheduler stopped")


async def start() -> None:  # pragma: no cover — asyncio task plumbing
    global _task
    _stop.clear()
    if _task is None or _task.done():
        _task = asyncio.create_task(_run_loop())


async def stop() -> None:  # pragma: no cover — asyncio task plumbing
    _stop.set()
    if _task is not None:
        try:
            await asyncio.wait_for(_task, timeout=5)
        except asyncio.TimeoutError:
            _task.cancel()
