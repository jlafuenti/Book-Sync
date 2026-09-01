"""
Auth / RBAC characterization tests (issue #46).

Covers login, token refresh, wrong-secret rejection, the role hierarchy gate,
and the must_reset_password change-password flow. These pin current behavior so
future refactors of the auth surface can't silently regress.
"""

import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient
import jwt
from sqlalchemy import select

from config import settings
from models.audit_log import AuditLog
from models.user import ROLE_HIERARCHY, User
from routers.auth import (
    create_access_token,
    create_media_token,
    create_refresh_token,
    get_admin_user,
    get_editor_user,
    require_role,
    verify_password,
)


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------

async def test_login_success_returns_tokens(client, make_user):
    await make_user(username="alice", password="s3cret")
    r = await client.post("/api/auth/login", json={"username": "alice", "password": "s3cret"})
    assert r.status_code == 200
    body = r.json()
    assert body["access_token"] and body["refresh_token"]
    assert body["token_type"] == "bearer"


async def test_login_wrong_password_rejected(client, make_user):
    await make_user(username="bob", password="right")
    r = await client.post("/api/auth/login", json={"username": "bob", "password": "wrong"})
    assert r.status_code == 401
    assert "Invalid username or password" in r.json()["detail"]


async def test_login_unknown_user_rejected(client):
    r = await client.post("/api/auth/login", json={"username": "ghost", "password": "x"})
    assert r.status_code == 401


async def test_login_inactive_account_forbidden(client, make_user):
    await make_user(username="pending", password="pw", is_active=False)
    r = await client.post("/api/auth/login", json={"username": "pending", "password": "pw"})
    assert r.status_code == 403
    assert "pending admin approval" in r.json()["detail"]


async def test_successful_login_writes_audit_row(client, make_user, db):
    await make_user(username="carol", password="pw")
    r = await client.post("/api/auth/login", json={"username": "carol", "password": "pw"})
    assert r.status_code == 200
    # get_db commits on the success path, so the 'login' audit row persists.
    rows = (await db.execute(select(AuditLog).where(AuditLog.action == "login"))).scalars().all()
    assert len(rows) == 1


# ---------------------------------------------------------------------------
# Refresh
# ---------------------------------------------------------------------------

async def test_refresh_with_valid_refresh_token(client, make_user):
    user = await make_user(username="dave", password="pw")
    token = create_refresh_token(user)
    r = await client.post("/api/auth/refresh", json={"refresh_token": token})
    assert r.status_code == 200
    assert r.json()["access_token"]


async def test_refresh_rejects_access_token(client, make_user):
    """An access token presented to /refresh must be rejected on the type check."""
    user = await make_user(username="erin", password="pw")
    access = create_access_token(user)
    r = await client.post("/api/auth/refresh", json={"refresh_token": access})
    assert r.status_code == 401


async def test_refresh_rejects_wrong_secret(client, make_user):
    user = await make_user(username="frank", password="pw")
    forged = jwt.encode(
        {"sub": str(user.id), "type": "refresh"},
        "some-other-secret",
        algorithm=settings.jwt_algorithm,
    )
    r = await client.post("/api/auth/refresh", json={"refresh_token": forged})
    assert r.status_code == 401


async def test_refresh_rejects_disabled_user(client, make_user):
    user = await make_user(username="grace", password="pw", is_active=False)
    token = create_refresh_token(user)
    r = await client.post("/api/auth/refresh", json={"refresh_token": token})
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# get_current_user (/me)
# ---------------------------------------------------------------------------

async def test_me_with_valid_token(client, make_user, auth_header):
    user = await make_user(username="heidi", password="pw", role="editor")
    r = await client.get("/api/auth/me", headers=auth_header(user))
    assert r.status_code == 200
    assert r.json()["username"] == "heidi"
    assert r.json()["role"] == "editor"


async def test_me_rejects_wrong_secret_token(client, make_user):
    user = await make_user(username="ivan", password="pw")
    forged = jwt.encode(
        {"sub": str(user.id), "type": "access"}, "not-the-real-secret",
        algorithm=settings.jwt_algorithm,
    )
    r = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {forged}"})
    assert r.status_code == 401


async def test_me_rejects_token_for_inactive_user(client, make_user, auth_header):
    """A validly-signed token for a since-disabled user is rejected."""
    user = await make_user(username="judy", password="pw", is_active=False)
    r = await client.get("/api/auth/me", headers=auth_header(user))
    assert r.status_code == 401


