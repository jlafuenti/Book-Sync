"""
Both images must ship the NLTK sentence tokenizer, not fetch it (issue #249).

`punkt_tab` is on the path of every pipeline run and every realign
(`epub_parser.extract_book_sentences`). An image that downloads it at runtime is
non-hermetic: a restricted-egress deployment fails at the EPUB-extraction step
*after* a multi-hour transcription has already finished, and on the server side a
slow GitHub CDN once stalled the FastAPI lifespan outright (issue #322).

The two Dockerfiles have drifted before — the Jetson one baked the corpus while
the server one did not — so the same contract is asserted against both from one
place, and it is the *whole* contract:

* the corpus is downloaded during the build, and
* into an explicit shared directory rather than the build user's home, which
  changes with the container user, and
* `NLTK_DATA` names that directory, so nothing depends on the path happening to
  be one of nltk's compiled-in defaults.

No CI job builds either image, so a line scan is the only guard available.

The second half of this file (issue #234) pins the other build-time contracts:
the web image installs from the committed lockfile, and every image has a
`.dockerignore` so `COPY . .` cannot ship the developer's disk.
"""

import json
import os
import re

import pytest

_SERVER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_REPO_ROOT = os.path.dirname(_SERVER_DIR)

# Both images provision here. It is already on `nltk.data.path` by default,
# which keeps the runtime working even if NLTK_DATA is unset by a wrapper.
NLTK_DATA_DIR = "/usr/local/share/nltk_data"

DOCKERFILES = {
    "server": os.path.join(_SERVER_DIR, "Dockerfile"),
    "jetson": os.path.join(_REPO_ROOT, "jetson", "Dockerfile"),
}


def _instructions(path: str) -> list[str]:
    """Non-comment, non-blank lines of a Dockerfile."""
    with open(path, encoding="utf-8") as fh:
        return [ln.strip() for ln in fh
                if ln.strip() and not ln.strip().startswith("#")]


def _assert_bakes_punkt_tab(path: str, image: str) -> None:
    lines = _instructions(path)

    provisioning = [ln for ln in lines
                    if "nltk" in ln.lower() and "punkt_tab" in ln]
    assert provisioning, (
        f"{image}: the image must download punkt_tab at build time. Without it "
        "every fresh container fetches the corpus over the network on first "
        "tokenize, which fails outright with no egress and stalls startup on a "
        "slow CDN (issues #249, #322)."
    )
    assert any(NLTK_DATA_DIR in ln for ln in provisioning), (
        f"{image}: provision into {NLTK_DATA_DIR}. A bare `nltk.download()` "
        "writes to the *build* user's home, which the runtime user may not "
        "share, so the baked copy is silently invisible and the container "
        "downloads it again anyway."
    )
    assert any(
        ln.startswith("ENV") and "NLTK_DATA" in ln and NLTK_DATA_DIR in ln
        for ln in lines
    ), (
        f"{image}: set ENV NLTK_DATA={NLTK_DATA_DIR} so the search path is "
        "declared by the image rather than inherited from nltk's compiled-in "
        "defaults."
    )


def test_server_image_bakes_the_nltk_tokenizer_data():
    _assert_bakes_punkt_tab(DOCKERFILES["server"], "server/Dockerfile")


def test_jetson_image_bakes_the_nltk_tokenizer_data():
    _assert_bakes_punkt_tab(DOCKERFILES["jetson"], "jetson/Dockerfile")


@pytest.mark.parametrize("image", sorted(DOCKERFILES))
def test_the_dockerfile_under_test_exists(image):
    """Guard against a rename quietly turning both tests into no-ops."""
    assert os.path.isfile(DOCKERFILES[image]), DOCKERFILES[image]


# ---------------------------------------------------------------------------
# Issue #234: reproducible web builds, and a build context carrying only what
# the image needs.
#
# `npm install` re-resolves the dependency tree at image-build time, so the
# bundle that ships is not the tree CI tested (`.github/workflows/web-tests.yml`
# runs `npm ci`) and two builds of the same commit can differ — which makes an
# incident unbisectable. `npm ci` installs exactly `web/package-lock.json`.
#
# `.dockerignore` is the other half: every image here does `COPY . .`, and with
# no ignore file that copies whatever is lying in the context — a virtualenv,
# test caches, a stray SQLite database, and (worst) an operator's `server/.env`,
# which pydantic would then read from *inside* the image at runtime
# (`server/config.py`, `env_file = ".env"`).
#
# Build contexts are `./server`, `./web` and `./jetson`
# (`docker-compose.example.yml`, `jetson/docker-compose.example.yml`), so docker
# reads the `.dockerignore` sitting next to each Dockerfile.
#
# No CI job builds any of these images, so a line scan is the only guard.
# ---------------------------------------------------------------------------

