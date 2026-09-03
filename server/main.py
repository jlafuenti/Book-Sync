"""
Tandem Server — Main Application

FastAPI entry point that ties together all routers and initializes
the database on startup.
"""

import os
import logging
from logging.handlers import RotatingFileHandler
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from rate_limit import limiter

from database import bootstrap_superadmin
from config import (
    settings,
    check_jwt_secret,
    check_db_credentials,
    check_cors_origins,
    check_forwarded_allow_ips,
    check_single_process,
)
from log_filters import AccessLogSecretFilter
from version import API_VERSION, APP_VERSION
from middleware import MultipartBodyLimitMiddleware
from services.credentials import validate_startup as validate_credential_keys
from routers import auth, library, sync, files, transcription, stats, chapters, match, users, troubleshoot
from routers import settings as settings_router
from routers import import_sources as import_sources_router

# Configure logging
# Ensure log directory exists
log_dir = os.path.join(settings.app_data_dir, "logs")
os.makedirs(log_dir, exist_ok=True)
log_file = os.path.join(log_dir, "server.log")

# Setup handlers
file_handler = RotatingFileHandler(log_file, maxBytes=10*1024*1024, backupCount=5)
# Set formatter
formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
file_handler.setFormatter(formatter)

# Configure basicConfig with both handlers
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        file_handler
    ]
)
class EndpointFilter(logging.Filter):
    """Filter out noisy access logs."""
    def filter(self, record: logging.LogRecord) -> bool:
        return "/api/transcription/queue HTTP" not in record.getMessage()

logging.getLogger("uvicorn.access").addFilter(EndpointFilter())
logging.getLogger("uvicorn.access").addFilter(AccessLogSecretFilter())
logging.getLogger("httpx").setLevel(logging.WARNING)
# Library loggers we don't want in normal operation
logging.getLogger("audible.auth").setLevel(logging.WARNING)
logging.getLogger("audible.client").setLevel(logging.WARNING)


# Filter out the polling endpoints that fire every 3s from the Import
# Sources page — they fill the log faster than anything useful.
class ImportPollingFilter(logging.Filter):
    _NOISY_PATHS = (
        "/api/import/sources HTTP",
        "/api/import/sources/audible/jobs HTTP",
        "/api/import/sources/acsm/jobs HTTP",
    )

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        return not any(p in msg for p in self._NOISY_PATHS)


logging.getLogger("uvicorn.access").addFilter(ImportPollingFilter())

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown events."""
    logger.info("Tandem server starting up...")

    # Fail fast on insecure defaults before touching the DB or minting tokens.
    check_jwt_secret(settings)
    check_db_credentials(settings)
    check_cors_origins(settings)
    check_forwarded_allow_ips(settings)  # warns only — bare deployments are fine
    # The queue manager, its cancel state and the schedulers below are
    # single-process only (issue #252) — refuse a multi-worker environment
    # rather than let it show up as a duplicated transcription.
    check_single_process(settings)
    logger.info(
        "Single-process mode: one queue manager, one import scheduler, one "
        "backup scheduler (issue #252)."
    )
    validate_credential_keys()

    # Schema is owned by Alembic now (issue #53): `alembic upgrade head` runs in
    # the container entrypoint before uvicorn, so the DB is already migrated by
    # the time we get here. We only seed data.
    await bootstrap_superadmin()

    # Reset any stale transcription jobs (legacy)
    from routers.transcription import reset_stale_transcriptions
    await reset_stale_transcriptions()

    # One-shot: move plaintext ABS API token into the encrypted credential store
    # if it's still living in system_settings. Idempotent; safe to call always.
    try:
        from services.abs_metadata import migrate_abs_token_to_credentials
        await migrate_abs_token_to_credentials()
    except Exception as e:
        logger.exception(f"ABS token migration failed: {e}")

    # Re-hydrate the DeACSM plugin's Adobe device authorization from the
    # encrypted credential store onto disk. Without this, every container
    # rebuild forces the user to re-authorize and burns a Google Play
    # device slot in the process.
    try:
        from services.import_sources.acsm import restore_adobe_account_from_credentials
        from database import async_session
        async with async_session() as db:
            await restore_adobe_account_from_credentials(db)
    except Exception as e:
        logger.exception(f"Adobe authorization restore failed: {e}")

    # Start the transcription queue manager
    from services.queue_manager import start_queue_manager, stop_queue_manager
    await start_queue_manager()

    # Start the import-source scheduler (auto-syncs + ACSM watched folder)
    from services import import_scheduler
    await import_scheduler.start()

    # Start the backup scheduler (nightly pg_dump + covers snapshot, retention)
    from services import backup_service
    await backup_service.start()

    yield

    # Shutdown
    await backup_service.stop()
    await import_scheduler.stop()
    await stop_queue_manager()
    logger.info("Tandem server shutting down...")


app = FastAPI(
    title="Tandem",
    description=(
        "Synchronize your reading position between ebooks and audiobooks. "
        "Seamlessly switch between reading and listening."
    ),
    version=APP_VERSION,
    lifespan=lifespan,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Refuse oversized multipart bodies with 413 before the parser runs — FastAPI
# parses multipart before auth, so this is reachable unauthenticated (#157).
app.add_middleware(MultipartBodyLimitMiddleware)

# CORS — controlled by CORS_ORIGINS env var (comma-separated); defaults to * for dev
_cors_origins = settings.cors_origins_list
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=_cors_origins != ["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register routers
app.include_router(auth.router)
app.include_router(users.router)
app.include_router(library.router)
app.include_router(sync.router)
app.include_router(files.router)
app.include_router(transcription.router)
app.include_router(stats.router)
app.include_router(settings_router.router)
app.include_router(import_sources_router.router)
app.include_router(troubleshoot.router)
app.include_router(chapters.router, prefix="/api/library")
app.include_router(match.router, prefix="/api/library")


@app.get("/")
async def root():
    """Health check / API info."""
    return {
        "name": "Tandem",
        "version": APP_VERSION,
        "status": "running",
        "docs": "/docs",
    }


@app.get("/api/health")
async def health():
    """Readiness probe for Docker/monitoring: verifies the database answers.

    Returns 503 when the DB is unreachable so orchestrators and uptime
    monitors see a DB outage instead of a false "up" (issue #47). Liveness
    (process alive, dependencies not checked) is /api/livez.

    The healthy payload also carries the version handshake (issue #174). This is
    the only unauthenticated endpoint a client can ask before it has credentials,
    which is what makes it the place to say which API this server speaks — the
    Android app compares `api_version` against the one it was built for and warns
    whichever side is stale. The 503 payload is deliberately left alone: uptime
    monitors match on its shape, and a client that cannot reach the database has
    a bigger problem than a version mismatch.
    """
    import asyncio

    from sqlalchemy import text

    import database

    try:
        async with asyncio.timeout(5):
            async with database.async_session() as session:
                await session.execute(text("SELECT 1"))
    except Exception:
        logger.exception("Health check failed: database unreachable")
        return JSONResponse(
            status_code=503, content={"status": "unhealthy", "db": "down"}
        )
    return {
        "status": "healthy",
        "app_version": APP_VERSION,
        "api_version": API_VERSION,
    }


@app.get("/api/livez")
async def livez():
    """Liveness probe: static 200 while the process is up."""
    return {"status": "alive"}
