"""
Stats router tests (issue #46, Phase 3).
"""

import os
import time

import pytest

from config import settings
from routers import stats


async def test_disk_usage_returns_shape(make_client, make_user, auth_header, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "ebook_dir", str(tmp_path / "e"))
    monkeypatch.setattr(settings, "audiobook_dir", str(tmp_path / "a"))
    monkeypatch.setattr(settings, "app_data_dir", str(tmp_path))
    (tmp_path / "e").mkdir()
    (tmp_path / "a").mkdir()

    user = await make_user(username="u", role="user")
    async with make_client(stats.router) as c:
        r = await c.get("/api/stats/disk_usage", headers=auth_header(user))

    assert r.status_code == 200
    body = r.json()
    for key in ("ebook_used_bytes", "audiobook_total_bytes", "app_data_free_bytes"):
        assert key in body and isinstance(body[key], int) and body[key] >= 0
    assert isinstance(body["ebook_used_human"], str)


async def test_disk_usage_requires_auth(make_client):
    async with make_client(stats.router) as c:
        r = await c.get("/api/stats/disk_usage")
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# Backup status / list / restore (issue #60)
# ---------------------------------------------------------------------------

def _make_backup(backups_dir, date, *, db_size=128, covers=True, mtime=None):
    """Create fake backup artifacts for `date` (YYYY-MM-DD) in backups_dir.

    Covers are a per-date snapshot directory (hardlink-snapshot scheme), not a
    tarball — a non-empty ``covers/<date>/`` dir means that date has covers.
    """
    db = backups_dir / f"booksync-db-{date}.dump"
    db.write_bytes(b"x" * db_size)
    if covers:
        snap = backups_dir / "covers" / date
        snap.mkdir(parents=True, exist_ok=True)
        (snap / "cover.jpg").write_bytes(b"y" * 64)
    if mtime is not None:
        os.utime(db, (mtime, mtime))
    return db


async def test_backup_status_recent(make_client, make_user, auth_header, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "backups_dir", str(tmp_path))
    _make_backup(tmp_path, "2026-07-13", db_size=200, mtime=time.time())

    user = await make_user(username="u", role="user")
    async with make_client(stats.router) as c:
        r = await c.get("/api/stats/backup", headers=auth_header(user))

    assert r.status_code == 200
    body = r.json()
    assert body["configured"] is True
    assert body["stale"] is False
    assert body["location"] == str(tmp_path)
    assert body["latest_db_file"] == "booksync-db-2026-07-13.dump"
    assert body["latest_db_size_bytes"] == 200
    assert isinstance(body["age_seconds"], int) and body["age_seconds"] >= 0
    assert body["last_backup_utc"] is not None


async def test_backup_status_stale_when_old(make_client, make_user, auth_header, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "backups_dir", str(tmp_path))
    old = time.time() - 3 * 24 * 3600
    _make_backup(tmp_path, "2026-07-10", mtime=old)

    user = await make_user(username="u", role="user")
    async with make_client(stats.router) as c:
        r = await c.get("/api/stats/backup", headers=auth_header(user))

    assert r.status_code == 200
    body = r.json()
    assert body["configured"] is True
    assert body["stale"] is True
    assert body["age_seconds"] > 36 * 3600


async def test_backup_status_empty_dir(make_client, make_user, auth_header, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "backups_dir", str(tmp_path / "does-not-exist"))

    user = await make_user(username="u", role="user")
    async with make_client(stats.router) as c:
        r = await c.get("/api/stats/backup", headers=auth_header(user))

    assert r.status_code == 200
    body = r.json()
    assert body["configured"] is False
    assert body["stale"] is True
    assert body["age_seconds"] is None
    assert body["last_backup_utc"] is None
    assert body["latest_db_file"] is None


async def test_backup_status_requires_auth(make_client):
    async with make_client(stats.router) as c:
        r = await c.get("/api/stats/backup")
    assert r.status_code == 401


async def test_backups_list(make_client, make_user, auth_header, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "backups_dir", str(tmp_path))
    _make_backup(tmp_path, "2026-07-11", db_size=100, covers=True)
    _make_backup(tmp_path, "2026-07-13", db_size=300, covers=False)

    user = await make_user(username="a", role="admin")
    async with make_client(stats.router) as c:
        r = await c.get("/api/stats/backups", headers=auth_header(user))

    assert r.status_code == 200
    body = r.json()
    assert body["location"] == str(tmp_path)
    items = body["items"]
    assert [i["id"] for i in items] == ["2026-07-13", "2026-07-11"]  # newest first
    latest, older = items
    assert latest["db_file"] == "booksync-db-2026-07-13.dump"
    assert latest["db_size_bytes"] == 300
    assert latest["has_covers"] is False
    assert older["has_covers"] is True


async def test_backups_list_requires_admin(make_client, make_user, auth_header, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "backups_dir", str(tmp_path))
    user = await make_user(username="u", role="user")
    async with make_client(stats.router) as c:
        r = await c.get("/api/stats/backups", headers=auth_header(user))
    assert r.status_code == 403