async def test_me_requires_a_token(client):
    r = await client.get("/api/auth/me")
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# RBAC — role hierarchy gate
# ---------------------------------------------------------------------------

def test_role_hierarchy_ordering():
    assert (
        ROLE_HIERARCHY["superadmin"]
        > ROLE_HIERARCHY["admin"]
        > ROLE_HIERARCHY["editor"]
        > ROLE_HIERARCHY["user"]
    )


@pytest.mark.parametrize(
    "role,minimum,allowed",
    [
        ("user", "editor", False),
        ("editor", "editor", True),
        ("editor", "admin", False),
        ("admin", "editor", True),
        ("admin", "admin", True),
        ("superadmin", "admin", True),
        ("user", "admin", False),
    ],
)
async def test_require_role_gate(make_user, role, minimum, allowed):
    """Call the dependency factory directly with a real user row."""
    user = await make_user(username=f"{role}_{minimum}", role=role)
    dep = require_role(minimum)
    if allowed:
        assert await dep(current_user=user) is user
    else:
        with pytest.raises(Exception) as exc:  # HTTPException
            await dep(current_user=user)
        assert getattr(exc.value, "status_code", None) == 403


async def test_admin_gated_route_end_to_end(make_user, auth_header):
    """
    Wire get_admin_user into a throwaway route and prove the full token ->
    user -> role check path: a `user` token gets 403, an `admin` token 200.
    """
    app = FastAPI()

    @app.get("/admin-only")
    async def _admin_only(u: User = Depends(get_admin_user)):
        return {"ok": u.username}

    @app.get("/editor-only")
    async def _editor_only(u: User = Depends(get_editor_user)):
        return {"ok": u.username}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        plain = await make_user(username="plainuser", role="user")
        admin = await make_user(username="adminuser", role="admin")

        assert (await c.get("/admin-only", headers=auth_header(plain))).status_code == 403
        assert (await c.get("/admin-only", headers=auth_header(admin))).status_code == 200
        # admin also satisfies the lower editor bar
        assert (await c.get("/editor-only", headers=auth_header(admin))).status_code == 200


# ---------------------------------------------------------------------------
# must_reset_password / change-password
# ---------------------------------------------------------------------------

async def test_change_password_success_clears_reset_flag(client, make_user, auth_header, db):
    user = await make_user(username="karl", password="oldpw", must_reset_password=True)
    r = await client.post(
        "/api/auth/change-password",
        headers=auth_header(user),
        json={"old_password": "oldpw", "new_password": "brandnewpw"},
    )
    assert r.status_code == 200

    refreshed = (await db.execute(select(User).where(User.id == user.id))).scalar_one()
    assert refreshed.must_reset_password is False
    assert verify_password("brandnewpw", refreshed.hashed_password)
    assert not verify_password("oldpw", refreshed.hashed_password)


async def test_change_password_wrong_old_rejected(client, make_user, auth_header, db):
    user = await make_user(username="lena", password="oldpw", must_reset_password=True)
    r = await client.post(
        "/api/auth/change-password",
        headers=auth_header(user),
        json={"old_password": "WRONG", "new_password": "brandnewpw"},
    )
    assert r.status_code == 400

    refreshed = (await db.execute(select(User).where(User.id == user.id))).scalar_one()
    # Unchanged: flag still set, old password still valid.
    assert refreshed.must_reset_password is True
    assert verify_password("oldpw", refreshed.hashed_password)


# ---------------------------------------------------------------------------
# Token revocation (token_version)
# ---------------------------------------------------------------------------

