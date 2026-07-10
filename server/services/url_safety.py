"""
SSRF guard for server-side fetches of URLs that originate from user/admin
input. See issue #51.

Threat model: this is a self-hosted, single-tenant app. The guard's job is
to stop a lower-trust actor (editor, or an attacker-controlled redirect
target reached via editor-supplied input) from making the server probe the
LAN or the cloud metadata endpoint. It is not designed to withstand a
sophisticated DNS-rebinding attacker with a multi-tenant/SaaS threat model.

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


def assert_safe_url(url: str, *, allow_private: bool = False) -> None:
    """Raise UnsafeUrlError if url is unsafe to fetch server-side. Call
    before every outbound request, including once per redirect hop.

    allow_private=True skips the private/LAN-range block (scheme and
    resolvability checks still apply) -- for admin-gated, intentionally
    LAN-reaching call sites (test-abs, test-remote, ABS enrichment)."""
    parsed = urlparse(url)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise UnsafeUrlError(f"Unsupported URL scheme: {parsed.scheme!r}")
    hostname = parsed.hostname
    if not hostname:
        raise UnsafeUrlError("URL has no hostname")

    if allow_private:
        return

    try:
        addr_infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror as e:
        raise UnsafeUrlError(f"Could not resolve host {hostname!r}: {e}")

    for _family, _type, _proto, _canonname, sockaddr in addr_infos:
        ip = ipaddress.ip_address(sockaddr[0])
        if _is_blocked_ip(ip):
            raise UnsafeUrlError(
                f"URL resolves to a disallowed address: {hostname} -> {sockaddr[0]}"
            )
