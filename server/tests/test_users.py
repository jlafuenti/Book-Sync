"""
User-management router tests (issue #46, Phase 3).

The users router is admin-gated and enforces several security invariants
(no creating/deleting superadmins, only superadmin modifies superadmin, no
self-delete). These are the highest-value branches to lock down.
"""

import pytest
from sqlalchemy import select

from database import async_session
from models.audit_log import AuditLog
from models.user import User
from routers import users, auth


async def _fresh(model, id_):
    async with async_session() as s:
        return (await s.execute(select(model).where(model.id == id_))).scalar_one_or_none()


# ---------------------------------------------------------------------------
# Auth gating
# ---------------------------------------------------------------------------

async def test_list_users_requires_admin(make_client, make_user, auth_header):
    plain = await make_user(username="plain", role="user")
    async with make_client(users.router) as c:
        r = await c.get("/api/users/", headers=auth_header(plain))
    assert r.status_code == 403


async def test_list_users_filter_pending(make_client, make_user, auth_header):
    admin = await make_user(username="admin1", role="admin")
    await make_user(username="pending1", role="user", is_active=False)
    await make_user(username="active1", role="user", is_active=True)
    async with make_client(users.router) as c:
        r = await c.get("/api/users/?filter=pending", headers=auth_header(admin))
    assert r.status_code == 200
    names = {u["username"] for u in r.json()}
    assert "pending1" in names
    assert "active1" not in names


# ---------------------------------------------------------------------------
# create_user
# ---------------------------------------------------------------------------

async def test_create_user_success(make_client, make_user, auth_header):
    admin = await make_user(username="admin1", role="admin")
    async with make_client(users.router) as c:
        r = await c.post("/api/users/", headers=auth_header(admin), json={
            "username": "neweditor", "email": "e@x.co",
            "password": "pw123456", "role": "editor",
        })
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["role"] == "editor"
    assert body["is_admin"] is False
    assert body["must_reset_password"] is True


async def test_create_user_invalid_role(make_client, make_user, auth_header):
    admin = await make_user(username="admin1", role="admin")
    async with make_client(users.router) as c:
        r = await c.post("/api/users/", headers=auth_header(admin), json={
            "username": "roletest", "email": "x@x.co", "password": "pw123456", "role": "wizard",
        })
    assert r.status_code == 400


async def test_create_user_refuses_superadmin(make_client, make_user, auth_header):
    admin = await make_user(username="admin1", role="admin")
    async with make_client(users.router) as c:
        r = await c.post("/api/users/", headers=auth_header(admin), json={
            "username": "superattempt", "email": "x@x.co", "password": "pw123456", "role": "superadmin",
        })
    assert r.status_code == 403


async def test_create_user_duplicate_username_and_email(make_client, make_user, auth_header):
    admin = await make_user(username="admin1", role="admin")
    await make_user(username="taken", email="taken@x.co", role="user")
    async with make_client(users.router) as c:
        dup_name = await c.post("/api/users/", headers=auth_header(admin), json={
            "username": "taken", "email": "fresh@x.co", "password": "pw123456", "role": "user",
        })
        dup_email = await c.post("/api/users/", headers=auth_header(admin), json={
            "username": "fresh", "email": "taken@x.co", "password": "pw123456", "role": "user",
        })
    assert dup_name.status_code == 409
    assert dup_email.status_code == 409


async def test_create_user_writes_audit_row(make_client, make_user, auth_header):
    admin = await make_user(username="admin1", role="admin")
    async with make_client(users.router) as c:
        r = await c.post("/api/users/", headers=auth_header(admin), json={
            "username": "audited", "email": "a@x.co", "password": "pw123456", "role": "user",
        })
    assert r.status_code == 201
    async with async_session() as s:
        rows = (await s.execute(
            select(AuditLog).where(AuditLog.action == "user_created")
        )).scalars().all()
    assert len(rows) == 1


# ---------------------------------------------------------------------------
# update_user
# ---------------------------------------------------------------------------

async def test_update_user_role_change(make_client, make_user, auth_header):
    admin = await make_user(username="admin1", role="admin")
    target = await make_user(username="t", role="user")
    async with make_client(users.router) as c:
        r = await c.patch(f"/api/users/{target.id}", headers=auth_header(admin),
                          json={"role": "editor"})
    assert r.status_code == 200
    assert r.json()["role"] == "editor"
    assert r.json()["is_admin"] is False


async def test_admin_cannot_modify_superadmin(make_client, make_user, auth_header):
    admin = await make_user(username="admin1", role="admin")
    superadmin = await make_user(username="root", role="superadmin")
    async with make_client(users.router) as c:
        r = await c.patch(f"/api/users/{superadmin.id}", headers=auth_header(admin),
                          json={"is_active": False})
    assert r.status_code == 403


async def test_admin_cannot_assign_superadmin_role(make_client, make_user, auth_header):
    admin = await make_user(username="admin1", role="admin")
    target = await make_user(username="t", role="user")
    async with make_client(users.router) as c:
        r = await c.patch(f"/api/users/{target.id}", headers=auth_header(admin),
                          json={"role": "superadmin"})
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# approve / reset-password / delete
# ---------------------------------------------------------------------------

