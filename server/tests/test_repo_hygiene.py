"""
Repo hygiene tests (issues #152, #153, #154 — pre-publication review).

The repo is about to go public. These tests pin the invariants that make that
safe: no tracked databases/keys/build outputs (#153), no oversized blobs
sneaking into the pack (#153), a LICENSE that matches the dependency tree
(#154), no DRM-circumvention binaries in the tree with the Dockerfile's
DRM plugin install strictly opt-in (#152), and no personal identifiers —
private domain, personal email, phone serial, host paths, private MCP/SSH
server names — in any tracked file (#188, #184, #183).

They run `git ls-files` via subprocess and skip cleanly when git or the .git
directory is unavailable (e.g. a tarball checkout).
"""

import fnmatch
import os
import re
import subprocess

import pytest

_SERVER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_REPO_ROOT = os.path.dirname(_SERVER_DIR)


def _git_ls_files() -> list[str]:
    """All tracked paths (forward-slash, repo-relative), or skip if no git."""
    try:
        subprocess.run(
            ["git", "rev-parse", "--git-dir"],
            cwd=_REPO_ROOT,
            check=True,
            capture_output=True,
        )
        result = subprocess.run(
            ["git", "ls-files"],
            cwd=_REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("git unavailable or not a git checkout")
    return [line for line in result.stdout.splitlines() if line.strip()]


# Patterns for files that must never be tracked: local databases, WAL/SHM
# sidecars, DB dumps, and signing keys.
_FORBIDDEN_FILE_PATTERNS = [
    "*.db",
    "*.db-wal",
    "*.db-shm",
    "*.sqlite*",
    "*.dump",
    "*.keystore",
    "*.jks",
    "*.p12",
]

# Directory prefixes that are build/cache output and must never be tracked.
_FORBIDDEN_PREFIXES = [
    "android/.gradle/",
    # Kotlin's incremental-compilation session dir, created by any local build.
    "android/.kotlin/",
    "android/build/",
    "android/app/build/",
]


def test_no_tracked_data_or_key_files():
    tracked = _git_ls_files()
    offenders = []
    for path in tracked:
        basename = os.path.basename(path)
        if any(fnmatch.fnmatch(basename, pat) for pat in _FORBIDDEN_FILE_PATTERNS):
            offenders.append(path)
        elif any(path.startswith(prefix) for prefix in _FORBIDDEN_PREFIXES):
            offenders.append(path)
    assert not offenders, (
        "Tracked files that must not be in the repository (databases, keys, "
        f"or build output): {offenders}. Remove with `git rm --cached` and "
        "extend .gitignore."
    )


# Files legitimately over 1 MB. Everything else that large is a mistake.
_SIZE_ALLOWLIST = ["web/package-lock.json"]
_SIZE_ALLOWLIST_GLOBS = ["web/public/*.png"]
_ONE_MB = 1024 * 1024


def test_no_tracked_file_over_1mb_outside_allowlist():
    tracked = _git_ls_files()
    offenders = []
    for path in tracked:
        if path in _SIZE_ALLOWLIST:
            continue
        if any(fnmatch.fnmatch(path, pat) for pat in _SIZE_ALLOWLIST_GLOBS):
            continue
        abs_path = os.path.join(_REPO_ROOT, *path.split("/"))
        try:
            size = os.path.getsize(abs_path)
        except OSError:
            continue  # tracked but missing on disk (sparse/partial checkout)
        if size > _ONE_MB:
            offenders.append(f"{path} ({size} bytes)")
    assert not offenders, (
        f"Tracked files over 1 MB outside the allowlist: {offenders}. "
        "Large binaries bloat every clone forever — keep them out of git."
    )


def test_license_file_present_and_named_in_readme():
    """Issue #154: the repo ships AGPL-3.0 and the README says so."""
    license_path = os.path.join(_REPO_ROOT, "LICENSE")
    assert os.path.isfile(license_path), "LICENSE file missing at repo root"
    with open(license_path, encoding="utf-8") as fh:
        license_text = fh.read()
    assert "GNU AFFERO GENERAL PUBLIC LICENSE" in license_text

    readme_path = os.path.join(_REPO_ROOT, "README.md")
    with open(readme_path, encoding="utf-8") as fh:
        readme_text = fh.read()
    assert "## License" in readme_text, "README.md lacks a '## License' heading"


# DRM plugin binaries and per-user key material must never be tracked (issue
# #152). Source files that merely *reference* these strings are fine — this
# matches filename components only.
_FORBIDDEN_DRM_NAME_PATTERNS = [
    "DeDRM*",
    "DeACSM*",
    "*.der",
    "*adobekey*",
    "*.voucher",
    "*activation_bytes*",
]


def test_no_drm_plugin_binaries_or_keys_tracked():
    tracked = _git_ls_files()
    offenders = []
    for path in tracked:
        for component in path.split("/"):
            if any(
                fnmatch.fnmatch(component, pat)
                for pat in _FORBIDDEN_DRM_NAME_PATTERNS
            ):
                offenders.append(path)
                break
    assert not offenders, (
        f"Tracked DRM plugin binaries or key material: {offenders}. "
        "These must never be committed."
    )


def test_dockerfile_drm_block_is_opt_in():
    """Issue #152: the default server image ships no DRM plugins.

    The DeACSM/DeDRM install must be gated behind INSTALL_DRM_PLUGINS
    (default off), same pattern as INSTALL_LOCAL_WHISPER. Pragmatic line
    scan: the ARG must default to false/0, and every calibre-customize
    invocation must appear after an `if` guard on INSTALL_DRM_PLUGINS
    within its RUN block.
    """
    dockerfile_path = os.path.join(_REPO_ROOT, "server", "Dockerfile")
    with open(dockerfile_path, encoding="utf-8") as fh:
        lines = fh.read().splitlines()

    assert any(
        line.strip() in ("ARG INSTALL_DRM_PLUGINS=false", "ARG INSTALL_DRM_PLUGINS=0")
        for line in lines
    ), "server/Dockerfile must declare ARG INSTALL_DRM_PLUGINS defaulting off"

    # Walk RUN blocks (a RUN plus its backslash continuations); every
    # calibre-customize line must be preceded, within the same block, by an
    # if-guard on INSTALL_DRM_PLUGINS.
    guarded = False
    in_run = False
    unguarded_uses = []
    for idx, raw in enumerate(lines, start=1):
        stripped = raw.strip()
        if not in_run and stripped.startswith("RUN "):
            in_run = True
            guarded = False
        if in_run:
            if "if" in stripped and "INSTALL_DRM_PLUGINS" in stripped:
                guarded = True
            if "calibre-customize" in stripped and not guarded:
                unguarded_uses.append(f"line {idx}: {stripped}")
            if not stripped.endswith("\\"):
                in_run = False
    assert not unguarded_uses, (
        "calibre-customize (DRM plugin install) runs unconditionally in "
        f"server/Dockerfile: {unguarded_uses}. Gate it behind "
        "INSTALL_DRM_PLUGINS."
    )


# ---------------------------------------------------------------------------
# Issue #187: .gitignore must fence off the file shapes that carry secrets.
#
# The bare `.env` and `/docker-compose.yml` rules match by exact name only, so a
# `docker-compose.yml.bak` (which carries the same JWT_SECRET_KEY,
# POSTGRES_PASSWORD and CREDENTIAL_ENC_KEYS as the original — one already exists
# untracked on the production host), an `.env.prod`, or an upload keystore would
# all be staged by a careless `git add -A`. Once pushed to a public repo that is
# permanent, even if the next commit deletes it. Nothing is leaked today; this
# test is the fence that keeps it that way.
# ---------------------------------------------------------------------------

# Paths that must be ignored. None of these exist in the tree — `git check-ignore`
# answers from the rules alone, so no fixture files are needed.
_MUST_BE_IGNORED = [
    # Backup/scratch copies of secret-bearing config
    "docker-compose.yml.bak",
    "server/.env.bak",
    "docker-compose.yml.orig",
    # ...and the suffixes nobody enumerated (issue #313). #187 fenced `*.bak`,
    # but the production host was found holding `docker-compose.yml.pre-151`
    # with the same five secret lines, matched by nothing. Ignore by stem
    # instead of guessing which suffix someone will type next.
    "docker-compose.yml.pre-151",
    "docker-compose.yml.old",
    "docker-compose.yml.2026-08-01",
    "jetson/docker-compose.yml.pre-151",
    # The reverse-proxy config. docs/operations.md tells operators to copy
    # Caddyfile.example and adapt it, so following the documented setup produces
    # an untracked Caddyfile holding whatever auth the proxy fronts with.
    "Caddyfile",
    "Caddyfile.bak",
    # Only `/Caddyfile.*` reaches these two; `Caddyfile.bak` alone is covered by
    # the older `*.bak` rule, so without them that rule would sit there pinned by
    # nothing -- which is the failure mode both #311 and #313 are about.
    "Caddyfile.old",
    "Caddyfile.pre-151",
    # Environment files of every flavour
    ".env.prod",
    ".env.production",
    "server/.env.local",
    # Signing material (Play upload/release keys)
    "android/app/release.keystore",
    "android/app/upload.jks",
    "certs/client.p12",
    "server/private.pem",
    "server/private.key",
]

# The `!.env.example` negative rule has to survive the `.env.*` glob, or the
# template a fresh clone copies from would stop being committable.
_MUST_NOT_BE_IGNORED = [
    ".env.example",
    "server/.env.example",
    ".env.sample",
    "web/.env.template",
    # The templates a fresh clone copies from. `docker-compose.example.yml` is a
    # different stem so the `/docker-compose.yml.*` rule cannot reach it, but
    # `Caddyfile.example` *is* matched by `/Caddyfile.*` and survives only on the
    # negation that follows it — pin all three so a reordering is caught here
    # rather than by someone's clone missing a file (issue #313).
    "docker-compose.example.yml",
    "jetson/docker-compose.example.yml",
    "Caddyfile.example",
]


def _check_ignore(path: str) -> bool:
    """True if git would ignore `path`. Skips when git is unavailable."""
    try:
        subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=_REPO_ROOT,
            check=True,
            capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("git unavailable or not a git checkout")
    # --no-index is load-bearing: without it `git check-ignore` reports any
    # *tracked* path as not-ignored no matter what the rules say. Nothing here is
    # tracked today, so both tests below would keep passing — the negative one
    # vacuously — the moment someone actually committed one of these files, which
    # is precisely when we would want to hear about it.
    result = subprocess.run(
        ["git", "check-ignore", "-q", "--no-index", path],
        cwd=_REPO_ROOT,
        capture_output=True,
    )
    # 0 = ignored, 1 = not ignored, 128 = error.
    assert result.returncode in (0, 1), (
        f"git check-ignore failed on {path!r}: {result.stderr!r}"
    )
    return result.returncode == 0


@pytest.mark.parametrize("path", _MUST_BE_IGNORED)
def test_secret_bearing_paths_are_gitignored(path):
    assert _check_ignore(path), (
        f"{path!r} is not covered by .gitignore. Backup copies, env variants and "
        "signing keys carry live secrets; one `git add -A` puts them in a public "
        "repo permanently. Add the matching pattern to .gitignore."
    )


@pytest.mark.parametrize("path", _MUST_NOT_BE_IGNORED)
def test_env_example_templates_stay_committable(path):
    assert not _check_ignore(path), (
        f"{path!r} is ignored, but example/template env files must stay tracked — "
        "they are what a fresh clone copies from. Keep the `!.env.example` "
        "exception after the `.env.*` rule in .gitignore."
    )


# ---------------------------------------------------------------------------
# Issues #188, #184, #183: no personal identifiers anywhere in the tree.
#
# The repo is published as a portfolio piece. None of the following grants
# access to anything (the hosts are LAN-only), but none of it has any value to
# a reader either: the owner's private domain, his personal email address, the
# adb serial of his phone (a persistent device identifier), the filesystem
# layout of his docker host, and the names of MCP/SSH servers nobody else has.
#
# Machine-local notes belong in `CLAUDE.local.md`, which is gitignored and
# therefore never reaches `git ls-files` at all.
#
# Every needle below is assembled from fragments at import time, so this guard
# does not itself publish the strings it exists to keep out: `git grep` for any
# of them must come back empty across the whole tree, this file included. The
# phone serial is matched by shape rather than by value (`adb -s <serial>`, which
# is the only way it ever appeared) for the same reason — a guard that spelled it
# out would be publishing the identifier it exists to remove.
# ---------------------------------------------------------------------------

# The owner's LAN-only domain. Also imported by
# `test_android_no_personal_hosts.py` so the two guards cannot disagree.
PERSONAL_DOMAIN = "lafuenti" + ".com"

_PERSONAL_PATTERNS = {
    "the owner's private domain": re.compile(
        r"\b[\w-]+(?:\.[\w-]+)*\.?" + re.escape(PERSONAL_DOMAIN) + r"\b"
    ),
    "a personal email address": re.compile(r"\b[\w.+-]+@" + r"gmail" + r"\.com\b"),
    "an adb device serial": re.compile(r"adb\s+(?:-s|--serial)\s+[0-9A-Za-z]{6,}"),
    "the docker host's filesystem layout": re.compile(
        r"/usr/share/docker-" + r"containers"
    ),
    "a private MCP or SSH server name": re.compile(
        r"\bssh-(?:docker|orin)\b|\bdocker" + r"server\b"
    ),
}

# Tracked paths exempt from the scan. `CLAUDE.local.md` is gitignored and cannot
# appear here anyway; naming it documents where this content is allowed to live.
_PERSONAL_SCAN_EXEMPT = {"CLAUDE.local.md"}


def scan_for_personal_identifiers(text: str) -> list[tuple[int, str, str]]:
    """(line number, what it is, matched text) for every personal identifier.

    Split out from the tree walk so the detectors can be exercised against a
    string: walking the real tree only shows the tree is clean today, not that
    the scanner would notice if it were not.
    """
    found = []
    for lineno, line in enumerate(text.splitlines(), 1):
        for label, pattern in _PERSONAL_PATTERNS.items():
            for match in pattern.finditer(line):
                found.append((lineno, label, match.group(0)))
    return found


def _tracked_text_files() -> list[str]:
    """Tracked paths that decode as UTF-8 text (binaries are skipped)."""
    paths = []
    for path in _git_ls_files():
        if path in _PERSONAL_SCAN_EXEMPT:
            continue
        abs_path = os.path.join(_REPO_ROOT, *path.split("/"))
        try:
            with open(abs_path, encoding="utf-8") as fh:
                fh.read()
        except (OSError, UnicodeDecodeError):
            continue
        paths.append(path)
    return paths


def test_no_personal_identifiers_in_tracked_files():
    offenders = []
    for path in _tracked_text_files():
        abs_path = os.path.join(_REPO_ROOT, *path.split("/"))
        with open(abs_path, encoding="utf-8") as fh:
            text = fh.read()
        for lineno, label, literal in scan_for_personal_identifiers(text):
            offenders.append(f"  {path}:{lineno} -> {label} ({literal!r})")
    assert not offenders, (
        "Personal identifiers in tracked files. None of this helps a reader, and "
        "a push to a public repo is permanent — keep machine-local detail in "
        "CLAUDE.local.md (gitignored) instead:\n" + "\n".join(offenders)
    )


def test_the_personal_identifier_scanner_reads_the_tree():
    """A silently-empty walk would make the test above pass for the wrong reason."""
    paths = _tracked_text_files()
    assert len(paths) > 100, f"only found {len(paths)} tracked text files"
    assert "CLAUDE.md" in paths


# Assembled from fragments for the same reason the patterns are: a sample that
# matches is, by definition, the thing this guard keeps out of the tree.
@pytest.mark.parametrize(
    "sample",
    [
        "https://tandem." + PERSONAL_DOMAIN,
        "someone@" + "gmail.com",
        "adb -s " + "R5CT20ABCDE logcat",
        "cd /usr/share/docker-" + "containers/Book-Sync",
        "use the ssh-" + "docker MCP server",
        "the ssh-" + "orin host",
        "ssh docker" + "server",
    ],
)
def test_personal_identifier_detectors_match_what_they_are_meant_to_catch(sample):
    assert scan_for_personal_identifiers(sample), f"missed: {sample!r}"


@pytest.mark.parametrize(
    "sample",
    [
        # The project's own address keeps the owner's GitHub handle: that is
        # public by definition, not a private identifier.
        "https://github.com/jlafuenti/Book-Sync",
        # Placeholders in documentation must stay writable.
        "adb -s <device-serial> logcat",
        "git config user.email <old-email>",
        "https://tandem.example.com",
        "docker compose logs server",
    ],
)
def test_personal_identifier_detectors_leave_legitimate_text_alone(sample):
    assert scan_for_personal_identifiers(sample) == [], f"false positive: {sample!r}"
