"""Unit tests for the SSRF guard (issue #51)."""

import socket

import pytest

from services.url_safety import assert_safe_url, UnsafeUrlError


def _stub_resolve(monkeypatch, ip: str):
    """Deterministic DNS: any hostname resolves to the given IP."""
    def fake_getaddrinfo(host, port, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0))]
    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)


@pytest.mark.parametrize("url", [
    "file:///etc/passwd",
    "gopher://127.0.0.1:70/",
    "ftp://example.com/",
    "javascript:alert(1)",
])
def test_rejects_non_http_scheme(url):
    with pytest.raises(UnsafeUrlError):
        assert_safe_url(url)


@pytest.mark.parametrize("url", [
    "file:///etc/passwd",
    "gopher://127.0.0.1:70/",
])
def test_rejects_non_http_scheme_even_with_allow_private(url):
    with pytest.raises(UnsafeUrlError):
        assert_safe_url(url, allow_private=True)


def test_rejects_no_hostname():
    with pytest.raises(UnsafeUrlError):
        assert_safe_url("http:///path")


def test_rejects_unresolvable_host(monkeypatch):
    def fake_getaddrinfo(*args, **kwargs):
        raise socket.gaierror("nope")
    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)

    with pytest.raises(UnsafeUrlError):
        assert_safe_url("http://does-not-resolve.invalid/")


def test_allow_private_permits_unresolvable_host(monkeypatch):
    """An admin-configured LAN hostname (e.g. a docker-internal or
    mDNS-only name) may not be resolvable from wherever validation runs.
    With nothing to classify, allow_private=True lets it through rather
    than hard-failing -- the actual connection attempt still has to
    resolve it, and a hostname that never resolves to a blocked address
    at connect time was never reachable anyway."""
    def fake_getaddrinfo(*args, **kwargs):
        raise socket.gaierror("nope")
    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)

    assert_safe_url("http://fake-jetson:9000/", allow_private=True)  # does not raise


def test_rejects_loopback(monkeypatch):
    _stub_resolve(monkeypatch, "127.0.0.1")
    with pytest.raises(UnsafeUrlError):
        assert_safe_url("http://localhost/")


@pytest.mark.parametrize("ip", ["10.0.0.1", "172.16.0.1", "192.168.1.1"])
def test_rejects_rfc1918_ranges(monkeypatch, ip):
    _stub_resolve(monkeypatch, ip)
    with pytest.raises(UnsafeUrlError):
        assert_safe_url("http://internal.example/")


def test_rejects_link_local_and_metadata(monkeypatch):
    _stub_resolve(monkeypatch, "169.254.169.254")
    with pytest.raises(UnsafeUrlError):
        assert_safe_url("http://metadata.example/latest/meta-data/")


def test_rejects_ipv6_loopback():
    with pytest.raises(UnsafeUrlError):
        assert_safe_url("http://[::1]/")


def test_rejects_ipv6_unique_local():
    with pytest.raises(UnsafeUrlError):
        assert_safe_url("http://[fc00::1]/")


def test_rejects_ipv6_link_local():
    with pytest.raises(UnsafeUrlError):
        assert_safe_url("http://[fe80::1]/")


def test_allows_public_ip(monkeypatch):
    _stub_resolve(monkeypatch, "93.184.216.34")
    assert_safe_url("http://public.example/")  # does not raise


def test_allow_private_allows_rfc1918_range(monkeypatch):
    _stub_resolve(monkeypatch, "192.168.1.50")
    assert_safe_url("http://lan-box.example/", allow_private=True)  # does not raise


def test_allow_private_allows_ipv6_ula_lan_range():
    assert_safe_url("http://[fc00::1]/", allow_private=True)  # does not raise -- LAN ULA, not fd00::/8 metadata-style


def test_allow_private_still_blocks_link_local_and_metadata():
    with pytest.raises(UnsafeUrlError):
        assert_safe_url("http://169.254.169.254/", allow_private=True)


def test_allow_private_still_blocks_ipv6_metadata_style():
    with pytest.raises(UnsafeUrlError):
        assert_safe_url("http://[fd00::1]/", allow_private=True)


def test_allow_private_still_blocks_loopback():
    with pytest.raises(UnsafeUrlError):
        assert_safe_url("http://127.0.0.1/", allow_private=True)


