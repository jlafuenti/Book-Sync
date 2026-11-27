"""
The ``demo`` role: an account that uses the app but cannot change itself (#963).

A public demo server shares one account whose password is published. Any
visitor who changes that password, deletes the account or signs it out
everywhere breaks the demo for every other visitor until an operator repairs
it. A ``demo`` user can browse, read, listen and save positions like a ``user``;
what it cannot do is change its own user record.

Pinned here:

* every self-service account mutation answers 403 ``demo_account_locked`` for a
  demo caller and still works for a ``user`` (the refusal is role-scoped, not a
  regression for everyone);
* the role clears every bar ``user`` clears, and nothing above it;
* plain ``logout`` still works, because it ends only the caller's own session;
* ``PUT /me`` keeps the walkthrough stamp but leaves the shared theme alone;
* ``must_reset_password`` is never set on a demo account: change-password is
  refused for it, so the forced-reset gate would be a dead end that locks
  every visitor out;
* the repair path, an admin resetting the password, still works.
"""

import pytest
from sqlalchemy import select

from database import async_session
from models.user import ROLE_HIERARCHY, VALID_ROLES, User
from routers import auth, users

DEMO_LOCKED = "demo_account_locked"


@pytest.fixture(autouse=True)
def _reset_password_failure_tracker():
    from rate_limit import failed_password_changes

    failed_password_changes.reset()
    yield
    failed_password_changes.reset()


async def _fresh(user_id: int) -> User:
    async with async_session() as s:
        return (await s.execute(select(User).where(User.id == user_id))).scalar_one()


# ---------------------------------------------------------------------------
# Role plumbing
# ---------------------------------------------------------------------------

def test_demo_is_a_valid_role():
    assert "demo" in VALID_ROLES


def test_demo_clears_what_user_clears_and_nothing_more():
    # Scored with `user`, not below it: every endpoint open to a signed-in
    # reader must stay open to the demo account, and an unknown role's 0 is
    # reserved for "clears nothing".
    assert ROLE_HIERARCHY["demo"] == ROLE_HIERARCHY["user"]
    demo = User(username="d", email="d@example.com", hashed_password="x", role="demo")
    assert demo.has_role("user")
    assert not demo.has_role("editor")
    assert demo.is_demo
    assert not User(username="u", email="u@example.com", hashed_password="x",
                    role="user").is_demo


async def test_demo_can_read_its_own_profile(client, make_user, auth_header):
    demo = await make_user(username="demo", role="demo")
    r = await client.get("/api/auth/me", headers=auth_header(demo))
    assert r.status_code == 200
    assert r.json()["role"] == "demo"


async def test_demo_is_refused_by_editor_routes(client, make_user, auth_header):
    from fastapi import Depends, FastAPI
    from httpx import ASGITransport, AsyncClient

    demo = await make_user(username="demo", role="demo")
    app = FastAPI()

    @app.get("/editor-only")
    async def _editor_only(_=Depends(auth.get_editor_user)):
        return {"ok": True}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/editor-only", headers=auth_header(demo))
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# Refused self-service mutations
# ---------------------------------------------------------------------------

async def test_demo_cannot_change_its_password(client, make_user, auth_header):
    demo = await make_user(username="demo", role="demo", password="published1")
    r = await client.post(
        "/api/auth/change-password", headers=auth_header(demo),
        json={"old_password": "published1", "new_password": "hijacked99"},
    )
    assert r.status_code == 403
    assert r.json()["detail"] == DEMO_LOCKED
    after = await _fresh(demo.id)
    assert auth.verify_password("published1", after.hashed_password)
    assert after.token_version == demo.token_version


async def test_demo_refusal_does_not_feed_the_password_oracle(client, make_user, auth_header):
    """Refused before the password is checked, so guesses against the demo
    account never count toward the per-user lockout that the account's other
    visitors share."""
    from rate_limit import failed_password_changes

    demo = await make_user(username="demo", role="demo", password="published1")
    for _ in range(3):
        r = await client.post(
            "/api/auth/change-password", headers=auth_header(demo),
            json={"old_password": "wrongwrong", "new_password": "hijacked99"},
        )
        assert r.status_code == 403
    assert failed_password_changes.retry_after(str(demo.id)) is None


async def test_demo_cannot_delete_itself(client, make_user, auth_header):
    demo = await make_user(username="demo", role="demo", password="published1")
    r = await client.request(
        "DELETE", "/api/auth/me", headers=auth_header(demo),
        json={"password": "published1"},
    )
    assert r.status_code == 403
    assert r.json()["detail"] == DEMO_LOCKED
    assert await _fresh(demo.id) is not None


async def test_demo_cannot_sign_out_everywhere(client, make_user, auth_header):
    demo = await make_user(username="demo", role="demo")
    r = await client.post("/api/auth/logout-all", headers=auth_header(demo))
    assert r.status_code == 403
    assert r.json()["detail"] == DEMO_LOCKED
    assert (await _fresh(demo.id)).token_version == demo.token_version


