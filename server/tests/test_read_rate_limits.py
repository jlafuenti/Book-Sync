"""
Per-user rate limits on the expensive read endpoints (issue #208).

Tandem runs a **single uvicorn worker**, so a handler that blocks stalls every
other request — login and `/api/health` included, and the compose healthcheck
gives up after 10 s. Several read endpoints stat every file in the library, walk
a data root or shell out, and until #208 nothing outside `/api/auth/*` was rate
limited at all: one authenticated user in a loop could hold the whole deployment
down.

Two layers of this file:

* the bucket itself (`rate_limit.UserRateLimiter`) — a sliding window over
  *every* request, unlike the auth trackers it is built on, which count only
  failures;
* the FastAPI dependency (`routers.auth.rate_limited`) that turns an exhausted
  bucket into a 429 with `Retry-After`, and — the part worth pinning — still
  runs the role check first, so an unauthorized caller gets 401/403 rather than
  learning about the bucket.

Keyed on the user id, never the client address: behind a proxy and docker NAT
every caller shares one address (#294), so an IP bucket would throttle the whole
deployment together.
"""

from fastapi import APIRouter, Depends

import rate_limit
from models.user import User
from routers.auth import get_editor_user, rate_limited


class _FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def _bucket(limit=3, window=60, clock=None):
    return rate_limit.UserRateLimiter(
        limit_supplier=lambda: limit,
        window_supplier=lambda: window,
        clock=clock,
    )


# ---------------------------------------------------------------------------
# The bucket
# ---------------------------------------------------------------------------

def test_requests_under_the_limit_are_allowed():
    bucket = _bucket(limit=3)
    assert [bucket.acquire(7) for _ in range(3)] == [None, None, None]


def test_the_request_over_the_limit_is_refused_with_a_wait():
    bucket = _bucket(limit=3, window=60)
    for _ in range(3):
        bucket.acquire(7)

    retry_after = bucket.acquire(7)
    assert retry_after is not None
    assert 0 < retry_after <= 61


def test_the_window_slides_so_the_bucket_recovers():
    """A limit that never released would be a lockout, not a rate limit."""
    clock = _FakeClock()
    bucket = _bucket(limit=3, window=60, clock=clock)
    for _ in range(3):
        bucket.acquire(7)
    assert bucket.acquire(7) is not None

    clock.advance(61)

    assert bucket.acquire(7) is None


def test_buckets_do_not_leak_between_users():
    """One user's loop must not lock everyone else out — the whole point of
    keying on the user id rather than the shared proxy address."""
    bucket = _bucket(limit=3)
    for _ in range(4):
        bucket.acquire(7)

    assert bucket.acquire(8) is None


def test_retuning_the_limit_takes_effect_without_a_restart():
    limit = {"value": 3}
    bucket = rate_limit.UserRateLimiter(
        limit_supplier=lambda: limit["value"],
        window_supplier=lambda: 60,
    )
    for _ in range(3):
        bucket.acquire(7)
    assert bucket.acquire(7) is not None

    limit["value"] = 10
    assert bucket.acquire(7) is None


def test_reset_clears_every_user():
    bucket = _bucket(limit=1)
    bucket.acquire(7)
    assert bucket.acquire(7) is not None

    bucket.reset()

    assert bucket.acquire(7) is None


def test_the_shipped_buckets_read_their_settings():
    """The three module-level buckets are wired to config, not to constants."""
    from config import settings

    assert rate_limit.expensive_reads.limit == settings.expensive_read_limit
    assert rate_limit.expensive_reads.window == settings.expensive_read_window_seconds
    assert rate_limit.search_reads.limit == settings.search_read_limit
    assert (
        rate_limit.external_metadata_searches.limit
        == settings.external_metadata_search_limit
    )


# ---------------------------------------------------------------------------
# The dependency
# ---------------------------------------------------------------------------

def _limited_router(bucket):
    router = APIRouter()

    @router.get("/limited")
    async def limited(user: User = Depends(rate_limited(bucket, get_editor_user))):
        return {"user": user.id}

    return router


async def test_dependency_returns_429_with_retry_after(
    make_client, make_user, auth_header
):
    bucket = _bucket(limit=2, window=60)
    user = await make_user(username="editor1", role="editor")

    async with make_client(_limited_router(bucket)) as c:
        first = [await c.get("/limited", headers=auth_header(user)) for _ in range(2)]
        over = await c.get("/limited", headers=auth_header(user))

    assert [r.status_code for r in first] == [200, 200]
    assert over.status_code == 429
    assert int(over.headers["Retry-After"]) > 0


async def test_dependency_checks_the_role_before_the_bucket(
    make_client, make_user, auth_header
):
    """A caller who may not use the endpoint gets 403 and spends no allowance.

    Otherwise any logged-in account could exhaust an editor-only bucket for the
    editors — a rate limit turned into a denial-of-service tool.
    """
    bucket = _bucket(limit=1, window=60)
    reader = await make_user(username="reader1", role="user")
    editor = await make_user(username="editor2", role="editor")

    async with make_client(_limited_router(bucket)) as c:
        refused = [
            await c.get("/limited", headers=auth_header(reader)) for _ in range(5)
        ]
        allowed = await c.get("/limited", headers=auth_header(editor))

    assert [r.status_code for r in refused] == [403] * 5
    assert allowed.status_code == 200


async def test_dependency_rejects_anonymous_callers_without_touching_the_bucket(
    make_client
):
    bucket = _bucket(limit=1, window=60)

    async with make_client(_limited_router(bucket)) as c:
        resp = await c.get("/limited")

    assert resp.status_code == 401
    assert bucket.acquire(1) is None
