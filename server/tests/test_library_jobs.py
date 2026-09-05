"""One library-mutating job at a time (issue #202).

`POST /api/library/scan` walks the whole library inside one request. On the
production library that is minutes, and the web proxy gives up on the request
long before the server does — so the operator sees an error, clicks again, and a
second walk starts on top of the first. Both walks read "this path is not in the
database yet" for every file the other has not committed, and before issue #256
put a unique index on `file_path` that produced two rows per book, two auto-made
pairs per book, and a reading position split across them.

The index makes the duplicate unrepresentable; it does not make the second click
*work* — it turns it into an `IntegrityError` storm instead. The fix is to refuse
the second job outright: `/scan`, `/rescan-all`, `/rehash` and `/enrich-abs` all
rewrite the same rows and the same files on disk, so they share one guard and any
of them running blocks all four with 409.

The guard is a module-level flag rather than a lock because the server runs a
single uvicorn worker (`entrypoint.sh`) and refusing is the wanted behaviour —
queueing would leave the second request hanging past the same proxy timeout that
caused the double click.
"""

import asyncio

import pytest

from services import library_jobs
from services.library_jobs import LibraryJobBusy


@pytest.fixture(autouse=True)
def _clear_guard():
    """No test may leak a held job into the next one."""
    library_jobs.reset()
    yield
    library_jobs.reset()


# ------------------------------------------------------------------ the guard


async def test_the_guard_reports_which_job_is_running():
    assert library_jobs.running_job() is None
    async with library_jobs.exclusive("scan"):
        assert library_jobs.running_job() == "scan"
    assert library_jobs.running_job() is None


async def test_a_second_job_is_refused_while_one_is_held():
    async with library_jobs.exclusive("scan"):
        with pytest.raises(LibraryJobBusy) as caught:
            async with library_jobs.exclusive("rehash"):
                pass
    assert caught.value.running == "scan"


async def test_the_guard_is_released_when_the_job_raises():
    with pytest.raises(ValueError):
        async with library_jobs.exclusive("scan"):
            raise ValueError("scan blew up")

    assert library_jobs.running_job() is None
    # And the next job may start.
    async with library_jobs.exclusive("rescan-all"):
        pass


# ------------------------------------------------------------- the endpoints

JOB_ENDPOINTS = ["/api/library/scan", "/api/library/rescan-all",
                 "/api/library/rehash", "/api/library/enrich-abs"]


@pytest.fixture
def editor_client(make_client, make_user, auth_header):
    """An httpx client already carrying an editor's bearer token."""
    import contextlib

    from routers import library

    @contextlib.asynccontextmanager
    async def _factory():
        user = await make_user(username="scanner", role="admin")
        async with make_client(library.router) as c:
            c.headers.update(auth_header(user))
            yield c

    return _factory


@pytest.mark.parametrize("endpoint", JOB_ENDPOINTS)
async def test_a_library_job_is_refused_while_a_scan_runs(endpoint, editor_client):
    """Every one of the four rewrites the same rows, so they share the guard."""
    async with editor_client() as c:
        async with library_jobs.exclusive("scan"):
            resp = await c.post(endpoint)

    assert resp.status_code == 409, resp.text
    assert "already running" in resp.json()["detail"]


async def test_two_overlapping_scans_do_not_interleave(editor_client, monkeypatch):
    """The real shape of the bug: a second POST while the first walk is mid-flight.

    The first scan is parked inside its walk so the second one arrives while the
    guard is genuinely held by a request rather than by the test.
    """
    from routers import library

    started = asyncio.Event()
    release = asyncio.Event()

    async def _slow_scan(db):
        started.set()
        await release.wait()
        return library.LibraryScanResponse(
            new_ebooks=0, new_audiobooks=0, auto_matched_pairs=0,
            multi_file_folders=0, message="done",
        )

    monkeypatch.setattr(library, "scan_library_impl", _slow_scan)

    async with editor_client() as c:
        first = asyncio.create_task(c.post("/api/library/scan"))
        await asyncio.wait_for(started.wait(), timeout=5)

        second = await c.post("/api/library/scan")
        assert second.status_code == 409, second.text

        release.set()
        assert (await asyncio.wait_for(first, timeout=5)).status_code == 200

        # The guard is free again, so a scan after the first one finishes works.
        assert (await c.post("/api/library/scan")).status_code == 200


async def test_the_guard_is_released_when_the_scan_fails(editor_client, monkeypatch):
    """A crashed scan must not lock the library out until the next restart."""
    from routers import library

    async def _boom(db):
        raise RuntimeError("disk went away")

    monkeypatch.setattr(library, "scan_library_impl", _boom)

    async with editor_client() as c:
        with pytest.raises(RuntimeError):
            await c.post("/api/library/scan")

    assert library_jobs.running_job() is None
