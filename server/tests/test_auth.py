"""
Auth / RBAC characterization tests (issue #46).

Covers login, token refresh, wrong-secret rejection, the role hierarchy gate,
and the must_reset_password change-password flow. These pin current behavior so
future refactors of the auth surface can't silently regress.
"""

import ast
import io
import os
from datetime import timedelta

import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient
import jwt
from sqlalchemy import select

from config import settings
from models.audit_log import AuditLog
from models.refresh_token import RefreshToken
from models.user import ROLE_HIERARCHY, User
from routers.auth import (
    create_access_token,
    create_media_token,
    create_refresh_token,
    create_token,
    get_admin_user,
    get_editor_user,
    require_role,
    verify_password,
)
from utils import utcnow

_SERVER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


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
# Audit-row size (issue #261)
#
# `details` is Text and on a failed login embeds the attempted username, so its
# size was whatever the attacker typed. Two independent guards: the schema
# refuses an over-long username outright, and log_audit truncates whatever it
# is handed.
# ---------------------------------------------------------------------------

async def test_login_with_a_huge_username_is_rejected_before_it_is_logged(client, db):
    r = await client.post(
        "/api/auth/login", json={"username": "x" * 10_000, "password": "pw"}
    )
    assert r.status_code == 422

    rows = (
        await db.execute(select(AuditLog).where(AuditLog.action == "login_failed"))
    ).scalars().all()
    assert rows == []


async def test_login_at_the_username_limit_writes_a_bounded_details_row(client, db):
    """50 chars is the max UserCreate already allowed; the row it produces must
    still be well under the details cap."""
    r = await client.post(
        "/api/auth/login", json={"username": "y" * 50, "password": "pw"}
    )
    assert r.status_code == 401

    rows = (
        await db.execute(select(AuditLog).where(AuditLog.action == "login_failed"))
    ).scalars().all()
    assert len(rows) == 1
    assert len(rows[0].details) <= 500


async def test_log_audit_truncates_over_long_details(db):
    """The cap lives in log_audit, not in the login route — every writer gets it."""
    from routers.auth import log_audit

    await log_audit(db, "test_action", details="z" * 5_000)
    await db.commit()

    row = (
        await db.execute(select(AuditLog).where(AuditLog.action == "test_action"))
    ).scalars().one()
    assert len(row.details) == 500
    assert row.details.endswith("…")


async def test_log_audit_leaves_short_details_alone(db):
    from routers.auth import log_audit

    await log_audit(db, "test_action", details="short and sweet")
    await db.commit()

    row = (
        await db.execute(select(AuditLog).where(AuditLog.action == "test_action"))
    ).scalars().one()
    assert row.details == "short and sweet"


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


# ---------------------------------------------------------------------------
# RBAC — an unknown *minimum* role must fail closed (issue #359)
#
# `ROLE_HIERARCHY.get(minimum_role, 0)` scored an unrecognised minimum as 0, and
# every caller clears a bar of 0. So `require_role("editorr")` did not fail the
# check, it removed it: the endpoint still looked gated in the source, nothing
# logged, nothing 500'd, and a plain `user` sailed through. The only symptom was
# a negative test nobody had written.
#
# The floor is now validated where the dependency is built — at import, because
# the three aliases are module-level — so a typo is a startup crash instead of a
# silently open route. `User.has_role` fails closed for the same reason.
# ---------------------------------------------------------------------------

def test_require_role_refuses_an_unknown_minimum():
    """The factory raises, so a typo'd floor cannot become a working dependency."""
    with pytest.raises(ValueError) as exc:
        require_role("editorr")
    assert "editorr" in str(exc.value)


def test_require_role_refuses_an_empty_or_none_minimum():
    for bad in ("", None):
        with pytest.raises(ValueError):
            require_role(bad)


async def test_has_role_fails_closed_on_an_unknown_minimum(make_user):
    """Even a superadmin does not clear a bar nobody can name."""
    boss = await make_user(username="boss359", role="superadmin")
    assert boss.has_role("superadmin") is True
    assert boss.has_role("editorr") is False
    assert boss.has_role("") is False
    assert boss.has_role(None) is False


