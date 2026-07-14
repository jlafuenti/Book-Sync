"""
Unit tests for the consolidated backup engine (issue #60, feature 2).

The destructive shell-outs (_run_pg_dump/_snapshot_covers/_run_pg_restore/
_restore_covers) are integration-only and monkeypatched here; these tests cover
the pure orchestration: id scheme, retention (manual-preserving), config, delete,
listing, and the scheduler decision.
"""

import time

import pytest

from config import settings
from models.settings import SystemSetting
from services import backup_service as bs


def _touch_backup(bdir, bid, *, covers=True, label=None, mtime=None):
    dump = bdir / f"booksync-db-{bid}.dump"
    dump.write_bytes(b"x" * 16)
    if covers:
        snap = bdir / "covers" / bid
        snap.mkdir(parents=True, exist_ok=True)
        (snap / "c.jpg").write_bytes(b"y" * 8)
    if label is not None:
        (bdir / f"booksync-db-{bid}.label").write_text(label)
    if mtime is not None:
        import os
        os.utime(dump, (mtime, mtime))
    return dump


# ── id helpers ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("bid,ok", [
    ("2026-07-13", True),
    ("2026-07-13_142530-manual", True),
    ("2026-7-1", False),
    ("2026-07-13_1425-manual", False),
    ("../secrets", False),
    ("2026-07-13/x", False),
    ("2026-07-13-manual", False),
    ("", False),
])
def test_valid_backup_id(bid, ok):
    assert bs.valid_backup_id(bid) is ok


def test_is_manual():
    assert bs.is_manual("2026-07-13_142530-manual") is True
    assert bs.is_manual("2026-07-13") is False


# ── scheduler decision ─────────────────────────────────────────────────────

def test_should_run_scheduled():
    cfg = bs.BackupConfig(enabled=True, hour=3, keep_daily=14, keep_monthly=6)
    assert bs._should_run_scheduled(3, cfg, today_exists=False) is True
    assert bs._should_run_scheduled(3, cfg, today_exists=True) is False   # already ran today
    assert bs._should_run_scheduled(4, cfg, today_exists=False) is False  # wrong hour
    assert bs._should_run_scheduled(3, bs.BackupConfig(False, 3, 14, 6), today_exists=False) is False


# ── config ─────────────────────────────────────────────────────────────────

async def test_get_backup_config_defaults(db):
    cfg = await bs.get_backup_config(db)
    assert cfg.enabled is True and cfg.hour == 3
    assert cfg.keep_daily == 14 and cfg.keep_monthly == 6


async def test_get_backup_config_overrides(db):
    db.add_all([
        SystemSetting(key="backup_enabled", value="false"),
        SystemSetting(key="backup_hour", value="1"),
        SystemSetting(key="backup_keep_daily", value="7"),
        SystemSetting(key="backup_keep_monthly", value="2"),
    ])
    await db.commit()
    cfg = await bs.get_backup_config(db)
    assert cfg.enabled is False and cfg.hour == 1
    assert cfg.keep_daily == 7 and cfg.keep_monthly == 2


# ── retention / prune ──────────────────────────────────────────────────────

def test_prune_keeps_daily_monthly_preserves_manual(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "backups_dir", str(tmp_path))
    for bid in ["2026-05-01", "2026-06-01", "2026-07-01", "2026-07-08",
                "2026-07-10", "2026-07-11", "2026-07-12", "2026-07-13"]:
        _touch_backup(tmp_path, bid)
    _touch_backup(tmp_path, "2026-07-05_120000-manual", label="before reorg")
    _touch_backup(tmp_path, "2026-01-02_030000-manual")

    pruned = bs.prune(bs.BackupConfig(enabled=True, hour=3, keep_daily=3, keep_monthly=2))

    assert set(pruned) == {"2026-07-10", "2026-07-08", "2026-05-01"}
    remaining = {i["id"] for i in bs.list_backups()}
    assert remaining == {
        "2026-07-13", "2026-07-12", "2026-07-11",   # daily window
        "2026-07-01", "2026-06-01",                 # month-firsts
        "2026-07-05_120000-manual", "2026-01-02_030000-manual",  # manual always kept
    }


def test_prune_removes_all_artifacts(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "backups_dir", str(tmp_path))
    for bid in ["2026-06-10", "2026-07-11", "2026-07-12", "2026-07-13"]:
        _touch_backup(tmp_path, bid, label="x")

    bs.prune(bs.BackupConfig(enabled=True, hour=3, keep_daily=3, keep_monthly=0))

    # 2026-06-10 is beyond the daily window and not a month-first → fully gone.
    assert not (tmp_path / "booksync-db-2026-06-10.dump").exists()
    assert not (tmp_path / "booksync-db-2026-06-10.label").exists()
    assert not (tmp_path / "covers" / "2026-06-10").exists()


# ── delete ─────────────────────────────────────────────────────────────────

def test_delete_backup_removes_artifacts(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "backups_dir", str(tmp_path))
    _touch_backup(tmp_path, "2026-07-13_120000-manual", label="keep me")

    bs.delete_backup("2026-07-13_120000-manual")

    assert not (tmp_path / "booksync-db-2026-07-13_120000-manual.dump").exists()
    assert not (tmp_path / "booksync-db-2026-07-13_120000-manual.label").exists()
    assert not (tmp_path / "covers" / "2026-07-13_120000-manual").exists()


def test_delete_backup_invalid_id(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "backups_dir", str(tmp_path))
    with pytest.raises(bs.InvalidBackupId):
        bs.delete_backup("../etc")


def test_delete_backup_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "backups_dir", str(tmp_path))
    with pytest.raises(bs.BackupNotFound):
        bs.delete_backup("2020-01-01")


# ── create ─────────────────────────────────────────────────────────────────

