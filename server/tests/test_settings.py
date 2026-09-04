"""
Settings router tests (issue #46, Phase 3).

Covers default-merge + typed casting on GET, admin gating on PUT, list-pattern
serialization, and the abs_api_token → encrypted credential-store routing.
test-remote's outbound call is mocked via httpx.MockTransport to verify it
actually distinguishes a correct key (success) from a wrong one (401 ->
"Authentication failed", not a false success) — see #38.
"""

import httpx
import pytest
from cryptography.fernet import Fernet

from config import settings as app_settings
from database import async_session
from routers import settings as settings_router
from services import credentials

_SECRET_PLACEHOLDER = "********"


def _patch_jetson_transport(monkeypatch, handler):
    """Redirect outbound httpx.AsyncClient calls (the Jetson request
    test_remote_connection makes) to a mock transport. routers.settings
    imports httpx locally inside the function, but that's the same shared
    httpx module object, so patching the real module's AsyncClient attribute
    affects it too — the test's own ASGI client (make_client, which always
    passes its own `transport=`) is left untouched so we don't collide with it."""
    transport = httpx.MockTransport(handler)
    real_async_client = httpx.AsyncClient

    def fake_async_client(*args, **kwargs):
        if "transport" in kwargs:
            return real_async_client(*args, **kwargs)  # the test harness's own ASGI client
        kwargs.pop("timeout", None)
        return real_async_client(*args, transport=transport, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", fake_async_client)


@pytest.fixture
def enc_key(monkeypatch):
    """A valid Fernet key for the credential store (the hardcoded dev fallback
    in credentials.py is not a valid Fernet key, so we set a real one)."""
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(app_settings, "credential_enc_keys", key)
    return key


async def test_get_settings_returns_defaults(make_client, make_user, auth_header):
    # Admin, not a plain user: the operator's configuration is admin-only since
    # issue #263 — see the allow-list tests below.
    admin = await make_user(username="admin0", role="admin")
    async with make_client(settings_router.router) as c:
        r = await c.get("/api/settings/", headers=auth_header(admin))
    assert r.status_code == 200
    body = r.json()
    assert body["whisper_model"] == "medium"
    assert body["transcription_remote_timeout"] == 86400
    assert body["abs_api_token"] == ""  # nothing stored yet


# ---------------------------------------------------------------------------
# Role-scoped GET (issue #263)
#
# `GET /api/settings/` used to hand every signed-in account the whole operator
# configuration: the Audiobookshelf URL, the transcription worker's URL, the
# filename patterns that describe the library layout, the backup schedule and
# retention. Secrets were masked, but the rest is precisely the reconnaissance
# an attacker wants, and no non-admin screen ever needed it.
#
# Below admin the response is an allow-list of exactly what the web reads
# outside the admin pages: `abs_enabled` (BookDetailPage decides whether to
# offer the ABS enrich action) and `hardcover_configured` (MatchTab picks its
# default metadata provider). `hardcover_configured` is a derived boolean, not
# the masked token string, so a non-admin never learns anything about the
# credential beyond "an admin set one".
# ---------------------------------------------------------------------------

PLAIN_USER_KEYS = {"abs_enabled", "hardcover_configured"}


@pytest.mark.parametrize("role", ["user", "editor"])
async def test_settings_get_is_an_allowlist_below_admin(
    make_client, make_user, auth_header, role
):
    user = await make_user(username=f"u_{role}", role=role)
    async with make_client(settings_router.router) as c:
        r = await c.get("/api/settings/", headers=auth_header(user))

    assert r.status_code == 200
    assert set(r.json()) == PLAIN_USER_KEYS


async def test_settings_get_hides_internal_config_from_plain_user(
    make_client, make_user, auth_header
):
    """The specific keys that made this a reconnaissance surface."""
    user = await make_user(username="u", role="user")
    async with make_client(settings_router.router) as c:
        body = (await c.get("/api/settings/", headers=auth_header(user))).json()

    for leaked in (
        "abs_url",
        "abs_api_token",
        "abs_audiobooks_prefix",
        "transcription_remote_url",
        "transcription_remote_key",
        "hardcover_api_token",
        "ebook_filename_patterns",
        "audiobook_filename_patterns",
        "whisper_model",
        "backup_enabled",
        "backup_hour",
        "backup_keep_daily",
        "backup_keep_monthly",
        "audit_log_retention_days",
    ):
        assert leaked not in body, f"{leaked} still reaches a plain user"


async def test_settings_get_full_for_admin(make_client, make_user, auth_header):
    admin = await make_user(username="admin_full", role="admin")
    async with make_client(settings_router.router) as c:
        body = (await c.get("/api/settings/", headers=auth_header(admin))).json()

    assert set(settings_router.DEFAULT_SETTINGS) <= set(body)
    assert "abs_url" in body and "transcription_remote_url" in body
    # The derived flag is in the admin payload too — MatchTab renders for
    # admins as well, and it reads the same key for every role.
    assert body["hardcover_configured"] is False


async def test_admin_secrets_are_still_masked(
    make_client, make_user, auth_header, enc_key
):
    """The allow-list must not become an excuse to stop masking."""
    admin = await make_user(username="admin_mask", role="admin")
    async with make_client(settings_router.router) as c:
        await c.put(
            "/api/settings/",
            headers=auth_header(admin),
            json={
                "abs_api_token": "abs-secret",
                "transcription_remote_key": "jetson-secret",
                "hardcover_api_token": "hc-secret",
            },
        )
        body = (await c.get("/api/settings/", headers=auth_header(admin))).json()

    assert body["abs_api_token"] == _SECRET_PLACEHOLDER
    assert body["transcription_remote_key"] == _SECRET_PLACEHOLDER
    assert body["hardcover_api_token"] == _SECRET_PLACEHOLDER
    for value in body.values():
        assert value not in ("abs-secret", "jetson-secret", "hc-secret")


async def test_hardcover_configured_tracks_the_credential_store(
    make_client, make_user, auth_header, enc_key
):
    admin = await make_user(username="admin_hc2", role="admin")
    user = await make_user(username="reader_hc", role="user")
    async with make_client(settings_router.router) as c:
        before = (await c.get("/api/settings/", headers=auth_header(user))).json()
        assert before["hardcover_configured"] is False

        await c.put(
            "/api/settings/",
            headers=auth_header(admin),
            json={"hardcover_api_token": "secret-hc-token"},
        )
        after = (await c.get("/api/settings/", headers=auth_header(user))).json()

    assert after["hardcover_configured"] is True
    # Still a boolean, never the token or its mask.
    assert after["hardcover_configured"] is not _SECRET_PLACEHOLDER


async def test_abs_enabled_reaches_a_plain_user(make_client, make_user, auth_header):
    """BookDetailPage's ABS action must keep working for a reader account."""
    admin = await make_user(username="admin_abs2", role="admin")
    user = await make_user(username="reader_abs", role="user")
    async with make_client(settings_router.router) as c:
        assert (
            await c.get("/api/settings/", headers=auth_header(user))
        ).json()["abs_enabled"] is False

        await c.put(
            "/api/settings/", headers=auth_header(admin), json={"abs_enabled": True}
        )
        body = (await c.get("/api/settings/", headers=auth_header(user))).json()

    assert body["abs_enabled"] is True


async def test_update_still_returns_the_full_dict_to_the_admin(
    make_client, make_user, auth_header
):
    """PUT echoes the settings back; the admin who just wrote them must see
    everything, not the reader allow-list."""
    admin = await make_user(username="admin_put", role="admin")
    async with make_client(settings_router.router) as c:
        r = await c.put(
            "/api/settings/", headers=auth_header(admin), json={"whisper_model": "small"}
        )

    assert r.status_code == 200
    assert r.json()["whisper_model"] == "small"
    assert set(settings_router.DEFAULT_SETTINGS) <= set(r.json())


async def test_put_does_not_persist_the_derived_flag(
    make_client, make_user, auth_header, db
):
    """A client echoing the GET payload back must not write `hardcover_configured`
    as a settings row — it is computed from the credential store on every read."""
    from sqlalchemy import select as _select

    from models.settings import SystemSetting

    admin = await make_user(username="admin_derived", role="admin")
    async with make_client(settings_router.router) as c:
        r = await c.put(
            "/api/settings/",
            headers=auth_header(admin),
            json={"hardcover_configured": True, "whisper_model": "tiny"},
        )

    assert r.status_code == 200
    assert r.json()["hardcover_configured"] is False  # no credential stored
    rows = (await db.execute(_select(SystemSetting))).scalars().all()
    assert "hardcover_configured" not in {row.key for row in rows}
    assert "whisper_model" in {row.key for row in rows}


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


async def test_remote_timeout_below_the_floor_is_rejected(make_client, make_user, auth_header):
    """Issue #248: the stored value is honoured now, so a typo would abandon
    every job seconds in. Reject it at the door and write nothing."""
    admin = await make_user(username="admin1", role="admin")
    async with make_client(settings_router.router) as c:
        put = await c.put("/api/settings/", headers=auth_header(admin),
                          json={"transcription_remote_timeout": 5, "whisper_model": "small"})
        assert put.status_code == 422
        body = (await c.get("/api/settings/", headers=auth_header(admin))).json()

    # A rejected PUT is all-or-nothing: the valid key alongside it is not saved.
    assert body["transcription_remote_timeout"] == 86400
    assert body["whisper_model"] == "medium"


async def test_backup_settings_round_trip_with_types(make_client, make_user, auth_header):
    admin = await make_user(username="admin1", role="admin")
    async with make_client(settings_router.router) as c:
        # Defaults exposed before anything is stored.
        get0 = await c.get("/api/settings/", headers=auth_header(admin))
        b0 = get0.json()
        assert b0["backup_enabled"] is True and b0["backup_hour"] == 3
        assert b0["backup_keep_daily"] == 14 and b0["backup_keep_monthly"] == 6

        put = await c.put("/api/settings/", headers=auth_header(admin), json={
            "backup_enabled": False, "backup_hour": 1,
            "backup_keep_daily": 7, "backup_keep_monthly": 2,
        })
        assert put.status_code == 200
        body = (await c.get("/api/settings/", headers=auth_header(admin))).json()

    assert body["backup_enabled"] is False
    assert body["backup_hour"] == 1 and isinstance(body["backup_hour"], int)
    assert body["backup_keep_daily"] == 7 and body["backup_keep_monthly"] == 2


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


def _jetson_health_handler(request: httpx.Request) -> httpx.Response:
    auth = request.headers.get("authorization")
    if auth == "Bearer correct-key":
        return httpx.Response(
            200, json={"gpu_available": True, "gpu_name": "Orin", "model_loaded": True}
        )
    return httpx.Response(401, json={"detail": "Unauthorized"})


async def test_test_remote_reports_success_with_correct_key(
    make_client, make_user, auth_header, enc_key, monkeypatch
):
    admin = await make_user(username="admin1", role="admin")
    _patch_jetson_transport(monkeypatch, _jetson_health_handler)

    async with make_client(settings_router.router) as c:
        r = await c.post(
            "/api/settings/test-remote", json={"url": "http://fake-jetson:9000", "key": "correct-key"},
            headers=auth_header(admin),
        )
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["model_loaded"] is True


async def test_test_remote_reports_auth_failure_with_wrong_key(
    make_client, make_user, auth_header, enc_key, monkeypatch
):
    """The core guarantee this endpoint exists for: a wrong key must surface
    as a clear authentication failure, never as a false "success": true."""
    admin = await make_user(username="admin1", role="admin")
    _patch_jetson_transport(monkeypatch, _jetson_health_handler)

    async with make_client(settings_router.router) as c:
        r = await c.post(
            "/api/settings/test-remote", json={"url": "http://fake-jetson:9000", "key": "wrong-key"},
            headers=auth_header(admin),
        )
    assert r.status_code == 400
    assert "Authentication failed" in r.json()["detail"]


async def test_test_remote_falls_back_to_saved_key_when_none_passed(
    make_client, make_user, auth_header, enc_key, monkeypatch
):
    """An empty key= (the UI's signal for "use whatever's already saved",
    e.g. when the field only shows the masked placeholder) must test against
    the real stored credential, not silently pass with no auth at all."""
    admin = await make_user(username="admin1", role="admin")
    async with async_session() as s:
        await credentials.set_credential(s, "transcription_remote", "correct-key")
        await s.commit()
    _patch_jetson_transport(monkeypatch, _jetson_health_handler)

    async with make_client(settings_router.router) as c:
        r = await c.post(
            "/api/settings/test-remote", json={"url": "http://fake-jetson:9000", "key": ""},
            headers=auth_header(admin),
        )
    assert r.status_code == 200
    assert r.json()["success"] is True


async def test_test_remote_requires_a_key(make_client, make_user, auth_header, enc_key):
    """No key passed and none saved -> a clear 400, not an unauthenticated
    request sent to the Jetson."""
    admin = await make_user(username="admin1", role="admin")
    async with make_client(settings_router.router) as c:
        r = await c.post(
            "/api/settings/test-remote", json={"url": "http://fake-jetson:9000"},
            headers=auth_header(admin),
        )
    assert r.status_code == 400
    assert "API Key" in r.json()["detail"]


# ---------------------------------------------------------------------------
# SSRF guard (issue #51) -- test-abs / test-remote are admin-only and must
# keep reaching LAN targets (that's their whole point), but should still
# reject a non-http(s) scheme.
# ---------------------------------------------------------------------------

async def test_test_remote_rejects_invalid_scheme(make_client, make_user, auth_header, enc_key, monkeypatch):
    def handler(request):
        raise AssertionError("transport should not be reached for an invalid scheme")
    _patch_jetson_transport(monkeypatch, handler)
    admin = await make_user(username="admin1", role="admin")

    async with make_client(settings_router.router) as c:
        r = await c.post(
            "/api/settings/test-remote", json={"url": "file:///etc/passwd", "key": "some-key"},
            headers=auth_header(admin),
        )
    assert r.status_code == 400
    assert "Invalid URL" in r.json()["detail"]


async def test_test_remote_allows_private_lan_url(make_client, make_user, auth_header, enc_key, monkeypatch):
    """Regression guard: the LAN-reaching Jetson use case must still work
    now that a guard sits in front of it."""
    admin = await make_user(username="admin1", role="admin")
    _patch_jetson_transport(monkeypatch, _jetson_health_handler)

    async with make_client(settings_router.router) as c:
        r = await c.post(
            "/api/settings/test-remote", json={"url": "http://192.0.2.50:9000", "key": "correct-key"},
            headers=auth_header(admin),
        )
    assert r.status_code == 200
    assert r.json()["success"] is True


def _abs_libraries_handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"libraries": [{"id": "1", "name": "Audiobooks", "mediaType": "book"}]})


