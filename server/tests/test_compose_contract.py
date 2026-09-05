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
import re

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


def _list_block(body: list[str], key: str) -> list[str]:
    """The `- item` lines following `key:` inside an already-stripped service body.

    Indentation is gone by the time `_services` hands the body over, but compose
    list items are contiguous, so "everything from `key:` up to the next line
    that isn't a `- ` item" is exactly the list.
    """
    try:
        start = body.index(key)
    except ValueError:
        return []
    items = []
    for line in body[start + 1:]:
        if not line.startswith("- "):
            break
        items.append(line[2:].strip())
    return items


def _env_vars(body: list[str]) -> dict[str, str]:
    """`NAME: value` for every `- NAME=value` under a service's `environment:`."""
    out = {}
    for item in _list_block(body, "environment:"):
        if "=" not in item:
            continue
        name, _, value = item.partition("=")
        out[name.strip()] = value.strip()
    return out


def _mounts(body: list[str]) -> list[str]:
    """`src:dst[:mode]` entries under a service's `volumes:`, comments stripped."""
    mounts = []
    for item in _list_block(body, "volumes:"):
        mounts.append(item.split("#", 1)[0].strip())
    return [m for m in mounts if m]


def _declared_volumes(path: str) -> list[str]:
    """Names under the top-level `volumes:` mapping."""
    with open(path, encoding="utf-8") as fh:
        lines = fh.read().splitlines()

    names = []
    in_volumes = False
    for raw in lines:
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        if indent == 0:
            in_volumes = stripped.rstrip() == "volumes:"
            continue
        if in_volumes and indent == 2 and stripped.endswith(":"):
            names.append(stripped[:-1])
    return names


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


def test_server_service_has_memory_and_pid_limits():
    """Cap the blast radius of oversized request bodies (issue #151).

    4g fits the 4 GiB MAX_UPLOAD_BYTES ceiling plus the app's working set;
    pids_limit stops a request flood from forking the container to death.
    """
    services = _services(COMPOSE_TEMPLATES[0])
    body = services["server"]
    assert "mem_limit: 4g" in body, "server service has no mem_limit: 4g"
    assert "pids_limit: 512" in body, "server service has no pids_limit: 512"


def test_caddyfile_template_caps_request_bodies():
    """Caddyfile.example must front the API with request_body caps (issue #151):
    a big-upload route group at 4GB and a default /api/* cap at 16MB."""
    path = os.path.join(_REPO_ROOT, "Caddyfile.example")
    assert os.path.isfile(path), "Caddyfile.example missing at repo root"
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    for needle in (
        "request_body",
        "4GB",
        "16MB",
        "/api/library/upload/",
        "/api/library/ebooks/",
        "/api/library/audiobooks/",
        "/api/troubleshoot/replace/",
        "/api/import/acsm/upload",
    ):
        assert needle in text, f"Caddyfile.example missing {needle!r}"


def test_jetson_template_states_the_lan_only_assumption_above_its_ports():
    """The worker's whole security model is "nobody outside the LAN can reach
    it" (issue #238): plaintext HTTP, one shared bearer token, every request
    carrying it in the clear. `ports: - "9000:9000"` publishes on every
    interface, so the assumption has to be stated where it is acted on — a
    cheap guard against the comment being dropped on the next template edit.
    """
    path = COMPOSE_TEMPLATES[1]
    with open(path, encoding="utf-8") as fh:
        lines = fh.read().splitlines()

    ports_line = next(
        (i for i, line in enumerate(lines) if line.strip() == "ports:"), None
    )
    assert ports_line is not None, "jetson template declares no ports:"

    preamble = "\n".join(lines[max(0, ports_line - 12):ports_line]).lower()
    for needle in ("lan", "must not", "internet"):
        assert needle in preamble, (
            f"jetson/docker-compose.example.yml: no comment above `ports:` "
            f"mentioning {needle!r} — the LAN-only assumption is unstated."
        )


