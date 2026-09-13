"""
Whether a newer Tandem release exists (issue #463).

A running server had no way to tell its operator it was out of date. The issue
first proposed stamping the commit into every build and asking GitHub how far
behind `main` it was. That was a workaround for a project with no releases —
and it contradicted `docs/releasing.md`, which already defines one semantic
version for server, web and Android, `vX.Y.Z` tags, and GitHub Releases.

So this does what comparable self-hosted software does, checked against their
source: Paperless-ngx asks `releases/latest` and compares with
`packaging.version`; Audiobookshelf parses each release `tag_name` as semver.
A release is itself the deliberate "worth updating to" signal, which removes
the need to filter out commits that touch only Android, CI or docs.

It is **opt-in**. When enabled, the server contacts api.github.com, which sees
the server's address. `docs/privacy.md` promises no unprompted outbound calls,
so disabled must mean no request at all — not one that is made and ignored.
"""

import asyncio

import httpx
import pytest

from models.settings import SystemSetting
from services import update_check as uc
from version import APP_VERSION


@pytest.fixture(autouse=True)
def _fresh_state(monkeypatch):
    """Module state is process-wide on purpose (the server is single-process);
    tests must not see each other's results."""
    monkeypatch.setattr(uc, "_state", uc._initial_state())
    monkeypatch.setattr(uc, "_worker_state", uc._initial_worker_state())