async def test_has_role_still_denies_an_unknown_user_role(make_user):
    """The other direction was already safe; keep it that way."""
    odd = await make_user(username="odd359", role="wizard")
    assert odd.has_role("user") is False


# --- the typo can never pass silently again ------------------------------

_ROLE_FLOOR_CALLS = {"require_role", "has_role"}


def _role_floor_literals(source: str):
    """String floors passed to require_role()/has_role(), parsed not grepped.

    Parsed, because the prose in this file and in `require_role`'s own docstring
    names the typo it guards against — a regex over raw text flags the warning
    label as a real call site.
    """
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
        if name not in _ROLE_FLOOR_CALLS:
            continue
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            yield first.value


def _server_source_files():
    """Every .py under server/, minus the test tree, the venv and caches."""
    for dirpath, dirnames, filenames in os.walk(_SERVER_DIR):
        dirnames[:] = [
            d for d in dirnames
            if d not in {".venv", "__pycache__", "tests", ".pytest_cache", "htmlcov"}
        ]
        for name in filenames:
            if name.endswith(".py"):
                yield os.path.join(dirpath, name)


def test_every_role_floor_literal_in_the_server_is_a_known_role():
    """Enumerate the floors the source actually asks for and check each one.

    The import-time guard in `require_role` only fires for call sites that run
    at import. This catches the rest — a floor named inside a request handler,
    or a `has_role` call — without waiting for someone to exercise the route
    with a low-privileged account.
    """
    found = []
    for path in _server_source_files():
        with io.open(path, encoding="utf-8") as fh:
            source = fh.read()
        for literal in _role_floor_literals(source):
            rel = os.path.relpath(path, _SERVER_DIR).replace(os.sep, "/")
            found.append((rel, literal))

    assert found, (
        "no require_role()/has_role() literals found in server/ — if the role "
        "floors moved to another spelling, point this test at it rather than "
        "deleting it."
    )
    unknown = sorted({f"{where}: {literal!r}" for where, literal in found
                      if literal not in ROLE_HIERARCHY})
    assert not unknown, (
        f"Role floors naming a role that does not exist: {unknown}. "
        f"Known roles: {sorted(ROLE_HIERARCHY)}."
    )


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


async def test_logout_invalidates_this_token(client, make_user):
    """Logout is per-device since issue #250, so what it invalidates is the
    session it was called with — not, as it once did, every session the account
    has. `test_logout_with_device_a_refresh_leaves_device_b_access_token_valid`
    is the other half of that contract."""
    await make_user(username="peggy", password="pw")
    r = await client.post("/api/auth/login",
                          json={"username": "peggy", "password": "pw"})
    tokens = r.json()
    header = {"Authorization": f"Bearer {tokens['access_token']}"}

    r = await client.post("/api/auth/logout", headers=header,
                          json={"refresh_token": tokens["refresh_token"]})
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
        json={"username": "quentin", "email": "q@example.com", "password": "pw123456"},
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
    """The bucket is per *token*, not per subject and not global.

    Found in live testing: keying on the subject meant twenty replays of one
    dead token also blocked that same user's freshly issued valid one for the
    rest of the window, so anyone holding a single stale token could stop a user
    renewing. This covers the same-user case as well as the bystander.
    """
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

    # And the abused user's own fresh token still works: the lockout follows the
    # token that was replayed, not the account behind it.
    stored = (await db.execute(select(User).where(User.id == noisy.id))).scalar_one()
    r = await client.post(
        "/api/auth/refresh", json={"refresh_token": create_refresh_token(stored)}
    )
    assert r.status_code == 200

# ---------------------------------------------------------------------------
# Per-device sessions (issue #250)
#
# Logging out used to bump `token_version`, which invalidated every token the
# account held everywhere — signing out of the browser signed out the phone,
# and the phone's unsynced reading positions then sat undelivered until someone
# noticed and signed in again. Refresh tokens now name a session row
# (`refresh_tokens.jti`), the access tokens minted from one carry the same value
# as `sid`, and `logout` revokes just that row.
#
# `token_version` is still the account-wide kill switch, and it must stay that
# way: password change, admin reset, and the new explicit `/logout-all` all use
# it.
# ---------------------------------------------------------------------------

