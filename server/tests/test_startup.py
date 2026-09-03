"""
Startup-time hardening checks (issues #35, #37, #72, #73): refusing to boot with
default secrets/credentials/wildcard CORS in prod, and never seeding a
guessable superadmin password.
"""

import logging

import pytest
from sqlalchemy import select, func

from config import (
    settings,
    check_jwt_secret,
    check_db_credentials,
    check_cors_origins,
    check_forwarded_allow_ips,
    check_single_process,
)
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


def test_prod_warns_when_forwarded_allow_ips_is_unset(monkeypatch, caplog):
    """Behind a reverse proxy, an unset FORWARDED_ALLOW_IPS silently collapses
    the auth rate limit into one global bucket (issue #156) — prod must warn."""
    monkeypatch.setattr(settings, "forwarded_allow_ips", None)
    monkeypatch.setattr(settings, "app_env", "prod")
    with caplog.at_level(logging.WARNING, logger="config"):
        check_forwarded_allow_ips(settings)  # warns, never raises
    assert any("FORWARDED_ALLOW_IPS" in r.getMessage() for r in caplog.records)


def test_prod_is_silent_when_forwarded_allow_ips_is_set(monkeypatch, caplog):
    monkeypatch.setattr(settings, "forwarded_allow_ips", "172.18.0.5")
    monkeypatch.setattr(settings, "app_env", "prod")
    with caplog.at_level(logging.WARNING, logger="config"):
        check_forwarded_allow_ips(settings)
    assert not caplog.records


def test_dev_is_silent_when_forwarded_allow_ips_is_unset(monkeypatch, caplog):
    monkeypatch.setattr(settings, "forwarded_allow_ips", None)
    monkeypatch.setattr(settings, "app_env", "dev")
    with caplog.at_level(logging.WARNING, logger="config"):
        check_forwarded_allow_ips(settings)
    assert not caplog.records


# --- the server must run as exactly one process (issue #252) -----------------
#
# The transcription pipeline is single-process by construction: the queue claim
# is a SELECT-then-UPDATE with no row lock, cancel/pause state lives in
# module-level Python sets, `reset_stale_items()` re-queues every in_progress
# row at every boot, and the import/backup schedulers are started per process.
# Two workers means the same audiobook transcribed twice, cancels that reach
# only one of them, and each process re-queueing the other's live job. Catch the
# misconfiguration at boot rather than by a double transcription.


@pytest.mark.parametrize("var", ["WEB_CONCURRENCY", "UVICORN_WORKERS", "GUNICORN_WORKERS"])
def test_check_single_process_raises_when_more_than_one_worker_is_configured(
    monkeypatch, var
):
    monkeypatch.setenv(var, "2")
    with pytest.raises(RuntimeError, match=var):
        check_single_process(settings)


@pytest.mark.parametrize("value", ["1", "01", " 1 "])
def test_check_single_process_allows_an_explicit_single_worker(monkeypatch, value):
    monkeypatch.setenv("WEB_CONCURRENCY", value)
    check_single_process(settings)  # should not raise


def test_check_single_process_is_silent_when_nothing_is_set(monkeypatch):
    for var in ("WEB_CONCURRENCY", "UVICORN_WORKERS", "GUNICORN_WORKERS"):
        monkeypatch.delenv(var, raising=False)
    check_single_process(settings)  # should not raise


def test_check_single_process_ignores_an_unparseable_value(monkeypatch, caplog):
    """A junk value is not a reason to refuse to boot — warn and carry on."""
    monkeypatch.setenv("WEB_CONCURRENCY", "auto")
    with caplog.at_level(logging.WARNING, logger="config"):
        check_single_process(settings)
    assert any("WEB_CONCURRENCY" in r.getMessage() for r in caplog.records)


def test_entrypoint_starts_uvicorn_with_a_single_worker():
    """The shipped container command must never grow `--workers`."""
    import os

    server_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(server_dir, "entrypoint.sh"), encoding="utf-8") as fh:
        entrypoint = fh.read()

    uvicorn_lines = [
        line
        for line in entrypoint.splitlines()
        if "uvicorn" in line and not line.strip().startswith("#")
    ]
    assert uvicorn_lines, "entrypoint.sh no longer starts uvicorn"
    for line in uvicorn_lines:
        assert "--workers" not in line, (
            "entrypoint.sh passes --workers; the queue manager, cancel state and "
            "schedulers are single-process only (issue #252)"
        )

    assert "single process" in entrypoint.lower(), (
        "entrypoint.sh must say why the worker count is pinned at one"
    )


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