async def test_login_use_refresh_cycle_works_within_one_version(client, make_user):
    """Regression: normal login -> use -> refresh still works untouched."""
    await make_user(username="mallory", password="pw")
    login_resp = await client.post("/api/auth/login", json={"username": "mallory", "password": "pw"})
    tokens = login_resp.json()

    me = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {tokens['access_token']}"})
    assert me.status_code == 200

    refreshed = await client.post("/api/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert refreshed.status_code == 200
    new_access = refreshed.json()["access_token"]
    me2 = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {new_access}"})
    assert me2.status_code == 200


async def test_token_rejected_after_version_bump(client, make_user, auth_header, db):
    """A token minted at ver=0 stops working once token_version is bumped."""
    user = await make_user(username="nate", password="pw")
    header = auth_header(user)

    r = await client.get("/api/auth/me", headers=header)
    assert r.status_code == 200

    stored = (await db.execute(select(User).where(User.id == user.id))).scalar_one()
    stored.token_version += 1
    await db.commit()

    r = await client.get("/api/auth/me", headers=header)
    assert r.status_code == 401


async def test_change_password_invalidates_previous_tokens(client, make_user, auth_header):
    user = await make_user(username="olga", password="oldpw")
    old_header = auth_header(user)
    old_refresh = create_refresh_token(user)

    r = await client.post(
        "/api/auth/change-password",
        headers=old_header,
        json={"old_password": "oldpw", "new_password": "brandnewpw"},
    )
    assert r.status_code == 200

    # The access token issued before the change no longer works...
    assert (await client.get("/api/auth/me", headers=old_header)).status_code == 401
    # ...and neither does the refresh token issued before the change.
    r = await client.post("/api/auth/refresh", json={"refresh_token": old_refresh})
    assert r.status_code == 401


async def test_logout_invalidates_existing_token(client, make_user, auth_header):
    user = await make_user(username="peggy", password="pw")
    header = auth_header(user)

    r = await client.post("/api/auth/logout", headers=header)
    assert r.status_code == 200

    r = await client.get("/api/auth/me", headers=header)
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# Public registration toggle
# ---------------------------------------------------------------------------

async def test_register_disabled_returns_403(client, monkeypatch):
    monkeypatch.setattr(settings, "allow_public_registration", False)
    r = await client.post(
        "/api/auth/register",
        json={"username": "quentin", "email": "q@example.com", "password": "pw12345"},
    )
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# Media tokens (issue #50)
# ---------------------------------------------------------------------------

async def test_create_media_token_has_expected_claims(make_user):
    user = await make_user(username="rex", password="pw")
    token = create_media_token(user, "cover", "some cover.jpg")
    payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
    assert payload["sub"] == str(user.id)
    assert payload["type"] == "media"
    assert payload["resource_type"] == "cover"
    assert payload["resource_id"] == "some cover.jpg"
    assert payload["ver"] == user.token_version


async def test_media_token_endpoint_requires_auth(client):
    r = await client.get("/api/auth/media-token", params={"resource_type": "cover", "resource_id": "x.jpg"})
    assert r.status_code == 401


async def test_media_token_endpoint_rejects_invalid_resource_type(client, make_user, auth_header):
    user = await make_user(username="sam", password="pw")
    r = await client.get(
        "/api/auth/media-token",
        params={"resource_type": "ebook", "resource_id": "x.epub"},
        headers=auth_header(user),
    )
    assert r.status_code == 400


async def test_media_token_endpoint_returns_scoped_token(client, make_user, auth_header):
    user = await make_user(username="tara", password="pw")
    r = await client.get(
        "/api/auth/media-token",
        params={"resource_type": "audiobook", "resource_id": "7"},
        headers=auth_header(user),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["expires_in"] == settings.jwt_media_token_expire_minutes * 60
    payload = jwt.decode(body["token"], settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
    assert payload["resource_type"] == "audiobook"
    assert payload["resource_id"] == "7"


async def test_media_token_batch_mints_per_resource(client, make_user, auth_header):
    user = await make_user(username="uma", password="pw")
    r = await client.post(
        "/api/auth/media-token/batch",
        json={"resources": [
            {"resource_type": "cover", "resource_id": "a.jpg"},
            {"resource_type": "audiobook", "resource_id": "3"},
        ]},
        headers=auth_header(user),
    )
    assert r.status_code == 200
    body = r.json()
    assert set(body["tokens"].keys()) == {"cover:a.jpg", "audiobook:3"}
    for key, token in body["tokens"].items():
        payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
        assert payload["type"] == "media"


async def test_media_token_batch_skips_invalid_resource_type(client, make_user, auth_header):
    user = await make_user(username="vic", password="pw")
    r = await client.post(
        "/api/auth/media-token/batch",
        json={"resources": [
            {"resource_type": "cover", "resource_id": "a.jpg"},
            {"resource_type": "ebook", "resource_id": "b.epub"},
        ]},
        headers=auth_header(user),
    )
    assert r.status_code == 200
    assert list(r.json()["tokens"].keys()) == ["cover:a.jpg"]


# ---------------------------------------------------------------------------
# Client IP: audit rows + rate-limit buckets (issue #156)
#
# Behind a reverse proxy every request shares one socket peer, so the client
# address must come from `scope["client"]` — which uvicorn's
# ProxyHeadersMiddleware rewrites from X-Forwarded-For only when the peer is a
# trusted proxy (FORWARDED_ALLOW_IPS). The application itself must never trust
# the header directly: it is attacker-chosen.
# ---------------------------------------------------------------------------

def _auth_app():
    """A minimal app mounting only the auth router (mirrors the client fixture)."""
    from fastapi import FastAPI
    from slowapi import _rate_limit_exceeded_handler
    from slowapi.errors import RateLimitExceeded

    from rate_limit import limiter
    from routers import auth as auth_module

    limiter.enabled = False
    app = FastAPI()
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.include_router(auth_module.router)
    return app


def test_get_client_ip_uses_the_socket_peer_not_the_header():
    from starlette.requests import Request

    from routers.auth import get_client_ip

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/auth/login",
        "headers": [(b"x-forwarded-for", b"1.2.3.4")],
        "client": ("10.0.0.9", 1),
    }
    assert get_client_ip(Request(scope)) == "10.0.0.9"


def test_get_client_ip_falls_back_when_the_peer_host_is_none():
    """ProxyHeadersMiddleware sets the client to (None, 0) when every hop in
    X-Forwarded-For is trusted (real docker NAT topologies) — audit rows must
    store "unknown", not NULL."""
    from starlette.requests import Request

    from routers.auth import get_client_ip

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/auth/login",
        "headers": [],
        "client": (None, 0),
    }
    assert get_client_ip(Request(scope)) == "unknown"


def test_get_client_ip_falls_back_when_there_is_no_peer_at_all():
    from starlette.requests import Request

    from routers.auth import get_client_ip

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/auth/login",
        "headers": [],
        "client": None,
    }
    assert get_client_ip(Request(scope)) == "unknown"