async def _sign_in(client, username, password="pw", device_id=None):
    """Log in the way a client does, optionally naming the device."""
    body = {"username": username, "password": password}
    if device_id is not None:
        body["device_id"] = device_id
    r = await client.post("/api/auth/login", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _bearer(tokens):
    return {"Authorization": f"Bearer {tokens['access_token']}"}


def _claims(token):
    return jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])


async def _sessions(db, user_id):
    return (await db.execute(
        select(RefreshToken).where(RefreshToken.user_id == user_id)
    )).scalars().all()


async def test_login_opens_a_session_both_tokens_name(client, make_user, db):
    user = await make_user(username="opener", password="pw")
    tokens = await _sign_in(client, "opener", device_id="device-a")

    rows = await _sessions(db, user.id)
    assert len(rows) == 1
    assert rows[0].device_id == "device-a"
    assert rows[0].revoked_at is None
    # The refresh token names the session, and the access token minted beside it
    # names the same one — that is what lets a per-device logout kill both.
    assert _claims(tokens["refresh_token"])["jti"] == rows[0].jti
    assert _claims(tokens["access_token"])["sid"] == rows[0].jti


async def test_logout_with_device_a_refresh_leaves_device_b_access_token_valid(
    client, make_user,
):
    await make_user(username="reader", password="pw")
    a = await _sign_in(client, "reader", device_id="device-a")
    b = await _sign_in(client, "reader", device_id="device-b")

    r = await client.post(
        "/api/auth/logout",
        headers=_bearer(a),
        json={"refresh_token": a["refresh_token"]},
    )
    assert r.status_code == 200
    assert r.json()["scope"] == "device"

    assert (await client.get("/api/auth/me", headers=_bearer(b))).status_code == 200


async def test_device_b_can_still_refresh_after_device_a_logs_out(client, make_user):
    """The phone's 15-minute position sweep has to survive a browser logout."""
    await make_user(username="reader", password="pw")
    a = await _sign_in(client, "reader", device_id="device-a")
    b = await _sign_in(client, "reader", device_id="device-b")

    await client.post("/api/auth/logout", headers=_bearer(a),
                      json={"refresh_token": a["refresh_token"]})

    r = await client.post("/api/auth/refresh",
                          json={"refresh_token": b["refresh_token"]})
    assert r.status_code == 200
    assert r.json()["access_token"]


async def test_revoked_refresh_token_cannot_be_used_again(client, make_user):
    await make_user(username="reader", password="pw")
    a = await _sign_in(client, "reader", device_id="device-a")

    await client.post("/api/auth/logout", headers=_bearer(a),
                      json={"refresh_token": a["refresh_token"]})

    r = await client.post("/api/auth/refresh",
                          json={"refresh_token": a["refresh_token"]})
    assert r.status_code == 401


async def test_logout_invalidates_the_calling_devices_access_token(client, make_user):
    """The old global bump did this as a side effect. It has to keep happening
    for the device that logged out, or per-device logout would be *weaker* than
    what it replaced: a 24h access token would go on working after sign-out."""
    await make_user(username="reader", password="pw")
    a = await _sign_in(client, "reader", device_id="device-a")

    await client.post("/api/auth/logout", headers=_bearer(a),
                      json={"refresh_token": a["refresh_token"]})

    assert (await client.get("/api/auth/me", headers=_bearer(a))).status_code == 401


async def test_logout_leaves_token_version_alone(client, make_user, db):
    await make_user(username="reader", password="pw")
    a = await _sign_in(client, "reader", device_id="device-a")

    await client.post("/api/auth/logout", headers=_bearer(a),
                      json={"refresh_token": a["refresh_token"]})

    stored = (await db.execute(select(User).where(User.username == "reader"))).scalar_one()
    assert stored.token_version == 0