async def test_test_abs_rejects_invalid_scheme(make_client, make_user, auth_header, monkeypatch):
    def handler(request):
        raise AssertionError("transport should not be reached for an invalid scheme")
    _patch_jetson_transport(monkeypatch, handler)
    admin = await make_user(username="admin1", role="admin")

    async with make_client(settings_router.router) as c:
        r = await c.post(
            "/api/settings/test-abs", json={"url": "file:///etc/passwd", "token": "tok"},
            headers=auth_header(admin),
        )
    assert r.status_code == 400
    assert "Invalid URL" in r.json()["detail"]


async def test_test_abs_allows_private_lan_url(make_client, make_user, auth_header, monkeypatch):
    """Regression guard: the LAN-reaching ABS use case must still work now
    that a guard sits in front of it."""
    admin = await make_user(username="admin1", role="admin")
    _patch_jetson_transport(monkeypatch, _abs_libraries_handler)

    async with make_client(settings_router.router) as c:
        r = await c.post(
            "/api/settings/test-abs", json={"url": "http://192.0.2.60:13378", "token": "tok"},
            headers=auth_header(admin),
        )
    assert r.status_code == 200
    assert r.json()["success"] is True


async def test_test_abs_falls_back_to_saved_token_when_placeholder_sent(
    make_client, make_user, auth_header, enc_key, monkeypatch,
):
    """The UI's token field round-trips the masked GET value -- clicking
    "Test Connection" without retyping the key must test against the real
    stored credential, not send "********" literally to ABS (issue: this
    previously 401'd every time unless the user retyped an unchanged key)."""
    admin = await make_user(username="admin1", role="admin")
    async with async_session() as s:
        await credentials.set_credential(s, "abs", "real-abs-token")
        await s.commit()

    seen_auth = []

    def handler(request):
        seen_auth.append(request.headers.get("authorization"))
        return _abs_libraries_handler(request)

    _patch_jetson_transport(monkeypatch, handler)

    async with make_client(settings_router.router) as c:
        r = await c.post(
            "/api/settings/test-abs", json={"url": "http://192.0.2.60:13378", "token": _SECRET_PLACEHOLDER},
            headers=auth_header(admin),
        )
    assert r.status_code == 200
    assert r.json()["success"] is True
    assert seen_auth == ["Bearer real-abs-token"]