@pytest.fixture
def create_seams(monkeypatch, tmp_path):
    """Replace the shell-outs with fakes that write plausible artifacts.

    Also point covers_dir at a real dir so create_backup's
    ``if Path(settings.covers_dir).is_dir()`` gate is satisfied and the
    (faked) covers snapshot runs — otherwise on a clean runner the default
    /data/app/covers doesn't exist and no snapshot is written.
    """
    live_covers = tmp_path / "live-covers"
    live_covers.mkdir()
    monkeypatch.setattr(settings, "covers_dir", str(live_covers))

    async def fake_pg_dump(dump_path):
        with open(dump_path, "wb") as f:
            f.write(b"PGDMP-fake")

    async def fake_snapshot(snapshot_dir):
        import os
        os.makedirs(snapshot_dir, exist_ok=True)
        with open(os.path.join(snapshot_dir, "c.jpg"), "wb") as f:
            f.write(b"cover")

    monkeypatch.setattr(bs, "_run_pg_dump", fake_pg_dump)
    monkeypatch.setattr(bs, "_snapshot_covers", fake_snapshot)


async def test_create_manual_backup_with_label(monkeypatch, tmp_path, create_seams):
    monkeypatch.setattr(settings, "backups_dir", str(tmp_path))

    item = await bs.create_backup(manual=True, label="before big reorg")

    assert item["is_manual"] is True
    assert item["id"].endswith("-manual")
    assert item["label"] == "before big reorg"
    assert item["has_covers"] is True
    assert (tmp_path / f"booksync-db-{item['id']}.dump").exists()
    assert (tmp_path / f"booksync-db-{item['id']}.label").read_text() == "before big reorg"


async def test_create_scheduled_backup(monkeypatch, tmp_path, create_seams):
    monkeypatch.setattr(settings, "backups_dir", str(tmp_path))

    item = await bs.create_backup(manual=False)

    assert item["is_manual"] is False
    assert not item["id"].endswith("-manual")
    assert item["label"] is None


# ── list / status ──────────────────────────────────────────────────────────

def test_list_backups(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "backups_dir", str(tmp_path))
    _touch_backup(tmp_path, "2026-07-11", covers=True)
    _touch_backup(tmp_path, "2026-07-13_090000-manual", covers=False, label="pre-upgrade")

    items = bs.list_backups()
    ids = [i["id"] for i in items]
    assert ids == ["2026-07-13_090000-manual", "2026-07-11"]  # newest first
    manual, scheduled = items
    assert manual["is_manual"] is True and manual["label"] == "pre-upgrade" and manual["has_covers"] is False
    assert scheduled["is_manual"] is False and scheduled["label"] is None and scheduled["has_covers"] is True


def test_get_status(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "backups_dir", str(tmp_path))
    _touch_backup(tmp_path, "2026-07-13", mtime=time.time())

    st = bs.get_status()
    assert st["configured"] is True and st["stale"] is False
    assert st["latest_db_file"] == "booksync-db-2026-07-13.dump"

    monkeypatch.setattr(settings, "backups_dir", str(tmp_path / "empty"))
    st2 = bs.get_status()
    assert st2["configured"] is False and st2["stale"] is True


def test_dump_path_for_download(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "backups_dir", str(tmp_path))
    _touch_backup(tmp_path, "2026-07-13")
    assert bs.dump_path_for_download("2026-07-13").name == "booksync-db-2026-07-13.dump"
    with pytest.raises(bs.InvalidBackupId):
        bs.dump_path_for_download("not-a-date")
    with pytest.raises(bs.BackupNotFound):
        bs.dump_path_for_download("2020-01-01")


# ── scheduler tick ─────────────────────────────────────────────────────────

import datetime as _dt


async def test_tick_takes_baseline_when_empty(monkeypatch, tmp_path, create_seams):
    monkeypatch.setattr(settings, "backups_dir", str(tmp_path))
    monkeypatch.setattr(settings, "covers_dir", str(tmp_path / "src"))
    (tmp_path / "src").mkdir()

    await bs._tick()

    assert bs._list_ids()  # a baseline backup was created on a fresh deployment


async def test_tick_creates_scheduled_at_hour(monkeypatch, tmp_path, create_seams, db):
    monkeypatch.setattr(settings, "backups_dir", str(tmp_path))
    monkeypatch.setattr(settings, "covers_dir", str(tmp_path / "src"))
    (tmp_path / "src").mkdir()
    _touch_backup(tmp_path, "2020-01-01")  # not a fresh deploy
    db.add(SystemSetting(key="backup_hour", value=str(_dt.datetime.utcnow().hour)))
    await db.commit()

    await bs._tick()

    today = _dt.datetime.utcnow().strftime("%Y-%m-%d")
    assert (tmp_path / f"booksync-db-{today}.dump").exists()


async def test_tick_skips_off_hour(monkeypatch, tmp_path, create_seams, db):
    monkeypatch.setattr(settings, "backups_dir", str(tmp_path))
    _touch_backup(tmp_path, "2020-01-01")
    db.add(SystemSetting(key="backup_hour", value=str((_dt.datetime.utcnow().hour + 1) % 24)))
    await db.commit()

    await bs._tick()

    today = _dt.datetime.utcnow().strftime("%Y-%m-%d")
    assert not (tmp_path / f"booksync-db-{today}.dump").exists()


async def test_safe_create_and_prune_swallows_errors(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "backups_dir", str(tmp_path))

    async def boom(**kwargs):
        raise RuntimeError("dump failed")

    monkeypatch.setattr(bs, "create_backup", boom)
    # Must not raise — a failed scheduled backup is logged, not fatal to the loop.
    await bs._safe_create_and_prune(bs.BackupConfig(enabled=True, hour=3, keep_daily=14, keep_monthly=6))
