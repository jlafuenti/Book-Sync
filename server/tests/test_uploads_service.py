"""Unit tests for the streaming upload helper (issue #151).

Uses a fake upload object rather than a real starlette UploadFile so the tests
pin the helper's own contract: chunked reads, byte-count return, 413 on
over-limit, .part staging that never truncates a pre-existing destination.
"""

import io

import pytest
from fastapi import HTTPException

from services.uploads import CHUNK_SIZE, stream_upload_to_file, stream_upload_to_path


class FakeUpload:
    """Minimal async stand-in for starlette's UploadFile."""

    def __init__(self, data: bytes, size=None):
        self._buf = io.BytesIO(data)
        self.size = size
        self.read_sizes: list[int] = []

    async def read(self, size):
        assert isinstance(size, int) and size > 0
        self.read_sizes.append(size)
        return self._buf.read(size)


async def test_success_writes_exact_bytes_and_cleans_up(tmp_path):
    data = b"x" * 1000
    dest = tmp_path / "book.epub"
    total = await stream_upload_to_path(FakeUpload(data), dest, limit=4096)
    assert total == 1000
    assert dest.read_bytes() == data
    assert not (tmp_path / "book.epub.part").exists()


async def test_over_limit_raises_413_and_leaves_nothing(tmp_path):
    dest = tmp_path / "book.epub"
    with pytest.raises(HTTPException) as exc:
        await stream_upload_to_path(FakeUpload(b"x" * 2000), dest, limit=1000)
    assert exc.value.status_code == 413
    assert not dest.exists()
    assert not (tmp_path / "book.epub.part").exists()


async def test_over_limit_keeps_preexisting_dest_intact(tmp_path):
    dest = tmp_path / "book.epub"
    dest.write_bytes(b"original-bytes")
    with pytest.raises(HTTPException) as exc:
        await stream_upload_to_path(FakeUpload(b"x" * 2000), dest, limit=1000)
    assert exc.value.status_code == 413
    assert dest.read_bytes() == b"original-bytes"
    assert not (tmp_path / "book.epub.part").exists()


async def test_success_replaces_preexisting_dest(tmp_path):
    """Pins os.replace semantics — Path.rename onto an existing file raises
    FileExistsError on Windows."""
    dest = tmp_path / "book.epub"
    dest.write_bytes(b"old-bytes")
    total = await stream_upload_to_path(FakeUpload(b"new-bytes!"), dest, limit=4096)
    assert total == 10
    assert dest.read_bytes() == b"new-bytes!"
    assert not (tmp_path / "book.epub.part").exists()


async def test_reads_are_chunked(tmp_path):
    upload = FakeUpload(b"x" * (CHUNK_SIZE + 10))
    dest = tmp_path / "big.bin"
    await stream_upload_to_path(upload, dest, limit=2 * CHUNK_SIZE)
    assert upload.read_sizes and all(s == CHUNK_SIZE for s in upload.read_sizes)


async def test_exactly_at_limit_succeeds(tmp_path):
    """The boundary is total > limit, so exactly-at-limit is accepted."""
    dest = tmp_path / "book.epub"
    total = await stream_upload_to_path(FakeUpload(b"x" * 1000), dest, limit=1000)
    assert total == 1000
    assert dest.read_bytes() == b"x" * 1000


async def test_declared_size_over_limit_rejected_before_reading(tmp_path):
    upload = FakeUpload(b"x" * 10, size=5000)
    with pytest.raises(HTTPException) as exc:
        await stream_upload_to_path(upload, tmp_path / "book.epub", limit=1000)
    assert exc.value.status_code == 413
    assert upload.read_sizes == []


async def test_stream_to_fileobj_over_limit_raises_413():
    buf = io.BytesIO()
    with pytest.raises(HTTPException) as exc:
        await stream_upload_to_file(FakeUpload(b"x" * 2000), buf, limit=1000)
    assert exc.value.status_code == 413
