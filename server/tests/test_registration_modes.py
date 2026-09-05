"""Registration modes, invites, and the non-enumerating register endpoint (#210).

Self-registration used to be on by default, unbounded, and it answered
``409 Username already taken`` / ``409 Email already registered`` — a free
oracle for which accounts exist on a host that is about to be public.

Three modes replace the single on/off flag:

``open``     anyone may file a request; an admin approves it (the old behaviour)
``invite``   the request form only works with a code an admin generated
``closed``   no self-registration at all; admins create the accounts

and the register endpoint answers the *same* 201 whatever it decides, so nothing
about the outcome leaks back to the caller.
"""

import asyncio
import logging
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from config import settings as app_settings
from models.audit_log import AuditLog
from models.invite import Invite
from models.settings import SystemSetting
from models.user import User
from routers import auth as auth_router
from services import invites as invite_service
from services import registration
from utils import utcnow

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

async def _set_setting(db, key: str, value):
    row = (
        await db.execute(select(SystemSetting).where(SystemSetting.key == key))
    ).scalar_one_or_none()
    if row is None:
        db.add(SystemSetting(key=key, value=str(value)))
    else:
        row.value = str(value)
    await db.commit()
    registration.forget_cached_settings()


async def _set_mode(db, mode: str):
    await _set_setting(db, "registration_mode", mode)


def _body(username="newcomer", email=None, password="password123", code=None):
    body = {
        "username": username,
        "email": email or f"{username}@example.com",
        "password": password,
    }
    if code is not None:
        body["invite_code"] = code
    return body


async def _user_count(db) -> int:
    return (await db.execute(select(func.count(User.id)))).scalar()


@pytest.fixture(autouse=True)
def _reset_registration_state():
    """The per-IP bucket and the settings cache are process-global."""
    from rate_limit import registration_attempts

    registration_attempts.reset()
    registration.forget_cached_settings()
    yield
    registration_attempts.reset()
    registration.forget_cached_settings()


# ---------------------------------------------------------------------------
# 1. The setting and how a database gets its first value
# ---------------------------------------------------------------------------

async def test_fresh_database_seeds_invite(db):
    """No users yet — a brand new install starts closed to strangers."""
    await registration.seed_mode(db)
    assert await registration.stored_mode(db) == registration.MODE_INVITE


async def test_database_with_users_seeds_open(db, make_user):
    """An upgrade must not change what the operator's server already does."""
    await make_user(username="incumbent")
    await registration.seed_mode(db)
    assert await registration.stored_mode(db) == registration.MODE_OPEN


async def test_seed_never_overwrites_an_existing_choice(db, make_user):
    await _set_mode(db, registration.MODE_CLOSED)
    await make_user(username="incumbent")
    await registration.seed_mode(db)
    assert await registration.stored_mode(db) == registration.MODE_CLOSED


async def test_mode_endpoint_answers_without_a_token(client, db):
    await _set_mode(db, registration.MODE_INVITE)
    r = await client.get("/api/auth/registration")
    assert r.status_code == 200
    assert r.json() == {"mode": "invite"}


async def test_mode_endpoint_falls_back_to_the_default(client):
    """Nothing seeded yet (a DB mid-upgrade): the endpoint still answers."""
    r = await client.get("/api/auth/registration")
    assert r.status_code == 200
    assert r.json()["mode"] in registration.MODES


async def test_plain_user_may_read_registration_mode_from_settings(
    make_client, make_user, auth_header
):
    """The allow-list below admin (#263) has to carry it — the login screens
    need the mode before anyone has signed in."""
    from routers import settings as settings_router

    user = await make_user(username="plain", role="user")
    async with make_client(settings_router.router) as c:
        body = (await c.get("/api/settings/", headers=auth_header(user))).json()
    assert "registration_mode" in body


async def test_settings_put_rejects_an_unknown_mode(make_client, make_user, auth_header):
    from routers import settings as settings_router

    admin = await make_user(username="boss", role="admin")
    async with make_client(settings_router.router) as c:
        r = await c.put(
            "/api/settings/",
            json={"registration_mode": "everyone"},
            headers=auth_header(admin),
        )
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# 2. Open mode — the old behaviour, minus the oracle
# ---------------------------------------------------------------------------

async def test_open_mode_accepts_a_request(client, db):
    await _set_mode(db, registration.MODE_OPEN)
    r = await client.post("/api/auth/register", json=_body())
    assert r.status_code == 201
    assert await _user_count(db) == 1


async def test_open_mode_ignores_an_invite_code(client, db):
    await _set_mode(db, registration.MODE_OPEN)
    r = await client.post("/api/auth/register", json=_body(code="nonsense"))
    assert r.status_code == 201
    assert await _user_count(db) == 1