WEB_DOCKERFILE = os.path.join(_REPO_ROOT, "web", "Dockerfile")

DOCKERIGNORES = {
    "server": os.path.join(_SERVER_DIR, ".dockerignore"),
    "web": os.path.join(_REPO_ROOT, "web", ".dockerignore"),
    "jetson": os.path.join(_REPO_ROOT, "jetson", ".dockerignore"),
}

# What each context must exclude. Categories, not an exhaustive list: tests,
# dependency/build trees, caches, docs and git metadata — plus, for the server,
# the two that actually leak (`.env` and local databases).
REQUIRED_IGNORES = {
    "server": [".env", ".venv/", "tests/", "__pycache__/", "*.db", "docs/", ".git"],
    "web": ["node_modules/", "dist/", "coverage/", "src/**/*.test.jsx", ".git"],
    "jetson": ["__pycache__/", "test_server.py", ".git"],
}


def _dockerignore_patterns(path: str) -> list[str]:
    with open(path, encoding="utf-8") as fh:
        return [ln.strip() for ln in fh
                if ln.strip() and not ln.strip().startswith("#")]


def test_web_image_installs_from_the_lockfile():
    """`npm ci` against a copied lockfile, never `npm install`."""
    lines = _instructions(WEB_DOCKERFILE)

    installs = [ln for ln in lines if "npm install" in ln]
    assert not installs, (
        "web/Dockerfile runs `npm install`, which re-resolves every dependency "
        f"at build time instead of installing the tested tree: {installs}. CI "
        "uses `npm ci`; the image must agree, or the shipped bundle is one no "
        "test ever saw."
    )

    assert any("npm ci" in ln for ln in lines), (
        "web/Dockerfile must install with `npm ci`"
    )

    # ...and the lockfile has to arrive *before* the install, not with the later
    # `COPY . .` — otherwise `npm ci` has nothing to install from.
    copy_lock = next(
        (i for i, ln in enumerate(lines)
         if ln.startswith("COPY") and "package-lock.json" in ln),
        None,
    )
    assert copy_lock is not None, (
        "web/Dockerfile must COPY package-lock.json before installing; arriving "
        "with the later `COPY . .` is too late for `npm ci`."
    )
    first_ci = min(i for i, ln in enumerate(lines) if "npm ci" in ln)
    assert copy_lock < first_ci, (
        "package-lock.json is copied after the install step in web/Dockerfile."
    )


def test_web_lockfile_is_committed_and_matches_package_json():
    """`npm ci` fails outright on a missing or drifted lockfile."""
    lock_path = os.path.join(_REPO_ROOT, "web", "package-lock.json")
    pkg_path = os.path.join(_REPO_ROOT, "web", "package.json")
    assert os.path.isfile(lock_path), "web/package-lock.json must be committed"

    with open(lock_path, encoding="utf-8") as fh:
        lock = json.load(fh)
    with open(pkg_path, encoding="utf-8") as fh:
        pkg = json.load(fh)

    root = lock.get("packages", {}).get("", {})
    for section in ("dependencies", "devDependencies"):
        assert root.get(section, {}) == pkg.get(section, {}), (
            f"web/package-lock.json's {section} no longer match package.json. "
            "`npm ci` refuses to run against a drifted lockfile — regenerate it "
            "with `npm install --package-lock-only` and commit the result."
        )


@pytest.mark.parametrize("image", sorted(DOCKERIGNORES))
def test_every_dockerfile_has_a_dockerignore(image):
    assert os.path.isfile(DOCKERIGNORES[image]), (
        f"{image}/.dockerignore is missing. Every image here does `COPY . .`, "
        "so without one the build context ships whatever happens to be sitting "
        "on the developer's disk."
    )


@pytest.mark.parametrize("image", sorted(REQUIRED_IGNORES))
def test_dockerignore_excludes_the_expected_categories(image):
    patterns = _dockerignore_patterns(DOCKERIGNORES[image])
    missing = [p for p in REQUIRED_IGNORES[image] if p not in patterns]
    assert not missing, (
        f"{image}/.dockerignore does not exclude {missing}. These are build "
        "context, secrets or tests — none of them belong in a shipped image."
    )