def test_allow_private_still_blocks_ipv6_loopback():
    with pytest.raises(UnsafeUrlError):
        assert_safe_url("http://[::1]/", allow_private=True)


def test_allow_private_still_blocks_ipv6_link_local():
    with pytest.raises(UnsafeUrlError):
        assert_safe_url("http://[fe80::1]/", allow_private=True)


# ---------- the lookup must never run on the event loop (issue #673) ----------
#
# One uvicorn worker serves every request, so a synchronous DNS lookup inside an
# `async def` stalls position sync, streaming, login and /api/health for its whole
# duration. A fast resolver hides it; a resolver that has to time out does not.

import asyncio
import threading
import time

from services.url_safety import assert_safe_url_async


def _stub_slow_resolve(monkeypatch, ip: str, seconds: float, seen_threads: list):
    """A resolver that takes `seconds` and records which thread it ran on."""
    def slow_getaddrinfo(host, port, *args, **kwargs):
        seen_threads.append(threading.current_thread())
        time.sleep(seconds)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0))]
    monkeypatch.setattr(socket, "getaddrinfo", slow_getaddrinfo)


async def test_async_guard_resolves_off_the_event_loop(monkeypatch):
    seen = []
    _stub_slow_resolve(monkeypatch, "93.184.216.34", 0.0, seen)

    await assert_safe_url_async("https://example.com/")

    assert seen, "the resolver must actually be called"
    assert seen[0] is not threading.main_thread(), (
        "the lookup ran on the event loop's thread; it must cross to a worker thread"
    )


async def test_the_event_loop_keeps_serving_during_a_slow_lookup(monkeypatch):
    """The property that actually matters: while one request waits on DNS,
    others are still served. A ticker coroutine must keep advancing for the
    whole duration of a deliberately slow lookup."""
    seen = []
    _stub_slow_resolve(monkeypatch, "93.184.216.34", 0.5, seen)

    ticks = 0
    stop = asyncio.Event()

    async def ticker():
        nonlocal ticks
        while not stop.is_set():
            ticks += 1
            await asyncio.sleep(0.02)

    task = asyncio.create_task(ticker())
    await assert_safe_url_async("https://example.com/")
    stop.set()
    await task

    # 0.5 s at one tick per 20 ms is ~25; a blocked loop manages at most 1-2.
    assert ticks >= 10, f"the event loop only ticked {ticks} times during a 0.5 s lookup"


async def test_async_guard_still_rejects_what_the_sync_guard_rejects(monkeypatch):
    """Moving the lookup to a thread must not change a single verdict."""
    _stub_resolve(monkeypatch, "10.0.0.5")
    with pytest.raises(UnsafeUrlError):
        await assert_safe_url_async("https://internal.example/")

    with pytest.raises(UnsafeUrlError):
        await assert_safe_url_async("file:///etc/passwd")

    _stub_resolve(monkeypatch, "169.254.169.254")
    with pytest.raises(UnsafeUrlError):
        await assert_safe_url_async("http://metadata.example/", allow_private=True)


async def test_async_guard_honours_allow_private(monkeypatch):
    _stub_resolve(monkeypatch, "10.0.0.5")
    await assert_safe_url_async("http://lan-host.example:9000/", allow_private=True)


def test_no_async_function_calls_the_blocking_guard_directly():
    """Source guard, so the next async caller cannot reintroduce this.

    The synchronous `assert_safe_url` stays — `abs_metadata.fetch_abs_index`
    is a sync function that every caller already runs via `asyncio.to_thread`,
    and it needs a sync entry point. What is forbidden is calling it from
    inside an `async def`, where it runs on the event loop.
    """
    import ast
    import pathlib

    server = pathlib.Path(__file__).resolve().parents[1]
    offenders = []
    for path in server.rglob("*.py"):
        if "tests" in path.parts or ".venv" in path.parts or "alembic" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for fn in ast.walk(tree):
            if not isinstance(fn, ast.AsyncFunctionDef):
                continue
            for node in ast.walk(fn):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "assert_safe_url"
                ):
                    offenders.append(f"{path.relative_to(server)}:{node.lineno} in {fn.name}()")

    assert not offenders, (
        "these async functions call the blocking SSRF guard on the event loop — "
        "use `await assert_safe_url_async(...)` instead (issue #673):\n  "
        + "\n  ".join(offenders)
    )