async def test_test_abs_falls_back_to_saved_token_when_no_token_passed(
    make_client, make_user, auth_header, enc_key, monkeypatch,
):
    admin = await make_user(username="admin1", role="admin")
    async with async_session() as s:
        await credentials.set_credential(s, "abs", "real-abs-token")
        await s.commit()
    _patch_jetson_transport(monkeypatch, _abs_libraries_handler)

    async with make_client(settings_router.router) as c:
        r = await c.post(
            "/api/settings/test-abs", json={"url": "http://192.0.2.60:13378"},
            headers=auth_header(admin),
        )
    assert r.status_code == 200
    assert r.json()["success"] is True


# ---------------------------------------------------------------------------
# Hardcover integration (metadata match provider)
# ---------------------------------------------------------------------------

async def test_hardcover_api_token_routed_to_credential_store(
    make_client, make_user, auth_header, enc_key
):
    admin = await make_user(username="admin1", role="admin")
    async with make_client(settings_router.router) as c:
        # Set a real token -> stored (encrypted) in the credential store.
        await c.put("/api/settings/", headers=auth_header(admin),
                    json={"hardcover_api_token": "secret-hc-token"})
        async with async_session() as s:
            assert await credentials.get_credential(s, "hardcover") == "secret-hc-token"

        # Placeholder -> leave unchanged.
        await c.put("/api/settings/", headers=auth_header(admin),
                    json={"hardcover_api_token": _SECRET_PLACEHOLDER})
        async with async_session() as s:
            assert await credentials.get_credential(s, "hardcover") == "secret-hc-token"

        # Empty string -> clear it.
        await c.put("/api/settings/", headers=auth_header(admin),
                    json={"hardcover_api_token": ""})
        async with async_session() as s:
            assert await credentials.get_credential(s, "hardcover") is None


