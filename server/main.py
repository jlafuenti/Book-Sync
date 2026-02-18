"""
BookSync Server — Main Application

FastAPI entry point that ties together all routers and initializes
the database on startup.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from database import init_db
from routers import auth, library, sync, files, transcription

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown events."""
    logger.info("BookSync server starting up...")
    await init_db()
    logger.info("Database initialized")
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