async def test_failed_login_audit_row_records_the_peer_ip(db):
    """An attacker-supplied X-Forwarded-For must not end up in the audit log."""
    transport = ASGITransport(app=_auth_app(), client=("10.0.0.9", 1))
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.post(
            "/api/auth/login",
            json={"username": "ghost", "password": "x"},
            headers={"X-Forwarded-For": "1.2.3.4"},
        )
    assert r.status_code == 401
    rows = (
        await db.execute(select(AuditLog).where(AuditLog.action == "login_failed"))
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].ip_address == "10.0.0.9"


async def test_failed_login_audit_row_uses_forwarded_ip_behind_trusted_proxy(db):
    """With uvicorn's ProxyHeadersMiddleware trusting the peer, scope["client"]
    is rewritten from X-Forwarded-For and the audit row records the real client."""
    from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

    app = ProxyHeadersMiddleware(_auth_app(), trusted_hosts="10.0.0.9")
    transport = ASGITransport(app=app, client=("10.0.0.9", 1))
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.post(
            "/api/auth/login",
            json={"username": "ghost", "password": "x"},
            headers={"X-Forwarded-For": "1.2.3.4"},
        )
    assert r.status_code == 401
    rows = (
        await db.execute(select(AuditLog).where(AuditLog.action == "login_failed"))
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].ip_address == "1.2.3.4"


async def test_login_rate_limit_is_per_client_ip():
    """Six bad logins from A must 429 A but leave B at a plain 401.

    Pins the limiter key: if it ever stops being the (proxy-corrected) socket
    peer, one client exhausting the bucket locks out everyone (issue #156).
    """
    from rate_limit import limiter

    app = _auth_app()  # sets limiter.enabled = False; re-enable below
    limiter.enabled = True
    limiter.reset()
    try:
        ta = ASGITransport(app=app, client=("10.1.1.1", 1))
        tb = ASGITransport(app=app, client=("10.2.2.2", 1))
        async with AsyncClient(transport=ta, base_url="http://test") as a, AsyncClient(
            transport=tb, base_url="http://test"
        ) as b:
            last = None
            for _ in range(6):
                last = await a.post(
                    "/api/auth/login", json={"username": "ghost", "password": "x"}
                )
            assert last.status_code == 429
            r = await b.post(
                "/api/auth/login", json={"username": "ghost", "password": "x"}
            )
            assert r.status_code == 401
    finally:
        limiter.enabled = False
        limiter.reset()


# ---------------------------------------------------------------------------
# Per-username failed-login bucket (issue #296)
#
# The slowapi limiter above keys on the client IP, which docker NAT can collapse
# into one bucket (issue #294). A second, username-keyed layer counts *failed*
# attempts in-route and returns 429 before the password is ever verified.
# ---------------------------------------------------------------------------


async def _fail_login(c, username, times, password="wrong"):
    """POST `times` bad logins for `username`; return the last response."""
    last = None
    for _ in range(times):
        last = await c.post(
            "/api/auth/login", json={"username": username, "password": password}
        )
    return last


