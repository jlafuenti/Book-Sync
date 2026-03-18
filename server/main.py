"""
BookSync Server — Main Application

FastAPI entry point that ties together all routers and initializes
the database on startup.
"""

import os
import logging
from logging.handlers import RotatingFileHandler
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from rate_limit import limiter

from database import init_db, bootstrap_superadmin
from config import settings
from routers import auth, library, sync, files, transcription, stats, chapters, match, users
from routers import settings as settings_router

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
logging.getLogger("httpx").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown events."""
    logger.info("BookSync server starting up...")
    await init_db()
    logger.info("Database initialized")
    await bootstrap_superadmin()

    # JWT secret key warning
    if settings.jwt_secret_key == "dev-secret-change-me":
        logger.warning("WARNING: Using default JWT secret key. Set JWT_SECRET_KEY environment variable for production!")
    
    # Reset any stale transcription jobs (legacy)
    from routers.transcription import reset_stale_transcriptions
    await reset_stale_transcriptions()
    
    # Start the transcription queue manager
    from services.queue_manager import start_queue_manager, stop_queue_manager
    await start_queue_manager()
    
    yield
    
    # Shutdown
    await stop_queue_manager()
    logger.info("BookSync server shutting down...")


app = FastAPI(
    title="BookSync",
    description=(
        "Synchronize your reading position between ebooks and audiobooks. "
        "Seamlessly switch between reading and listening."
    ),
    version="0.1.0",
    lifespan=lifespan,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

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
app.include_router(chapters.router, prefix="/api/library")
app.include_router(match.router, prefix="/api/library")


@app.get("/")
async def root():
    """Health check / API info."""
    return {
        "name": "BookSync",
        "version": "0.1.0",
        "status": "running",
        "docs": "/docs",
    }


@app.get("/api/health")
async def health():
    """Health check endpoint for Docker/monitoring."""
    return {"status": "healthy"}
