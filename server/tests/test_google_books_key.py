"""Issue #739 follow-up: the Google Books API key can be set on the System page.

Stored like the Hardcover token: encrypted in the credential store, masked on
read, the placeholder meaning "unchanged". A key saved there wins over the
`GOOGLE_BOOKS_API_KEY` environment variable, which keeps working as before.
Both Google Books callers - the Match search and the print page lookup - use it.
"""
import httpx
import pytest
from cryptography.fernet import Fernet

from config import settings as app_settings
from database import async_session
from routers import library, match, settings as settings_router
from services import credentials, google_books, print_pages

PLACEHOLDER = "********"


@pytest.fixture(autouse=True)
def enc_key(monkeypatch):
    monkeypatch.setattr(app_settings, "credential_enc_keys", Fernet.generate_key().decode())
    monkeypatch.setattr(app_settings, "google_books_api_key", None)


async def _store(key):
    async with async_session() as s:
        await credentials.set_credential(s, google_books.CREDENTIAL, key)
        await s.commit()


def _google(monkeypatch, handler):
    """Send the code's outbound httpx calls to `handler`; leave test clients alone."""
    transport = httpx.MockTransport(handler)
    real = httpx.AsyncClient

    def fake(*args, **kwargs):
        if "transport" in kwargs:
            return real(*args, **kwargs)
        kwargs.pop("timeout", None)
        return real(*args, transport=transport, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", fake)


@pytest.fixture
async def admin(make_client, make_user, auth_header):
    user = await make_user(username="adm", role="admin")
    async with make_client(settings_router.router, library.router) as c:
        yield c, auth_header(user)


# ---------- which key ----------

async def test_no_key_anywhere_is_none():
    assert await google_books.api_key() is None


async def test_the_environment_key_is_used_when_none_is_saved(monkeypatch):
    monkeypatch.setattr(app_settings, "google_books_api_key", "env-key")
    assert await google_books.api_key() == "env-key"


async def test_a_saved_key_wins_over_the_environment(monkeypatch):
    monkeypatch.setattr(app_settings, "google_books_api_key", "env-key")
    await _store("saved-key")
    assert await google_books.api_key() == "saved-key"


# ---------- the settings API ----------

async def test_an_admin_saves_clears_and_never_reads_back_the_key(admin):
    c, h = admin
    before = (await c.get("/api/settings/", headers=h)).json()
    assert before["google_books_api_key"] == ""
    assert before["google_books_key_from_env"] is False

    await c.put("/api/settings/", headers=h, json={"google_books_api_key": "saved-key"})
    assert await google_books.api_key() == "saved-key"
    body = (await c.get("/api/settings/", headers=h)).json()
    assert body["google_books_api_key"] == PLACEHOLDER
    assert "saved-key" not in body.values()

    # Round-tripping the masked value leaves the key alone.
    await c.put("/api/settings/", headers=h, json={"google_books_api_key": PLACEHOLDER})
    assert await google_books.api_key() == "saved-key"

    await c.put("/api/settings/", headers=h, json={"google_books_api_key": ""})
    assert await google_books.api_key() is None


async def test_the_settings_say_when_the_key_comes_from_the_environment(admin, monkeypatch):
    c, h = admin
    monkeypatch.setattr(app_settings, "google_books_api_key", "env-key")
    body = (await c.get("/api/settings/", headers=h)).json()
    assert body["google_books_api_key"] == ""
    assert body["google_books_key_from_env"] is True
    assert "env-key" not in body.values()
    # A derived value, never stored as a setting row.
    await c.put("/api/settings/", headers=h, json={"google_books_key_from_env": False})
    monkeypatch.setattr(app_settings, "google_books_api_key", None)
    assert (await c.get("/api/settings/", headers=h)).json()["google_books_key_from_env"] is False


async def test_a_plain_user_sees_nothing_of_the_key(make_client, make_user, auth_header):
    user = await make_user(username="reader", role="user")
    await _store("saved-key")
    async with make_client(settings_router.router) as c:
        body = (await c.get("/api/settings/", headers=auth_header(user))).json()
    assert "google_books_api_key" not in body
    assert "google_books_key_from_env" not in body


# ---------- testing the key ----------

def _volumes_handler(request: httpx.Request) -> httpx.Response:
    assert request.url.host == "www.googleapis.com"
    key = request.url.params.get("key")
    if key == "good":
        return httpx.Response(200, json={"totalItems": 1, "items": []})
    if key == "spent":
        return httpx.Response(429, json={"error": {"errors": [{"reason": "rateLimitExceeded"}]}})
    return httpx.Response(400, json={"error": {"errors": [{"reason": "keyInvalid"}]}})


async def test_a_good_key_passes_the_test(admin, monkeypatch):
    c, h = admin
    _google(monkeypatch, _volumes_handler)
    r = await c.post("/api/settings/test-google-books", headers=h, json={"key": "good"})
    assert r.status_code == 200, r.text
    assert r.json()["success"] is True


async def test_a_rejected_key_fails_the_test(admin, monkeypatch):
    c, h = admin
    _google(monkeypatch, _volumes_handler)
    r = await c.post("/api/settings/test-google-books", headers=h, json={"key": "bad"})
    assert r.status_code == 400
    assert "rejected" in r.json()["detail"].lower()


async def test_a_spent_key_says_the_limit_is_used_up(admin, monkeypatch):
    c, h = admin
    _google(monkeypatch, _volumes_handler)
    r = await c.post("/api/settings/test-google-books", headers=h, json={"key": "spent"})
    assert r.status_code == 400
    assert "limit" in r.json()["detail"].lower()


async def test_the_placeholder_tests_the_saved_key(admin, monkeypatch):
    c, h = admin
    await _store("good")
    _google(monkeypatch, _volumes_handler)
    r = await c.post("/api/settings/test-google-books", headers=h, json={"key": PLACEHOLDER})
    assert r.status_code == 200, r.text


async def test_testing_with_no_key_at_all_asks_for_one(admin):
    c, h = admin
    r = await c.post("/api/settings/test-google-books", headers=h, json={"key": ""})
    assert r.status_code == 400
    assert "key" in r.json()["detail"].lower()


async def test_only_an_admin_tests_the_key(make_client, make_user, auth_header):
    editor = await make_user(username="ed", role="editor")
    async with make_client(settings_router.router) as c:
        r = await c.post("/api/settings/test-google-books", headers=auth_header(editor), json={"key": "x"})
    assert r.status_code == 403


# ---------- the callers use it ----------

async def test_the_print_page_lookup_sends_the_saved_key(monkeypatch):
    await _store("saved-key")
    seen = []

    def handler(request):
        seen.append(request.url.params.get("key"))
        return httpx.Response(200, json={"items": []})

    _google(monkeypatch, handler)
    await print_pages.google_volumes("isbn:9780000000002")
    assert seen == ["saved-key"]


async def test_the_print_page_status_counts_a_saved_key(admin):
    c, h = admin
    assert (await c.get("/api/library/print-pages/status", headers=h)).json()["api_key_configured"] is False
    await _store("saved-key")
    assert (await c.get("/api/library/print-pages/status", headers=h)).json()["api_key_configured"] is True


async def test_the_match_search_sends_the_saved_key(monkeypatch):
    await _store("saved-key")
    seen = []

    def handler(request):
        seen.append(request.url.params.get("key"))
        return httpx.Response(200, json={"items": []})

    _google(monkeypatch, handler)
    async with async_session() as s:
        await match.fetch_google_books("Some Title", None, db=s)
    assert seen == ["saved-key"]