async def test_approve_user(make_client, make_user, auth_header):
    admin = await make_user(username="admin1", role="admin")
    pending = await make_user(username="p", role="user", is_active=False)
    async with make_client(users.router) as c:
        ok = await c.post(f"/api/users/{pending.id}/approve", headers=auth_header(admin))
        again = await c.post(f"/api/users/{pending.id}/approve", headers=auth_header(admin))
    assert ok.status_code == 200 and ok.json()["is_active"] is True
    assert again.status_code == 400  # already active


async def test_reset_password_sets_must_reset(make_client, make_user, auth_header):
    admin = await make_user(username="admin1", role="admin")
    target = await make_user(username="t", role="user", password="oldpw")
    async with make_client(users.router) as c:
        r = await c.post(f"/api/users/{target.id}/reset-password",
                         headers=auth_header(admin), json={"new_password": "brandnew1"})
    assert r.status_code == 200
    refreshed = await _fresh(User, target.id)
    assert refreshed.must_reset_password is True


async def test_reset_password_invalidates_existing_token(make_client, make_user, auth_header):
    admin = await make_user(username="admin1", role="admin")
    target = await make_user(username="t", role="user", password="oldpw")
    target_header = auth_header(target)
    async with make_client(users.router, auth.router) as c:
        assert (await c.get("/api/auth/me", headers=target_header)).status_code == 200

        r = await c.post(f"/api/users/{target.id}/reset-password",
                          headers=auth_header(admin), json={"new_password": "brandnew1"})
        assert r.status_code == 200

        assert (await c.get("/api/auth/me", headers=target_header)).status_code == 401


async def test_reset_superadmin_password_forbidden_for_admin(make_client, make_user, auth_header):
    admin = await make_user(username="admin1", role="admin")
    superadmin = await make_user(username="root", role="superadmin")
    async with make_client(users.router) as c:
        r = await c.post(f"/api/users/{superadmin.id}/reset-password",
                         headers=auth_header(admin), json={"new_password": "x1234567"})
    assert r.status_code == 403


async def test_delete_user_ok(make_client, make_user, auth_header):
    admin = await make_user(username="admin1", role="admin")
    target = await make_user(username="doomed", role="user")
    async with make_client(users.router) as c:
        r = await c.delete(f"/api/users/{target.id}", headers=auth_header(admin))
    assert r.status_code == 200
    assert await _fresh(User, target.id) is None


async def test_cannot_delete_superadmin(make_client, make_user, auth_header):
    admin = await make_user(username="admin1", role="admin")
    superadmin = await make_user(username="root", role="superadmin")
    async with make_client(users.router) as c:
        r = await c.delete(f"/api/users/{superadmin.id}", headers=auth_header(admin))
    assert r.status_code == 403
    assert await _fresh(User, superadmin.id) is not None


async def test_cannot_delete_self(make_client, make_user, auth_header):
    admin = await make_user(username="admin1", role="admin")
    async with make_client(users.router) as c:
        r = await c.delete(f"/api/users/{admin.id}", headers=auth_header(admin))
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# Deleting a user who has read something (issue #198)
#
# `test_delete_user_ok` above deletes a user with no child rows, which is the
# only case that ever worked in production: `user_progress.user_id` had no
# `ON DELETE` action and no ORM cascade, so Postgres raised a
# ForeignKeyViolation for anyone who had opened a book and the admin got an
# unexplained 500. The SQLite harness ran with FK enforcement off, so nothing
# in CI could see it — `test_harness_db_isolation.py` now pins the PRAGMA.
# ---------------------------------------------------------------------------

async def test_deleting_a_user_removes_their_positions(
    db, make_client, make_user, auth_header
):
    from models.bookmark import Bookmark, BookmarkLog, PositionHint
    from models.progress import UserProgress
    from routers import sync
    from tests.factories import make_book_pair

    admin = await make_user(username="admin1", role="admin")
    reader = await make_user(username="reader", role="user")
    pair = await make_book_pair(db)

    async with make_client(sync.router, users.router) as c:
        r = await c.put(
            f"/api/sync/position/pair/{pair.id}",
            json={
                "source": "ebook",
                "epub_chapter": 2,
                "epub_sentence_index": 4,
                "epub_progress_percent": 12.5,
                "device_id": "pixel-8",
                "device_name": "Pixel 8",
                "append_to_log": True,
                "hint": {"kind": "epubjs_cfi", "value": "epubcfi(/6/8!/4/2)"},
            },
            headers=auth_header(reader),
        )
        assert r.status_code == 200, r.text

        r = await c.delete(f"/api/users/{reader.id}", headers=auth_header(admin))
        assert r.status_code == 200, r.text

    async with async_session() as s:
        assert (await s.execute(
            select(User).where(User.id == reader.id)
        )).scalar_one_or_none() is None

        bookmark_ids = (await s.execute(
            select(Bookmark.id).where(Bookmark.user_id == reader.id)
        )).scalars().all()
        assert bookmark_ids == []

        assert (await s.execute(
            select(UserProgress.id).where(UserProgress.user_id == reader.id)
        )).scalars().all() == []

        # The bookmark's own children go with it, so nothing is left pointing
        # at a bookmark that no longer exists either.
        assert (await s.execute(select(BookmarkLog.id))).scalars().all() == []
        assert (await s.execute(select(PositionHint.id))).scalars().all() == []

        # History outlives the account: audit_logs.user_id is SET NULL.
        actions = (await s.execute(
            select(AuditLog.action).where(AuditLog.target_user_id == reader.id)
        )).scalars().all()
        assert "user_deleted" in actions
