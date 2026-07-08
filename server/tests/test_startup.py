"""
Startup-time hardening checks (issues #35, #37): refusing to boot with
default secrets in prod, and never seeding a guessable superadmin password.
"""

import pytest
from sqlalchemy import select, func

from config import settings, check_jwt_secret
from database import bootstrap_superadmin
from models.user import User
from routers.auth import verify_password


def test_check_jwt_secret_raises_on_default_in_prod(monkeypatch):
    monkeypatch.setattr(settings, "jwt_secret_key", "dev-secret-change-me")
    monkeypatch.setattr(settings, "app_env", "prod")
    with pytest.raises(RuntimeError):
        check_jwt_secret(settings)


def test_check_jwt_secret_warns_on_default_in_dev(monkeypatch):
    monkeypatch.setattr(settings, "jwt_secret_key", "dev-secret-change-me")
    monkeypatch.setattr(settings, "app_env", "dev")
    check_jwt_secret(settings)  # should not raise


def test_check_jwt_secret_allows_real_secret_in_prod(monkeypatch):
    monkeypatch.setattr(settings, "jwt_secret_key", "a-real-randomly-generated-secret")
    monkeypatch.setattr(settings, "app_env", "prod")
    check_jwt_secret(settings)  # should not raise


async def test_bootstrap_superadmin_password_is_not_admin(db):
    await bootstrap_superadmin()
    result = await db.execute(select(User).where(User.username == "admin"))
    user = result.scalar_one()
    assert verify_password("admin", user.hashed_password) is False
    assert user.must_reset_password is True