# ---------------------------------------------------------------------------
# 3. No enumeration
# ---------------------------------------------------------------------------

async def test_duplicate_username_is_indistinguishable_from_success(client, db):
    await _set_mode(db, registration.MODE_OPEN)
    first = await client.post("/api/auth/register", json=_body(username="twice"))
    second = await client.post(
        "/api/auth/register",
        json=_body(username="twice", email="other@example.com"),
    )
    assert first.status_code == second.status_code == 201
    assert first.json() == second.json()
    assert await _user_count(db) == 1


async def test_duplicate_email_is_indistinguishable_from_success(client, db):
    await _set_mode(db, registration.MODE_OPEN)
    first = await client.post(
        "/api/auth/register", json=_body(username="one", email="same@example.com")
    )
    second = await client.post(
        "/api/auth/register", json=_body(username="two", email="same@example.com")
    )
    assert first.status_code == second.status_code == 201
    assert first.json() == second.json()
    assert await _user_count(db) == 1


async def test_a_duplicate_still_pays_for_the_password_hash(client, db, monkeypatch):
    """Timing must not answer the question the status code no longer does.

    bcrypt dominates the cost of this endpoint, so skipping it on the duplicate
    path is a stopwatch oracle even with identical bodies.
    """
    calls = []
    real = auth_router.hash_password
    monkeypatch.setattr(
        auth_router, "hash_password", lambda pw: (calls.append(pw), real(pw))[1]
    )

    await _set_mode(db, registration.MODE_OPEN)
    await client.post("/api/auth/register", json=_body(username="dup"))
    assert len(calls) == 1
    await client.post("/api/auth/register", json=_body(username="dup"))
    assert len(calls) == 2


async def test_a_refused_duplicate_is_audited(client, db):
    await _set_mode(db, registration.MODE_OPEN)
    await client.post("/api/auth/register", json=_body(username="dup"))
    await client.post("/api/auth/register", json=_body(username="dup"))
    actions = (await db.execute(select(AuditLog.action))).scalars().all()
    assert "register_duplicate" in actions


# ---------------------------------------------------------------------------
# 4. Invite mode
# ---------------------------------------------------------------------------

async def _make_invite(db, admin=None, days=None):
    invite, code = await invite_service.create_invite(
        db, created_by_user_id=admin.id if admin else None, expiry_days=days
    )
    await db.commit()
    return invite, code


async def test_invite_mode_refuses_a_missing_code_neutrally(client, db):
    await _set_mode(db, registration.MODE_OPEN)
    accepted = await client.post("/api/auth/register", json=_body(username="opener"))

    await _set_mode(db, registration.MODE_INVITE)
    refused = await client.post("/api/auth/register", json=_body(username="stranger"))

    assert refused.status_code == accepted.status_code == 201
    assert refused.json() == accepted.json()
    assert await _user_count(db) == 1  # only the open-mode one


async def test_invite_mode_refuses_a_bad_code_neutrally(client, db):
    await _set_mode(db, registration.MODE_INVITE)
    r = await client.post("/api/auth/register", json=_body(code="not-a-real-code"))
    assert r.status_code == 201
    assert await _user_count(db) == 0


async def test_a_good_code_registers_and_is_consumed(client, db):
    await _set_mode(db, registration.MODE_INVITE)
    invite, code = await _make_invite(db)

    r = await client.post("/api/auth/register", json=_body(username="guest", code=code))
    assert r.status_code == 201

    user = (
        await db.execute(select(User).where(User.username == "guest"))
    ).scalar_one()
    assert user.is_active is False  # still needs an admin

    await db.refresh(invite)
    assert invite.used_at is not None
    assert invite.used_by_user_id == user.id


async def test_a_code_cannot_be_used_twice(client, db):
    await _set_mode(db, registration.MODE_INVITE)
    _, code = await _make_invite(db)

    first = await client.post(
        "/api/auth/register", json=_body(username="first", code=code)
    )
    second = await client.post(
        "/api/auth/register", json=_body(username="second", code=code)
    )
    assert first.status_code == second.status_code == 201
    assert first.json() == second.json()
    assert await _user_count(db) == 1


async def test_consumption_is_atomic_under_concurrency(db):
    """Two racing consumers, one winner. The claim is a single conditional
    UPDATE, so there is no read-then-write window to lose."""
    _, code = await _make_invite(db)

    async def claim():
        from database import async_session

        async with async_session() as session:
            claimed = await invite_service.consume_invite(session, code)
            await session.commit()
            return claimed

    results = await asyncio.gather(claim(), claim())
    assert sorted(r is None for r in results) == [False, True]


