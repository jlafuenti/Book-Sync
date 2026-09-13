"""
How the update check (issue #463) reaches the System page, and how it is switched on.

The decision itself — is the latest release newer — is `test_update_check.py`'s
subject. This covers the surface: an admin-only status endpoint, two settings
that are off by default and invisible to plain users, and the one moment the
check runs outside its six-hour schedule — right after an admin enables it.
"""

import httpx
import pytest

from routers import settings as settings_router
from routers import stats
from services import update_check as uc
from version import APP_VERSION


@pytest.fixture(autouse=True)
def _fresh_state(monkeypatch):
    monkeypatch.setattr(uc, "_state", uc._initial_state())


_STATUS_KEYS = {
    "enabled", "prompted", "status", "reason",
    "running_version", "latest_version", "release_url", "checked_at",
}


# ---------------------------------------------------------------------------
# GET /api/stats/update
# ---------------------------------------------------------------------------


async def test_a_fresh_install_reports_the_check_as_off_and_unasked(
    make_client, make_user, auth_header,
):
    """Off and not yet prompted is what makes the System page ask the question."""
    admin = await make_user(username="admin", role="admin")
    async with make_client(stats.router) as c:
        r = await c.get("/api/stats/update", headers=auth_header(admin))

    assert r.status_code == 200
    body = r.json()
    assert set(body) == _STATUS_KEYS, "the response model and the service dict must move together"
    assert body["enabled"] is False
    assert body["prompted"] is False
    assert body["status"] == "unknown"
    assert body["reason"] == "disabled"
    assert body["running_version"] == APP_VERSION


async def test_a_plain_user_cannot_read_it(make_client, make_user, auth_header):
    """Release state and the server's version are an operator's business."""
    user = await make_user(username="reader", role="user")
    async with make_client(stats.router) as c:
        r = await c.get("/api/stats/update", headers=auth_header(user))

    assert r.status_code == 403


async def test_an_enabled_check_reports_what_it_found(
    make_client, make_user, auth_header, db,
):
    from models.settings import SystemSetting

    db.add(SystemSetting(key="update_check_enabled", value="True"))
    db.add(SystemSetting(key="update_check_prompted", value="True"))
    await db.commit()
    uc._state.update({
        "status": "available", "reason": None, "latest_version": "0.2.0",
        "release_url": "https://github.com/jlafuenti/Book-Sync/releases/tag/v0.2.0",
        "checked_at": "2026-09-13T00:00:00+00:00",
    })

    admin = await make_user(username="admin", role="admin")
    async with make_client(stats.router) as c:
        r = await c.get("/api/stats/update", headers=auth_header(admin))

    body = r.json()
    assert body["enabled"] is True
    assert body["prompted"] is True
    assert body["status"] == "available"
    assert body["latest_version"] == "0.2.0"


async def test_reading_the_status_never_contacts_github(
    make_client, make_user, auth_header, monkeypatch, db,
):
    """The endpoint serves the scheduler's cached answer.

    Calling GitHub on the request path would make the System page as slow and
    as fallible as GitHub — and would spend the unauthenticated rate limit on
    every refresh of the page.
    """
    from models.settings import SystemSetting

    db.add(SystemSetting(key="update_check_enabled", value="True"))
    await db.commit()

    transport = httpx.MockTransport(
        lambda request: (_ for _ in ()).throw(AssertionError("GET /api/stats/update called GitHub"))
    )
    real = httpx.AsyncClient

    def fake(*args, **kwargs):
        if "transport" in kwargs:
            return real(*args, **kwargs)
        return real(*args, transport=transport, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", fake)

    admin = await make_user(username="admin", role="admin")
    async with make_client(stats.router) as c:
        r = await c.get("/api/stats/update", headers=auth_header(admin))

    assert r.status_code == 200


# ---------------------------------------------------------------------------
# The settings
# ---------------------------------------------------------------------------


async def test_both_settings_default_off_for_an_admin(make_client, make_user, auth_header):
    admin = await make_user(username="admin", role="admin")
    async with make_client(settings_router.router) as c:
        r = await c.get("/api/settings/", headers=auth_header(admin))

    body = r.json()
    assert body["update_check_enabled"] is False
    assert body["update_check_prompted"] is False


async def test_a_plain_user_does_not_see_the_settings(make_client, make_user, auth_header):
    user = await make_user(username="reader", role="user")
    async with make_client(settings_router.router) as c:
        r = await c.get("/api/settings/", headers=auth_header(user))

    body = r.json()
    assert "update_check_enabled" not in body
    assert "update_check_prompted" not in body


async def test_enabling_the_check_runs_one_immediately(
    make_client, make_user, auth_header, monkeypatch,
):
    """Without this an admin who just turned it on would wait six hours to learn it works."""
    kicked = []
    monkeypatch.setattr(uc, "kick", lambda: kicked.append(True))

    admin = await make_user(username="admin", role="admin")
    async with make_client(settings_router.router) as c:
        r = await c.put(
            "/api/settings/",
            json={"update_check_enabled": True, "update_check_prompted": True},
            headers=auth_header(admin),
        )

    assert r.status_code == 200
    assert kicked == [True]


@pytest.mark.parametrize("payload", [
    {"update_check_enabled": False, "update_check_prompted": True},  # "No thanks"
    {"update_check_prompted": True},
    {"auto_transcribe_enabled": True},  # an unrelated save
])
async def test_other_saves_do_not_run_a_check(
    make_client, make_user, auth_header, monkeypatch, payload,
):
    """Every settings save runs through the same handler; only enabling may reach GitHub."""
    kicked = []
    monkeypatch.setattr(uc, "kick", lambda: kicked.append(True))

    admin = await make_user(username="admin", role="admin")
    async with make_client(settings_router.router) as c:
        r = await c.put("/api/settings/", json=payload, headers=auth_header(admin))

    assert r.status_code == 200
    assert kicked == []
