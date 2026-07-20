"""
SSRF guard for server-side fetches of URLs that originate from user/admin
input. See issue #51 (original guard) and #79 (allow_private hardening,
issue #79).

Threat model: this is a self-hosted, single-tenant app. The guard's job is
to stop a lower-trust actor (editor, or an attacker-controlled redirect
target reached via editor-supplied input) from making the server probe the
LAN or the cloud metadata endpoint. It is not designed to withstand a
sophisticated DNS-rebinding attacker with a multi-tenant/SaaS threat model.

allow_private=True (admin-gated call sites that intentionally reach LAN
hosts) still resolves the hostname and blocks link-local/metadata addresses
and loopback -- it only lifts the block on ordinary RFC1918/ULA LAN ranges.
A compromised or tricked admin therefore still cannot use these endpoints to
reach the cloud metadata endpoint or the server's own loopback services.

Known limitation, accepted rather than fixed here: the hostname is resolved
now, and httpx resolves it again at connect time -- a DNS-rebinding attack
(hostname resolves to a public IP at validation time, then to a private IP
moments later at connect time) is not closed by this check alone. Closing
it fully would require a pinned-IP custom transport, which isn't justified
for this threat tier: reaching any of these endpoints already requires
editor/admin credentials, and a rebinding attack additionally requires
controlling authoritative DNS with a very short TTL and winning a timing
race against the guard + connect.
"""

import ipaddress
import socket
from urllib.parse import urlparse

_ALLOWED_SCHEMES = {"http", "https"}


class UnsafeUrlError(ValueError):
    """Raised when a URL fails the SSRF safety check."""


_IPV6_METADATA_NET = ipaddress.ip_network("fd00::/8")


def _is_blocked_ip(ip) -> bool:
    # is_link_local already covers 169.254.0.0/16, which includes the
    # AWS/GCP/Azure metadata IP 169.254.169.254 -- no special-case needed.
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def _is_blocked_even_when_private_allowed(ip) -> bool:
    """Link-local (incl. 169.254.169.254 cloud metadata), IPv6 metadata-style
    ULA (fd00::/8), loopback, and other non-LAN specials -- blocked even for
    admin-gated, intentionally LAN-reaching call sites (allow_private=True).
    Ordinary RFC1918 / ULA LAN ranges are not blocked by this check."""
    return (
        ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_unspecified
        or ip.is_reserved
        or (isinstance(ip, ipaddress.IPv6Address) and ip in _IPV6_METADATA_NET)
    )


def assert_safe_url(url: str, *, allow_private: bool = False) -> None:
    """Raise UnsafeUrlError if url is unsafe to fetch server-side. Call
    before every outbound request, including once per redirect hop.

    allow_private=True narrows the block to link-local/metadata/loopback
    addresses instead of the full private-range block -- for admin-gated,
    intentionally LAN-reaching call sites (test-abs, test-remote, ABS
    enrichment) that still must not be usable to reach cloud metadata
    endpoints or the server's own loopback services."""
    parsed = urlparse(url)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise UnsafeUrlError(f"Unsupported URL scheme: {parsed.scheme!r}")
    hostname = parsed.hostname
    if not hostname:
        raise UnsafeUrlError("URL has no hostname")

    try:
        addr_infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror as e:
        if allow_private:
            # Nothing to classify -- an admin-configured LAN hostname may
            # only resolve at connect time (docker-internal DNS, mDNS).
            return
        raise UnsafeUrlError(f"Could not resolve host {hostname!r}: {e}")

    is_blocked = _is_blocked_even_when_private_allowed if allow_private else _is_blocked_ip
    for _family, _type, _proto, _canonname, sockaddr in addr_infos:
        ip = ipaddress.ip_address(sockaddr[0])
        if is_blocked(ip):
            raise UnsafeUrlError(
                f"URL resolves to a disallowed address: {hostname} -> {sockaddr[0]}"
            )
