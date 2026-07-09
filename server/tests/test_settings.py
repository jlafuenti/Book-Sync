"""
Settings router tests (issue #46, Phase 3).

Covers default-merge + typed casting on GET, admin gating on PUT, list-pattern
serialization, and the abs_api_token → encrypted credential-store routing. The
outbound test-abs/test-remote calls are external and covered only for gating.
"""

import pytest
from cryptography.fernet import Fernet

from config import settings as app_settings
from database import async_session
from routers import settings as settings_router
from services import credentials

_SECRET_PLACEHOLDER = "********"


@pytest.fixture
def enc_key(monkeypatch):
    """A valid Fernet key for the credential store (the hardcoded dev fallback
    in credentials.py is not a valid Fernet key, so we set a real one)."""
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(app_settings, "credential_enc_keys", key)
    return key


async def test_get_settings_returns_defaults(make_client, make_user, auth_header):
    user = await make_user(username="u", role="user")
    async with make_client(settings_router.router) as c:
        r = await c.get("/api/settings/", headers=auth_header(user))
    assert r.status_code == 200
    body = r.json()
    assert body["whisper_model"] == "medium"
    assert body["transcription_remote_timeout"] == 86400
    assert body["abs_api_token"] == ""  # nothing stored yet


async def test_update_requires_admin(make_client, make_user, auth_header):
    user = await make_user(username="u", role="user")
    async with make_client(settings_router.router) as c:
        r = await c.put("/api/settings/", headers=auth_header(user),
                        json={"whisper_model": "small"})
    assert r.status_code == 403


async def test_update_persists_and_casts_typed_value(make_client, make_user, auth_header):
    admin = await make_user(username="admin1", role="admin")
    async with make_client(settings_router.router) as c:
        put = await c.put("/api/settings/", headers=auth_header(admin),
                          json={"transcription_remote_timeout": 500})
        assert put.status_code == 200
        get = await c.get("/api/settings/", headers=auth_header(admin))
    # Stored as a string but cast back to int on read (per DEFAULT_SETTINGS type).
    val = get.json()["transcription_remote_timeout"]
    assert val == 500 and isinstance(val, int)


async def test_update_serializes_list_patterns(make_client, make_user, auth_header):
    admin = await make_user(username="admin1", role="admin")
    patterns = ["<Title>", "<Author>/<Title>"]
    async with make_client(settings_router.router) as c:
        put = await c.put("/api/settings/", headers=auth_header(admin),
                          json={"ebook_filename_patterns": patterns})
        assert put.status_code == 200
        get = await c.get("/api/settings/", headers=auth_header(admin))
    assert get.json()["ebook_filename_patterns"] == patterns


async def test_abs_api_token_routed_to_credential_store(make_client, make_user, auth_header, enc_key):
    admin = await make_user(username="admin1", role="admin")
    async with make_client(settings_router.router) as c:
        # Set a real token -> stored (encrypted) in the credential store.
        await c.put("/api/settings/", headers=auth_header(admin),
                    json={"abs_api_token": "secret-abs-token"})
        async with async_session() as s:
            assert await credentials.get_credential(s, "abs") == "secret-abs-token"

        # Placeholder -> leave unchanged.
        await c.put("/api/settings/", headers=auth_header(admin),
                    json={"abs_api_token": _SECRET_PLACEHOLDER})
        async with async_session() as s:
            assert await credentials.get_credential(s, "abs") == "secret-abs-token"

        # Empty string -> clear it.
        await c.put("/api/settings/", headers=auth_header(admin),
                    json={"abs_api_token": ""})
        async with async_session() as s:
            assert await credentials.get_credential(s, "abs") is None


async def test_get_masks_stored_abs_token(make_client, make_user, auth_header, enc_key):
    admin = await make_user(username="admin1", role="admin")
    async with make_client(settings_router.router) as c:
        await c.put("/api/settings/", headers=auth_header(admin),
                    json={"abs_api_token": "secret-abs-token"})
        get = await c.get("/api/settings/", headers=auth_header(admin))
    # The GET masks the real secret with the placeholder.
    assert get.json()["abs_api_token"] == _SECRET_PLACEHOLDER


async def test_transcription_remote_key_routed_to_credential_store(make_client, make_user, auth_header, enc_key):
    admin = await make_user(username="admin1", role="admin")
    async with make_client(settings_router.router) as c:
        # Set a real key -> stored (encrypted) in the credential store.
        await c.put("/api/settings/", headers=auth_header(admin),
                    json={"transcription_remote_key": "secret-jetson-key"})
        async with async_session() as s:
            assert await credentials.get_credential(s, "transcription_remote") == "secret-jetson-key"

        # Placeholder -> leave unchanged.
        await c.put("/api/settings/", headers=auth_header(admin),
                    json={"transcription_remote_key": _SECRET_PLACEHOLDER})
        async with async_session() as s:
            assert await credentials.get_credential(s, "transcription_remote") == "secret-jetson-key"

        # Empty string -> clear it.
        await c.put("/api/settings/", headers=auth_header(admin),
                    json={"transcription_remote_key": ""})
        async with async_session() as s:
            assert await credentials.get_credential(s, "transcription_remote") is None


async def test_get_masks_stored_transcription_remote_key(make_client, make_user, auth_header, enc_key):
    admin = await make_user(username="admin1", role="admin")
    async with make_client(settings_router.router) as c:
        await c.put("/api/settings/", headers=auth_header(admin),
                    json={"transcription_remote_key": "secret-jetson-key"})
        get = await c.get("/api/settings/", headers=auth_header(admin))
    assert get.json()["transcription_remote_key"] == _SECRET_PLACEHOLDER


async def test_generate_transcription_remote_key_requires_admin(make_client, make_user, auth_header):
    user = await make_user(username="u", role="user")
    async with make_client(settings_router.router) as c:
        r = await c.post("/api/settings/transcription-remote-key/generate", headers=auth_header(user))
    assert r.status_code == 403


async def test_generate_transcription_remote_key_persists_and_returns_once(
    make_client, make_user, auth_header, enc_key
):
    admin = await make_user(username="admin1", role="admin")
    async with make_client(settings_router.router) as c:
        gen = await c.post("/api/settings/transcription-remote-key/generate", headers=auth_header(admin))
        assert gen.status_code == 200
        key = gen.json()["key"]
        assert len(key) > 20  # secrets.token_urlsafe(32) output

        async with async_session() as s:
            assert await credentials.get_credential(s, "transcription_remote") == key

        # GET only ever returns the masked placeholder, never the plaintext key.
        get = await c.get("/api/settings/", headers=auth_header(admin))
        assert get.json()["transcription_remote_key"] == _SECRET_PLACEHOLDER