async def test_get_masks_stored_hardcover_token(make_client, make_user, auth_header, enc_key):
    admin = await make_user(username="admin1", role="admin")
    async with make_client(settings_router.router) as c:
        get0 = await c.get("/api/settings/", headers=auth_header(admin))
        assert get0.json()["hardcover_api_token"] == ""  # nothing stored yet

        await c.put("/api/settings/", headers=auth_header(admin),
                    json={"hardcover_api_token": "secret-hc-token"})
        get = await c.get("/api/settings/", headers=auth_header(admin))
    assert get.json()["hardcover_api_token"] == _SECRET_PLACEHOLDER


def _hardcover_me_handler(request: httpx.Request) -> httpx.Response:
    assert request.url.host == "api.hardcover.app"
    if request.headers.get("authorization") == "Bearer correct-token":
        return httpx.Response(200, json={"data": {"me": [{"username": "jesse"}]}})
    return httpx.Response(200, json={"errors": [{"message": "unauthorized"}]})


async def test_test_hardcover_reports_success_with_valid_token(
    make_client, make_user, auth_header, enc_key, monkeypatch
):
    admin = await make_user(username="admin1", role="admin")
    _patch_jetson_transport(monkeypatch, _hardcover_me_handler)

    async with make_client(settings_router.router) as c:
        r = await c.post(
            "/api/settings/test-hardcover", json={"token": "correct-token"},
                        headers=auth_header(admin))
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["username"] == "jesse"


