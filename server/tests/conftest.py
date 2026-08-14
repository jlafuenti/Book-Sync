"""
Shared pytest harness for the BookSync backend.

Strategy (see issue #46): DB-backed tests run against a throwaway **SQLite**
database. The ORM models use SQLAlchemy's portable ``JSON`` type (with a Postgres
``JSONB`` variant), so ``Base.metadata.create_all`` builds the whole schema on
SQLite — no Postgres and no Docker required. Production schema is managed by
Alembic (issue #53), which uses Postgres-only DDL and is exercised separately by
``test_migrations_postgres.py``; the SQLite suite never runs it.

The key trick: we set ``DATABASE_URL`` to the SQLite URL *before* importing any
application module. ``config.Settings`` reads it at import time, so the
module-level ``engine`` / ``async_session`` in ``database.py`` bind to SQLite.
That means both the ``get_db`` FastAPI dependency and any code that calls
``async_session()`` directly (e.g. the queue manager) hit the test DB with no
per-call plumbing.
"""

import os
import sys
import tempfile

# --- Point the app at a throwaway SQLite DB BEFORE importing app modules ------
_SERVER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SERVER_DIR not in sys.path:
    sys.path.insert(0, _SERVER_DIR)

# The Postgres migration job (RUN_PG_TESTS=1) runs the Alembic migrations against
# a real Postgres service and supplies its own DATABASE_URL — don't clobber it
# with SQLite there.
_PG_MODE = os.environ.get("RUN_PG_TESTS") == "1"

if not _PG_MODE:
    # One SQLite file per pytest process. This used to be a fixed
    # ``booksync_test.db`` shared by every run on the machine, which the autouse
    # ``_fresh_schema`` fixture below turns into a live hazard: it drops and
    # recreates the entire schema before *each test*, so two concurrent runs —
    # trivially easy with git worktrees, one suite per branch — tear down each
    # other's tables mid-test. The symptom is a storm of ``no such table`` /
    # ``table already exists`` errors in files unrelated to whatever you changed.
    # A crashed run could also leave the shared file locked on Windows, breaking
    # every later run until the handle was released. See
    # ``test_harness_db_isolation.py``.
    _TEST_DB_PATH = os.path.join(
        tempfile.gettempdir(), f"booksync_test_{os.getpid()}.db"
    )
    # SQLAlchemy wants a forward-slashed absolute path in the URL (Windows-safe).
    os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + _TEST_DB_PATH.replace("\\", "/")

# Tests rely on the zero-config dev defaults (default JWT secret, insecure
# dev Fernet key) — run in dev mode unless a test explicitly overrides it.
os.environ.setdefault("APP_ENV", "dev")

import httpx  # noqa: E402
import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402

import database  # noqa: E402  (binds engine to SQLite via DATABASE_URL above)
from database import Base, engine, async_session  # noqa: E402

# Import every model module so Base.metadata knows the full schema. Mirrors the
# import list in alembic/env.py.
from models import user, book, sync_map, bookmark, progress  # noqa: E402,F401
from models.settings import SystemSetting  # noqa: E402,F401
from models.transcription_queue import TranscriptionQueueItem  # noqa: E402,F401
from models.transcript import AudioTranscript  # noqa: E402,F401
from models.audit_log import AuditLog  # noqa: E402,F401
from models.import_source import ImportSource, ImportSourceCredential, ImportJob  # noqa: E402,F401
from models.library_issue import LibraryCheckResult  # noqa: E402,F401

from models.user import User  # noqa: E402
from routers.auth import hash_password, create_access_token  # noqa: E402

# Speed up bcrypt in tests: min rounds (4) turns each ~0.3s hash into ~ms, which
# matters across the many make_user() calls. hash_password/verify_password read
# the module-level pwd_context at call time, so reassigning it here is enough.
import routers.auth as _auth  # noqa: E402
from passlib.context import CryptContext as _CryptContext  # noqa: E402
_auth.pwd_context = _CryptContext(schemes=["bcrypt"], deprecated="auto", bcrypt__rounds=4)


def pytest_sessionfinish(session, exitstatus):
    """Delete this run's SQLite file so per-process DBs don't pile up in tempdir.

    Best-effort: the file is already unique to this PID, so a leftover can never
    break a later run — it just wastes a few hundred KB. Connections are disposed
    first because Windows refuses to unlink a file that still has an open handle,
    which is exactly how the old shared DB ended up permanently locked.
    """
    if _PG_MODE:
        return
    try:
        engine.sync_engine.dispose()
    except Exception:
        pass
    # -wal / -shm are SQLite's journal sidecars; they linger if the DB was not
    # closed cleanly.
    for suffix in ("", "-wal", "-shm"):
        try:
            os.remove(_TEST_DB_PATH + suffix)
        except OSError:
            pass


@pytest_asyncio.fixture(autouse=True)
async def _fresh_schema():
    """Drop and recreate all tables before each test for isolation."""
    if _PG_MODE:
        # The Postgres migration test manages its own schema via Alembic.
        yield
        return
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield


@pytest_asyncio.fixture
async def db():
    """A raw AsyncSession for tests that build/inspect DB rows directly."""
    async with async_session() as session:
        yield session


@pytest_asyncio.fixture
async def client():
    """
    An httpx AsyncClient wired to a minimal app containing only the routers
    under test. We avoid the real ``main.app`` on purpose: its lifespan starts
    the transcription queue + import scheduler and its full router set pulls in
    heavy deps (torch/whisper/ebooklib). The routers here reuse the same
    ``get_db`` dependency, which is already bound to the SQLite test engine.
    """
    from fastapi import FastAPI
    from slowapi import _rate_limit_exceeded_handler
    from slowapi.errors import RateLimitExceeded

    from rate_limit import limiter
    from routers import auth, sync

    # The @limiter.limit("5/minute") on /register and /login would otherwise
    # 429 the 6th auth call in a run. Disable it for tests.
    limiter.enabled = False

    app = FastAPI()
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.include_router(auth.router)
    app.include_router(sync.router)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
def make_client():
    """
    Factory that builds an AsyncClient for a minimal app mounting the given
    router(s). Reuses the SQLite-bound get_db dependency and disables the
    limiter. Usage:

        async with make_client(users.router) as c:
            await c.get(...)
    """
    import contextlib

    from fastapi import FastAPI
    from slowapi import _rate_limit_exceeded_handler
    from slowapi.errors import RateLimitExceeded

    from rate_limit import limiter

    @contextlib.asynccontextmanager
    async def _factory(*routers):
        limiter.enabled = False
        app = FastAPI()
        app.state.limiter = limiter
        app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
        for r in routers:
            app.include_router(r)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            yield c

    return _factory


@pytest_asyncio.fixture
def make_user():
    """
    Factory that inserts a User and returns it. Usage:

        user = await make_user(role="admin", password="pw")
    """
    async def _make(
        username: str = "tester",
        password: str = "password123",
        role: str = "user",
        is_active: bool = True,
        must_reset_password: bool = False,
        email: str | None = None,
    ) -> User:
        async with async_session() as session:
            u = User(
                username=username,
                email=email or f"{username}@example.com",
                hashed_password=hash_password(password),
                role=role,
                is_admin=role in ("admin", "superadmin"),
                is_active=is_active,
                must_reset_password=must_reset_password,
            )
            session.add(u)
            await session.commit()
            await session.refresh(u)
            return u

    return _make


@pytest.fixture
def auth_header():
    """Return a callable that builds a Bearer auth header for a user."""
    def _header(user: User) -> dict:
        return {"Authorization": f"Bearer {create_access_token(user)}"}

    return _header
