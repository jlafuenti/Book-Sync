"""
Match router tests (issue #46, Phase 3).

Only the input-validation branch of /search is unit-testable — the "google" and
"openlibrary" providers make outbound HTTP calls to external metadata APIs and
are intentionally left to manual verification.
"""

import pytest

from routers import match


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