async def test_password_change_still_invalidates_every_device(client, make_user):
    """The global path must not have been lost on the way to per-device logout:
    a password change is exactly the case where every device has to go."""
    await make_user(username="reader", password="pw")
    a = await _sign_in(client, "reader", device_id="device-a")
    b = await _sign_in(client, "reader", device_id="device-b")

    r = await client.post(
        "/api/auth/change-password",
        headers=_bearer(a),
        json={"old_password": "pw", "new_password": "a-new-password"},
    )
    assert r.status_code == 200

    assert (await client.get("/api/auth/me", headers=_bearer(a))).status_code == 401
    assert (await client.get("/api/auth/me", headers=_bearer(b))).status_code == 401
    for tokens in (a, b):
        r = await client.post("/api/auth/refresh",
                              json={"refresh_token": tokens["refresh_token"]})
        assert r.status_code == 401


async def test_logout_all_invalidates_every_device(client, make_user, db):
    await make_user(username="reader", password="pw")
    a = await _sign_in(client, "reader", device_id="device-a")
    b = await _sign_in(client, "reader", device_id="device-b")

    r = await client.post("/api/auth/logout-all", headers=_bearer(a))
    assert r.status_code == 200
    assert r.json()["scope"] == "all"

    assert (await client.get("/api/auth/me", headers=_bearer(a))).status_code == 401
    assert (await client.get("/api/auth/me", headers=_bearer(b))).status_code == 401
    for tokens in (a, b):
        r = await client.post("/api/auth/refresh",
                              json={"refresh_token": tokens["refresh_token"]})
        assert r.status_code == 401

    stored = (await db.execute(select(User).where(User.username == "reader"))).scalar_one()
    assert stored.token_version == 1
    assert all(row.revoked_at is not None for row in await _sessions(db, stored.id))


async def test_logout_all_is_audited_separately_from_a_device_logout(client, make_user, db):
    await make_user(username="reader", password="pw")
    a = await _sign_in(client, "reader", device_id="device-a")

    await client.post("/api/auth/logout-all", headers=_bearer(a))

    actions = (await db.execute(select(AuditLog.action))).scalars().all()
    assert "logout_all" in actions


async def test_refreshing_keeps_the_session_and_its_other_tokens_alive(client, make_user, db):
    """Refresh deliberately does *not* invalidate the token it was given.

    Both clients single-flight their refresh (web `refreshSession`, Android
    `TokenAuthenticator`), but neither survives a refresh whose response is lost
    on the way back — with strict rotation that would strand the device with a
    dead token and no way to renew it. So the session keeps its `jti` and the
    new refresh token simply carries a later expiry; revocation is what ends a
    session, not use. See the note in `routers/auth.py`.
    """
    user = await make_user(username="reader", password="pw")
    a = await _sign_in(client, "reader", device_id="device-a")
    first = _claims(a["refresh_token"])["jti"]

    r = await client.post("/api/auth/refresh",
                          json={"refresh_token": a["refresh_token"]})
    assert r.status_code == 200
    assert _claims(r.json()["refresh_token"])["jti"] == first
    assert len(await _sessions(db, user.id)) == 1

    # The token that was presented still works: a lost response must not cost
    # the device its session.
    again = await client.post("/api/auth/refresh",
                              json={"refresh_token": a["refresh_token"]})
    assert again.status_code == 200


async def test_a_second_login_from_the_same_device_replaces_its_session(
    client, make_user, db,
):
    """Signing in again on a device supersedes the session that device had,
    rather than stacking another one that nothing will ever revoke."""
    user = await make_user(username="reader", password="pw")
    first = await _sign_in(client, "reader", device_id="device-a")
    second = await _sign_in(client, "reader", device_id="device-a")

    live = [row for row in await _sessions(db, user.id) if row.revoked_at is None]
    assert len(live) == 1
    assert live[0].jti == _claims(second["refresh_token"])["jti"]

    assert (await client.post(
        "/api/auth/refresh", json={"refresh_token": first["refresh_token"]}
    )).status_code == 401
    assert (await client.post(
        "/api/auth/refresh", json={"refresh_token": second["refresh_token"]}
    )).status_code == 200


async def test_logins_without_a_device_id_get_independent_sessions(client, make_user, db):
    """A client that sends no device id (or an old one) is not thereby merged
    with every other anonymous session on the account."""
    user = await make_user(username="reader", password="pw")
    a = await _sign_in(client, "reader")
    b = await _sign_in(client, "reader")

    assert len({_claims(a["refresh_token"])["jti"],
                _claims(b["refresh_token"])["jti"]}) == 2
    assert len([r for r in await _sessions(db, user.id) if r.revoked_at is None]) == 2


