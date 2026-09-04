"""
One password policy, enforced server-side (issue #205).

Every schema that accepts a *new* password shares the same bounds — 8..128
characters — so the web form, the Android sheet and the API cannot disagree
about what is acceptable. `UserLogin.password` is deliberately unbounded: an
account created under the old 6-character floor must still be able to sign in
and change its password, and 422-ing a login would lock those users out.
"""

import pytest
from pydantic import ValidationError

from schemas import (
    PASSWORD_MAX_LENGTH,
    PASSWORD_MIN_LENGTH,
    PASSWORD_POLICY_MESSAGE,
    PasswordChange,
    UserCreate,
    UserCreateAdmin,
    UserLogin,
    UserPasswordReset,
)
from database import async_session
from models.user import User
from routers import users
from sqlalchemy import select


def _too_short():
    return "a" * (PASSWORD_MIN_LENGTH - 1)


def _at_min():
    return "a" * PASSWORD_MIN_LENGTH


def _at_max():
    return "a" * PASSWORD_MAX_LENGTH


def _too_long():
    return "a" * (PASSWORD_MAX_LENGTH + 1)


def test_the_policy_is_eight_to_one_hundred_twenty_eight():
    assert (PASSWORD_MIN_LENGTH, PASSWORD_MAX_LENGTH) == (8, 128)


def test_the_policy_message_names_both_bounds():
    # The clients mirror this string; if the numbers move, the message moves.
    assert "8" in PASSWORD_POLICY_MESSAGE and "128" in PASSWORD_POLICY_MESSAGE


def _build(schema, password):
    if schema is UserCreate:
        return schema(username="someone", email="someone@example.com", password=password)
    if schema is UserCreateAdmin:
        return schema(username="someone", email="someone@example.com",
                      password=password, role="user")
    if schema is PasswordChange:
        return schema(old_password="whatever", new_password=password)
    return schema(new_password=password)


NEW_PASSWORD_SCHEMAS = [UserCreate, UserCreateAdmin, PasswordChange, UserPasswordReset]


@pytest.mark.parametrize("schema", NEW_PASSWORD_SCHEMAS)
def test_a_password_one_char_under_the_floor_is_rejected(schema):
    with pytest.raises(ValidationError):
        _build(schema, _too_short())


@pytest.mark.parametrize("schema", NEW_PASSWORD_SCHEMAS)
def test_a_password_exactly_at_the_floor_is_accepted(schema):
    assert _build(schema, _at_min()) is not None


@pytest.mark.parametrize("schema", NEW_PASSWORD_SCHEMAS)
def test_a_password_exactly_at_the_ceiling_is_accepted(schema):
    assert _build(schema, _at_max()) is not None


@pytest.mark.parametrize("schema", NEW_PASSWORD_SCHEMAS)
def test_a_password_one_char_over_the_ceiling_is_rejected(schema):
    with pytest.raises(ValidationError):
        _build(schema, _too_long())


def test_login_leaves_the_password_unconstrained():
    # A legacy 6-character password predates the policy; refusing it here would
    # lock the account out of the very endpoint that could fix it.
    assert UserLogin(username="someone", password="abc123").password == "abc123"
    assert UserLogin(username="someone", password="x" * 500).password == "x" * 500


# ---------------------------------------------------------------------------
# End to end through the routers
# ---------------------------------------------------------------------------

async def test_register_rejects_a_short_password(client):
    r = await client.post("/api/auth/register", json={
        "username": "shorty", "email": "shorty@example.com",
        "password": _too_short(),
    })
    assert r.status_code == 422


async def test_register_accepts_a_password_at_the_floor(client, monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "allow_public_registration", True, raising=False)
    r = await client.post("/api/auth/register", json={
        "username": "longenough", "email": "longenough@example.com",
        "password": _at_min(),
    })
    assert r.status_code in (200, 201), r.text


async def test_a_legacy_short_password_can_still_log_in(client, make_user):
    # Seeded straight into the DB, as an account created under the old floor is.
    await make_user(username="legacy", password="abc123")
    r = await client.post("/api/auth/login",
                          json={"username": "legacy", "password": "abc123"})
    assert r.status_code == 200


async def test_change_password_rejects_a_short_new_password(client, make_user, auth_header):
    user = await make_user(username="changer", password="oldpassword1")
    r = await client.post(
        "/api/auth/change-password",
        json={"old_password": "oldpassword1", "new_password": _too_short()},
        headers=auth_header(user),
    )
    assert r.status_code == 422


async def test_change_password_accepts_a_new_password_at_the_floor(client, make_user, auth_header):
    user = await make_user(username="changer2", password="oldpassword1")
    r = await client.post(
        "/api/auth/change-password",
        json={"old_password": "oldpassword1", "new_password": _at_min()},
        headers=auth_header(user),
    )
    assert r.status_code == 200, r.text


async def test_admin_reset_rejects_a_short_password(make_client, make_user, auth_header):
    admin = await make_user(username="policyadmin", role="admin")
    victim = await make_user(username="policyvictim", role="user")
    async with make_client(users.router) as c:
        r = await c.post(f"/api/users/{victim.id}/reset-password",
                         json={"new_password": _too_short()},
                         headers=auth_header(admin))
    assert r.status_code == 422


async def test_admin_create_user_rejects_an_over_long_password(make_client, make_user, auth_header):
    admin = await make_user(username="policyadmin2", role="admin")
    async with make_client(users.router) as c:
        r = await c.post("/api/users/", json={
            "username": "newperson", "email": "newperson@example.com",
            "password": _too_long(), "role": "user",
        }, headers=auth_header(admin))
    assert r.status_code == 422
    async with async_session() as s:
        assert (await s.execute(
            select(User).where(User.username == "newperson")
        )).scalar_one_or_none() is None