async def test_test_hardcover_reports_auth_failure_with_bad_token(
    make_client, make_user, auth_header, enc_key, monkeypatch
):
    admin = await make_user(username="admin1", role="admin")
    _patch_jetson_transport(monkeypatch, _hardcover_me_handler)

    async with make_client(settings_router.router) as c:
        r = await c.post(
            "/api/settings/test-hardcover", json={"token": "wrong-token"},
                        headers=auth_header(admin))
    assert r.status_code == 400
    assert "authentication failed" in r.json()["detail"].lower()


async def test_test_hardcover_falls_back_to_saved_token_when_placeholder_sent(
    make_client, make_user, auth_header, enc_key, monkeypatch
):
    admin = await make_user(username="admin1", role="admin")
    async with async_session() as s:
        await credentials.set_credential(s, "hardcover", "correct-token")
        await s.commit()
    _patch_jetson_transport(monkeypatch, _hardcover_me_handler)

    async with make_client(settings_router.router) as c:
        r = await c.post(
            "/api/settings/test-hardcover", json={"token": _SECRET_PLACEHOLDER},
                        headers=auth_header(admin))
    assert r.status_code == 200
    assert r.json()["success"] is True


async def test_test_hardcover_requires_a_token(make_client, make_user, auth_header, enc_key):
    admin = await make_user(username="admin1", role="admin")
    async with make_client(settings_router.router) as c:
        r = await c.post(
            "/api/settings/test-hardcover", json={}, headers=auth_header(admin))
    assert r.status_code == 400
    assert "token" in r.json()["detail"].lower()