async def test_expired_sessions_are_pruned_when_the_user_signs_in(client, make_user, db):
    """The table must not grow forever. A session whose refresh token has
    outlived `JWT_REFRESH_TOKEN_EXPIRE_DAYS` can never authenticate again — the
    JWT's own `exp` sees to that — so the row is dead weight."""
    user = await make_user(username="reader", password="pw")
    stale_at = utcnow() - timedelta(days=settings.jwt_refresh_token_expire_days + 1)
    db.add(RefreshToken(
        user_id=user.id, jti="long-dead", device_id="old-phone",
        issued_at=stale_at, last_used_at=stale_at,
    ))
    await db.commit()

    await _sign_in(client, "reader", device_id="device-a")

    jtis = {row.jti for row in await _sessions(db, user.id)}
    assert "long-dead" not in jtis


async def test_refresh_rejects_a_session_that_does_not_exist(client, make_user):
    """A signature-valid token naming an unknown session is not a session."""
    user = await make_user(username="reader", password="pw")
    forged = create_token(
        {"sub": str(user.id), "type": "refresh", "ver": user.token_version,
         "jti": "never-issued"},
        timedelta(days=1),
    )
    assert (await client.post(
        "/api/auth/refresh", json={"refresh_token": forged}
    )).status_code == 401


async def test_a_session_cannot_be_revoked_by_another_account(client, make_user):
    """`logout` revokes by `jti`, so it has to check the session belongs to the
    caller — otherwise anyone holding a stray token could sign out a stranger's
    device."""
    await make_user(username="victim", password="pw")
    await make_user(username="attacker", password="pw")
    victim = await _sign_in(client, "victim", device_id="device-a")
    attacker = await _sign_in(client, "attacker", device_id="device-b")

    r = await client.post(
        "/api/auth/logout",
        headers=_bearer(attacker),
        json={"refresh_token": victim["refresh_token"]},
    )
    assert r.status_code == 200
    # The victim's session survived; the attacker signed only themselves out.
    assert (await client.get("/api/auth/me", headers=_bearer(victim))).status_code == 200
    assert (await client.get("/api/auth/me", headers=_bearer(attacker))).status_code == 401


async def test_logging_out_a_dead_session_does_not_sign_out_the_other_devices(
    client, make_user,
):
    """Idempotence matters more than it looks: falling back to the global bump
    whenever the named session is already gone would turn a retried logout — a
    flaky network, a double tap — into the very "signed out everywhere" this
    issue is about."""
    await make_user(username="reader", password="pw")
    a = await _sign_in(client, "reader", device_id="device-a")
    b = await _sign_in(client, "reader", device_id="device-b")

    body = {"refresh_token": a["refresh_token"]}
    assert (await client.post("/api/auth/logout", headers=_bearer(a),
                              json=body)).status_code == 200
    # Same call again, this time authenticated by the device that is still live.
    r = await client.post("/api/auth/logout", headers=_bearer(b), json=body)
    assert r.status_code == 200
    # ...which signed out B's own session, and left token_version alone.
    assert r.json()["scope"] == "device"


# --- Backwards compatibility with clients that have not been updated --------
#
# `create_access_token(user)` / `create_refresh_token(user)` with no session are
# exactly the tokens this deployment had already handed out before the upgrade,
# so these tests are the deploy rehearsal.


async def test_an_access_token_minted_before_the_upgrade_still_authenticates(
    client, make_user, auth_header,
):
    user = await make_user(username="early", password="pw")
    assert (await client.get("/api/auth/me",
                             headers=auth_header(user))).status_code == 200


