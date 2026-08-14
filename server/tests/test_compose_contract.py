"""
Compose template contract test (issue #105).

`web` shipped with `restart: unless-stopped` while `server` and `db` didn't, so a
host reboot left the DB and API down permanently and nginx crash-looping against a
missing `server` upstream. Nothing in CI noticed. This test pins the invariant:
every service in every compose template declares a restart policy.

Parsed by hand rather than with PyYAML — that isn't in server/requirements*.txt,
so it may not exist in CI. The templates are plain 2-space-indented YAML with no
anchors or flow mappings at the service level, which a line scan handles fine.
"""

import os

import pytest

_SERVER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_REPO_ROOT = os.path.dirname(_SERVER_DIR)

COMPOSE_TEMPLATES = [
    os.path.join(_REPO_ROOT, "docker-compose.example.yml"),
    os.path.join(_REPO_ROOT, "jetson", "docker-compose.example.yml"),
]


def _services(path: str) -> dict[str, list[str]]:
    """Map each service name to the lines of its block.

    Top-level keys sit at column 0, service names at 2 spaces, service keys at 4+.
    """
    with open(path, encoding="utf-8") as fh:
        lines = fh.read().splitlines()

    services: dict[str, list[str]] = {}
    in_services = False
    current: str | None = None

    for raw in lines:
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue

        indent = len(raw) - len(raw.lstrip(" "))

        if indent == 0:
            in_services = stripped.rstrip() == "services:"
            current = None
            continue

        if not in_services:
            continue

        if indent == 2 and stripped.endswith(":"):
            current = stripped[:-1]
            services[current] = []
        elif current is not None:
            services[current].append(stripped)

    return services


@pytest.mark.parametrize("path", COMPOSE_TEMPLATES, ids=lambda p: os.path.relpath(p, _REPO_ROOT))
def test_every_compose_service_restarts_unless_stopped(path):
    services = _services(path)
    assert services, f"no services parsed out of {path}"

    missing = [
        name
        for name, body in services.items()
        if "restart: unless-stopped" not in body
    ]
    assert not missing, (
        f"{os.path.relpath(path, _REPO_ROOT)}: services {missing} have no "
        "`restart: unless-stopped` — they won't come back after a host reboot."
    )


def test_server_service_healthchecks_the_api():
    """The server service must poll /api/health (issue #47).

    /api/health is a real readiness probe now (503 on a dead DB); without a
    compose healthcheck nothing consumes it and Docker keeps reporting a
    broken backend as up.
    """
    services = _services(COMPOSE_TEMPLATES[0])
    body = services["server"]
    assert "healthcheck:" in body, "server service has no healthcheck block"
    assert any("/api/health" in line for line in body), (
        "server healthcheck does not hit /api/health"
    )


def test_parser_finds_the_expected_services():
    """Guard the hand-rolled parser itself: a silently-empty parse would pass above."""
    main = _services(COMPOSE_TEMPLATES[0])
    assert set(main) == {"server", "db", "web"}

    jetson = _services(COMPOSE_TEMPLATES[1])
    assert set(jetson) == {"transcriber"}
