"""Streaming upload helpers (issue #151)."""
import os
from pathlib import Path

from fastapi import HTTPException, UploadFile

CHUNK_SIZE = 1024 * 1024  # 1 MiB


def _too_large(limit: int) -> HTTPException:
    return HTTPException(
        status_code=413,
        detail=f"File exceeds the maximum upload size ({limit} bytes)",
    )


async def stream_upload_to_file(upload: UploadFile, fileobj, limit: int) -> int:
    """Copy an UploadFile into an open binary file object in chunks.

    Raises HTTPException(413) as soon as the running total exceeds ``limit``
    (the loop, not the Content-Length header, is authoritative). Returns the
    number of bytes written.
    """
    if upload.size is not None and upload.size > limit:
        raise _too_large(limit)
    total = 0
    while chunk := await upload.read(CHUNK_SIZE):
        total += len(chunk)
        if total > limit:
            raise _too_large(limit)
        fileobj.write(chunk)
    return total


async def stream_upload_to_path(upload: UploadFile, dest: Path, limit: int) -> int:
    # .part staging so a pre-existing dest is never truncated; os.replace, not
    # Path.rename, because rename onto an existing path raises on Windows.
    dest = Path(dest)
    part = dest.with_name(dest.name + ".part")
    try:
        with open(part, "wb") as f:
            total = await stream_upload_to_file(upload, f, limit)
        os.replace(part, dest)
        return total
    except BaseException:
        part.unlink(missing_ok=True)
        raise