async def test_a_refresh_token_minted_before_the_upgrade_still_works(
    client, make_user, db,
):
    """No client is signed out by the deploy. A token with no `jti` predates
    sessions, so it is accepted on its `ver` alone — as it always was — and the
    tokens handed back name a real session from then on."""
    user = await make_user(username="early", password="pw")
    legacy = create_refresh_token(user)
    assert "jti" not in _claims(legacy)

    r = await client.post(
        "/api/auth/refresh",
        json={"refresh_token": legacy, "device_id": "device-a"},
    )
    assert r.status_code == 200

    upgraded = _claims(r.json()["refresh_token"])
    rows = await _sessions(db, user.id)
    assert len(rows) == 1
    assert rows[0].jti == upgraded["jti"]
    assert rows[0].device_id == "device-a"


async def test_logout_from_a_client_with_no_session_signs_out_every_device(
    client, make_user, auth_header,
):
    """An old client presents an old access token and no body, so there is no
    session to name. The conservative answer is the behaviour it was built
    against: revoke everything."""
    user = await make_user(username="early", password="pw")
    b = await _sign_in(client, "early", device_id="device-b")

    r = await client.post("/api/auth/logout", headers=auth_header(user))
    assert r.status_code == 200
    assert r.json()["scope"] == "all"

    assert (await client.get("/api/auth/me", headers=_bearer(b))).status_code == 401


async def test_logout_is_per_device_even_when_the_client_sends_no_body(
    client, make_user,
):
    """An updated server in front of a client that has not learned to send its
    refresh token still gets per-device logout: the access token authenticating
    the call already names the session."""
    await make_user(username="reader", password="pw")
    a = await _sign_in(client, "reader", device_id="device-a")
    b = await _sign_in(client, "reader", device_id="device-b")

    r = await client.post("/api/auth/logout", headers=_bearer(a))
    assert r.status_code == 200
    assert r.json()["scope"] == "device"

    assert (await client.get("/api/auth/me", headers=_bearer(a))).status_code == 401
    assert (await client.get("/api/auth/me", headers=_bearer(b))).status_code == 200


async def test_a_refresh_names_the_device_a_session_did_not_know_about(
    client, make_user, db,
):
    """A client that learns to send its device id names the session it already
    has, rather than having to sign in again to be nameable."""
    user = await make_user(username="reader", password="pw")
    tokens = await _sign_in(client, "reader")
    assert (await _sessions(db, user.id))[0].device_id is None

    r = await client.post("/api/auth/refresh", json={
        "refresh_token": tokens["refresh_token"], "device_id": "device-a",
    })
    assert r.status_code == 200

    rows = await _sessions(db, user.id)
    assert len(rows) == 1
    assert rows[0].device_id == "device-a"


async def test_logout_ignores_a_refresh_token_it_cannot_read(client, make_user):
    """Garbage in the body must not cost the caller anything: `logout` falls
    back to the session its access token names, and still signs out only this
    device."""
    await make_user(username="reader", password="pw")
    a = await _sign_in(client, "reader", device_id="device-a")
    b = await _sign_in(client, "reader", device_id="device-b")

    r = await client.post("/api/auth/logout", headers=_bearer(a),
                          json={"refresh_token": "not-a-jwt"})
    assert r.status_code == 200
    assert r.json()["scope"] == "device"

    assert (await client.get("/api/auth/me", headers=_bearer(a))).status_code == 401
    assert (await client.get("/api/auth/me", headers=_bearer(b))).status_code == 200


async def test_a_device_id_longer_than_the_column_is_refused(client, make_user):
    """The column is String(100) and the value is client-supplied; a 422 is
    better than a truncated id or a Postgres error mid-login."""
    await make_user(username="reader", password="pw")
    r = await client.post("/api/auth/login", json={
        "username": "reader", "password": "pw", "device_id": "x" * 200,
    })
    assert r.status_code == 422


async def test_media_tokens_name_the_session_that_minted_them(
    client, make_user,
):
    """Logout used to kill cached media tokens through the `token_version`
    bump. They have to keep dying with their session, or a signed-out browser
    could still stream covers and audio for the token's remaining lifetime."""
    await make_user(username="reader", password="pw")
    a = await _sign_in(client, "reader", device_id="device-a")

    r = await client.get(
        "/api/auth/media-token",
        params={"resource_type": "cover", "resource_id": "x.jpg"},
        headers=_bearer(a),
    )
    assert r.status_code == 200
    assert _claims(r.json()["token"])["sid"] == _claims(a["access_token"])["sid"]