def test_no_dockerfile_is_missing_from_the_contract():
    """A new image must not silently escape the checks above."""
    skip_dirs = {".git", "node_modules", ".venv", "venv", "build", ".gradle",
                 "dist", "__pycache__", ".pytest_cache"}
    found = set()
    for dirpath, dirnames, filenames in os.walk(_REPO_ROOT):
        dirnames[:] = [d for d in dirnames if d not in skip_dirs]
        if "Dockerfile" in filenames:
            found.add(os.path.join(dirpath, "Dockerfile"))
    known = set(DOCKERFILES.values()) | {WEB_DOCKERFILE}
    assert found == known, (
        f"Dockerfiles not covered by this contract: {sorted(found - known)}; "
        f"named here but missing from the tree: {sorted(known - found)}"
    )


# ---------------------------------------------------------------------------
# Issue #180: neither shipped image ever left uid 0. `docker-compose.example.yml`
# now sets `user: "${PUID:-1000}:${PGID:-1000}"` on both, but a compose file is
# not the image's contract — someone running `docker run` on either image, or
# copying the template before this change, gets whatever the Dockerfile ends on.
# So the image drops privilege itself, and compose only *retargets* the uid.
#
# The consequence that keeps biting: a non-root uid can't write anywhere the
# build left root-owned. Every runtime write path therefore has to be prepared
# at build time, and the ones that aren't obvious are pinned below.
#
# `jetson/Dockerfile` is deliberately out of scope: its dustynv CUDA base image
# expects to run as root (device nodes, the preinstalled CUDA/torch tree under
# /root), and the worker is a LAN-only single-purpose box. See docs/operations.md.
# ---------------------------------------------------------------------------

# Where the server image parks per-user state that the build must pre-create.
SERVER_HOME = "/home/tandem"
# tempfile.gettempdir() honours TMPDIR, and every temp path in server/ goes
# through tempfile — including Starlette's SpooledTemporaryFile, which is where
# a multi-GB audiobook upload spills once it outgrows its in-memory spool.
SERVER_TMPDIR = "/data/app/tmp"

ROOT_USERS = {"root", "0", "0:0", "root:root"}


def _final_user(path: str) -> str | None:
    """The argument of the last `USER` instruction, or None if there is none."""
    users = [ln.split(None, 1)[1].strip()
             for ln in _instructions(path) if ln.upper().startswith("USER ")]
    return users[-1] if users else None


def _base_images(path: str) -> list[str]:
    return [ln.split(None, 2)[1] for ln in _instructions(path)
            if ln.upper().startswith("FROM ")]


def test_server_image_does_not_end_as_root():
    user = _final_user(DOCKERFILES["server"])
    assert user is not None, (
        "server/Dockerfile has no USER instruction, so the image runs as uid 0 "
        "(issue #180). It is the internet-facing process and it has the whole "
        "library, the import staging area and the backup share mounted "
        "read-write."
    )
    assert user.split(":")[0] not in ROOT_USERS, f"server/Dockerfile ends as {user!r}"


def test_web_image_does_not_end_as_root():
    """Either an unprivileged base image or an explicit non-root USER."""
    user = _final_user(WEB_DOCKERFILE)
    unprivileged_base = any(
        "nginx-unprivileged" in image for image in _base_images(WEB_DOCKERFILE)
    )
    assert unprivileged_base or user is not None, (
        "web/Dockerfile neither builds on nginxinc/nginx-unprivileged nor sets "
        "a USER — the nginx master would run as root (issue #180)."
    )
    if user is not None:
        assert user.split(":")[0] not in ROOT_USERS, (
            f"web/Dockerfile's last USER is {user!r}"
        )


