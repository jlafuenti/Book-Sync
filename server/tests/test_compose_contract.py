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


def _top_level_names(path: str, section: str) -> list[str]:
    """Names under a top-level mapping such as `volumes:` or `secrets:`."""
    with open(path, encoding="utf-8") as fh:
        lines = fh.read().splitlines()

    names = []
    in_section = False
    for raw in lines:
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        if indent == 0:
            in_section = stripped.rstrip() == f"{section}:"
            continue
        if in_section and indent == 2 and stripped.endswith(":"):
            names.append(stripped[:-1])
    return names


def _declared_volumes(path: str) -> list[str]:
    """Names under the top-level `volumes:` mapping."""
    return _top_level_names(path, "volumes")


def _declared_secrets(path: str) -> dict[str, str]:
    """`name: file` for every entry in the top-level `secrets:` mapping."""
    with open(path, encoding="utf-8") as fh:
        lines = fh.read().splitlines()

    out: dict[str, str] = {}
    in_section = False
    current = None
    for raw in lines:
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        if indent == 0:
            in_section = stripped.rstrip() == "secrets:"
            current = None
            continue
        if not in_section:
            continue
        if indent == 2 and stripped.endswith(":"):
            current = stripped[:-1]
            out[current] = ""
        elif current is not None and stripped.startswith("file:"):
            out[current] = stripped.split(":", 1)[1].strip()
    return out


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