async def test_an_expired_code_is_refused(client, db):
    await _set_mode(db, registration.MODE_INVITE)
    invite, code = await _make_invite(db)
    invite.expires_at = utcnow() - timedelta(minutes=1)
    await db.commit()

    r = await client.post("/api/auth/register", json=_body(code=code))
    assert r.status_code == 201
    assert await _user_count(db) == 0


async def test_default_expiry_is_seven_days(db):
    invite, _ = await _make_invite(db)
    assert 6 < (invite.expires_at - utcnow()).total_seconds() / 86400 <= 7


async def test_invite_audit_rows_never_carry_the_code(client, db, make_user):
    admin = await make_user(username="boss", role="admin")
    await _set_mode(db, registration.MODE_INVITE)
    invite, code = await _make_invite(db, admin=admin)
    await auth_router.log_audit(db, "invite_created", user_id=admin.id,
                                details=f"Invite {invite.id} created")
    await db.commit()

    await client.post("/api/auth/register", json=_body(code=code))

    rows = (await db.execute(select(AuditLog))).scalars().all()
    actions = {r.action for r in rows}
    assert "invite_consumed" in actions
    for row in rows:
        assert code not in (row.details or "")


# ---------------------------------------------------------------------------
# 5. Closed mode
# ---------------------------------------------------------------------------

async def test_closed_mode_refuses(client, db):
    await _set_mode(db, registration.MODE_CLOSED)
    r = await client.post("/api/auth/register", json=_body())
    assert r.status_code == 403
    assert await _user_count(db) == 0


async def test_env_kill_switch_forces_closed(client, db, monkeypatch):
    """``ALLOW_PUBLIC_REGISTRATION=false`` was the old off switch; an operator
    who set it keeps a closed server whatever the row says."""
    await _set_mode(db, registration.MODE_OPEN)
    monkeypatch.setattr(app_settings, "allow_public_registration", False)
    r = await client.post("/api/auth/register", json=_body())
    assert r.status_code == 403

    mode = await client.get("/api/auth/registration")
    assert mode.json()["mode"] == "closed"


# ---------------------------------------------------------------------------
# 6. Bounded: the pending cap and the per-IP bucket
# ---------------------------------------------------------------------------

async def test_pending_cap_refuses_neutrally_and_stores_nothing(client, db, caplog):
    await _set_mode(db, registration.MODE_OPEN)
    await _set_setting(db, "registration_pending_max", 2)

    accepted = [
        await client.post("/api/auth/register", json=_body(username=f"pending{i}"))
        for i in range(2)
    ]
    with caplog.at_level(logging.WARNING):
        refused = await client.post("/api/auth/register", json=_body(username="pending2"))

    assert refused.status_code == 201
    assert refused.json() == accepted[0].json()
    assert await _user_count(db) == 2
    assert any("pending" in r.message.lower() for r in caplog.records)


async def test_active_users_do_not_count_against_the_cap(client, db, make_user):
    await _set_mode(db, registration.MODE_OPEN)
    await _set_setting(db, "registration_pending_max", 1)
    for i in range(3):
        await make_user(username=f"member{i}", is_active=True)

    r = await client.post("/api/auth/register", json=_body(username="hopeful"))
    assert r.status_code == 201
    assert (
        await db.execute(select(func.count(User.id)).where(User.is_active.is_(False)))
    ).scalar() == 1


async def test_the_cap_does_not_spend_an_invite(client, db):
    await _set_mode(db, registration.MODE_INVITE)
    await _set_setting(db, "registration_pending_max", 0)
    invite, code = await _make_invite(db)

    r = await client.post("/api/auth/register", json=_body(code=code))
    assert r.status_code == 201
    await db.refresh(invite)
    assert invite.used_at is None


async def test_register_is_rate_limited_per_ip(client, db):
    await _set_mode(db, registration.MODE_OPEN)
    await _set_setting(db, "registration_rate_limit", 2)

    ok = [
        await client.post("/api/auth/register", json=_body(username=f"rated{i}"))
        for i in range(2)
    ]
    assert [r.status_code for r in ok] == [201, 201]

    limited = await client.post("/api/auth/register", json=_body(username="rated2"))
    assert limited.status_code == 429
    assert limited.headers["Retry-After"]
    assert await _user_count(db) == 2


async def test_rate_limit_default_is_five_per_ten_minutes():
    assert registration.DEFAULTS["registration_rate_limit"] == 5
    assert registration.DEFAULTS["registration_rate_window_seconds"] == 600
    assert registration.DEFAULTS["registration_pending_max"] == 20


# ---------------------------------------------------------------------------
# 7. Admin invite endpoints
# ---------------------------------------------------------------------------

async def test_create_invite_requires_admin(client, make_user, auth_header):
    user = await make_user(username="plain", role="user")
    assert (await client.post("/api/auth/invites")).status_code == 401
    r = await client.post("/api/auth/invites", headers=auth_header(user))
    assert r.status_code == 403


