"""
Reverse-proxy trust contract tests (issue #156).

Behind Caddy (production) or the shipped nginx `/api/` proxy, every request
reaches uvicorn from the same socket peer. Unless uvicorn is told which peer to
trust (`FORWARDED_ALLOW_IPS`), the slowapi rate-limit buckets on
/api/auth/login and /register key on that single peer address — one global
bucket for the whole internet — and the audit log records the proxy's address
(or an attacker-chosen X-Forwarded-For) instead of the client's.

These tests pin the two infra halves of the fix:
  * the compose template ships a `FORWARDED_ALLOW_IPS` line on the server
    service so uvicorn's ProxyHeadersMiddleware rewrites `scope["client"]`
    from X-Forwarded-For only for the trusted proxy peer;
  * the nginx proxy in web/Dockerfile actually SENDS X-Forwarded-For /
    X-Forwarded-Proto (it historically set only Host and X-Real-IP).

File-reading style, like test_compose_contract.py — no Docker required.
"""

import os
import re

_SERVER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_REPO_ROOT = os.path.dirname(_SERVER_DIR)

COMPOSE_TEMPLATE = os.path.join(_REPO_ROOT, "docker-compose.example.yml")
WEB_DOCKERFILE = os.path.join(_REPO_ROOT, "web", "Dockerfile")


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def test_compose_server_service_sets_forwarded_allow_ips():
    """The server service must declare FORWARDED_ALLOW_IPS.

    uvicorn only honors X-Forwarded-For from peers listed there; without it the
    default trust list is 127.0.0.1 and every proxied client shares one
    rate-limit bucket keyed on the proxy's address.
    """
    text = _read(COMPOSE_TEMPLATE)
    # Environment entries are `- KEY=value` lines under the server service.
    assert re.search(r"^\s*-\s*FORWARDED_ALLOW_IPS=", text, re.MULTILINE), (
        "docker-compose.example.yml: the server service has no "
        "FORWARDED_ALLOW_IPS environment entry — uvicorn will not trust the "
        "reverse proxy's X-Forwarded-For header."
    )


def test_web_nginx_api_location_forwards_client_ip_headers():
    """The /api/ proxy block must pass X-Forwarded-For and X-Forwarded-Proto."""
    text = _read(WEB_DOCKERFILE)
    m = re.search(r"location /api/ \{(.*?)\}", text, re.DOTALL)
    assert m, "web/Dockerfile: no `location /api/` block found in the nginx config"
    block = m.group(1)

    assert re.search(
        r"proxy_set_header\s+X-Forwarded-For\s+\$proxy_add_x_forwarded_for", block
    ), (
        "web/Dockerfile: the /api/ location does not set "
        "`proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for` — the "
        "backend never sees the real client IP."
    )
    assert re.search(r"proxy_set_header\s+X-Forwarded-Proto\s+\$scheme", block), (
        "web/Dockerfile: the /api/ location does not set "
        "`proxy_set_header X-Forwarded-Proto $scheme`."
    )
