"""
Health endpoints must tell the truth about the database (issue #47).

`GET /api/health` used to return a static `{"status": "healthy"}` — Docker
healthchecks and uptime monitors kept reporting "up" through a Postgres
outage. It is now a readiness probe: it runs `SELECT 1` and returns 503 when
the database doesn't answer. `GET /api/livez` is the static liveness probe
(process alive, dependencies not checked) so orchestrators can tell the two
states apart.
"""

import json
import os
import threading
import time

import httpx
import pytest

import version
from config import settings


# Importing main for real runs, at module scope,
# `os.makedirs(settings.app_data_dir + "/logs")` — and app_data_dir defaults to
# "/data/app", which is not writable on a Linux CI runner. Patch it to a tmp dir
# before the first import happens.
#
# test_startup.py and test_branding.py carry the same fixture for the same
# reason. It has to be duplicated rather than shared: it is module-scoped, test
# order is randomized, and `import main` is cached in sys.modules — so whichever
# module runs first is the one that triggers the real import, and all must be safe.
@pytest.fixture(scope="module", autouse=True)
def _app_data_dir_for_main_import(tmp_path_factory):
    original = settings.app_data_dir
    settings.app_data_dir = str(tmp_path_factory.mktemp("app_data"))
    yield
    settings.app_data_dir = original


class _BrokenSessionFactory:
    """Stand-in for database.async_session whose sessions never open."""

    def __call__(self):
        return self

    async def __aenter__(self):
        raise RuntimeError("simulated database outage")

    async def __aexit__(self, *exc):
        return False


async def test_health_returns_healthy_when_db_answers():
    pytest.importorskip("audible")
    import main

    body = await main.health()
    assert body["status"] == "healthy"


async def test_health_reports_the_api_and_app_version(monkeypatch):
    """The healthy payload carries the handshake fields (issue #174).

    An Android client installed from Play updates on the user's schedule and the
    server on the operator's, so the two drift. `/api/health` is unauthenticated,
    which is what makes it the thing a client can ask before it has credentials —
    but only if it answers with a version.
    """
    pytest.importorskip("audible")
    import main

    body = await main.health()

    assert body["api_version"] == version.API_VERSION
    assert isinstance(body["api_version"], int)
    assert body["app_version"] == version.APP_VERSION


async def test_root_reports_the_app_version():
    """`GET /` used to hard-code the version string next to a second copy in
    `FastAPI(version=...)`. One source, so they cannot drift (issue #174)."""
    pytest.importorskip("audible")
    import main

    body = await main.root()

    assert body["version"] == version.APP_VERSION
    assert main.app.version == version.APP_VERSION


async def test_health_returns_503_when_db_is_down(monkeypatch):
    pytest.importorskip("audible")
    import database
    import main

    monkeypatch.setattr(database, "async_session", _BrokenSessionFactory())

    resp = await main.health()
    assert resp.status_code == 503
    # Shape deliberately unchanged by issue #174: uptime monitors match on it,
    # and a client that cannot reach the database cannot use any API version.
    assert json.loads(resp.body) == {"status": "unhealthy", "db": "down"}


async def test_livez_is_alive_even_when_db_is_down(monkeypatch):
    pytest.importorskip("audible")
    import database
    import main

    monkeypatch.setattr(database, "async_session", _BrokenSessionFactory())

    body = await main.livez()
    assert body == {"status": "alive"}


# ---------------------------------------------------------------------------
# GET /api/health/backup — unauthenticated backup-staleness probe (issue #233)
#
# `GET /api/stats/backup` already knows whether the newest dump is stale, but it
# requires an admin login and access tokens last 24 h, so no external monitor
# can poll it without a token-refresh dance. Nothing else notices a nightly
# backup that has been failing for a week.
#
# The probe therefore has to answer with no credentials, which fixes what it may
# say: a status word and a coarse age, never the backup directory, a filename,
# a size or a timestamp — all of which `/api/stats/backup` returns and this must
# not. It is one directory listing plus one stat, cached, and never touches the
# database.
# ---------------------------------------------------------------------------

def _write_dump(backups_dir, date: str, *, age_hours: float = 0.0):
    """A fake pg_dump artifact for `date`, backdated by `age_hours`."""
    dump = backups_dir / f"booksync-db-{date}.dump"
    dump.write_bytes(b"x" * 64)
    when = time.time() - age_hours * 3600
    os.utime(dump, (when, when))
    return dump


