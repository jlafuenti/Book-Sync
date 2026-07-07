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
    token = create_refresh_token(user.id)
    r = await client.post("/api/auth/refresh", json={"refresh_token": token})
    assert r.status_code == 200
    assert r.json()["access_token"]


async def test_refresh_rejects_access_token(client, make_user):
    """An access token presented to /refresh must be rejected on the type check."""
    user = await make_user(username="erin", password="pw")
    access = create_access_token(user.id)
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
    token = create_refresh_token(user.id)
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
