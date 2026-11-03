"""Per-account "Next up" dismissals (issue #921)."""

import asyncio

import pytest_asyncio
from sqlalchemy import func, select

from database import async_session
from models.next_up_dismissal import NextUpDismissal
from routers import next_up

URL = "/api/next-up/dismissals"


async def _count():
    async with async_session() as s:
        return (await s.execute(select(func.count()).select_from(NextUpDismissal))).scalar_one()


@pytest_asyncio.fixture
async def ctx(make_client, make_user, auth_header):
    user = await make_user("reader")
    other = await make_user("other")
    async with make_client(next_up.router) as c:
        yield c, auth_header(user), auth_header(other)


async def test_list_empty(ctx):
    c, h, _ = ctx
    r = await c.get(URL, headers=h)
    assert r.status_code == 200 and r.json() == []


async def test_post_creates_and_returns_series_as_sent(ctx):
    c, h, _ = ctx
    r = await c.post(URL, json={"series": "  Axis Saga "}, headers=h)
    assert r.status_code == 200
    body = r.json()
    assert body["series"] == "Axis Saga" and body["dismissed_at"]
    assert (await c.get(URL, headers=h)).json()[0]["series"] == "Axis Saga"


async def test_post_again_updates_time_and_keeps_one_row(ctx):
    c, h, _ = ctx
    first = (await c.post(URL, json={"series": "Axis Saga"}, headers=h)).json()
    await asyncio.sleep(0.01)
    second = (await c.post(URL, json={"series": "  AXIS saga  "}, headers=h)).json()
    assert second["series"] == "AXIS saga"
    assert second["dismissed_at"] > first["dismissed_at"]
    assert await _count() == 1


async def test_list_newest_first(ctx):
    c, h, _ = ctx
    await c.post(URL, json={"series": "One"}, headers=h)
    await asyncio.sleep(0.01)
    await c.post(URL, json={"series": "Two"}, headers=h)
    assert [d["series"] for d in (await c.get(URL, headers=h)).json()] == ["Two", "One"]


async def test_delete_removes_case_insensitively(ctx):
    c, h, _ = ctx
    await c.post(URL, json={"series": "Axis Saga"}, headers=h)
    r = await c.delete(URL, params={"series": " axis SAGA "}, headers=h)
    assert r.status_code == 204
    assert (await c.get(URL, headers=h)).json() == []


async def test_delete_missing_is_404(ctx):
    c, h, _ = ctx
    r = await c.delete(URL, params={"series": "Nope"}, headers=h)
    assert r.status_code == 404


async def test_blank_series_is_422(ctx):
    c, h, _ = ctx
    assert (await c.post(URL, json={"series": "   "}, headers=h)).status_code == 422
    assert (await c.post(URL, json={"series": ""}, headers=h)).status_code == 422
    assert (await c.delete(URL, params={"series": "  "}, headers=h)).status_code == 422


async def test_other_users_rows_invisible_and_undeletable(ctx):
    c, h, other = ctx
    await c.post(URL, json={"series": "Axis Saga"}, headers=h)
    assert (await c.get(URL, headers=other)).json() == []
    assert (await c.delete(URL, params={"series": "Axis Saga"}, headers=other)).status_code == 404
    assert len((await c.get(URL, headers=h)).json()) == 1
    # The same series for the other user is a separate row.
    await c.post(URL, json={"series": "Axis Saga"}, headers=other)
    assert await _count() == 2


async def test_unauthenticated_is_401(ctx):
    c, _, _ = ctx
    assert (await c.get(URL)).status_code == 401
    assert (await c.post(URL, json={"series": "x"})).status_code == 401
    assert (await c.delete(URL, params={"series": "x"})).status_code == 401