async def test_test_hardcover_requires_admin(make_client, make_user, auth_header):
    user = await make_user(username="u", role="user")
    async with make_client(settings_router.router) as c:
        r = await c.post(
            "/api/settings/test-hardcover", json={"token": "x"}, headers=auth_header(user))
    assert r.status_code == 403


async def test_test_abs_requires_a_token(make_client, make_user, auth_header, enc_key):
    """No token passed and none saved -> a clear 400, not an unauthenticated
    request sent to ABS."""
    admin = await make_user(username="admin1", role="admin")
    async with make_client(settings_router.router) as c:
        r = await c.post(
            "/api/settings/test-abs", json={"url": "http://192.0.2.60:13378"},
            headers=auth_header(admin),
        )
    assert r.status_code == 400
    assert "token" in r.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Off-hours transcription window (issue #106)
# ---------------------------------------------------------------------------

async def test_test_remote_passes_through_the_workers_model_state(
    make_client, make_user, auth_header, enc_key, monkeypatch
):
    """Since #106 an unloaded model is the idle resting state, so the UI needs
    the worker's own wording rather than inferring a fault from a boolean."""
    admin = await make_user(username="admin1", role="admin")

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "gpu_available": True, "gpu_name": "Orin",
            "model_loaded": False, "model_state": "unloaded",
        })

    _patch_jetson_transport(monkeypatch, _handler)

    async with make_client(settings_router.router) as c:
        r = await c.post(
            "/api/settings/test-remote", json={"url": "http://fake-jetson:9000", "key": "k"},
            headers=auth_header(admin),
        )

    body = r.json()
    assert body["success"] is True
    assert body["model_state"] == "unloaded"


async def test_offhours_defaults_are_exposed_and_disabled(make_client, make_user, auth_header):
    """Default off => transcription behaves exactly as it did before #106."""
    # Admin: the window is operator configuration, admin-only since #263.
    admin = await make_user(username="admin_oh0", role="admin")
    async with make_client(settings_router.router) as c:
        body = (await c.get("/api/settings/", headers=auth_header(admin))).json()
    assert body["transcription_offhours_enabled"] is False
    assert body["transcription_offhours_start"] == "01:00"
    assert body["transcription_offhours_end"] == "07:00"
    assert body["transcription_offhours_timezone"] == "UTC"


async def test_offhours_window_round_trips(make_client, make_user, auth_header):
    admin = await make_user(username="admin1", role="admin")
    async with make_client(settings_router.router) as c:
        put = await c.put("/api/settings/", headers=auth_header(admin), json={
            "transcription_offhours_enabled": True,
            "transcription_offhours_start": "22:00",
            "transcription_offhours_end": "06:00",
            "transcription_offhours_timezone": "America/New_York",
        })
        assert put.status_code == 200
        body = (await c.get("/api/settings/", headers=auth_header(admin))).json()

    assert body["transcription_offhours_enabled"] is True
    assert body["transcription_offhours_start"] == "22:00"
    assert body["transcription_offhours_end"] == "06:00"
    assert body["transcription_offhours_timezone"] == "America/New_York"