async def test_create_invite_returns_the_code_once(client, db, make_user, auth_header):
    admin = await make_user(username="boss", role="admin")
    r = await client.post("/api/auth/invites", headers=auth_header(admin))
    assert r.status_code == 201
    created = r.json()
    assert created["code"]

    listed = await client.get("/api/auth/invites", headers=auth_header(admin))
    assert listed.status_code == 200
    rows = listed.json()
    assert len(rows) == 1
    assert "code" not in rows[0]
    assert created["code"] not in listed.text

    stored = (await db.execute(select(Invite))).scalars().all()
    assert len(stored) == 1
    assert created["code"] not in (stored[0].code_hash or "")


async def test_create_invite_is_audited_without_the_code(
    client, db, make_user, auth_header
):
    admin = await make_user(username="boss", role="admin")
    code = (
        await client.post("/api/auth/invites", headers=auth_header(admin))
    ).json()["code"]

    rows = (
        await db.execute(select(AuditLog).where(AuditLog.action == "invite_created"))
    ).scalars().all()
    assert rows
    assert all(code not in (r.details or "") for r in rows)


async def test_revoking_an_invite_stops_it_working(client, db, make_user, auth_header):
    admin = await make_user(username="boss", role="admin")
    created = (
        await client.post("/api/auth/invites", headers=auth_header(admin))
    ).json()

    await _set_mode(db, registration.MODE_INVITE)
    r = await client.delete(
        f"/api/auth/invites/{created['id']}", headers=auth_header(admin)
    )
    assert r.status_code == 204

    used = await client.post(
        "/api/auth/register", json=_body(code=created["code"])
    )
    assert used.status_code == 201
    assert await _user_count(db) == 1  # the admin only


async def test_revoking_an_unknown_invite_is_404(client, make_user, auth_header):
    admin = await make_user(username="boss", role="admin")
    r = await client.delete("/api/auth/invites/4242", headers=auth_header(admin))
    assert r.status_code == 404


async def test_invite_list_reports_status(client, db, make_user, auth_header):
    admin = await make_user(username="boss", role="admin")
    created = (
        await client.post("/api/auth/invites", headers=auth_header(admin))
    ).json()
    await _set_mode(db, registration.MODE_INVITE)
    await client.post("/api/auth/register", json=_body(username="guest", code=created["code"]))

    rows = (await client.get("/api/auth/invites", headers=auth_header(admin))).json()
    assert rows[0]["status"] == "used"
    assert rows[0]["used_by"] == "guest"


# ---------------------------------------------------------------------------
# 8. Settings validation and the coercion of stored rows
#
# Every one of these values arrives as text from a `system_settings` row that an
# admin — or an earlier version of this app — wrote, so each has to survive
# being wrong without taking registration down with it.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "key",
    [
        "invite_expiry_days",
        "registration_pending_max",
        "registration_rate_limit",
        "registration_rate_window_seconds",
    ],
)
def test_validate_settings_rejects_a_non_number(key):
    with pytest.raises(ValueError):
        registration.validate_settings({key: "soon"})


def test_validate_settings_rejects_a_limit_below_one():
    with pytest.raises(ValueError):
        registration.validate_settings({"registration_rate_limit": 0})


def test_validate_settings_allows_a_pending_cap_of_zero():
    """Zero is a real choice: hold the queue shut while still being `open` for
    an invite the admin is about to issue."""
    body = {"registration_pending_max": "0"}
    registration.validate_settings(body)
    assert body["registration_pending_max"] == 0


def test_validate_settings_normalises_the_mode():
    body = {"registration_mode": " INVITE "}
    registration.validate_settings(body)
    assert body["registration_mode"] == registration.MODE_INVITE


async def test_a_garbage_mode_row_reads_as_the_default(db):
    await _set_setting(db, "registration_mode", "sideways")
    assert await registration.stored_mode(db) == registration.DEFAULTS["registration_mode"]


async def test_a_garbage_number_row_reads_as_its_default(db):
    await _set_setting(db, "registration_pending_max", "lots")
    values = await registration.load(db)
    assert values["registration_pending_max"] == (
        registration.DEFAULTS["registration_pending_max"]
    )


async def test_seed_replaces_a_value_it_does_not_recognise(db, make_user):
    await _set_setting(db, "registration_mode", "sideways")
    await make_user(username="incumbent")
    await registration.seed_mode(db)
    assert await registration.stored_mode(db) == registration.MODE_OPEN


async def test_invite_status_reports_expiry(db):
    invite, _ = await _make_invite(db)
    assert invite_service.status_of(invite) == "active"
    invite.expires_at = utcnow() - timedelta(seconds=1)
    assert invite_service.status_of(invite) == "expired"
    assert "Invite" in repr(invite)