async def test_repeated_failures_lock_that_username_only(client, make_user):
    """N failures for A → A is 429'd; B is untouched (401 wrong pw, 200 right pw)."""
    from config import settings as s
    from rate_limit import failed_logins

    n = s.login_failure_limit
    await make_user(username="lockme", password="pw")
    await make_user(username="bystander", password="pw")

    last = await _fail_login(client, "lockme", n)
    assert last.status_code == 401  # the Nth failure still reads as a plain 401

    r = await client.post("/api/auth/login", json={"username": "lockme", "password": "pw"})
    assert r.status_code == 429
    retry_after = int(r.headers["Retry-After"])
    assert 0 < retry_after <= s.login_failure_window_seconds

    # A different username shares neither the counter nor the lock.
    assert failed_logins.failure_count("bystander") == 0
    r = await client.post("/api/auth/login", json={"username": "bystander", "password": "nope"})
    assert r.status_code == 401
    r = await client.post("/api/auth/login", json={"username": "bystander", "password": "pw"})
    assert r.status_code == 200


async def test_correct_password_while_locked_is_still_429(client, make_user):
    """No oracle: the lock is checked before the password is verified."""
    from config import settings as s

    await make_user(username="locked", password="correcthorse")
    await _fail_login(client, "locked", s.login_failure_limit)

    r = await client.post(
        "/api/auth/login", json={"username": "locked", "password": "correcthorse"}
    )
    assert r.status_code == 429
    assert "Retry-After" in r.headers


async def test_successful_login_clears_the_failure_counter(client, make_user):
    """N-1 failures, a success, then N-1 more failures must not trip the lock."""
    from config import settings as s
    from rate_limit import failed_logins

    n = s.login_failure_limit
    await make_user(username="clearme", password="pw")

    await _fail_login(client, "clearme", n - 1)
    assert failed_logins.failure_count("clearme") == n - 1

    r = await client.post("/api/auth/login", json={"username": "clearme", "password": "pw"})
    assert r.status_code == 200
    assert failed_logins.failure_count("clearme") == 0

    last = await _fail_login(client, "clearme", n - 1)
    assert last.status_code == 401  # still short of the threshold


async def test_lock_expires_after_the_window(client, make_user):
    """With a controlled clock, the bucket unlocks once the window passes."""
    from config import settings as s
    from rate_limit import failed_logins

    now = [1000.0]
    failed_logins.clock = lambda: now[0]

    await make_user(username="waiter", password="pw")
    await _fail_login(client, "waiter", s.login_failure_limit)

    r = await client.post("/api/auth/login", json={"username": "waiter", "password": "pw"})
    assert r.status_code == 429

    now[0] += s.login_failure_window_seconds + 1
    r = await client.post("/api/auth/login", json={"username": "waiter", "password": "pw"})
    assert r.status_code == 200


async def test_username_bucket_is_case_and_whitespace_normalized(client, make_user):
    """'Alice', 'alice' and ' alice ' all share one bucket."""
    from config import settings as s

    n = s.login_failure_limit
    await make_user(username="Alice", password="pw")

    # Spread the failures across spellings; each is a distinct DB lookup that
    # misses (usernames are stored case-sensitively) but one shared bucket.
    await _fail_login(client, "alice", n - 1)
    last = await _fail_login(client, " ALICE ", 1)
    assert last.status_code == 401

    r = await client.post("/api/auth/login", json={"username": "Alice", "password": "pw"})
    assert r.status_code == 429


async def test_locked_login_writes_a_persisted_audit_row(client, make_user, db):
    """The login_locked row must survive the 429 (commit-before-raise, PR #289)."""
    from config import settings as s

    await make_user(username="audited", password="pw")
    await _fail_login(client, "audited", s.login_failure_limit)

    r = await client.post("/api/auth/login", json={"username": "audited", "password": "pw"})
    assert r.status_code == 429

    rows = (
        await db.execute(select(AuditLog).where(AuditLog.action == "login_locked"))
    ).scalars().all()
    assert len(rows) == 1
    assert "audited" in rows[0].details
    # The failed attempts that built the lock are still logged separately.
    failed = (
        await db.execute(select(AuditLog).where(AuditLog.action == "login_failed"))
    ).scalars().all()
    assert len(failed) == s.login_failure_limit