@pytest.fixture
def restore_spies(monkeypatch):
    """Replace the destructive restore shell-outs with recording fakes."""
    calls = {"db": None, "covers": None}

    async def fake_run_pg_restore(path):
        calls["db"] = path

    def fake_restore_covers(path):
        calls["covers"] = path

    monkeypatch.setattr(stats, "_run_pg_restore", fake_run_pg_restore)
    monkeypatch.setattr(stats, "_restore_covers", fake_restore_covers)
    return calls


async def test_restore_happy_path(make_client, make_user, auth_header, monkeypatch, tmp_path, restore_spies):
    monkeypatch.setattr(settings, "backups_dir", str(tmp_path))
    _make_backup(tmp_path, "2026-07-12", covers=True)

    user = await make_user(username="s", role="superadmin")
    async with make_client(stats.router) as c:
        r = await c.post(
            "/api/stats/restore",
            json={"backup_id": "2026-07-12", "confirm": True},
            headers=auth_header(user),
        )

    assert r.status_code == 200
    body = r.json()
    assert body == {"restored": True, "backup_id": "2026-07-12", "covers_restored": True}
    assert restore_spies["db"].endswith("booksync-db-2026-07-12.dump")
    assert restore_spies["covers"].endswith(os.path.join("covers", "2026-07-12"))


async def test_restore_without_covers(make_client, make_user, auth_header, monkeypatch, tmp_path, restore_spies):
    monkeypatch.setattr(settings, "backups_dir", str(tmp_path))
    _make_backup(tmp_path, "2026-07-12", covers=False)

    user = await make_user(username="s", role="superadmin")
    async with make_client(stats.router) as c:
        r = await c.post(
            "/api/stats/restore",
            json={"backup_id": "2026-07-12", "confirm": True},
            headers=auth_header(user),
        )

    assert r.status_code == 200
    assert r.json()["covers_restored"] is False
    assert restore_spies["covers"] is None


async def test_restore_requires_superadmin(make_client, make_user, auth_header, monkeypatch, tmp_path, restore_spies):
    monkeypatch.setattr(settings, "backups_dir", str(tmp_path))
    _make_backup(tmp_path, "2026-07-12")

    user = await make_user(username="a", role="admin")
    async with make_client(stats.router) as c:
        r = await c.post(
            "/api/stats/restore",
            json={"backup_id": "2026-07-12", "confirm": True},
            headers=auth_header(user),
        )

    assert r.status_code == 403
    assert restore_spies["db"] is None


async def test_restore_requires_confirmation(make_client, make_user, auth_header, monkeypatch, tmp_path, restore_spies):
    monkeypatch.setattr(settings, "backups_dir", str(tmp_path))
    _make_backup(tmp_path, "2026-07-12")

    user = await make_user(username="s", role="superadmin")
    async with make_client(stats.router) as c:
        r = await c.post(
            "/api/stats/restore",
            json={"backup_id": "2026-07-12", "confirm": False},
            headers=auth_header(user),
        )

    assert r.status_code == 400
    assert restore_spies["db"] is None


async def test_restore_unknown_id(make_client, make_user, auth_header, monkeypatch, tmp_path, restore_spies):
    monkeypatch.setattr(settings, "backups_dir", str(tmp_path))

    user = await make_user(username="s", role="superadmin")
    async with make_client(stats.router) as c:
        r = await c.post(
            "/api/stats/restore",
            json={"backup_id": "2020-01-01", "confirm": True},
            headers=auth_header(user),
        )

    assert r.status_code == 404
    assert restore_spies["db"] is None


@pytest.mark.parametrize("bad_id", ["../secrets", "2026-7-1", "2026-07-12/x", "not-a-date"])
async def test_restore_rejects_bad_id(make_client, make_user, auth_header, monkeypatch, tmp_path, restore_spies, bad_id):
    monkeypatch.setattr(settings, "backups_dir", str(tmp_path))

    user = await make_user(username="s", role="superadmin")
    async with make_client(stats.router) as c:
        r = await c.post(
            "/api/stats/restore",
            json={"backup_id": bad_id, "confirm": True},
            headers=auth_header(user),
        )

    assert r.status_code == 400
    assert restore_spies["db"] is None


async def test_restore_surfaces_helper_failure(make_client, make_user, auth_header, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "backups_dir", str(tmp_path))
    _make_backup(tmp_path, "2026-07-12")

    async def boom(path):
        raise RuntimeError("pg_restore exploded")

    monkeypatch.setattr(stats, "_run_pg_restore", boom)

    user = await make_user(username="s", role="superadmin")
    async with make_client(stats.router) as c:
        r = await c.post(
            "/api/stats/restore",
            json={"backup_id": "2026-07-12", "confirm": True},
            headers=auth_header(user),
        )

    assert r.status_code == 500
