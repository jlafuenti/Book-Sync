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
