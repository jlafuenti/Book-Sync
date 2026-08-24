"""
Auth / RBAC characterization tests (issue #46).

Covers login, token refresh, wrong-secret rejection, the role hierarchy gate,
and the must_reset_password change-password flow. These pin current behavior so
future refactors of the auth surface can't silently regress.
"""

import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient
from jose import jwt
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
