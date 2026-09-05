"""
Stats router tests (issue #46, Phase 3).
"""

import os
import threading
import time

import pytest

from config import settings
from routers import stats
from services import backup_service


async def test_disk_usage_returns_shape(make_client, make_user, auth_header, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "ebook_dir", str(tmp_path / "e"))
    monkeypatch.setattr(settings, "audiobook_dir", str(tmp_path / "a"))
    monkeypatch.setattr(settings, "app_data_dir", str(tmp_path))
    (tmp_path / "e").mkdir()
    (tmp_path / "a").mkdir()

    user = await make_user(username="u", role="admin")
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

    user = await make_user(username="u", role="admin")
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

    user = await make_user(username="u", role="admin")
    async with make_client(stats.router) as c:
        r = await c.get("/api/stats/backup", headers=auth_header(user))

    assert r.status_code == 200
    body = r.json()
    assert body["configured"] is True
    assert body["stale"] is True
    assert body["age_seconds"] > 36 * 3600


async def test_backup_status_empty_dir(make_client, make_user, auth_header, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "backups_dir", str(tmp_path / "does-not-exist"))

    user = await make_user(username="u", role="admin")
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

    monkeypatch.setattr(backup_service, "_run_pg_restore", fake_run_pg_restore)
    monkeypatch.setattr(backup_service, "_restore_covers", fake_restore_covers)
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

    monkeypatch.setattr(backup_service, "_run_pg_restore", boom)

    user = await make_user(username="s", role="superadmin")
    async with make_client(stats.router) as c:
        r = await c.post(
            "/api/stats/restore",
            json={"backup_id": "2026-07-12", "confirm": True},
            headers=auth_header(user),
        )

    assert r.status_code == 500


# ── manual create / delete / download ──────────────────────────────────────

@pytest.fixture
def create_seams(monkeypatch, tmp_path):
    """Stub the pg_dump/covers shell-outs so a manual backup can be created."""
    async def fake_pg_dump(dump_path):
        with open(dump_path, "wb") as f:
            f.write(b"PGDMP-fake")

    async def fake_snapshot(snapshot_dir):
        os.makedirs(snapshot_dir, exist_ok=True)
        with open(os.path.join(snapshot_dir, "c.jpg"), "wb") as f:
            f.write(b"cover")

    monkeypatch.setattr(backup_service, "_run_pg_dump", fake_pg_dump)
    monkeypatch.setattr(backup_service, "_snapshot_covers", fake_snapshot)


async def test_create_manual_backup(make_client, make_user, auth_header, monkeypatch, tmp_path, create_seams):
    monkeypatch.setattr(settings, "backups_dir", str(tmp_path))
    monkeypatch.setattr(settings, "covers_dir", str(tmp_path / "src-covers"))
    (tmp_path / "src-covers").mkdir()

    user = await make_user(username="s", role="superadmin")
    async with make_client(stats.router) as c:
        r = await c.post("/api/stats/backups", json={"label": "before reorg"}, headers=auth_header(user))

    assert r.status_code == 200
    body = r.json()
    assert body["is_manual"] is True and body["label"] == "before reorg"
    assert body["id"].endswith("-manual")
    assert (tmp_path / f"booksync-db-{body['id']}.dump").exists()


async def test_create_manual_backup_requires_superadmin(make_client, make_user, auth_header, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "backups_dir", str(tmp_path))
    user = await make_user(username="a", role="admin")
    async with make_client(stats.router) as c:
        r = await c.post("/api/stats/backups", json={}, headers=auth_header(user))
    assert r.status_code == 403


async def test_delete_backup(make_client, make_user, auth_header, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "backups_dir", str(tmp_path))
    _make_backup(tmp_path, "2026-07-12", covers=True)

    user = await make_user(username="s", role="superadmin")
    async with make_client(stats.router) as c:
        r = await c.delete("/api/stats/backups/2026-07-12", headers=auth_header(user))

    assert r.status_code == 200
    assert r.json()["deleted"] is True
    assert not (tmp_path / "booksync-db-2026-07-12.dump").exists()
    assert not (tmp_path / "covers" / "2026-07-12").exists()


async def test_delete_backup_requires_superadmin(make_client, make_user, auth_header, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "backups_dir", str(tmp_path))
    _make_backup(tmp_path, "2026-07-12")
    user = await make_user(username="a", role="admin")
    async with make_client(stats.router) as c:
        r = await c.delete("/api/stats/backups/2026-07-12", headers=auth_header(user))
    assert r.status_code == 403
    assert (tmp_path / "booksync-db-2026-07-12.dump").exists()


