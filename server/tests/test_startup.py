"""
Startup-time hardening checks (issues #35, #37, #72, #73): refusing to boot with
default secrets/credentials/wildcard CORS in prod, and never seeding a
guessable superadmin password.
"""

import pytest
from sqlalchemy import select, func

from config import settings, check_jwt_secret, check_db_credentials, check_cors_origins
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


def test_check_db_credentials_raises_on_default_in_prod(monkeypatch):
    monkeypatch.setattr(settings, "database_url", "postgresql+asyncpg://booksync:booksync@db:5432/booksync")
    monkeypatch.setattr(settings, "app_env", "prod")
    with pytest.raises(RuntimeError):
        check_db_credentials(settings)


def test_check_db_credentials_warns_on_default_in_dev(monkeypatch):
    monkeypatch.setattr(settings, "database_url", "postgresql+asyncpg://booksync:booksync@db:5432/booksync")
    monkeypatch.setattr(settings, "app_env", "dev")
    check_db_credentials(settings)  # should not raise


def test_check_db_credentials_allows_real_value_in_prod(monkeypatch):
    monkeypatch.setattr(settings, "database_url", "postgresql+asyncpg://booksync:a-real-generated-password@db:5432/booksync")
    monkeypatch.setattr(settings, "app_env", "prod")
    check_db_credentials(settings)  # should not raise


def test_check_cors_origins_raises_on_wildcard_in_prod(monkeypatch):
    monkeypatch.setattr(settings, "cors_origins", "*")
    monkeypatch.setattr(settings, "app_env", "prod")
    with pytest.raises(RuntimeError):
        check_cors_origins(settings)


def test_check_cors_origins_warns_on_wildcard_in_dev(monkeypatch):
    monkeypatch.setattr(settings, "cors_origins", "*")
    monkeypatch.setattr(settings, "app_env", "dev")
    check_cors_origins(settings)  # should not raise


def test_check_cors_origins_allows_explicit_origin_in_prod(monkeypatch):
    monkeypatch.setattr(settings, "cors_origins", "http://localhost:3000")
    monkeypatch.setattr(settings, "app_env", "prod")
    check_cors_origins(settings)  # should not raise


async def test_bootstrap_superadmin_password_is_not_admin(db):
    await bootstrap_superadmin()
    result = await db.execute(select(User).where(User.username == "admin"))
    user = result.scalar_one()
    assert verify_password("admin", user.hashed_password) is False
    assert user.must_reset_password is True


# --- main.py's lifespan actually calls the three check_* functions (issue #81) ----
#
# main.py has never been imported by the test suite: conftest's `client` fixture
# deliberately builds an ad-hoc FastAPI() app instead of importing main.app, to
# avoid its lifespan side effects and heavy router imports. Importing main for
# real also runs, at module scope, `os.makedirs(settings.app_data_dir + "/logs")`
# — app_data_dir defaults to "/data/app", not writable in a test environment — so
# app_data_dir must be patched to a tmp dir before the first import happens.


@pytest.fixture(scope="module", autouse=True)
def _app_data_dir_for_main_import(tmp_path_factory):
    original = settings.app_data_dir
    settings.app_data_dir = str(tmp_path_factory.mktemp("app_data"))
    yield
    settings.app_data_dir = original


async def test_lifespan_raises_when_jwt_secret_is_default(monkeypatch):
    pytest.importorskip("audible")
    monkeypatch.setattr(settings, "app_env", "prod")
    monkeypatch.setattr(settings, "jwt_secret_key", "dev-secret-change-me")
    monkeypatch.setattr(settings, "database_url", "postgresql+asyncpg://u:realpass@db:5432/booksync")
    monkeypatch.setattr(settings, "cors_origins", "https://example.com")

    import main
    from fastapi import FastAPI

    app = FastAPI(lifespan=main.lifespan)
    with pytest.raises(RuntimeError, match="JWT_SECRET_KEY"):
        async with main.lifespan(app):
            pass


async def test_lifespan_raises_when_db_credentials_are_default(monkeypatch):
    pytest.importorskip("audible")
    monkeypatch.setattr(settings, "app_env", "prod")
    monkeypatch.setattr(settings, "jwt_secret_key", "a-real-randomly-generated-secret")
    monkeypatch.setattr(settings, "database_url", "postgresql+asyncpg://booksync:booksync@db:5432/booksync")
    monkeypatch.setattr(settings, "cors_origins", "https://example.com")

    import main
    from fastapi import FastAPI

    app = FastAPI(lifespan=main.lifespan)
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        async with main.lifespan(app):
            pass


async def test_lifespan_raises_when_cors_is_wildcard(monkeypatch):
    pytest.importorskip("audible")
    monkeypatch.setattr(settings, "app_env", "prod")
    monkeypatch.setattr(settings, "jwt_secret_key", "a-real-randomly-generated-secret")
    monkeypatch.setattr(settings, "database_url", "postgresql+asyncpg://u:realpass@db:5432/booksync")
    monkeypatch.setattr(settings, "cors_origins", "*")

    import main
    from fastapi import FastAPI

    app = FastAPI(lifespan=main.lifespan)
    with pytest.raises(RuntimeError, match="CORS_ORIGINS"):
        async with main.lifespan(app):
            pass
