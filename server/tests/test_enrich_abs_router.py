"""
Tests for POST /api/library/audiobooks/{id}/enrich-abs and the bulk
POST /api/library/enrich-abs endpoint.

Regression coverage: previously these endpoints discarded the boolean
returned by write_metadata_to_file, so a tag-write failure (e.g. an m4b
chapter title with invalid UTF-8 bytes) was logged as a WARNING but the
HTTP response still claimed success. These tests isolate the router logic
from the real ABS HTTP call and the real mutagen write by monkeypatching
the names routers.library imported by value.
"""

import pytest

from routers import library
from models.book import AudioBook


async def _make_audiobook(db, **overrides):
    overrides.setdefault("title", "Antiagon Fire")
    overrides.setdefault("filename", "Antiagon Fire.m4b")
    overrides.setdefault(
        "file_path",
        "/data/audiobooks/L. E. Modesitt Jr/Antiagon Fire/Antiagon Fire.m4b",
    )
    ab = AudioBook(author="L. E. Modesitt Jr", **overrides)
    db.add(ab)
    await db.commit()
    await db.refresh(ab)
    return ab


def _stub_abs_settings(monkeypatch):
    async def _fake_load_abs_settings(db):
        return True, "http://abs.local", "token", ""
    monkeypatch.setattr(library, "_load_abs_settings", _fake_load_abs_settings)
    monkeypatch.setattr(library, "fetch_abs_index", lambda url, token, prefix: {"x": {}})


@pytest.mark.asyncio
async def test_single_book_enrich_reports_tag_write_failure(
    db, make_client, make_user, auth_header, monkeypatch
):
    user = await make_user(role="editor")
    ab = await _make_audiobook(db)
    _stub_abs_settings(monkeypatch)
    monkeypatch.setattr(
        library, "enrich_from_abs",
        lambda file_meta, file_path, abs_index, prefix, force=False: (
            {**file_meta, "description": "New description"}, True, True
        ),
    )
    monkeypatch.setattr(
        library, "write_metadata_to_file",
        lambda file_path, meta: (False, "chapter 0 title: 'utf-8' codec can't decode byte 0xc4"),
    )

    async with make_client(library.router) as c:
        resp = await c.post(
            f"/api/library/audiobooks/{ab.id}/enrich-abs",
            headers=auth_header(user),
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "tag_write_failed"
    assert "0xc4" in body["tag_write_error"]
    assert "failed" in body["message"].lower()
    # DB metadata should still have been updated even though the file tag write failed.
    assert body["book"]["description"] == "New description"


@pytest.mark.asyncio
async def test_single_book_enrich_success_has_no_tag_write_error(
    db, make_client, make_user, auth_header, monkeypatch
):
    user = await make_user(role="editor")
    ab = await _make_audiobook(db)
    _stub_abs_settings(monkeypatch)
    monkeypatch.setattr(
        library, "enrich_from_abs",
        lambda file_meta, file_path, abs_index, prefix, force=False: (
            {**file_meta, "description": "New description"}, True, True
        ),
    )
    monkeypatch.setattr(
        library, "write_metadata_to_file",
        lambda file_path, meta: (True, None),
    )

    async with make_client(library.router) as c:
        resp = await c.post(
            f"/api/library/audiobooks/{ab.id}/enrich-abs",
            headers=auth_header(user),
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "enriched"
    assert body["tag_write_error"] is None


@pytest.mark.asyncio
async def test_bulk_enrich_collects_per_book_tag_write_failures(
    db, make_client, make_user, auth_header, monkeypatch
):
    user = await make_user(role="editor")
    ab_ok = await _make_audiobook(db, title="Book OK", filename="ok.m4b",
                                   file_path="/data/audiobooks/ok.m4b")
    ab_fail = await _make_audiobook(db, title="Book Fail", filename="fail.m4b",
                                     file_path="/data/audiobooks/fail.m4b")
    _stub_abs_settings(monkeypatch)
    monkeypatch.setattr(
        library, "enrich_from_abs",
        lambda file_meta, file_path, abs_index, prefix, force=False: (
            {**file_meta, "description": "New description"}, True, True
        ),
    )

    def fake_write(file_path, meta):
        if file_path == ab_fail.file_path:
            return False, "invalid continuation byte"
        return True, None

    monkeypatch.setattr(library, "write_metadata_to_file", fake_write)

    async with make_client(library.router) as c:
        resp = await c.post("/api/library/enrich-abs", headers=auth_header(user))

    assert resp.status_code == 200
    body = resp.json()
    assert body["updated"] == 2
    assert len(body["tag_write_failures"]) == 1
    assert body["tag_write_failures"][0]["title"] == "Book Fail"
    assert "invalid continuation byte" in body["tag_write_failures"][0]["error"]