def test_jetson_template_sizes_and_places_the_upload_temp_dir():
    """Uploads cost twice the file size in temp space, and the container's
    default /tmp is not the volume the README tells you to size (#238)."""
    body = _services(COMPOSE_TEMPLATES[1])["transcriber"]
    env = _env_vars(body)

    assert "TMPDIR" in env, (
        "jetson/docker-compose.example.yml does not set TMPDIR — the spooled "
        "upload and its copy would land on the container's unsized /tmp."
    )
    mounted = [m.split(":")[1] for m in _mounts(body) if ":" in m]
    assert any(env["TMPDIR"].startswith(target) for target in mounted), (
        f"TMPDIR={env['TMPDIR']} is not inside any mounted volume."
    )
    assert "MAX_UPLOAD_BYTES" in env, "no MAX_UPLOAD_BYTES cap in the jetson template"


def test_parser_finds_the_expected_services():
    """Guard the hand-rolled parser itself: a silently-empty parse would pass above."""
    main = _services(COMPOSE_TEMPLATES[0])
    assert set(main) == {"server", "db", "web"}

    jetson = _services(COMPOSE_TEMPLATES[1])
    assert set(jetson) == {"transcriber"}


def test_helper_parsers_find_the_expected_lists():
    """Guard the environment/volume scanners too — a silently-empty parse would
    make every assertion below vacuously true."""
    server = _services(COMPOSE_TEMPLATES[0])["server"]
    assert "DATABASE_URL" in _env_vars(server)
    assert any(m.endswith(":/data/app") for m in _mounts(server))
    assert _declared_volumes(COMPOSE_TEMPLATES[0]), "no top-level volumes parsed"


# ---------------------------------------------------------------------------
# Issue #232: the template is the first file a self-hoster edits, so every line
# in it has to be true. A variable the app never reads, or an env name that is
# not a setting, teaches the reader something false about how the stack works.
# ---------------------------------------------------------------------------


def test_web_service_declares_only_vite_vars_the_app_reads():
    """A `VITE_*` var the web app never reads is a lie in the template.

    `VITE_API_URL=http://localhost:8000` shipped here for a long time while
    `web/src/api.js` hardcoded `/api` and `web/Dockerfile`'s nginx proxied it —
    and `VITE_*` is build-time only, so a runtime env on the nginx container
    could not have taken effect even if the code had read it.
    """
    body = _services(COMPOSE_TEMPLATES[0])["web"]
    declared = [name for name in _env_vars(body) if name.startswith("VITE_")]
    if not declared:
        return

    haystack = []
    src_root = os.path.join(_REPO_ROOT, "web", "src")
    for dirpath, _dirnames, filenames in os.walk(src_root):
        for filename in filenames:
            with open(
                os.path.join(dirpath, filename), encoding="utf-8", errors="ignore"
            ) as fh:
                haystack.append(fh.read())
    vite_config = os.path.join(_REPO_ROOT, "web", "vite.config.js")
    if os.path.isfile(vite_config):
        with open(vite_config, encoding="utf-8") as fh:
            haystack.append(fh.read())
    blob = "\n".join(haystack)

    unread = [name for name in declared if name not in blob]
    assert not unread, (
        f"docker-compose.example.yml sets {unread} on the `web` service, but "
        "nothing under web/src/ or web/vite.config.js reads them. Delete the "
        "line rather than leaving a variable that does nothing."
    )


def test_server_service_env_vars_are_settings_aliases():
    """Every `- NAME=` under `server` must be a real setting the app reads."""
    from config import Settings

    aliases = {field.alias for field in Settings.model_fields.values() if field.alias}
    body = _services(COMPOSE_TEMPLATES[0])["server"]
    unknown = sorted(name for name in _env_vars(body) if name not in aliases)
    assert not unknown, (
        f"docker-compose.example.yml sets {unknown} on the `server` service, "
        "but they are not aliases of any field on config.Settings — the server "
        "would ignore them. Fix the name or drop the line."
    )


def test_server_whisper_model_matches_the_code_default():
    """The template shipped `small` while config.py and the README said `medium`."""
    from config import Settings

    body = _services(COMPOSE_TEMPLATES[0])["server"]
    template_value = _env_vars(body).get("WHISPER_MODEL")
    if template_value is None:
        return  # dropping the line is a valid fix — the code default then applies
    assert template_value == Settings.model_fields["whisper_model"].default, (
        "docker-compose.example.yml's WHISPER_MODEL disagrees with the default "
        "in server/config.py; a reader can't tell which one is real."
    )