async def test_lock_does_not_fire_on_the_inactive_account_path(client, make_user):
    """A correct password for a pending account clears the counter (403, not 429)."""
    from config import settings as s
    from rate_limit import failed_logins

    await make_user(username="pendinguser", password="pw", is_active=False)
    await _fail_login(client, "pendinguser", s.login_failure_limit - 1)

    r = await client.post(
        "/api/auth/login", json={"username": "pendinguser", "password": "pw"}
    )
    assert r.status_code == 403
    assert failed_logins.failure_count("pendinguser") == 0


# --- FailedLoginTracker unit tests ------------------------------------------


def test_tracker_stays_quiet_below_the_threshold():
    from rate_limit import FailedLoginTracker

    t = FailedLoginTracker()
    for _ in range(t.limit - 1):
        t.record_failure("u")
    assert t.retry_after("u") is None
    t.record_failure("u")
    assert t.retry_after("u") is not None


def test_tracker_retry_after_counts_down_as_entries_age_out():
    from rate_limit import FailedLoginTracker

    now = [0.0]
    t = FailedLoginTracker(clock=lambda: now[0])
    for i in range(t.limit):
        now[0] = float(i)
        t.record_failure("u")

    first = t.retry_after("u")
    now[0] += 10
    second = t.retry_after("u")
    assert second is not None and second < first


def test_tracker_reset_clears_every_bucket():
    from rate_limit import FailedLoginTracker

    t = FailedLoginTracker()
    for _ in range(t.limit):
        t.record_failure("a")
        t.record_failure("b")
    assert t.retry_after("a") and t.retry_after("b")
    t.reset()
    assert t.retry_after("a") is None
    assert t.failure_count("b") == 0


def test_tracker_evicts_stale_buckets_so_a_username_spray_cannot_grow_forever():
    from rate_limit import FailedLoginTracker

    now = [0.0]
    t = FailedLoginTracker(clock=lambda: now[0], max_tracked=50)
    for i in range(500):
        t.record_failure(f"user{i}")
    assert t.tracked_usernames <= 50


def test_tracker_clear_is_safe_for_an_unknown_username():
    from rate_limit import FailedLoginTracker

    FailedLoginTracker().clear("never-seen")  # must not raise


# ---------------------------------------------------------------------------
# must_reset_password is enforced server-side (issue #209)
#
# The flag is set by admin-create, admin-reset and the fresh-install bootstrap,
# and until now the only thing that honoured it was a React early-return in
# App.jsx. So the temporary password the admin typed — and, for the bootstrap
# account, one that is printed to the logs — stayed a fully working credential
# for curl and for Android, which has no concept of the flag at all.
# ---------------------------------------------------------------------------


async def test_must_reset_user_is_refused_on_ordinary_routes(
    client, make_user, auth_header,
):
    user = await make_user(username="temp", must_reset_password=True)

    r = await client.get("/api/auth/media-token?resource_type=cover&resource_id=x.jpg",
                         headers=auth_header(user))

    assert r.status_code == 403
    assert r.json()["detail"] == "password_reset_required"


async def test_must_reset_user_can_reach_me_change_password_and_logout(
    client, make_user, auth_header,
):
    """The allow-list has to be exactly big enough to complete the reset."""
    user = await make_user(username="temp", password="temporary1", must_reset_password=True)
    header = auth_header(user)

    assert (await client.get("/api/auth/me", headers=header)).status_code == 200

    r = await client.post(
        "/api/auth/change-password",
        json={"old_password": "temporary1", "new_password": "brand-new-pw-99"},
        headers=header,
    )
    assert r.status_code == 200

    # logout is on the list too, so a user who would rather not reset can leave.
    other = await make_user(username="temp2", must_reset_password=True)
    assert (await client.post("/api/auth/logout",
                              headers=auth_header(other))).status_code == 200


async def test_must_reset_user_cannot_edit_their_profile(client, make_user, auth_header):
    """PUT /me shares a path with GET /me, so the allow-list is keyed on
    (method, path): a temp credential must not be able to change the account's
    email before the password is reset."""
    user = await make_user(username="temp", must_reset_password=True)

    r = await client.put("/api/auth/me", json={"theme": "ember"},
                         headers=auth_header(user))

    assert r.status_code == 403
    assert r.json()["detail"] == "password_reset_required"


async def test_role_gated_route_also_refuses_must_reset_user(
    make_client, make_user, auth_header,
):
    """require_role derives from get_current_user, so it inherits the gate —
    an admin with a temp password is still a temp password.

    Uses the real users router rather than a synthetic route so this pins the
    inheritance as actually wired, not as re-declared in a test.
    """
    from routers import users as users_router

    admin = await make_user(username="tempadmin", role="admin", must_reset_password=True)

    async with make_client(users_router.router) as c:
        r = await c.get("/api/users/", headers=auth_header(admin))

    assert r.status_code == 403
    assert r.json()["detail"] == "password_reset_required"