def test_web_service_healthchecks_the_shell():
    """The web service must probe its own SPA shell (issue #327).

    nginx serves index.html for every unknown path, so a probe on "/" or an
    invented path proves nothing; the check must fetch /index.html and look
    for the app's root element, which is only there when the built bundle is.
    """
    services = _services(COMPOSE_TEMPLATES[0])
    body = services["web"]
    assert "healthcheck:" in body, "web service has no healthcheck block"
    assert any("/index.html" in line for line in body), (
        "web healthcheck does not fetch /index.html"
    )
    assert any("id=" in line and "root" in line for line in body), (
        "web healthcheck does not check for the root element"
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
    # Not DATABASE_URL: the server assembles it from the Postgres password
    # secret now, so the template no longer carries it (issue #180).
    assert "APP_ENV" in _env_vars(server)
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
    from config import SECRET_FILE_ENV_VARS, SECRET_FILE_SUFFIX, Settings

    aliases = {field.alias for field in Settings.model_fields.values() if field.alias}
    # `<NAME>_FILE` is read by config.SecretFileSettingsSource rather than
    # declared as a field of its own (issue #180) — real, just not an alias.
    aliases |= {name + SECRET_FILE_SUFFIX for name in SECRET_FILE_ENV_VARS}
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
    """`POSTGRES_PASSWORD` needs a documented source (issue #232).

    The shipped template reads it from a docker secret rather than
    interpolating it (issue #180), but the plain-environment path is still
    supported and still documented in the template — so `.env.example` keeps
    the row, and says which of the two it belongs to.
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


# ---------------------------------------------------------------------------
# Issue #180: the containers ran as uid 0 with a full capability set. `server`
# is the internet-facing process, it shells out to Calibre/ffmpeg/pg_restore,
# and it has the whole library, the import staging area and the backup share
# bind-mounted read-write — so an RCE there was uid 0 over all of it.
#
# The template now pins three things per hardened service: a non-root `user:`
# the operator can retarget with PUID/PGID, `cap_drop: [ALL]`, and
# `no-new-privileges`. `db` is deliberately excluded from the first two: the
# official postgres image starts as root and drops to `postgres` itself, which
# needs SETUID/SETGID/CHOWN/DAC_OVERRIDE/FOWNER and a root entrypoint. Pinning
# that exclusion matters as much as pinning the inclusion — "harden everything"
# is exactly how someone breaks the database.
# ---------------------------------------------------------------------------

HARDENED_SERVICES = ["server", "web"]


def _security_opts(body: list[str]) -> list[str]:
    return _list_block(body, "security_opt:")


@pytest.mark.parametrize("service", HARDENED_SERVICES)
def test_hardened_services_run_as_a_configurable_non_root_user(service):
    body = _services(COMPOSE_TEMPLATES[0])[service]
    user_lines = [line for line in body if line.startswith("user:")]
    assert user_lines, (
        f"`{service}` has no `user:` — it runs as uid 0, so any code-execution "
        "bug there owns the bind mounts (issue #180)."
    )
    value = user_lines[0].split(":", 1)[1].strip().strip('"').strip("'")
    assert "PUID" in value and "PGID" in value, (
        f"`{service}` pins a literal uid ({value!r}). It must interpolate "
        "PUID/PGID so an operator whose library is owned by another uid can "
        "retarget it from .env without editing the compose file."
    )
    assert ":-1000" in value, (
        f"`{service}`'s user: must default to 1000 when PUID/PGID are unset "
        f"(got {value!r}) — an unset variable would otherwise expand to the "
        "empty string and compose would fail to start the service."
    )


@pytest.mark.parametrize("service", HARDENED_SERVICES)
def test_hardened_services_drop_all_capabilities(service):
    body = _services(COMPOSE_TEMPLATES[0])[service]
    dropped = _list_block(body, "cap_drop:")
    assert dropped == ["ALL"], (
        f"`{service}` must declare `cap_drop:` with a single `- ALL` "
        f"(got {dropped!r}). Neither process needs a capability: uvicorn binds "
        "8000 and nginx binds 3000, both unprivileged ports, and both now run "
        "as a non-root uid that owns its files."
    )


@pytest.mark.parametrize("service", ["db", "server", "web"])
def test_every_service_sets_no_new_privileges(service):
    """The cheapest half of the hardening, and safe even for `db`.

    `no-new-privileges` blocks *gaining* privilege through a setuid binary. The
    postgres entrypoint's root→postgres drop is the opposite direction, so this
    one flag is applied to all three services including the one left otherwise
    untouched.
    """
    body = _services(COMPOSE_TEMPLATES[0])[service]
    assert "no-new-privileges:true" in _security_opts(body), (
        f"`{service}` has no `security_opt: [no-new-privileges:true]`."
    )


def test_db_service_keeps_the_postgres_images_own_user_handling():
    """Do NOT harden `db` the way `server` and `web` are hardened.

    postgres:16-alpine's entrypoint runs as root, `chown`s the data directory
    and `su-exec`s to the `postgres` user. Setting `user:` skips that setup, and
    `cap_drop: [ALL]` removes the SETUID/SETGID/CHOWN/DAC_OVERRIDE/FOWNER it
    needs to perform it — either one turns a working database into a boot loop.
    This test exists so a later "finish the hardening" pass fails here instead
    of in production.
    """
    body = _services(COMPOSE_TEMPLATES[0])["db"]
    assert not [line for line in body if line.startswith("user:")], (
        "`db` must not set `user:` — the postgres image switches user itself."
    )
    assert not _list_block(body, "cap_drop:"), (
        "`db` must not drop capabilities — its entrypoint needs CHOWN/SETUID/"
        "SETGID/DAC_OVERRIDE/FOWNER to initialise the data directory."
    )


def test_server_service_gets_a_sized_tmpfs_for_tmp():
    """/tmp is the last-resort scratch path once the container is non-root.

    Everything in the app reaches temp space through `tempfile`, which honours
    TMPDIR — and `server/Dockerfile` points TMPDIR at the app-data volume so a
    multi-GB spooled audiobook upload lands on disk, not in RAM. This tmpfs is
    the fallback for anything that ignores TMPDIR, so it is deliberately small
    and deliberately sized: an unsized tmpfs defaults to half of host RAM.
    """
    body = _services(COMPOSE_TEMPLATES[0])["server"]
    entries = _list_block(body, "tmpfs:")
    tmp = [e for e in entries if e.split(":")[0] == "/tmp"]
    assert tmp, f"`server` declares no tmpfs for /tmp (got {entries!r})"
    assert "size=" in tmp[0], (
        f"`server`'s /tmp tmpfs has no size= ({tmp[0]!r}); an unsized tmpfs can "
        "grow to half the host's RAM and OOM the box."
    )


def test_env_example_documents_the_puid_and_pgid_knobs():
    """`user: "${PUID:-1000}:${PGID:-1000}"` needs a documented source.

    Unlike POSTGRES_PASSWORD these have working defaults, so a missing .env
    entry is not fatal — but an operator whose library is owned by another uid
    has no way to discover the knob if the template never names it.
    """
    path = os.path.join(_REPO_ROOT, ".env.example")
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    for name in ("PUID", "PGID"):
        assert f"{name}=" in text, f".env.example does not document {name}"


# ---------------------------------------------------------------------------
# Issue #180, second half: secrets must not be passed in `environment:`.
#
# `docker inspect` prints a container's whole environment, and so does
# `/proc/1/environ` — so `JWT_SECRET_KEY`, `CREDENTIAL_ENC_KEYS` and the
# password inside `DATABASE_URL` were readable by anyone in the `docker` group
# and by anything that reached code execution inside the container. Compose
# `secrets:` puts each value in a file mounted at `/run/secrets/<name>`, and the
# environment carries only the path, which `server/config.py` resolves through
# its `<NAME>_FILE` support.
#
# The plain-`environment:` path still works in the code — an operator who
# prefers it can uncomment it — but it must not be what the template *ships*:
# the template is what gets copied, and a commented alternative is a choice
# while an uncommented one is the default.
# ---------------------------------------------------------------------------

# Names that must never appear uncommented under any service's `environment:`.
PLAIN_SECRET_ENV_NAMES = [
    "JWT_SECRET_KEY",
    "CREDENTIAL_ENC_KEYS",
    "DATABASE_URL",
    "POSTGRES_PASSWORD",
]


def test_the_template_passes_no_secret_through_environment():
    for service, body in _services(COMPOSE_TEMPLATES[0]).items():
        present = sorted(set(_env_vars(body)) & set(PLAIN_SECRET_ENV_NAMES))
        assert not present, (
            f"docker-compose.example.yml passes {present} to `{service}` through "
            "`environment:`, where `docker inspect` and /proc/1/environ expose "
            "them. Use the `<NAME>_FILE` form backed by a compose secret, and "
            "leave the plain form commented out for operators who choose it."
        )


def test_every_file_variable_points_at_a_declared_secret():
    """`JWT_SECRET_KEY_FILE=/run/secrets/jwt_secret_key` is a promise that a
    secret called `jwt_secret_key` is mounted into that service. A typo in
    either half fails at boot with a missing-file error, so pin both."""
    path = COMPOSE_TEMPLATES[0]
    declared = _declared_secrets(path)
    assert declared, "docker-compose.example.yml declares no top-level secrets:"

    seen = 0
    for service, body in _services(path).items():
        granted = _list_block(body, "secrets:")
        for name, value in _env_vars(body).items():
            if not name.endswith("_FILE"):
                continue
            seen += 1
            assert value.startswith("/run/secrets/"), (
                f"`{service}`'s {name}={value!r} is not a compose secret path; "
                "a secret mounted by compose always lands in /run/secrets/."
            )
            secret = value[len("/run/secrets/"):]
            assert secret in declared, (
                f"`{service}` reads {name}={value!r}, but no top-level secret "
                f"called `{secret}` is declared."
            )
            assert secret in granted, (
                f"`{service}` reads {name}={value!r}, but `{secret}` is not in "
                "that service's own `secrets:` list, so it is never mounted."
            )
    assert seen >= 3, (
        f"only {seen} `*_FILE` variables in the template — the three shipped "
        "secrets (JWT key, Postgres password, credential encryption keys) must "
        "all be passed by file."
    )


def test_every_declared_secret_is_file_backed_and_used():
    """A declared secret nobody mounts reads as documentation that it is in
    use — the same failure `test_every_declared_volume_is_mounted_by_a_service`
    exists for."""
    path = COMPOSE_TEMPLATES[0]
    declared = _declared_secrets(path)
    assert declared, "docker-compose.example.yml declares no top-level secrets:"
    granted = {
        name
        for body in _services(path).values()
        for name in _list_block(body, "secrets:")
    }
    orphans = sorted(set(declared) - granted)
    assert not orphans, (
        f"top-level secrets {orphans} are declared but granted to no service."
    )
    for name, source in declared.items():
        assert source.startswith("./secrets/"), (
            f"secret `{name}` is backed by {source!r}; the template's secrets "
            "live in ./secrets/ beside the compose file, which .gitignore "
            "fences off (see test_repo_hygiene.py)."
        )


def test_server_and_db_share_one_postgres_password_secret():
    """The password has to be identical in two containers. Two copies drift;
    one file cannot. The official postgres image reads the `_FILE` form
    natively, which is what makes this possible."""
    services = _services(COMPOSE_TEMPLATES[0])
    server_path = _env_vars(services["server"]).get("POSTGRES_PASSWORD_FILE")
    db_path = _env_vars(services["db"]).get("POSTGRES_PASSWORD_FILE")
    assert server_path, "`server` does not read POSTGRES_PASSWORD_FILE"
    assert db_path, "`db` does not read POSTGRES_PASSWORD_FILE"
    assert server_path == db_path, (
        f"`server` reads the Postgres password from {server_path!r} and `db` "
        f"from {db_path!r} — they must be the same secret."
    )


def test_the_secrets_readme_documents_every_secret_file():
    """A file the operator has to create by hand, with no instructions, is how
    a deployment ends up with an empty secret."""
    path = os.path.join(_REPO_ROOT, "secrets", "README.md")
    assert os.path.isfile(path), (
        "secrets/README.md is missing — docker-compose.example.yml points at "
        "./secrets/, so a fresh clone has an empty directory and no idea what "
        "goes in it."
    )
    with open(path, encoding="utf-8") as fh:
        text = fh.read()

    for source in _declared_secrets(COMPOSE_TEMPLATES[0]).values():
        filename = source.rsplit("/", 1)[-1]
        assert filename in text, f"secrets/README.md does not mention {filename!r}"

    for needle in ("umask 077", "secrets.token_urlsafe", "Fernet.generate_key"):
        assert needle in text, (
            f"secrets/README.md does not give the {needle!r} step — the point "
            "of the file is that the operator can follow it verbatim."
        )