def _serve(monkeypatch, handler):
    """Route the check's outbound request to [handler]; record what was asked."""
    seen = []

    def recording(request):
        seen.append(request)
        return handler(request)

    transport = httpx.MockTransport(recording)
    real = httpx.AsyncClient

    def fake(*args, **kwargs):
        if "transport" in kwargs:
            return real(*args, **kwargs)  # a test harness's own client
        kwargs.pop("timeout", None)
        return real(*args, transport=transport, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", fake)
    return seen


def _release(tag: str, url: str = "https://github.com/jlafuenti/Book-Sync/releases/tag/x"):
    return lambda request: httpx.Response(200, json={"tag_name": tag, "html_url": url})


async def _enable(db, value: bool = True):
    db.add(SystemSetting(key="update_check_enabled", value=str(value)))
    await db.commit()


# ---------------------------------------------------------------------------
# The rule: is the latest release newer than what is running?
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("running, tag, expected", [
    ("0.1.0", "v0.2.0", "available"),
    ("0.1.0", "v0.1.1", "available"),
    ("0.1.0", "v1.0.0", "available"),
    ("0.1.0", "v0.1.0", "current"),
    ("0.2.0", "v0.1.0", "current"),   # running ahead of the latest release, e.g. a dev build
])
def test_newer_equal_and_older(running, tag, expected):
    assert uc.update_status(running, tag) == expected


def test_versions_compare_numerically_not_as_strings():
    """"0.10.0" < "0.9.0" as strings. That bug would silently hide every release from 0.10 on."""
    assert uc.update_status("0.9.0", "v0.10.0") == "available"
    assert uc.update_status("0.10.0", "v0.9.0") == "current"


def test_a_tag_without_the_v_prefix_still_compares():
    assert uc.update_status("0.1.0", "0.2.0") == "available"


@pytest.mark.parametrize("tag", [None, "", "latest", "release-2026-09", "vNEXT"])
def test_no_tag_or_an_unrecognisable_one_is_unknown(tag):
    """Guessing here would put a false banner in front of an operator."""
    assert uc.update_status("0.1.0", tag) == "unknown"


@pytest.mark.parametrize("tag", ["v0.2.0rc1", "v0.2.0a1", "v0.2.0.dev3"])
def test_a_pre_release_is_never_offered(tag):
    """`releases/latest` already excludes pre-releases; this guards the day that changes."""
    assert uc.update_status("0.1.0", tag) == "unknown"


def test_the_running_version_is_the_real_one():
    """The check compares against `version.APP_VERSION`, not a second copy of it."""
    assert uc.running_version() == APP_VERSION


# ---------------------------------------------------------------------------
# Disabled means no request, ever
# ---------------------------------------------------------------------------


async def test_disabled_by_default_makes_no_request(monkeypatch, db):
    def handler(request):
        raise AssertionError("the update check contacted GitHub while disabled")

    seen = _serve(monkeypatch, handler)
    await uc._tick()
    assert seen == []


async def test_explicitly_disabled_makes_no_request(monkeypatch, db):
    await _enable(db, False)

    def handler(request):
        raise AssertionError("the update check contacted GitHub while disabled")

    seen = _serve(monkeypatch, handler)
    await uc._tick()
    assert seen == []


# ---------------------------------------------------------------------------
# Enabled: what it asks, and what it concludes
# ---------------------------------------------------------------------------


async def test_it_asks_the_project_s_latest_release(monkeypatch, db):
    await _enable(db)
    seen = _serve(monkeypatch, _release("v0.1.0"))

    await uc._tick()

    assert len(seen) == 1
    assert str(seen[0].url) == "https://api.github.com/repos/jlafuenti/Book-Sync/releases/latest"
    assert seen[0].method == "GET"


async def test_a_newer_release_is_reported_available(monkeypatch, db):
    await _enable(db)
    _serve(monkeypatch, _release("v99.0.0", "https://github.com/jlafuenti/Book-Sync/releases/tag/v99.0.0"))

    await uc._tick()
    status = uc.get_status(enabled=True, prompted=True)

    assert status["status"] == "available"
    assert status["latest_version"] == "99.0.0"
    assert status["release_url"] == "https://github.com/jlafuenti/Book-Sync/releases/tag/v99.0.0"
    assert status["checked_at"] is not None


async def test_the_running_release_is_reported_current(monkeypatch, db):
    await _enable(db)
    _serve(monkeypatch, _release(f"v{APP_VERSION}"))

    await uc._tick()

    assert uc.get_status(enabled=True, prompted=True)["status"] == "current"


async def test_no_releases_yet_is_unknown_with_a_reason(monkeypatch, db):
    """GitHub answers 404 for `releases/latest` until the first release exists.

    That is the state of this project today, so it is the most likely answer an
    operator gets — it must read as "none published yet", not as an error.
    """
    await _enable(db)
    _serve(monkeypatch, lambda request: httpx.Response(404, json={"message": "Not Found"}))

    await uc._tick()
    status = uc.get_status(enabled=True, prompted=True)

    assert status["status"] == "unknown"
    assert status["reason"] == "no_releases"


@pytest.mark.parametrize("code", [403, 429])
async def test_a_rate_limit_is_unknown_and_does_not_raise(monkeypatch, db, code):
    await _enable(db)
    _serve(monkeypatch, lambda request: httpx.Response(code, json={"message": "rate limited"}))

    await uc._tick()
    status = uc.get_status(enabled=True, prompted=True)

    assert status["status"] == "unknown"
    assert status["reason"] == "rate_limited"


async def test_an_unreachable_github_is_unknown_and_does_not_raise(monkeypatch, db):
    await _enable(db)

    def handler(request):
        raise httpx.ConnectTimeout("timed out", request=request)

    _serve(monkeypatch, handler)

    await uc._tick()  # must not raise into the scheduler loop
    status = uc.get_status(enabled=True, prompted=True)

    assert status["status"] == "unknown"
    assert status["reason"] == "unreachable"


async def test_a_server_error_is_unknown(monkeypatch, db):
    await _enable(db)
    _serve(monkeypatch, lambda request: httpx.Response(502, text="bad gateway"))

    await uc._tick()

    assert uc.get_status(enabled=True, prompted=True)["reason"] == "unreachable"


async def test_a_malformed_answer_is_unknown(monkeypatch, db):
    await _enable(db)
    _serve(monkeypatch, lambda request: httpx.Response(200, text="<html>not json</html>"))

    await uc._tick()

    assert uc.get_status(enabled=True, prompted=True)["status"] == "unknown"


async def test_an_unrecognisable_tag_is_unknown(monkeypatch, db):
    await _enable(db)
    _serve(monkeypatch, _release("nightly"))

    await uc._tick()
    status = uc.get_status(enabled=True, prompted=True)

    assert status["status"] == "unknown"
    assert status["reason"] == "unrecognised_version"


async def test_a_failed_check_does_not_erase_a_good_one(monkeypatch, db):
    """A blip on GitHub's side must not turn a known "update available" into nothing.

    The operator would see the banner vanish and reappear hours later, which reads
    like the update was withdrawn.
    """
    await _enable(db)
    _serve(monkeypatch, _release("v99.0.0"))
    await uc._tick()

    def down(request):
        raise httpx.ConnectError("down", request=request)

    _serve(monkeypatch, down)
    await uc._tick()
    status = uc.get_status(enabled=True, prompted=True)

    assert status["status"] == "available"
    assert status["latest_version"] == "99.0.0"


# ---------------------------------------------------------------------------
# What the page is told
# ---------------------------------------------------------------------------


async def test_a_disabled_check_reports_nothing_it_learned_earlier(monkeypatch, db):
    """Turning the check off must take the banner down, not freeze it."""
    await _enable(db)
    _serve(monkeypatch, _release("v99.0.0"))
    await uc._tick()

    status = uc.get_status(enabled=False, prompted=True)

    assert status["enabled"] is False
    assert status["status"] == "unknown"
    assert status["reason"] == "disabled"
    assert status["latest_version"] is None


def test_before_the_first_check_it_says_so():
    status = uc.get_status(enabled=True, prompted=True)
    assert status["status"] == "unknown"
    assert status["reason"] == "not_checked_yet"
    assert status["running_version"] == APP_VERSION


async def test_check_now_runs_one_check_on_demand(monkeypatch, db):
    """Used right after an admin enables it, so they don't wait hours to learn it works."""
    seen = _serve(monkeypatch, _release("v99.0.0"))

    await uc.check_now()

    assert len(seen) == 1
    assert uc.get_status(enabled=True, prompted=True)["status"] == "available"


async def test_kick_schedules_a_check_without_blocking_the_caller(monkeypatch, db):
    """The settings request that enables the check must not wait on GitHub."""
    started = asyncio.Event()

    async def fake_check_now():
        started.set()

    monkeypatch.setattr(uc, "check_now", fake_check_now)

    task = uc.kick()
    await asyncio.wait_for(task, timeout=1)

    assert started.is_set()


# ---------------------------------------------------------------------------
# The transcription worker's version
# ---------------------------------------------------------------------------
#
# The Jetson worker is deployed by hand, separately from the server, and until
# now nothing compared the two. A worker left behind after a server upgrade
# fails in ways that do not name the cause. The scheduler asks the configured
# worker for its `worker_version` (from `/v1/health`) on every tick and the
# System page says when it is behind the server. This is the operator's own
# machine, so it needs no opt-in: the server already calls it for every job.


@pytest.mark.parametrize("server, worker, expected", [
    ("0.2.0", "0.1.0", "behind"),
    ("0.1.1", "0.1.0", "behind"),
    ("0.10.0", "0.9.0", "behind"),      # numeric, not string, comparison
    ("0.1.0", "0.1.0", "current"),
    ("0.1.0", "0.2.0", "current"),      # a worker ahead of the server is not a problem to flag
    ("0.1.0", None, "unknown"),
    ("0.1.0", "", "unknown"),
    ("0.1.0", "latest", "unknown"),
])
def test_worker_behind_current_or_unknown(server, worker, expected):
    assert uc.worker_status(server, worker) == expected


async def _configure_worker(db, monkeypatch, url="http://worker.example:9000", key="shared-secret"):
    db.add(SystemSetting(key="transcription_remote_url", value=url))
    await db.commit()

    async def fake_key(_db, source_key):
        assert source_key == "transcription_remote"
        return key

    monkeypatch.setattr(uc.credential_store, "get_credential", fake_key)


def _worker_health(version):
    payload = {"status": "healthy", "model_state": "unloaded"}
    if version is not None:
        payload["worker_version"] = version
    return lambda request: httpx.Response(200, json=payload)


async def test_no_worker_configured_means_no_probe(monkeypatch, db):
    def handler(request):
        raise AssertionError(f"probed {request.url} with no worker configured")

    seen = _serve(monkeypatch, handler)
    await uc._tick()

    assert seen == []
    worker = uc.get_status(enabled=False, prompted=False)["worker"]
    assert worker["configured"] is False
    assert worker["status"] == "unknown"
    assert worker["reason"] == "not_configured"


async def test_a_configured_worker_is_asked_with_its_key_even_when_github_is_off(monkeypatch, db):
    await _configure_worker(db, monkeypatch)
    seen = _serve(monkeypatch, _worker_health("0.0.1"))

    await uc._tick()

    assert [str(r.url) for r in seen] == ["http://worker.example:9000/v1/health"]
    assert seen[0].headers["authorization"] == "Bearer shared-secret"
    worker = uc.get_status(enabled=False, prompted=False)["worker"]
    assert worker["configured"] is True
    assert worker["status"] == "behind"
    assert worker["version"] == "0.0.1"
    assert worker["server_version"] == APP_VERSION
    assert worker["checked_at"] is not None


async def test_a_worker_on_the_server_s_version_is_current(monkeypatch, db):
    await _configure_worker(db, monkeypatch)
    _serve(monkeypatch, _worker_health(APP_VERSION))

    await uc._tick()

    assert uc.get_status(enabled=False, prompted=False)["worker"]["status"] == "current"


async def test_a_worker_without_a_key_is_asked_without_a_header(monkeypatch, db):
    await _configure_worker(db, monkeypatch, key="")
    seen = _serve(monkeypatch, _worker_health(APP_VERSION))

    await uc._tick()

    assert "authorization" not in seen[0].headers


async def test_an_older_worker_that_reports_no_version_is_unknown(monkeypatch, db):
    """A worker built before this field existed answers /v1/health without it.
    That is "unreported", never "current" — the point is to catch stale workers."""
    await _configure_worker(db, monkeypatch)
    _serve(monkeypatch, _worker_health(None))

    await uc._tick()
    worker = uc.get_status(enabled=False, prompted=False)["worker"]

    assert worker["status"] == "unknown"
    assert worker["reason"] == "unreported"
    assert worker["version"] is None


async def test_an_unreachable_worker_is_unknown_and_does_not_raise(monkeypatch, db):
    await _configure_worker(db, monkeypatch)

    def handler(request):
        raise httpx.ConnectError("refused")

    _serve(monkeypatch, handler)
    await uc._tick()
    worker = uc.get_status(enabled=False, prompted=False)["worker"]

    assert worker["status"] == "unknown"
    assert worker["reason"] == "unreachable"


async def test_a_worker_answering_401_is_unknown_with_a_reason(monkeypatch, db):
    """A wrong shared secret must read as "check the key", not as "up to date"."""
    await _configure_worker(db, monkeypatch)
    _serve(monkeypatch, lambda request: httpx.Response(401, json={"detail": "bad key"}))

    await uc._tick()
    worker = uc.get_status(enabled=False, prompted=False)["worker"]

    assert worker["status"] == "unknown"
    assert worker["reason"] == "unauthorized"


async def test_a_failed_probe_does_not_erase_a_known_behind_worker(monkeypatch, db):
    await _configure_worker(db, monkeypatch)
    _serve(monkeypatch, _worker_health("0.0.1"))
    await uc._tick()

    def handler(request):
        raise httpx.ConnectError("blip")

    _serve(monkeypatch, handler)
    await uc._tick()
    worker = uc.get_status(enabled=False, prompted=False)["worker"]

    assert worker["status"] == "behind"
    assert worker["version"] == "0.0.1"


async def test_the_github_check_and_the_worker_probe_run_in_the_same_tick(monkeypatch, db):
    await _enable(db)
    await _configure_worker(db, monkeypatch)

    def handler(request):
        if request.url.host == "api.github.com":
            return httpx.Response(200, json={
                "tag_name": f"v{APP_VERSION}",
                "html_url": "https://github.com/jlafuenti/Book-Sync/releases/tag/x",
            })
        return _worker_health(APP_VERSION)(request)

    seen = _serve(monkeypatch, handler)
    await uc._tick()

    assert sorted(r.url.host for r in seen) == ["api.github.com", "worker.example"]
    status = uc.get_status(enabled=True, prompted=True)
    assert status["status"] == "current"
    assert status["worker"]["status"] == "current"


async def test_kick_worker_probes_once_in_the_background(monkeypatch, db):
    """Saving a new worker URL should not wait six hours to be checked."""
    await _configure_worker(db, monkeypatch)
    seen = _serve(monkeypatch, _worker_health("0.0.1"))

    await uc.kick_worker()

    assert len(seen) == 1
    assert uc.get_status(enabled=False, prompted=False)["worker"]["status"] == "behind"