async def test_lifespan_refuses_multi_worker_env(monkeypatch):
    """The guard is wired into the lifespan, not just importable (issue #252)."""
    pytest.importorskip("audible")
    monkeypatch.setattr(settings, "app_env", "dev")
    monkeypatch.setenv("WEB_CONCURRENCY", "4")

    import main
    from fastapi import FastAPI

    app = FastAPI(lifespan=main.lifespan)
    with pytest.raises(RuntimeError, match="WEB_CONCURRENCY"):
        async with main.lifespan(app):
            pass


async def test_lifespan_starts_and_stops_the_background_services(monkeypatch):
    """The happy path: only the three refusal cases above were covered before.

    Every long-running service is stubbed — the point is that startup reaches
    `yield` and that shutdown stops all three in order, not that the schedulers
    themselves work (they have their own tests).
    """
    pytest.importorskip("audible")
    monkeypatch.setattr(settings, "app_env", "dev")

    import main
    from fastapi import FastAPI
    from services import backup_service, import_scheduler
    import services.queue_manager as queue_manager

    calls: list[str] = []

    def _record(name):
        async def _stub(*args, **kwargs):
            calls.append(name)
        return _stub

    monkeypatch.setattr(queue_manager, "start_queue_manager", _record("queue.start"))
    monkeypatch.setattr(queue_manager, "stop_queue_manager", _record("queue.stop"))
    monkeypatch.setattr(import_scheduler, "start", _record("scheduler.start"))
    monkeypatch.setattr(import_scheduler, "stop", _record("scheduler.stop"))
    monkeypatch.setattr(backup_service, "start", _record("backup.start"))
    monkeypatch.setattr(backup_service, "stop", _record("backup.stop"))

    app = FastAPI(lifespan=main.lifespan)
    async with main.lifespan(app):
        assert calls == ["queue.start", "scheduler.start", "backup.start"]

    # Shutdown tears them down in reverse order of how they were brought up.
    assert calls[3:] == ["backup.stop", "scheduler.stop", "queue.stop"]


# ---------------------------------------------------------------------------
# The generated bootstrap password must not land in the rotating file log
# (issue #209).
#
# README tells the operator to read it from `docker compose logs`, so it has to
# reach stdout. But main.py also attaches a RotatingFileHandler (10 MB x 5) to
# the *root* logger, and `bootstrap_superadmin` used to log through
# `logging.getLogger(__name__)`, which propagates there — leaving a plaintext
# superadmin password on disk under APP_DATA_DIR across five rotations. That is
# a second, far longer-lived copy that nothing asked for, for an account whose
# whole point is that it must be reset on first login.
# ---------------------------------------------------------------------------


def test_bootstrap_password_does_not_reach_the_root_file_handler(tmp_path):
    import logging

    from database import bootstrap_logger

    secret = "generated-password-should-not-be-here"
    log_file = tmp_path / "server.log"
    root = logging.getLogger()
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    root.addHandler(file_handler)
    try:
        bootstrap_logger().warning("password: %s", secret)
        file_handler.flush()
    finally:
        root.removeHandler(file_handler)
        file_handler.close()

    assert secret not in log_file.read_text(encoding="utf-8")


def test_an_ordinary_module_logger_does_reach_the_root_file_handler(tmp_path):
    """Counterweight: proves the test above is actually observing propagation
    rather than a file handler that never worked."""
    import logging

    marker = "ordinary-propagating-message"
    log_file = tmp_path / "server.log"
    root = logging.getLogger()
    root_level = root.level
    root.setLevel(logging.INFO)
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    root.addHandler(file_handler)
    try:
        logging.getLogger("database").warning(marker)
        file_handler.flush()
    finally:
        root.removeHandler(file_handler)
        file_handler.close()
        root.setLevel(root_level)

    assert marker in log_file.read_text(encoding="utf-8")


def test_bootstrap_logger_still_emits_somewhere(tmp_path):
    """It must not be silenced — the operator reads it from the container log."""
    import logging

    from database import bootstrap_logger

    records = []

    class _Capture(logging.Handler):
        def emit(self, record):
            records.append(record.getMessage())

    log = bootstrap_logger()
    handler = _Capture()
    log.addHandler(handler)
    try:
        log.warning("password: %s", "abc123")
    finally:
        log.removeHandler(handler)

    assert any("abc123" in m for m in records)
    assert log.handlers, (
        "bootstrap logger has no handler of its own, so with propagate=False the "
        "operator would never see the generated password at all"
    )