async def test_role_gated_route_still_serves_an_unflagged_admin(
    make_client, make_user, auth_header,
):
    """Counterweight: the gate must not be refusing admins in general."""
    from routers import users as users_router

    admin = await make_user(username="realadmin", role="admin")

    async with make_client(users_router.router) as c:
        r = await c.get("/api/users/", headers=auth_header(admin))

    assert r.status_code == 200


async def test_change_password_lifts_the_gate(client, make_user, auth_header):
    user = await make_user(username="temp", password="temporary1", must_reset_password=True)

    assert (await client.post(
        "/api/auth/change-password",
        json={"old_password": "temporary1", "new_password": "brand-new-pw-99"},
        headers=auth_header(user),
    )).status_code == 200

    # change_password bumps token_version, so the caller needs a fresh token —
    # exactly what the web flow does after a successful reset.
    login = await client.post("/api/auth/login",
                              json={"username": "temp", "password": "brand-new-pw-99"})
    assert login.status_code == 200
    fresh = {"Authorization": f"Bearer {login.json()['access_token']}"}

    r = await client.get("/api/auth/media-token?resource_type=cover&resource_id=x.jpg",
                         headers=fresh)
    assert r.status_code != 403


async def test_unflagged_user_is_unaffected(client, make_user, auth_header):
    """The gate must not fire for everybody else."""
    user = await make_user(username="normal", must_reset_password=False)

    r = await client.get("/api/auth/media-token?resource_type=cover&resource_id=x.jpg",
                         headers=auth_header(user))

    assert r.status_code != 403


# ---------------------------------------------------------------------------
# Issue #264: throttling and auditing the password change.
#
# change-password verifies the current password and had no limit and no audit
# row on failure. Someone holding a stolen 24h access token could brute-force
# the current password against bcrypt at whatever rate the server sustains, then
# set a new one -- turning a temporary token into permanent takeover, locking
# the real user out (the change bumps token_version), with nothing in the audit
# log to show it happened.
#
# The bucket is keyed by **user id**, not IP. Behind Caddy every client shares
# the proxy's address (issue #294), so a per-IP limit here would throttle all
# users together and protect nobody.
#
# Note the limit is much lower than login's. login keys on username, so a high
# threshold is deliberate there -- an attacker who knows a username could
# otherwise lock the real user out (see config.py). That inverts here: reaching
# this endpoint at all requires the victim's own valid access token, so the
# caller *is* the session. Locking it out is the intended outcome, not
# collateral damage.
# ---------------------------------------------------------------------------


async def _wrong_password(client, headers, n=1):
    last = None
    for _ in range(n):
        last = await client.post(
            "/api/auth/change-password",
            json={"old_password": "not-the-password", "new_password": "irrelevant1"},
            headers=headers,
        )
    return last


async def test_repeated_wrong_current_passwords_lock_the_account_out(
    client, make_user, auth_header
):
    from config import settings as s
    from rate_limit import failed_password_changes

    failed_password_changes.reset()
    user = await make_user(username="brute", password="realpassword")
    headers = auth_header(user)

    for _ in range(s.password_change_failure_limit):
        r = await _wrong_password(client, headers)
        assert r.status_code == 400, "a wrong current password is a 400, not a lockout"

    r = await _wrong_password(client, headers)
    assert r.status_code == 429
    assert "Retry-After" in r.headers


async def test_the_lockout_is_per_user_not_shared(client, make_user, auth_header):
    """The test that actually proves the key is the user id.

    A shared bucket -- which is what keying on the proxy's IP would give -- would
    lock this second user out too, and every other user of the deployment with
    them.
    """
    from config import settings as s
    from rate_limit import failed_password_changes

    failed_password_changes.reset()
    attacker_victim = await make_user(username="locked", password="realpassword")
    bystander = await make_user(username="bystander", password="realpassword")

    await _wrong_password(client, auth_header(attacker_victim), s.password_change_failure_limit)
    assert (await _wrong_password(client, auth_header(attacker_victim))).status_code == 429

    r = await client.post(
        "/api/auth/change-password",
        json={"old_password": "realpassword", "new_password": "brandnewpw1"},
        headers=auth_header(bystander),
    )
    assert r.status_code == 200