async def test_delete_backup_unknown(make_client, make_user, auth_header, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "backups_dir", str(tmp_path))
    user = await make_user(username="s", role="superadmin")
    async with make_client(stats.router) as c:
        r = await c.delete("/api/stats/backups/2020-01-01", headers=auth_header(user))
    assert r.status_code == 404


async def test_delete_backup_bad_id(make_client, make_user, auth_header, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "backups_dir", str(tmp_path))
    user = await make_user(username="s", role="superadmin")
    async with make_client(stats.router) as c:
        # A malformed-but-routable id reaches the handler and fails validation.
        r = await c.delete("/api/stats/backups/not-a-date", headers=auth_header(user))
    assert r.status_code == 400


async def test_download_backup(make_client, make_user, auth_header, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "backups_dir", str(tmp_path))
    dump = _make_backup(tmp_path, "2026-07-12")
    dump.write_bytes(b"PGDMP-bytes")

    user = await make_user(username="s", role="superadmin")
    async with make_client(stats.router) as c:
        r = await c.get("/api/stats/backups/2026-07-12/download", headers=auth_header(user))

    assert r.status_code == 200
    assert r.content == b"PGDMP-bytes"


async def test_download_backup_requires_superadmin(make_client, make_user, auth_header, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "backups_dir", str(tmp_path))
    _make_backup(tmp_path, "2026-07-12")
    user = await make_user(username="a", role="admin")
    async with make_client(stats.router) as c:
        r = await c.get("/api/stats/backups/2026-07-12/download", headers=auth_header(user))
    assert r.status_code == 403


async def test_download_backup_unknown(make_client, make_user, auth_header, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "backups_dir", str(tmp_path))
    user = await make_user(username="s", role="superadmin")
    async with make_client(stats.router) as c:
        r = await c.get("/api/stats/backups/2020-01-01/download", headers=auth_header(user))
    assert r.status_code == 404


async def test_download_backup_bad_id(make_client, make_user, auth_header, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "backups_dir", str(tmp_path))
    user = await make_user(username="s", role="superadmin")
    async with make_client(stats.router) as c:
        r = await c.get("/api/stats/backups/not-a-date/download", headers=auth_header(user))
    assert r.status_code == 400


async def test_backups_list_includes_manual_and_label(make_client, make_user, auth_header, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "backups_dir", str(tmp_path))
    _make_backup(tmp_path, "2026-07-11")
    _make_backup(tmp_path, "2026-07-13_090000-manual", covers=False)
    (tmp_path / "booksync-db-2026-07-13_090000-manual.label").write_text("pre-upgrade")

    user = await make_user(username="a", role="admin")
    async with make_client(stats.router) as c:
        r = await c.get("/api/stats/backups", headers=auth_header(user))

    assert r.status_code == 200
    items = {i["id"]: i for i in r.json()["items"]}
    assert items["2026-07-13_090000-manual"]["is_manual"] is True
    assert items["2026-07-13_090000-manual"]["label"] == "pre-upgrade"
    assert items["2026-07-11"]["is_manual"] is False


# ---------------------------------------------------------------------------
# Issue #283: the admin console's read endpoints accepted any authenticated
# user. No privilege was gained -- every mutating endpoint was already gated --
# but a plain reader could see disk capacity and usage for all three data roots
# and the backup health of the deployment. Defence in depth, plus infra-info
# disclosure that matters more once the repo is public.
#
# Android calls neither endpoint (BookSyncApi.kt declares no stats routes), so
# tightening these cannot break the app.
# ---------------------------------------------------------------------------


async def test_disk_usage_requires_admin(make_client, make_user, auth_header, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "ebook_dir", str(tmp_path / "e"))
    monkeypatch.setattr(settings, "audiobook_dir", str(tmp_path / "a"))
    monkeypatch.setattr(settings, "app_data_dir", str(tmp_path))
    (tmp_path / "e").mkdir()
    (tmp_path / "a").mkdir()

    user = await make_user(username="plain", role="user")
    async with make_client(stats.router) as c:
        r = await c.get("/api/stats/disk_usage", headers=auth_header(user))
    assert r.status_code == 403


async def test_disk_usage_editor_is_not_enough(make_client, make_user, auth_header, monkeypatch, tmp_path):
    # Editors maintain the library; disk capacity is infrastructure.
    monkeypatch.setattr(settings, "ebook_dir", str(tmp_path / "e"))
    monkeypatch.setattr(settings, "audiobook_dir", str(tmp_path / "a"))
    monkeypatch.setattr(settings, "app_data_dir", str(tmp_path))
    (tmp_path / "e").mkdir()
    (tmp_path / "a").mkdir()

    user = await make_user(username="ed", role="editor")
    async with make_client(stats.router) as c:
        r = await c.get("/api/stats/disk_usage", headers=auth_header(user))
    assert r.status_code == 403


