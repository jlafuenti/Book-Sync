"""
File serving router: download ebooks, audiobooks, and sync maps.
Supports range requests for audio streaming.
"""

import os
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
import jwt
from jwt import PyJWTError as JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from config import settings

from database import get_db
from models.user import User
from models.book import EBook, AudioBook, BookPair
from models.sync_map import SyncMap
from schemas import SyncMapResponse
from routers.auth import get_current_user
from utils import safe_join

router = APIRouter(prefix="/api/files", tags=["files"])


def _decode_or_401(token: str) -> dict:
    try:
        return jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")


async def _load_active_user(db: AsyncSession, payload: dict) -> User:
    user_id: Optional[str] = payload.get("sub")
    if user_id is None:
        raise HTTPException(status_code=401, detail="Invalid token")
    result = await db.execute(select(User).where(User.id == int(user_id)))
    user = result.scalar_one_or_none()
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="Invalid token")
    return user


async def _resolve_media_user(
    request: Request,
    db: AsyncSession,
    token_query: Optional[str],
    resource_type: str,
    resource_id: str,
) -> User:
    """Shared auth resolver for media endpoints: accepts a full access token
    via the Authorization header (never exposed in a URL/log), or a token
    scoped to this exact resource via ?token= (required for consumers that
    can't send a header, e.g. img tags, the Cast SDK's media URL)."""
    auth_header = request.headers.get("authorization", "") if request else ""

    if auth_header.lower().startswith("bearer "):
        payload = _decode_or_401(auth_header[7:])
        if payload.get("type") != "access":
            raise HTTPException(status_code=401, detail="Invalid token")
    elif token_query:
        payload = _decode_or_401(token_query)
        if (
            payload.get("type") != "media"
            or payload.get("resource_type") != resource_type
            or payload.get("resource_id") != resource_id
        ):
            raise HTTPException(status_code=401, detail="Invalid or mismatched media token")
    else:
        raise HTTPException(status_code=401, detail="Not authenticated")

    user = await _load_active_user(db, payload)
    if payload.get("type") == "media" and payload.get("ver", 0) != user.token_version:
        raise HTTPException(status_code=401, detail="Invalid token")
    return user


async def get_user_for_cover(
    filename: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    token_query: Optional[str] = Query(None, alias="token"),
) -> User:
    """Auth dependency for cover serving: accepts a full access token via
    header, or a media token scoped to this exact filename via ?token=."""
    return await _resolve_media_user(request, db, token_query, "cover", filename)


async def get_user_for_audiobook(
    audiobook_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    token_query: Optional[str] = Query(None, alias="token"),
) -> User:
    """Auth dependency for audio streaming: accepts a full access token via
    header, or a media token scoped to this exact audiobook_id via ?token=."""
    return await _resolve_media_user(request, db, token_query, "audiobook", str(audiobook_id))

# MIME type mapping
MIME_TYPES = {
    ".epub": "application/epub+zip",
    ".pdf": "application/pdf",
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".m4b": "audio/mp4",
    ".flac": "audio/flac",
    ".ogg": "audio/ogg",
    ".wav": "audio/wav",
    ".aac": "audio/aac",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}


@router.get("/ebook/{ebook_id}")
async def download_ebook(
    ebook_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Download an ebook file."""
    result = await db.execute(select(EBook).where(EBook.id == ebook_id))
    ebook = result.scalar_one_or_none()
    if not ebook:
        raise HTTPException(status_code=404, detail="EBook not found")

    if not os.path.isfile(ebook.file_path):
        raise HTTPException(status_code=404, detail="EBook file not found on disk")

    ext = Path(ebook.filename).suffix.lower()
    media_type = MIME_TYPES.get(ext, "application/octet-stream")

    return FileResponse(
        path=ebook.file_path,
        filename=ebook.filename,
        media_type=media_type,
    )

@router.get("/covers/{filename}")
async def get_cover(
    filename: str,
    _: User = Depends(get_user_for_cover),
):
    """Serve a cover image by filename. Requires auth via header or ?token= query param."""
    file_path = safe_join(settings.covers_dir, filename)

    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="Cover not found")

    ext = file_path.suffix.lower()
    media_type = MIME_TYPES.get(ext, "image/jpeg")

    return FileResponse(
        path=file_path,
        media_type=media_type,
    )


@router.get("/audiobook/{audiobook_id}")
async def download_audiobook(
    audiobook_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_user_for_audiobook),
):
    """
    Download or stream an audiobook file.
    Supports HTTP Range requests for seeking/streaming.
    """
    result = await db.execute(select(AudioBook).where(AudioBook.id == audiobook_id))
    audiobook = result.scalar_one_or_none()
    if not audiobook:
        raise HTTPException(status_code=404, detail="AudioBook not found")

    if not os.path.isfile(audiobook.file_path):
        raise HTTPException(status_code=404, detail="AudioBook file not found on disk")

    file_path = audiobook.file_path
    file_size = os.path.getsize(file_path)
    ext = Path(audiobook.filename).suffix.lower()
    media_type = MIME_TYPES.get(ext, "application/octet-stream")

    # Handle range requests for audio streaming
    range_header = request.headers.get("range")
    if range_header:
        return _range_response(file_path, file_size, media_type, range_header)

    # Don't set `filename=` — that adds Content-Disposition: attachment, which makes the
    # Cast receiver's HTML5 <audio> element refuse to play (it tries to download instead).
    # Cast streaming is the only consumer of this endpoint; downloads happen via direct file
    # access on the device for offline playback.
    return FileResponse(
        path=file_path,
        media_type=media_type,
        headers={
            "Accept-Ranges": "bytes",
            "Content-Disposition": "inline",
        },
    )


def _range_response(
    file_path: str, file_size: int, media_type: str, range_header: str
) -> StreamingResponse:
    """Create a 206 Partial Content response for range requests."""
    try:
        range_spec = range_header.replace("bytes=", "")
        start_str, end_str = range_spec.split("-")
        start = int(start_str) if start_str else 0
        end = int(end_str) if end_str else file_size - 1
    except (ValueError, IndexError):
        raise HTTPException(status_code=416, detail="Invalid range header")

    if start >= file_size or end >= file_size:
        raise HTTPException(status_code=416, detail="Range out of bounds")

    chunk_size = end - start + 1

    def iterfile():
        with open(file_path, "rb") as f:
            f.seek(start)
            remaining = chunk_size
            while remaining > 0:
                read_size = min(8192, remaining)
                data = f.read(read_size)
                if not data:
                    break
                remaining -= len(data)
                yield data

    return StreamingResponse(
        iterfile(),
        status_code=206,
        media_type=media_type,
        headers={
            "Content-Range": f"bytes {start}-{end}/{file_size}",
            "Content-Length": str(chunk_size),
            "Accept-Ranges": "bytes",
        },
    )


@router.get("/syncmap/{pair_id}", response_model=SyncMapResponse)
async def download_sync_map(
    pair_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Download the sync map for a book pair (sentence-to-timestamp mapping)."""
    result = await db.execute(
        select(SyncMap)
        .options(selectinload(SyncMap.sync_points))
        .where(SyncMap.book_pair_id == pair_id)
    )
    sync_map = result.scalar_one_or_none()

    if not sync_map:
        raise HTTPException(
            status_code=404,
            detail="Sync map not found. Has transcription been completed for this pair?",
        )

    return sync_map