def test_env_example_ships_postgres_password():
    """`${POSTGRES_PASSWORD}` needs a documented source (issue #232).

    Compose interpolates it from a `.env` beside the compose file. With no
    `.env.example` to copy, a fresh clone substitutes the empty string and
    postgres:16-alpine refuses to initialise.
    """
    path = os.path.join(_REPO_ROOT, ".env.example")
    assert os.path.isfile(path), (
        ".env.example missing at repo root — docker-compose.example.yml "
        "interpolates ${POSTGRES_PASSWORD} with nothing to copy from."
    )
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    assert "POSTGRES_PASSWORD=" in text, ".env.example does not set POSTGRES_PASSWORD"


# ---------------------------------------------------------------------------
# Issue #235: one source per container path, and no volume declared for show.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path", COMPOSE_TEMPLATES, ids=lambda p: os.path.relpath(p, _REPO_ROOT)
)
def test_every_declared_volume_is_mounted_by_a_service(path):
    """A named volume nobody mounts reads as documentation that it is in use.

    `booksync_data` was declared and referenced by no service, the README
    listed it as real, and the live deployment ended up mounting it *alongside*
    the bind on the same path.
    """
    declared = _declared_volumes(path)
    mounted = {
        mount.split(":")[0]
        for body in _services(path).values()
        for mount in _mounts(body)
    }
    orphans = [name for name in declared if name not in mounted]
    assert not orphans, (
        f"{os.path.relpath(path, _REPO_ROOT)}: top-level volumes {orphans} are "
        "declared but mounted by no service. Delete the declaration or mount it."
    )


@pytest.mark.parametrize(
    "path", COMPOSE_TEMPLATES, ids=lambda p: os.path.relpath(p, _REPO_ROOT)
)
def test_no_service_mounts_two_sources_on_one_target(path):
    """Two sources on one container path: one is an invisible, stale copy.

    The live deployment carried `booksync_data:/data/app` immediately followed
    by `./data:/data/app`. Only one is in effect; restoring a backup into the
    other loses covers and logs. Pin it out of the template forever.
    """
    for name, body in _services(path).items():
        targets = [mount.split(":")[1] for mount in _mounts(body) if ":" in mount]
        duplicates = sorted({t for t in targets if targets.count(t) > 1})
        assert not duplicates, (
            f"{os.path.relpath(path, _REPO_ROOT)}: service `{name}` mounts more "
            f"than one source on {duplicates}. Exactly one may win; the rest are "
            "invisible copies."
        )


def test_jetson_template_points_the_model_cache_at_the_persisted_volume():
    """Issue #374: the dustynv base image sets HF_HOME=/data/models/huggingface,
    so a volume mounted at /root/.cache/huggingface/hub is never used and every
    container recreate downloads the model again. The template must set HF_HOME
    to the parent of the mount so the hub cache *is* the volume."""
    path = os.path.join(_REPO_ROOT, "jetson", "docker-compose.example.yml")
    with open(path, encoding="utf-8") as f:
        text = f.read()
    mount = re.search(r"whisper_models:(\S+)/hub\b", text)
    assert mount, "jetson/docker-compose.example.yml: whisper_models must be mounted at <HF_HOME>/hub"
    assert f"HF_HOME={mount.group(1)}" in text, (
        "jetson/docker-compose.example.yml: HF_HOME must equal the parent of the "
        "whisper_models mount, or the base image's default cache is used instead (issue #374)"
    )


def test_root_template_does_not_publish_the_api_port_on_the_lan():
    """Issue #179: the API must not be reachable around the proxy. The web
    container's nginx already proxies `/api/` to the `server` service by name,
    so a fresh install never needs the API port published at all; if it is
    published for debugging it must bind loopback only."""
    body = _services(COMPOSE_TEMPLATES[0])["server"]
    published = []
    in_ports = False
    for line in body:
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if stripped == "ports:":
            in_ports = True
            continue
        if in_ports:
            if stripped.startswith("- "):
                published.append(stripped[2:].strip().strip('"').strip("'"))
                continue
            in_ports = False
    for entry in published:
        assert entry.startswith("127.0.0.1:"), (
            f"docker-compose.example.yml publishes the API on every interface: {entry!r}. "
            "Bind it to 127.0.0.1 or drop the mapping — nginx in the web image reaches "
            "the server by container name (issue #179)."
        )

