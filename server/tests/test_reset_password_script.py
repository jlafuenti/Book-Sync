"""
Break-glass password reset (issue #204).

The one path that exists when nobody can reach the admin UI — the sole
superadmin has forgotten their password, and `bootstrap_superadmin` only mints
an account on an empty database. It runs inside the server container against the
configured `DATABASE_URL`, so it needs shell access to the host: that *is* the
authorisation, and it is why the reset is a script rather than an endpoint.
"""

import pytest
from sqlalchemy import select

from database import async_session
from models.audit_log import AuditLog
from models.refresh_token import RefreshToken
from models.user import User
from routers.auth import verify_password
from scripts.reset_password import UnknownUser, main, reset_password


async def _fresh(user_id):
    async with async_session() as s:
        return (await s.execute(select(User).where(User.id == user_id))).scalar_one()


async def test_the_old_password_stops_working_and_the_printed_one_works(make_user):
    user = await make_user(username="lockedout", password="oldpassword1",
                           role="superadmin")

    new_password = await reset_password("lockedout")

    after = await _fresh(user.id)
    assert not verify_password("oldpassword1", after.hashed_password)
    assert verify_password(new_password, after.hashed_password)


async def test_the_generated_password_satisfies_the_password_policy(make_user):
    from schemas import PASSWORD_MAX_LENGTH, PASSWORD_MIN_LENGTH

    await make_user(username="policyuser", password="oldpassword1")
    new_password = await reset_password("policyuser")
    assert PASSWORD_MIN_LENGTH <= len(new_password) <= PASSWORD_MAX_LENGTH


async def test_the_user_must_choose_their_own_password_at_next_login(make_user):
    user = await make_user(username="mustreset", password="oldpassword1")
    await reset_password("mustreset")
    assert (await _fresh(user.id)).must_reset_password is True


async def test_every_existing_token_is_invalidated(make_user):
    user = await make_user(username="tokenbump", password="oldpassword1")
    before = (await _fresh(user.id)).token_version
    await reset_password("tokenbump")
    assert (await _fresh(user.id)).token_version == before + 1


async def test_live_sessions_are_revoked(make_user):
    user = await make_user(username="sessionuser", password="oldpassword1")
    async with async_session() as s:
        s.add(RefreshToken(user_id=user.id, jti="jti-for-break-glass-test",
                           device_id="a-device"))
        await s.commit()

    await reset_password("sessionuser")

    async with async_session() as s:
        row = (await s.execute(
            select(RefreshToken).where(RefreshToken.user_id == user.id)
        )).scalar_one()
        assert row.revoked_at is not None


async def test_a_deactivated_account_is_reactivated(make_user):
    # `is_active=False` is the database-level lockout: an account that was
    # deactivated cannot log in whatever password it holds, so a break-glass
    # reset that left it off would hand out a password that still doesn't work.
    user = await make_user(username="deactivated", password="oldpassword1",
                           is_active=False)
    await reset_password("deactivated")
    assert (await _fresh(user.id)).is_active is True


async def test_an_audit_row_records_the_reset(make_user):
    user = await make_user(username="audited", password="oldpassword1")
    await reset_password("audited")

    async with async_session() as s:
        rows = (await s.execute(
            select(AuditLog).where(AuditLog.action == "password_reset")
        )).scalars().all()
    assert len(rows) == 1
    assert rows[0].target_user_id == user.id
    # No admin performed it, so there is no acting user id to record.
    assert rows[0].user_id is None
    assert "break-glass" in (rows[0].details or "")


async def test_the_new_password_is_never_written_to_the_audit_row(make_user):
    await make_user(username="nosecret", password="oldpassword1")
    new_password = await reset_password("nosecret")

    async with async_session() as s:
        rows = (await s.execute(select(AuditLog))).scalars().all()
    assert all(new_password not in (r.details or "") for r in rows)


async def test_an_unknown_username_raises_and_touches_nothing(make_user):
    user = await make_user(username="present", password="oldpassword1")
    before = (await _fresh(user.id)).hashed_password

    with pytest.raises(UnknownUser):
        await reset_password("absent")

    assert (await _fresh(user.id)).hashed_password == before
    async with async_session() as s:
        assert (await s.execute(select(AuditLog))).scalars().all() == []


async def test_an_explicit_password_is_used_instead_of_a_generated_one(make_user):
    user = await make_user(username="chosen", password="oldpassword1")
    returned = await reset_password("chosen", new_password="a-chosen-passphrase")
    assert returned == "a-chosen-passphrase"
    assert verify_password("a-chosen-passphrase",
                           (await _fresh(user.id)).hashed_password)


async def test_an_explicit_password_below_the_policy_is_refused(make_user):
    user = await make_user(username="tooshort", password="oldpassword1")
    before = (await _fresh(user.id)).hashed_password

    with pytest.raises(ValueError):
        await reset_password("tooshort", new_password="short")

    assert (await _fresh(user.id)).hashed_password == before


# ---------------------------------------------------------------------------
# CLI wrapper
# ---------------------------------------------------------------------------

async def test_main_prints_the_new_password_and_exits_zero(make_user, capsys):
    await make_user(username="cliuser", password="oldpassword1")

    code = await main(["cliuser"])

    assert code == 0
    printed = capsys.readouterr().out
    async with async_session() as s:
        user = (await s.execute(
            select(User).where(User.username == "cliuser")
        )).scalar_one()
    # The printed line has to actually be the password that now works — the
    # operator has nothing else to go on.
    candidate = [w for line in printed.splitlines() for w in line.split()
                 if verify_password(w, user.hashed_password)]
    assert candidate, printed


async def test_main_exits_nonzero_for_an_unknown_username(capsys):
    code = await main(["nobody-by-that-name"])
    assert code != 0
    assert "nobody-by-that-name" in capsys.readouterr().err
