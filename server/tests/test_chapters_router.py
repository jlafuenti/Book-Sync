"""
Regression test for routers.chapters.update_audiobook_chapters: it exports
the file's existing metadata via ffmpeg -f ffmetadata, then used to reopen
that export with a hard-coded `encoding='utf-8'` text read. ffmpeg copies
chapter title bytes through opaquely without validating encoding, so a file
with a non-UTF-8 chapter title (see services/chapter_repair.py and
services/test_abs_metadata.py for the underlying mutagen bug this mirrors)
would crash this endpoint with a UnicodeDecodeError before it ever got to
write the new chapters.
"""

import asyncio

import pytest

from routers import chapters
from models.book import AudioBook


class _FakeProcess:
    def __init__(self, returncode=0):
        self.returncode = returncode

    async def communicate(self):
        return b"", b""


@pytest.mark.asyncio
async def test_update_chapters_survives_non_utf8_exported_metadata(
    db, make_client, make_user, auth_header, monkeypatch, tmp_path
):
    user = await make_user(role="editor")
    filepath = str(tmp_path / "book.m4b")
    with open(filepath, "wb") as f:
        f.write(b"fake m4b")
    ab = AudioBook(title="Antiagon Fire", filename="book.m4b", file_path=filepath)
    db.add(ab)
    await db.commit()
    await db.refresh(ab)

    async def fake_exec(*cmd, **kwargs):
        if "-f" in cmd and "ffmetadata" in cmd:
            export_path = cmd[-1]
            with open(export_path, "wb") as f:
                f.write(
                    b";FFMETADATA1\n"
                    b"[CHAPTER]\n"
                    b"TIMEBASE=1/1000\n"
                    b"START=0\n"
                    b"END=5000\n"
                    b"title=Chapter \xc4ne\n"
                )
            return _FakeProcess(returncode=0)
        elif "-map_metadata" in cmd:
            out_path = cmd[-1]
            with open(out_path, "wb") as f:
                f.write(b"fake m4b bytes")
            return _FakeProcess(returncode=0)
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    async with make_client(chapters.router) as c:
        resp = await c.put(
            f"/audiobooks/{ab.id}/chapters",
            json=[{"id": 0, "start_time": 0.0, "end_time": 10.0, "title": "New Chapter"}],
            headers=auth_header(user),
        )

    assert resp.status_code == 200