async def test_demo_can_still_sign_out_this_device(client, make_user, auth_header):
    demo = await make_user(username="demo", role="demo")
    r = await client.post("/api/auth/logout", headers=auth_header(demo))
    assert r.status_code == 200


# The same calls keep working for an ordinary user: the lock is the role's.

async def test_user_can_still_change_password(client, make_user, auth_header):
    user = await make_user(username="u", role="user", password="original1")
    r = await client.post(
        "/api/auth/change-password", headers=auth_header(user),
        json={"old_password": "original1", "new_password": "brandnew99"},
    )
    assert r.status_code == 200


async def test_user_can_still_sign_out_everywhere(client, make_user, auth_header):
    user = await make_user(username="u", role="user")
    r = await client.post("/api/auth/logout-all", headers=auth_header(user))
    assert r.status_code == 200


async def test_user_can_still_delete_itself(client, make_user, auth_header):
    user = await make_user(username="u", role="user", password="original1")
    r = await client.request(
        "DELETE", "/api/auth/me", headers=auth_header(user),
        json={"password": "original1"},
    )
    assert r.status_code == 204


# ---------------------------------------------------------------------------
# PUT /me: walkthrough stamp kept, shared theme left alone
# ---------------------------------------------------------------------------

async def test_demo_theme_change_is_not_persisted(client, make_user, auth_header):
    """Both clients apply the theme locally and sync it best-effort, so the
    request succeeds (no error for the visitor) but the account's stored theme,
    which is the next visitor's default, does not move."""
    demo = await make_user(username="demo", role="demo")
    r = await client.put("/api/auth/me", headers=auth_header(demo), json={"theme": "ember"})
    assert r.status_code == 200
    assert r.json()["theme"] == "blueprint"
    assert (await _fresh(demo.id)).theme == "blueprint"


async def test_demo_theme_is_still_validated(client, make_user, auth_header):
    demo = await make_user(username="demo", role="demo")
    r = await client.put("/api/auth/me", headers=auth_header(demo), json={"theme": "nope"})
    assert r.status_code == 400


async def test_demo_can_stamp_the_walkthrough_offer(client, make_user, auth_header):
    demo = await make_user(username="demo", role="demo")
    r = await client.put("/api/auth/me", headers=auth_header(demo),
                         json={"web_tour_offered": True})
    assert r.status_code == 200
    assert r.json()["web_tour_offered_at"] is not None


async def test_user_theme_change_still_persists(client, make_user, auth_header):
    user = await make_user(username="u", role="user")
    r = await client.put("/api/auth/me", headers=auth_header(user), json={"theme": "ember"})
    assert r.status_code == 200
    assert (await _fresh(user.id)).theme == "ember"


# ---------------------------------------------------------------------------
# Admin side: assigning the role, and the repair path
# ---------------------------------------------------------------------------

async def test_admin_can_create_a_demo_user_without_a_forced_reset(
    make_client, make_user, auth_header,
):
    admin = await make_user(username="admin1", role="admin")
    async with make_client(users.router) as c:
        r = await c.post("/api/users/", headers=auth_header(admin), json={
            "username": "demo", "email": "demo@example.com",
            "password": "published1", "role": "demo",
        })
    assert r.status_code == 201, r.text
    assert r.json()["role"] == "demo"
    assert r.json()["must_reset_password"] is False


async def test_changing_a_user_to_demo_clears_a_pending_reset(
    make_client, make_user, auth_header,
):
    admin = await make_user(username="admin1", role="admin")
    target = await make_user(username="demo", role="user", must_reset_password=True)
    async with make_client(users.router) as c:
        r = await c.patch(f"/api/users/{target.id}", headers=auth_header(admin),
                          json={"role": "demo"})
    assert r.status_code == 200, r.text
    assert r.json()["role"] == "demo"
    assert (await _fresh(target.id)).must_reset_password is False


async def test_admin_can_reset_the_demo_password_without_a_forced_reset(
    make_client, make_user, auth_header,
):
    admin = await make_user(username="admin1", role="admin")
    demo = await make_user(username="demo", role="demo", password="hijacked99")
    async with make_client(users.router) as c:
        r = await c.post(f"/api/users/{demo.id}/reset-password",
                         headers=auth_header(admin), json={"new_password": "published1"})
    assert r.status_code == 200, r.text
    after = await _fresh(demo.id)
    assert auth.verify_password("published1", after.hashed_password)
    assert after.must_reset_password is False
    # Still a global sign-out: the repair has to evict whoever changed it.
    assert after.token_version == demo.token_version + 1


async def test_cli_reset_of_the_demo_account_does_not_force_a_reset(make_user):
    from scripts.reset_password import reset_password

    demo = await make_user(username="demo", role="demo")
    await reset_password("demo", "published1")
    after = await _fresh(demo.id)
    assert auth.verify_password("published1", after.hashed_password)
    assert after.must_reset_password is False


async def test_registration_cannot_ask_for_the_demo_role(client):
    """`UserCreate` has no role field; registration always creates `user`."""
    from schemas import UserCreate

    assert "role" not in UserCreate.model_fields