def test_server_image_prepares_every_path_the_non_root_process_writes():
    """A non-root uid can only write where the build made it possible.

    The app writes in five places that are not bind mounts, and every one of
    them is root-owned by default because the build runs as root:

      * $HOME — Calibre's config dir. `calibre-customize` installs the DeACSM /
        DeDRM plugins under `$HOME/.config/calibre/plugins` at build time, and
        `services/import_sources/acsm.py` writes the Adobe account files back
        there at runtime. Pointing HOME at a world-writable dir before the
        plugin steps is what makes the build-time install and the runtime write
        land in the same place whatever uid compose picks.
      * $TMPDIR — spooled multipart uploads, chapter_repair's staging files,
        the ACSM plugin's unpacked working copies.
      * /data/{app,imports,ebooks,audiobooks} — mountpoints, which must exist
        and be traversable before docker binds over them.

    The mode has to be permissive rather than a fixed `chown 1000`, because
    PUID/PGID let the operator run as any uid.
    """
    lines = _instructions(DOCKERFILES["server"])

    assert any(ln.startswith("ENV") and f"HOME={SERVER_HOME}" in ln for ln in lines), (
        f"server/Dockerfile must set ENV HOME={SERVER_HOME}. Left unset, HOME "
        "is /root: the build installs the Calibre plugins into a directory the "
        "runtime uid can neither read nor write, and the ACSM Adobe account "
        "restore fails with a permission error at startup."
    )
    assert any(ln.startswith("ENV") and f"TMPDIR={SERVER_TMPDIR}" in ln for ln in lines), (
        f"server/Dockerfile must set ENV TMPDIR={SERVER_TMPDIR}. The default "
        "/tmp is a small tmpfs in the compose template, and a spooled "
        "multi-GB audiobook upload would fill it."
    )

    # Written either literally or as $HOME / ${HOME} — the ENV above already
    # pins what that expands to.
    home_prep = [ln for ln in lines
                 if "chmod" in ln
                 and (SERVER_HOME in ln or "$HOME" in ln or "${HOME}" in ln)]
    assert home_prep, (
        f"server/Dockerfile never makes {SERVER_HOME} writable by a non-root "
        "uid — Calibre cannot start and the ACSM account restore fails."
    )

    home_env_at = min(i for i, ln in enumerate(lines)
                      if ln.startswith("ENV") and f"HOME={SERVER_HOME}" in ln)
    customize_at = [i for i, ln in enumerate(lines) if "calibre-customize" in ln]
    assert all(home_env_at < i for i in customize_at), (
        "server/Dockerfile sets HOME after `calibre-customize` runs, so the "
        "plugins install under /root and the runtime user never sees them."
    )


def test_server_entrypoint_needs_no_root_privileges():
    """`sh entrypoint.sh` runs alembic then uvicorn — nothing that needs uid 0.

    A `chown`, `chmod`, `mkdir` outside the writable set, or a `su`/`gosu` hop
    would make the container fail to start as a non-root user, and the failure
    would be at migration time with the database already half-upgraded.
    """
    path = os.path.join(_SERVER_DIR, "entrypoint.sh")
    with open(path, encoding="utf-8") as fh:
        body = [ln.strip() for ln in fh
                if ln.strip() and not ln.strip().startswith("#")]
    forbidden = ("chown", "chmod", "gosu", "su-exec", "setpriv", "usermod", "useradd")
    offenders = [ln for ln in body
                 if any(word in ln.split("#", 1)[0] for word in forbidden)]
    assert not offenders, (
        f"server/entrypoint.sh does something that needs root: {offenders}. "
        "The container runs as an unprivileged uid (issue #180)."
    )


def test_web_nginx_listens_on_an_unprivileged_port_only():
    """Binding <1024 needs CAP_NET_BIND_SERVICE, which `cap_drop: [ALL]` removes.

    The unprivileged base image ships its own `listen 8080` default.conf; ours
    overwrites it, so the only listen directive in the image is this one.
    """
    text = "\n".join(_instructions(WEB_DOCKERFILE))
    listens = re.findall(r"listen\s+(\d+)", text)
    assert listens, "web/Dockerfile's nginx config has no listen directive"
    assert "3000" in listens, f"web nginx no longer listens on 3000 (found {listens})"
    privileged = [port for port in listens if int(port) < 1024]
    assert not privileged, (
        f"web nginx listens on privileged port(s) {privileged}. The container "
        "drops all capabilities, so it cannot bind below 1024 (issue #180)."
    )


def test_web_nginx_keeps_the_spa_fallback_and_the_api_proxy():
    """The two behaviours a base-image swap is most likely to drop silently.

    Without the fallback every deep link 404s; without the proxy the app has no
    API at all, because `web/src/api.js` hardcodes the `/api` base.
    """
    text = "\n".join(_instructions(WEB_DOCKERFILE))
    assert "try_files $uri /index.html" in text, (
        "web nginx lost its SPA fallback — every route but / would 404."
    )
    assert "proxy_pass http://server:8000/api/" in text, (
        "web nginx lost the /api/ proxy to the server service."
    )


def test_web_image_makes_the_nginx_scratch_dirs_writable_by_any_uid():
    """`user:` in compose can name a uid the base image never chowned for.

    nginxinc/nginx-unprivileged chowns its cache dirs to uid 101 group 0; a
    compose `user: "1000:1000"` matches neither, so nginx fails on the first
    proxied response that needs a temp file. Widening the mode is what makes
    the PUID knob actually work.
    """
    text = "\n".join(_instructions(WEB_DOCKERFILE))
    assert "/var/cache/nginx" in text and "chmod" in text, (
        "web/Dockerfile never widens /var/cache/nginx — nginx cannot buffer a "
        "proxied response under an arbitrary PUID."
    )