@pytest.mark.parametrize("patch,expect_in_detail", [
    ({"transcription_offhours_start": "25:00"}, "hh:mm"),
    ({"transcription_offhours_end": "half past"}, "hh:mm"),
    ({"transcription_offhours_timezone": "Mars/Olympus_Mons"}, "timezone"),
    ({"transcription_offhours_start": "03:00", "transcription_offhours_end": "03:00"}, "differ"),
])
async def test_offhours_invalid_window_rejected_with_400(
    make_client, make_user, auth_header, patch, expect_in_detail
):
    admin = await make_user(username="admin1", role="admin")
    async with make_client(settings_router.router) as c:
        r = await c.put("/api/settings/", headers=auth_header(admin), json=patch)
    assert r.status_code == 400
    assert expect_in_detail in r.json()["detail"].lower()


async def test_offhours_partial_update_checked_against_stored_end(
    make_client, make_user, auth_header
):
    """Changing only `start` to match the persisted `end` must still 400 —
    otherwise a two-step edit produces an ambiguous zero-width window."""
    admin = await make_user(username="admin1", role="admin")
    async with make_client(settings_router.router) as c:
        ok = await c.put("/api/settings/", headers=auth_header(admin), json={
            "transcription_offhours_start": "01:00",
            "transcription_offhours_end": "07:00",
        })
        assert ok.status_code == 200
        r = await c.put("/api/settings/", headers=auth_header(admin),
                        json={"transcription_offhours_start": "07:00"})
    assert r.status_code == 400


async def test_offhours_invalid_window_is_not_persisted(make_client, make_user, auth_header):
    """A rejected PUT must leave every key untouched, including valid siblings
    sent in the same body."""
    admin = await make_user(username="admin1", role="admin")
    async with make_client(settings_router.router) as c:
        r = await c.put("/api/settings/", headers=auth_header(admin), json={
            "whisper_model": "large",
            "transcription_offhours_start": "nope",
        })
        assert r.status_code == 400
        body = (await c.get("/api/settings/", headers=auth_header(admin))).json()
    assert body["whisper_model"] == "medium"
    assert body["transcription_offhours_start"] == "01:00"


# ---------------------------------------------------------------------------
# Issue #284: the secret must not ride in the URL.
#
# These three carried long-lived, non-resource-scoped third-party credentials as
# query parameters, so every call wrote them into the request line and from
# there into uvicorn's access log. That is not theoretical: a
# `test-remote?url=...&key=...` line was found in the production container's log.
#
# Flipping the endpoints to POST is only half the fix -- the assertion that
# matters is that the credential is absent from the URL, which is what these
# pin. Without them a future change could quietly reintroduce a query parameter
# and every other test here would still pass.
# ---------------------------------------------------------------------------

_SECRET = "not-a-real-key-1234567890"


async def test_test_remote_key_never_appears_in_the_url(
    make_client, make_user, auth_header, enc_key
):
    admin = await make_user(username="admin_u1", role="admin")
    async with make_client(settings_router.router) as c:
        r = await c.post(
            "/api/settings/test-remote",
            json={"url": "http://192.0.2.10:9000", "key": _SECRET},
            headers=auth_header(admin),
        )
    assert _SECRET not in str(r.request.url)
    assert not r.request.url.query


async def test_test_abs_token_never_appears_in_the_url(
    make_client, make_user, auth_header, enc_key
):
    admin = await make_user(username="admin_u2", role="admin")
    async with make_client(settings_router.router) as c:
        r = await c.post(
            "/api/settings/test-abs",
            json={"url": "http://192.0.2.11:13378", "token": _SECRET},
            headers=auth_header(admin),
        )
    assert _SECRET not in str(r.request.url)
    assert not r.request.url.query


async def test_test_hardcover_token_never_appears_in_the_url(
    make_client, make_user, auth_header, enc_key
):
    admin = await make_user(username="admin_u3", role="admin")
    async with make_client(settings_router.router) as c:
        r = await c.post(
            "/api/settings/test-hardcover",
            json={"token": _SECRET},
            headers=auth_header(admin),
        )
    assert _SECRET not in str(r.request.url)
    assert not r.request.url.query


async def test_the_old_get_shape_is_gone(make_client, make_user, auth_header, enc_key):
    """Pin the query-string form closed, so it cannot come back unnoticed."""
    admin = await make_user(username="admin_u4", role="admin")
    async with make_client(settings_router.router) as c:
        for path in ("test-abs", "test-hardcover", "test-remote"):
            r = await c.get(f"/api/settings/{path}", params={"token": _SECRET, "key": _SECRET, "url": "http://192.0.2.12"})
            assert r.status_code == 405, f"{path} still answers GET"