async def test_backup_status_requires_admin(make_client, make_user, auth_header):
    user = await make_user(username="plain2", role="user")
    async with make_client(stats.router) as c:
        r = await c.get("/api/stats/backup", headers=auth_header(user))
    assert r.status_code == 403


async def test_backup_status_allows_admin(make_client, make_user, auth_header):
    user = await make_user(username="adm2", role="admin")
    async with make_client(stats.router) as c:
        r = await c.get("/api/stats/backup", headers=auth_header(user))
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# /api/stats/disk_usage must not stall the server (issue #208)
#
# `get_dir_size` is a recursive `os.scandir` over three data roots on a NAS
# mount, run synchronously inside an `async def`. The server is a single uvicorn
# worker, so while it runs *nothing else is served* — not login, not
# `/api/health`, whose compose healthcheck gives up after 10 s. The System page
# calls it on load, and anyone with an admin token could call it in a loop.
# ---------------------------------------------------------------------------

async def test_disk_usage_runs_off_the_event_loop(
    make_client, make_user, auth_header, monkeypatch, tmp_path
):
    monkeypatch.setattr(settings, "ebook_dir", str(tmp_path))
    monkeypatch.setattr(settings, "audiobook_dir", str(tmp_path))
    monkeypatch.setattr(settings, "app_data_dir", str(tmp_path))
    threads = []

    def _spy(path):
        threads.append(threading.current_thread())
        return 0

    monkeypatch.setattr(stats, "get_dir_size", _spy)

    user = await make_user(username="adm3", role="admin")
    async with make_client(stats.router) as c:
        r = await c.get("/api/stats/disk_usage", headers=auth_header(user))

    assert r.status_code == 200
    assert threads, "get_dir_size was never called"
    assert all(t is not threading.main_thread() for t in threads)


async def test_disk_usage_is_cached(
    make_client, make_user, auth_header, monkeypatch, tmp_path
):
    """A polling page and a hostile loop should cost one scan, not N."""
    monkeypatch.setattr(settings, "ebook_dir", str(tmp_path))
    monkeypatch.setattr(settings, "audiobook_dir", str(tmp_path))
    monkeypatch.setattr(settings, "app_data_dir", str(tmp_path))
    calls = []
    monkeypatch.setattr(stats, "get_dir_size", lambda path: calls.append(path) or 0)

    user = await make_user(username="adm4", role="admin")
    async with make_client(stats.router) as c:
        first = await c.get("/api/stats/disk_usage", headers=auth_header(user))
        second = await c.get("/api/stats/disk_usage", headers=auth_header(user))

    assert first.json() == second.json()
    assert len(calls) == 3, f"expected one scan per data root, got {calls}"


async def test_disk_usage_cache_expires(
    make_client, make_user, auth_header, monkeypatch, tmp_path
):
    """The cache is a rate damper, not a freeze — capacity does change."""
    monkeypatch.setattr(settings, "ebook_dir", str(tmp_path))
    monkeypatch.setattr(settings, "audiobook_dir", str(tmp_path))
    monkeypatch.setattr(settings, "app_data_dir", str(tmp_path))
    monkeypatch.setattr(settings, "disk_usage_cache_seconds", 0)
    calls = []
    monkeypatch.setattr(stats, "get_dir_size", lambda path: calls.append(path) or 0)

    user = await make_user(username="adm5", role="admin")
    async with make_client(stats.router) as c:
        await c.get("/api/stats/disk_usage", headers=auth_header(user))
        await c.get("/api/stats/disk_usage", headers=auth_header(user))

    assert len(calls) == 6


async def test_disk_usage_is_rate_limited(
    make_client, make_user, auth_header, monkeypatch, tmp_path
):
    """Even cached, a loop costs a request each; 429 names when to come back."""
    monkeypatch.setattr(settings, "ebook_dir", str(tmp_path))
    monkeypatch.setattr(settings, "audiobook_dir", str(tmp_path))
    monkeypatch.setattr(settings, "app_data_dir", str(tmp_path))
    monkeypatch.setattr(settings, "expensive_read_limit", 2)

    user = await make_user(username="adm6", role="admin")
    async with make_client(stats.router) as c:
        codes = [
            (await c.get("/api/stats/disk_usage", headers=auth_header(user))).status_code
            for _ in range(3)
        ]
        over = await c.get("/api/stats/disk_usage", headers=auth_header(user))

    assert codes == [200, 200, 429]
    assert over.status_code == 429
    assert int(over.headers["Retry-After"]) > 0