@pytest.fixture(autouse=True)
def _clear_backup_probe_cache():
    """The probe caches; each test here must see its own filesystem."""
    pytest.importorskip("audible")
    import main

    main.backup_probe_cache.invalidate()
    yield
    main.backup_probe_cache.invalidate()


async def test_backup_probe_answers_without_authentication(monkeypatch, tmp_path):
    """No Authorization header, through the real app's routing table."""
    pytest.importorskip("audible")
    import main

    monkeypatch.setattr(settings, "backups_dir", str(tmp_path))
    _write_dump(tmp_path, "2026-07-13", age_hours=2)

    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get("/api/health/backup")

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "age_hours": 2}


async def test_backup_probe_reports_stale_when_the_newest_dump_is_old(
    monkeypatch, tmp_path
):
    pytest.importorskip("audible")
    import main

    monkeypatch.setattr(settings, "backups_dir", str(tmp_path))
    _write_dump(tmp_path, "2026-07-10", age_hours=72)

    body = await main.backup_health()
    assert body == {"status": "stale", "age_hours": 72}


async def test_backup_probe_reports_never_when_no_backup_exists(monkeypatch, tmp_path):
    """An empty backups dir is not "fresh" — it is the loudest case there is."""
    pytest.importorskip("audible")
    import main

    monkeypatch.setattr(settings, "backups_dir", str(tmp_path))

    body = await main.backup_health()
    assert body == {"status": "never", "age_hours": None}


async def test_backup_probe_uses_the_newest_dump(monkeypatch, tmp_path):
    pytest.importorskip("audible")
    import main

    monkeypatch.setattr(settings, "backups_dir", str(tmp_path))
    _write_dump(tmp_path, "2026-07-01", age_hours=200)
    _write_dump(tmp_path, "2026-07-13", age_hours=1)

    body = await main.backup_health()
    assert body == {"status": "ok", "age_hours": 1}


async def test_backup_probe_leaks_nothing_but_freshness(monkeypatch, tmp_path):
    """Unauthenticated, so the payload is exactly two keys and no more.

    `/api/stats/backup` answers the same question with `location`,
    `latest_db_file`, `latest_db_size_bytes` and `last_backup_utc` attached.
    None of that may reach an anonymous caller.
    """
    pytest.importorskip("audible")
    import main

    monkeypatch.setattr(settings, "backups_dir", str(tmp_path))
    _write_dump(tmp_path, "2026-07-13", age_hours=1)

    body = await main.backup_health()
    assert set(body) == {"status", "age_hours"}


async def test_backup_probe_runs_off_the_event_loop(monkeypatch, tmp_path):
    """The stat is on a NAS mount; a hung mount must not stall every request."""
    pytest.importorskip("audible")
    import main
    from services import backup_service

    monkeypatch.setattr(settings, "backups_dir", str(tmp_path))
    seen = []

    def _spy():
        seen.append(threading.current_thread())
        return {"status": "ok", "age_hours": 0}

    monkeypatch.setattr(backup_service, "get_freshness", _spy)

    await main.backup_health()

    assert seen and seen[0] is not threading.main_thread()


async def test_backup_probe_is_cached(monkeypatch, tmp_path):
    """It is unauthenticated: a polling monitor and a hostile loop cost the same."""
    pytest.importorskip("audible")
    import main
    from services import backup_service

    monkeypatch.setattr(settings, "backups_dir", str(tmp_path))
    calls = []

    def _counting():
        calls.append(1)
        return {"status": "ok", "age_hours": 0}

    monkeypatch.setattr(backup_service, "get_freshness", _counting)

    first = await main.backup_health()
    second = await main.backup_health()

    assert first == second
    assert len(calls) == 1


async def test_backup_probe_survives_a_backup_dir_it_cannot_read(monkeypatch, tmp_path):
    """A probe that 500s tells a monitor nothing useful about the backup."""
    pytest.importorskip("audible")
    from services import backup_service

    monkeypatch.setattr(settings, "backups_dir", str(tmp_path / "does-not-exist"))

    assert backup_service.get_freshness() == {"status": "never", "age_hours": None}
