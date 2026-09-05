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
