"""
Tests for the Troubleshoot Library "corrupt chapter titles" category: a live
detector (no scan needed) surfaces m4b audiobooks that mutagen can't open
because of a non-UTF-8 chapter title, plus single/bulk repair endpoints.
"""

import pytest

from routers import troubleshoot
from services import chapter_repair
from models.book import AudioBook


async def _make_audiobook(db, **overrides):
    overrides.setdefault("title", "Antiagon Fire")
    overrides.setdefault("filename", "Antiagon Fire.m4b")
    overrides.setdefault("file_path", "/data/audiobooks/Antiagon Fire.m4b")
    ab = AudioBook(author="L. E. Modesitt Jr", **overrides)
    db.add(ab)
    await db.commit()
    await db.refresh(ab)
    return ab


@pytest.mark.asyncio
async def test_get_issues_surfaces_bad_chapter_encoding(
    db, make_client, make_user, auth_header, monkeypatch, tmp_path
):
    user = await make_user(role="editor")
    bad_path = str(tmp_path / "bad.m4b")
    good_path = str(tmp_path / "good.m4b")
    with open(bad_path, "wb") as f:
        f.write(b"x")
    with open(good_path, "wb") as f:
        f.write(b"x")
    bad = await _make_audiobook(db, title="Bad Book", filename="bad.m4b", file_path=bad_path)
    await _make_audiobook(db, title="Good Book", filename="good.m4b", file_path=good_path)

    def fake_check(path):
        if path == bad_path:
            return False, "chapter 0 title: invalid continuation byte"
        return True, None

    monkeypatch.setattr(chapter_repair, "check_chapter_encoding", fake_check)

    async with make_client(troubleshoot.router) as c:
        resp = await c.get("/api/troubleshoot/issues", headers=auth_header(user))

    assert resp.status_code == 200
    body = resp.json()
    bucket = body["categories"]["chapter_encoding_bad"]
    assert len(bucket) == 1
    assert bucket[0]["item_id"] == bad.id
    assert "invalid continuation byte" in bucket[0]["detail"]


@pytest.mark.asyncio
async def test_repair_chapter_encoding_endpoint_success(
    db, make_client, make_user, auth_header, monkeypatch, tmp_path
):
    user = await make_user(role="editor")
    path = str(tmp_path / "book.m4b")
    with open(path, "wb") as f:
        f.write(b"x")
    ab = await _make_audiobook(db, file_path=path)

    monkeypatch.setattr(chapter_repair, "repair_chapter_encoding", lambda p: (True, None))
    monkeypatch.setattr(chapter_repair, "check_chapter_encoding", lambda p: (True, None))

    async with make_client(troubleshoot.router) as c:
        resp = await c.post(
            f"/api/troubleshoot/repair-chapter-encoding/{ab.id}",
            headers=auth_header(user),
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "repaired"
    assert body["item_id"] == ab.id


@pytest.mark.asyncio
async def test_repair_chapter_encoding_endpoint_failure(
    db, make_client, make_user, auth_header, monkeypatch, tmp_path
):
    user = await make_user(role="editor")
    path = str(tmp_path / "book.m4b")
    with open(path, "wb") as f:
        f.write(b"x")
    ab = await _make_audiobook(db, file_path=path)

    monkeypatch.setattr(
        chapter_repair, "repair_chapter_encoding", lambda p: (False, "ffmpeg not found")
    )
    monkeypatch.setattr(
        chapter_repair, "check_chapter_encoding", lambda p: (False, "chapter 0 title: still bad")
    )

    async with make_client(troubleshoot.router) as c:
        resp = await c.post(
            f"/api/troubleshoot/repair-chapter-encoding/{ab.id}",
            headers=auth_header(user),
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "failed"
    assert "ffmpeg not found" in body["detail"]


@pytest.mark.asyncio
async def test_bulk_repair_chapter_encoding_reports_mixed_results(
    db, make_client, make_user, auth_header, monkeypatch, tmp_path
):
    user = await make_user(role="editor")
    ok_path = str(tmp_path / "ok.m4b")
    fail_path = str(tmp_path / "fail.m4b")
    with open(ok_path, "wb") as f:
        f.write(b"x")
    with open(fail_path, "wb") as f:
        f.write(b"x")
    ok_book = await _make_audiobook(db, title="OK Book", filename="ok.m4b", file_path=ok_path)
    fail_book = await _make_audiobook(db, title="Fail Book", filename="fail.m4b", file_path=fail_path)

    def fake_repair(path):
        if path == fail_path:
            return False, "ffmpeg not found"
        return True, None

    monkeypatch.setattr(chapter_repair, "repair_chapter_encoding", fake_repair)
    monkeypatch.setattr(chapter_repair, "check_chapter_encoding", lambda p: (True, None))

    async with make_client(troubleshoot.router) as c:
        resp = await c.post(
            "/api/troubleshoot/bulk-repair-chapter-encoding",
            json={"item_ids": [ok_book.id, fail_book.id]},
            headers=auth_header(user),
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["repaired"] == 1
    assert len(body["failures"]) == 1
    assert body["failures"][0]["item_id"] == fail_book.id
    assert body["failures"][0]["title"] == "Fail Book"
    assert "ffmpeg not found" in body["failures"][0]["error"]
