"""
Match router tests.

Provider fetches (google / openlibrary / audible / hardcover) make outbound
HTTP calls; those are redirected to an httpx.MockTransport so each provider's
field mapping — including series name/number, genres, tags, narrators — is
asserted against realistic fixture payloads.
"""

import httpx
import pytest
from cryptography.fernet import Fernet

from config import settings as app_settings
from database import async_session
from routers import match
from services import credentials


def _patch_outbound_transport(monkeypatch, handler):
    """Redirect outbound httpx.AsyncClient calls to a mock transport, leaving
    the test harness's own ASGI client (which always passes transport=) alone.
    Same trick as test_settings._patch_jetson_transport."""
    transport = httpx.MockTransport(handler)
    real_async_client = httpx.AsyncClient

    def fake_async_client(*args, **kwargs):
        if "transport" in kwargs:
            return real_async_client(*args, **kwargs)
        kwargs.pop("timeout", None)
        return real_async_client(*args, transport=transport, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", fake_async_client)


@pytest.fixture
def enc_key(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(app_settings, "credential_enc_keys", key)
    return key


async def test_search_invalid_provider_returns_400(make_client, make_user, auth_header):
    user = await make_user(username="u", role="user")
    async with make_client(match.router) as c:
        r = await c.post("/match/search", headers=auth_header(user),
                         json={"provider": "bogus", "query": "Dune", "author": None})
    assert r.status_code == 400


async def test_search_requires_auth(make_client):
    async with make_client(match.router) as c:
        r = await c.post("/match/search",
                         json={"provider": "google", "query": "Dune", "author": None})
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# Audible provider
# ---------------------------------------------------------------------------

_AUDIBLE_PRODUCT = {
    "asin": "B002UZKL8W",
    "title": "The Name of the Wind",
    "authors": [{"asin": "B000AP9A6K", "name": "Patrick Rothfuss"}],
    "narrators": [{"name": "Nick Podehl"}],
    "series": [{"asin": "B0BLV8W4S8", "sequence": "1", "title": "The Kingkiller Chronicle"}],
    "publisher_name": "Audible Studios",
    "release_date": "2009-05-14",
    "publisher_summary": "<p>Told in Kvothe's <b>own voice</b>, this is the tale.</p>",
    "merchandising_summary": "<p>Short blurb.</p>",
    "runtime_length_min": 1673,
    "language": "english",
    "category_ladders": [
        {"ladder": [{"id": "1", "name": "Science Fiction & Fantasy"},
                    {"id": "2", "name": "Fantasy"}], "root": "Genres"},
        {"ladder": [{"id": "1", "name": "Science Fiction & Fantasy"},
                    {"id": "3", "name": "Epic"}], "root": "Genres"},
    ],
    "product_images": {"500": "https://m.media-amazon.com/images/I/cover._SL500_.jpg"},
}


def _audible_handler(request: httpx.Request) -> httpx.Response:
    assert request.url.host == "api.audible.com"
    assert request.url.path == "/1.0/catalog/products"
    assert "series" in request.url.params.get("response_groups", "")
    return httpx.Response(200, json={"products": [_AUDIBLE_PRODUCT], "total_results": 1})


async def test_audible_maps_series_and_rich_fields(make_client, make_user, auth_header, monkeypatch):
    user = await make_user(username="u", role="user")
    _patch_outbound_transport(monkeypatch, _audible_handler)

    async with make_client(match.router) as c:
        r = await c.post("/match/search", headers=auth_header(user),
                         json={"provider": "audible", "query": "name of the wind",
                               "author": "Rothfuss"})
    assert r.status_code == 200
    (res,) = r.json()
    assert res["id"] == "B002UZKL8W"
    assert res["asin"] == "B002UZKL8W"
    assert res["title"] == "The Name of the Wind"
    assert res["author"] == "Patrick Rothfuss"
    assert res["narrators"] == "Nick Podehl"
    assert res["series"] == "The Kingkiller Chronicle"
    assert res["series_index"] == 1.0
    assert res["publisher"] == "Audible Studios"
    assert res["publish_year"] == 2009
    # HTML stripped from the publisher summary.
    assert res["description"] == "Told in Kvothe's own voice, this is the tale."
    # Category ladder names, deduped, order preserved.
    assert res["genres"] == "Science Fiction & Fantasy, Fantasy, Epic"
    assert res["language"] == "English"
    assert res["duration_seconds"] == 1673 * 60
    assert res["cover_url"] == "https://m.media-amazon.com/images/I/cover._SL500_.jpg"


async def test_audible_unparseable_sequence_keeps_series_name(
    make_client, make_user, auth_header, monkeypatch
):
    """Bundles report sequences like "1-3" — keep the series name, skip the number."""
    user = await make_user(username="u", role="user")
    product = dict(_AUDIBLE_PRODUCT,
                   series=[{"sequence": "1-3", "title": "The Kingkiller Chronicle"}])

    def handler(request):
        return httpx.Response(200, json={"products": [product]})

    _patch_outbound_transport(monkeypatch, handler)
    async with make_client(match.router) as c:
        r = await c.post("/match/search", headers=auth_header(user),
                         json={"provider": "audible", "query": "kingkiller"})
    (res,) = r.json()
    assert res["series"] == "The Kingkiller Chronicle"
    assert res["series_index"] is None


async def test_audible_upstream_error_returns_502(make_client, make_user, auth_header, monkeypatch):
    user = await make_user(username="u", role="user")

    def handler(request):
        return httpx.Response(500, json={"message": "boom"})

    _patch_outbound_transport(monkeypatch, handler)
    async with make_client(match.router) as c:
        r = await c.post("/match/search", headers=auth_header(user),
                         json={"provider": "audible", "query": "kingkiller"})
    assert r.status_code == 502


# ---------------------------------------------------------------------------
# Hardcover provider
# ---------------------------------------------------------------------------

_HARDCOVER_DOCUMENT = {
    "id": "313528",
    "title": "The Name of the Wind",
    "author_names": ["Patrick Rothfuss"],
    "description": "Told in Kvothe's own voice, this is the tale.",
    "featured_series": {"series_name": "The Kingkiller Chronicle", "position": 1},
    "featured_series_position": 1,
    "genres": ["Fantasy", "Fiction"],
    "tags": ["Epic Fantasy", "Magic"],
    "isbns": ["9780756404741", "0756404746"],
    "release_year": 2007,
    "image": {"url": "https://assets.hardcover.app/external_data/cover.jpg"},
    "slug": "the-name-of-the-wind",
}


def _hardcover_handler(request: httpx.Request) -> httpx.Response:
    assert request.url.host == "api.hardcover.app"
    assert request.url.path == "/v1/graphql"
    assert request.headers.get("authorization") == "Bearer hc-test-token"
    return httpx.Response(200, json={
        "data": {"search": {"results": {"found": 1, "hits": [
            {"document": _HARDCOVER_DOCUMENT}
        ]}}}
    })


async def test_hardcover_maps_series_and_rich_fields(
    make_client, make_user, auth_header, monkeypatch, enc_key
):
    user = await make_user(username="u", role="user")
    async with async_session() as s:
        await credentials.set_credential(s, "hardcover", "hc-test-token")
        await s.commit()
    _patch_outbound_transport(monkeypatch, _hardcover_handler)

    async with make_client(match.router) as c:
        r = await c.post("/match/search", headers=auth_header(user),
                         json={"provider": "hardcover", "query": "name of the wind",
                               "author": "Rothfuss"})
    assert r.status_code == 200
    (res,) = r.json()
    assert res["id"] == "313528"
    assert res["title"] == "The Name of the Wind"
    assert res["author"] == "Patrick Rothfuss"
    assert res["series"] == "The Kingkiller Chronicle"
    assert res["series_index"] == 1.0
    assert res["publish_year"] == 2007
    assert res["description"] == "Told in Kvothe's own voice, this is the tale."
    assert res["genres"] == "Fantasy, Fiction"
    assert res["tags"] == "Epic Fantasy, Magic"
    assert res["isbn"] == "9780756404741"
    assert res["cover_url"] == "https://assets.hardcover.app/external_data/cover.jpg"


async def test_hardcover_without_token_returns_400(
    make_client, make_user, auth_header, monkeypatch, enc_key
):
    user = await make_user(username="u", role="user")

    def handler(request):
        raise AssertionError("no outbound request should be made without a token")

    _patch_outbound_transport(monkeypatch, handler)
    async with make_client(match.router) as c:
        r = await c.post("/match/search", headers=auth_header(user),
                         json={"provider": "hardcover", "query": "anything"})
    assert r.status_code == 400
    assert "hardcover" in r.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Existing providers: previously-ignored fields
# ---------------------------------------------------------------------------

async def test_google_books_maps_categories_to_genres(make_client, make_user, auth_header, monkeypatch):
    user = await make_user(username="u", role="user")

    def handler(request):
        assert request.url.host == "www.googleapis.com"
        return httpx.Response(200, json={"items": [{
            "id": "abc123",
            "volumeInfo": {
                "title": "Dune",
                "authors": ["Frank Herbert"],
                "publishedDate": "1965-08-01",
                "categories": ["Fiction / Science Fiction", "Classics"],
                "description": "Spice.",
            },
        }]})

    _patch_outbound_transport(monkeypatch, handler)
    async with make_client(match.router) as c:
        r = await c.post("/match/search", headers=auth_header(user),
                         json={"provider": "google", "query": "dune"})
    (res,) = r.json()
    assert res["genres"] == "Fiction / Science Fiction, Classics"


async def test_open_library_maps_subjects_to_tags(make_client, make_user, auth_header, monkeypatch):
    user = await make_user(username="u", role="user")

    def handler(request):
        assert request.url.host == "openlibrary.org"
        return httpx.Response(200, json={"docs": [{
            "key": "/works/OW1",
            "title": "Dune",
            "author_name": ["Frank Herbert"],
            "subject": ["Science fiction", "Deserts", "Politics"],
        }]})

    _patch_outbound_transport(monkeypatch, handler)
    async with make_client(match.router) as c:
        r = await c.post("/match/search", headers=auth_header(user),
                         json={"provider": "openlibrary", "query": "dune"})
    (res,) = r.json()
    assert res["tags"] == "Science fiction, Deserts, Politics"
