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

from database import init_db
from config import settings
from routers import auth, library, sync, files, transcription, stats
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
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown events."""
    logger.info("BookSync server starting up...")
    await init_db()
    logger.info("Database initialized")
    
    # Reset any stale transcription jobs
    from routers.transcription import reset_stale_transcriptions
    await reset_stale_transcriptions()
    
    yield
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

# CORS — allow all origins for development; tighten for production
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register routers
app.include_router(auth.router)
app.include_router(library.router)
app.include_router(sync.router)
app.include_router(files.router)
app.include_router(transcription.router)
app.include_router(stats.router)
app.include_router(settings_router.router)


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
