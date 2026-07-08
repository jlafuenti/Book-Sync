"""
Stats router tests (issue #46, Phase 3).
"""

import pytest

from config import settings
from routers import stats


async def test_disk_usage_returns_shape(make_client, make_user, auth_header, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "ebook_dir", str(tmp_path / "e"))
    monkeypatch.setattr(settings, "audiobook_dir", str(tmp_path / "a"))
    monkeypatch.setattr(settings, "app_data_dir", str(tmp_path))
    (tmp_path / "e").mkdir()
    (tmp_path / "a").mkdir()

    user = await make_user(username="u", role="user")
    async with make_client(stats.router) as c:
        r = await c.get("/api/stats/disk_usage", headers=auth_header(user))

    assert r.status_code == 200
    body = r.json()
    for key in ("ebook_used_bytes", "audiobook_total_bytes", "app_data_free_bytes"):
        assert key in body and isinstance(body[key], int) and body[key] >= 0
    assert isinstance(body["ebook_used_human"], str)


async def test_disk_usage_requires_auth(make_client):
    async with make_client(stats.router) as c:
        r = await c.get("/api/stats/disk_usage")
    assert r.status_code == 401
