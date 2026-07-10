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


def test_allow_private_skips_range_check(monkeypatch):
    _stub_resolve(monkeypatch, "192.168.1.50")
    assert_safe_url("http://lan-box.example/", allow_private=True)  # does not raise