async def test_the_lockout_expires(client, make_user, auth_header):
    """Advance the injected clock instead of sleeping."""
    import time

    from config import settings as s
    from rate_limit import failed_password_changes

    failed_password_changes.reset()
    now = [5000.0]
    failed_password_changes.clock = lambda: now[0]
    try:
        user = await make_user(username="patient", password="realpassword")
        headers = auth_header(user)

        await _wrong_password(client, headers, s.password_change_failure_limit)
        assert (await _wrong_password(client, headers)).status_code == 429

        now[0] += s.password_change_failure_window_seconds + 1
        r = await client.post(
            "/api/auth/change-password",
            json={"old_password": "realpassword", "new_password": "brandnewpw1"},
            headers=headers,
        )
        assert r.status_code == 200
    finally:
        failed_password_changes.clock = time.monotonic


async def test_a_correct_password_clears_the_bucket(client, make_user, auth_header):
    from rate_limit import failed_password_changes

    failed_password_changes.reset()
    user = await make_user(username="recovers", password="realpassword")
    headers = auth_header(user)

    await _wrong_password(client, headers, 2)
    assert failed_password_changes.failure_count(str(user.id)) == 2

    r = await client.post(
        "/api/auth/change-password",
        json={"old_password": "realpassword", "new_password": "brandnewpw1"},
        headers=headers,
    )
    assert r.status_code == 200
    assert failed_password_changes.failure_count(str(user.id)) == 0


async def test_a_failed_change_is_audited(client, make_user, auth_header, db):
    """Without this the brute force leaves no trace at all."""
    from rate_limit import failed_password_changes

    failed_password_changes.reset()
    user = await make_user(username="audited", password="realpassword")

    await _wrong_password(client, auth_header(user))

    rows = (await db.execute(
        select(AuditLog).where(AuditLog.action == "password_change_failed")
    )).scalars().all()
    assert len(rows) == 1
    assert rows[0].user_id == user.id


# ---------------------------------------------------------------------------
# Issue #264, the /auth/refresh half.
#
# The issue proposed `@limiter.limit("30/minute")` keyed by the token's `sub`.
# That is not implementable: the refresh token arrives in the request *body*,
# and slowapi key functions are synchronous and cannot read it — the same wall
# this repo hit in #296, recorded in rate_limit.py's docstring. The remaining
# decorator option, keying on the client address, is worse than nothing here:
# behind Caddy and docker NAT every caller shares the proxy's address (#294), so
# a 30/minute bucket would be shared by the whole deployment.
#
# So the bound is in-handler and counts *rejected* refreshes, which is the abuse
# that costs anything: each one is a DB lookup, and a valid refresh is cheap and
# self-limiting now that the web client single-flights them (#268).
# ---------------------------------------------------------------------------


async def test_repeated_rejected_refreshes_are_throttled(client, make_user, db):
    from config import settings as s
    from rate_limit import failed_refreshes

    failed_refreshes.reset()
    user = await make_user(username="refresher", password="pw")
    stale = create_refresh_token(user)
    # Invalidate it the way a logout does. Re-load through the db fixture first:
    # make_user commits on its own session and hands back a detached object, so
    # bumping that instance reaches nothing the request will read — the token
    # stays valid and every assertion below passes against a 200. Same shape as
    # test_token_rejected_after_version_bump.
    stored = (await db.execute(select(User).where(User.id == user.id))).scalar_one()
    stored.token_version += 1
    await db.commit()

    for _ in range(s.refresh_failure_limit):
        r = await client.post("/api/auth/refresh", json={"refresh_token": stale})
        assert r.status_code == 401

    r = await client.post("/api/auth/refresh", json={"refresh_token": stale})
    assert r.status_code == 429
    assert "Retry-After" in r.headers


async def test_a_valid_refresh_is_unaffected_by_another_tokens_failures(client, make_user, db):
    """The bucket is per token subject, not global — one bad actor must not
    stop everyone else renewing their sessions."""
    from config import settings as s
    from rate_limit import failed_refreshes

    failed_refreshes.reset()
    victim = await make_user(username="innocent", password="pw")
    noisy = await make_user(username="noisy", password="pw")

    stale = create_refresh_token(noisy)
    stored = (await db.execute(select(User).where(User.id == noisy.id))).scalar_one()
    stored.token_version += 1
    await db.commit()
    for _ in range(s.refresh_failure_limit + 1):
        await client.post("/api/auth/refresh", json={"refresh_token": stale})

    good = create_refresh_token(victim)
    r = await client.post("/api/auth/refresh", json={"refresh_token": good})
    assert r.status_code == 200
