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
    # Docker compose secrets (issue #180). These are the live values, one per
    # file, in the directory docker-compose.example.yml points its `secrets:`
    # block at — the most concentrated collection of secrets in the whole
    # deployment, sitting inside the checkout by design.
    "secrets/jwt_secret_key",
    "secrets/postgres_password",
    "secrets/credential_enc_keys",
    "secrets/database_url",
    "secrets/anything-the-operator-adds-later",
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
    # The one tracked file inside the otherwise-ignored secrets directory: it
    # explains what the operator has to create there. `/secrets/*` plus a
    # `!/secrets/README.md` negation — and not `/secrets/`, because git cannot
    # re-include a file inside an excluded *directory* (issue #180).
    "secrets/README.md",
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

# The owner's LAN-only domain, shared with `test_android_no_personal_hosts.py`
# through `tests/personal_identifiers.py` so the two guards cannot disagree.
from tests.personal_identifiers import PERSONAL_DOMAIN  # noqa: E402

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


# ---------------------------------------------------------------------------
# Issue #189: the personal-data guard, widened to the whole repository.
#
# `test_android_no_personal_hosts.py` has walked `android/app/**` since issue
# #58 and never grew past it: docs, server, web, CI config and the agent
# guidance files were all out of scope, which is where every repo-level leak
# found in the pre-publication review actually sat. The detectors were the good
# part; the walk was the gap. So this reuses them verbatim -- one set of
# regexes, hardened by that file's own tests (#311) -- and points them at every
# tracked text file instead of one directory.
#
# Once the repo is public a regression here is a published one, and git history
# makes it permanent.
# ---------------------------------------------------------------------------

import re  # noqa: E402  (kept next to the block that uses it)

from tests.test_android_no_personal_hosts import (  # noqa: E402
    ALLOWED_LITERALS,
    _PERSONAL_HOST,
    _PRIVATE_IP,
)

try:  # comment-aware stripping was dropped from the Android guard in #372
    from tests.test_android_no_personal_hosts import strip_comments  # noqa: E402
except ImportError:  # pragma: no cover - depends on the sibling guard's shape
    def strip_comments(text: str, suffix: str) -> str:  # noqa: D401
        """No comment stripping: scan every file raw (stricter, not looser)."""
        return text

# Rule name -> detector. The first two are imported above rather than restated:
# a second copy of a regex is a second copy to keep correct, and #311 was
# exactly a detector that had quietly stopped matching.
_PERSONAL_DATA_RULES = {
    "private_ip": _PRIVATE_IP,
    "personal_host": _PERSONAL_HOST,
    # The maintainer's own deployment layout: useless to anyone else, and a free
    # map of the host for anyone else.
    "deploy_path": re.compile(r"/usr/share/docker-" + r"containers"),
    # SSH aliases that only resolve inside the maintainer's own config.
    "ssh_alias": re.compile(r"\bssh-(?:docker|orin)\b"),
    # A physical device serial (`adb -s <serial>`), which identifies one phone.
    "device_serial": re.compile(r"\badb\s+(?:-s|--serial)\s+\S+"),
    # A developer's home directory, the usual way an absolute local path leaks
    # into a doc or a script.
    "windows_home": re.compile(r"[A-Za-z]:\\Users\\[^\\\s\"'`]+"),
}

# Comment stripping is only meaningful for the languages the Android guard knows
# how to parse, and its deliberate "prose describing history is not a baked-in
# host" exemption applies to those. Everything else is scanned raw: a private
# address in a Markdown paragraph or a YAML comment is still published.
_COMMENT_AWARE_SUFFIXES = {".kt", ".kts", ".xml", ".pro"}

# Binary shapes: scanning them yields mojibake, not findings. Anything not on
# this list is still sniffed for a NUL byte below, so an unknown binary format
# does not need a code change to be skipped.
_BINARY_SUFFIXES = (
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".webp", ".svgz", ".pdf", ".zip",
    ".jar", ".aar", ".so", ".dll", ".exe", ".ttf", ".otf", ".woff", ".woff2",
    ".mp3", ".m4a", ".m4b", ".wav", ".epub", ".mobi", ".azw3", ".keystore",
    ".jks", ".p12", ".der", ".bin",
)

_SKIPPED_PATHS = {
    # Machine-local credentials file. Gitignored, so it should never appear in
    # the tracked list at all -- named here so that if it ever is committed, the
    # failure is the hygiene test that catches tracked secrets rather than a
    # confusing pile of hits from this one.
    "CLAUDE.local.md",
    # Generated, enormous, and full of registry hashes that look like nothing
    # this guard is looking for.
    "web/package-lock.json",
}

# (path, rule) -> why this file is allowed to trip that rule. Deliberately keyed
# by rule and not by literal: an allow-listed file is still scanned by every
# other rule, and no entry here has to spell out the very string the guard
# exists to keep out of the repo.
_PERSONAL_DATA_ALLOWLIST = {
    # -- Documented examples. RFC1918 addresses are the correct thing to write
    # in a self-hosting guide; these are illustrations, not anyone's network.
    ("docker-compose.example.yml", "private_ip"): "example subnet in a template",
    ("docs/android.md", "private_ip"): "documented example LAN address",
    ("docs/operations.md", "private_ip"): "documented example subnet",
    ("docs/demo-server.md", "private_ip"): "documented example subnet in the Caddy/compose sample",
    # -- Production code whose subject *is* the address ranges.
    ("server/services/url_safety.py", "private_ip"): (
        "the SSRF blocklist: these ranges are the thing it refuses to fetch"
    ),
    ("web/src/pages/SystemPage.jsx", "private_ip"): "placeholder in a form field",
    # -- Synthetic test inputs. Every one is a made-up address chosen to
    # exercise a code path; see the note in test_android_no_personal_hosts.py
    # about keeping such samples fictional.
    ("server/tests/test_abs_metadata.py", "private_ip"): "synthetic test input",
    ("server/tests/test_audit_retention.py", "private_ip"): "synthetic test input",
    ("server/tests/test_auth.py", "private_ip"): "synthetic test input",
    ("server/tests/test_match_apply_cover.py", "private_ip"): "synthetic test input",
    ("server/tests/test_settings.py", "private_ip"): "synthetic test input",
    ("server/tests/test_startup.py", "private_ip"): "synthetic test input",
    ("server/tests/test_url_safety.py", "private_ip"): "synthetic test input",
    ("web/src/api.test.js", "private_ip"): "synthetic test input",
    ("web/src/pages/SystemPage.test.jsx", "private_ip"): "synthetic test input",
    ("web/src/pages/UserManagementPage.auditlog.test.jsx", "private_ip"): (
        "synthetic test input"
    ),
    # -- The guards themselves. Their detectors cannot be tested without
    # fixtures shaped like the thing they detect.
    ("server/tests/test_android_no_personal_hosts.py", "private_ip"): (
        "the detector's own fixtures"
    ),
    ("server/tests/test_android_no_personal_hosts.py", "personal_host"): (
        "the detector's own fixtures, plus the legacy hostname it migrates away from"
    ),
    ("server/tests/test_repo_hygiene.py", "deploy_path"): "the rule's own pattern",
    ("server/tests/test_repo_hygiene.py", "ssh_alias"): "the rule's own fixtures",
    ("server/tests/test_repo_hygiene.py", "device_serial"): "the rule's own fixtures",
    ("server/tests/test_repo_hygiene.py", "windows_home"): "the rule's own fixtures",
}


def _looks_binary(raw: bytes) -> bool:
    return b"\x00" in raw[:8192]


def _scannable_tracked_files() -> list[str]:
    """Tracked repo-relative paths this guard reads. Binaries excluded."""
    paths = []
    for path in _git_ls_files():
        if path in _SKIPPED_PATHS or path.endswith(_BINARY_SUFFIXES):
            continue
        abs_path = os.path.join(_REPO_ROOT, *path.split("/"))
        try:
            with open(abs_path, "rb") as fh:
                head = fh.read(8192)
        except OSError:
            continue  # tracked but missing on disk (sparse/partial checkout)
        if _looks_binary(head):
            continue
        paths.append(path)
    return paths


def _personal_data_hits(text: str, suffix: str) -> list[tuple[int, str, str]]:
    """(line number, rule, literal) for every detector hit that isn't allowed."""
    if suffix in _COMMENT_AWARE_SUFFIXES:
        text = strip_comments(text, suffix)
    hits = []
    for lineno, line in enumerate(text.splitlines(), 1):
        for rule, pattern in _PERSONAL_DATA_RULES.items():
            for match in pattern.finditer(line):
                literal = match.group(0)
                if literal in ALLOWED_LITERALS:
                    continue
                hits.append((lineno, rule, literal))
    return hits


def test_no_personal_data_in_any_tracked_file():
    offenders = []
    for path in _scannable_tracked_files():
        abs_path = os.path.join(_REPO_ROOT, *path.split("/"))
        with open(abs_path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        suffix = os.path.splitext(path)[1]
        for lineno, rule, literal in _personal_data_hits(text, suffix):
            if (path, rule) in _PERSONAL_DATA_ALLOWLIST:
                continue
            offenders.append(f"  {path}:{lineno} [{rule}] -> {literal}")
    assert not offenders, (
        "Personal or private details in tracked files. This repo is going "
        "public: a private address, a personal hostname, a deployment path or a "
        "device serial committed here is published and stays in the history.\n"
        + "\n".join(offenders)
        + "\n\nRemove them, or -- if the literal is genuinely a documented "
        "example or a synthetic test input -- add a (path, rule) entry to "
        "_PERSONAL_DATA_ALLOWLIST with a one-line reason."
    )


def test_the_scan_reaches_docs_and_ci_config():
    """The widening is the whole point of #189, so assert the walk is wide.

    A guard that silently stops covering something keeps passing, which is how
    the Android-only version sat here looking like a repo-wide gate.
    """
    scanned = set(_scannable_tracked_files())
    assert "CLAUDE.md" in scanned
    assert "README.md" in scanned
    assert any(p.startswith("docs/") and p.endswith(".md") for p in scanned), (
        "no docs/*.md reached the scan"
    )
    assert any(p.startswith(".github/workflows/") for p in scanned), (
        "no CI workflow reached the scan"
    )
    assert any(p.startswith("server/") and p.endswith(".py") for p in scanned)
    assert any(p.startswith("web/src/") for p in scanned)
    assert any(p.startswith("android/") for p in scanned)
    assert len(scanned) > 300, f"only {len(scanned)} files reached the scan"


def test_binary_and_local_files_are_excluded_from_the_scan():
    scanned = set(_scannable_tracked_files())
    assert "CLAUDE.local.md" not in scanned, (
        "the machine-local credentials file must never be scanned -- or tracked"
    )
    assert "web/package-lock.json" not in scanned
    assert not any(p.endswith(_BINARY_SUFFIXES) for p in scanned), (
        "a binary file reached the scan; it would produce mojibake, not findings"
    )


def test_a_nul_byte_marks_a_file_binary():
    """The sniff, not the suffix list, is what catches an unknown binary format."""
    assert _looks_binary(b"PK\x03\x04\x00\x00stuff")
    assert not _looks_binary(b"plain text, no NUL here\n")


# Synthetic fixtures for the rules this file adds. The two imported detectors
# are already exercised in test_android_no_personal_hosts.py; these four are
# new, and a rule that silently matches nothing is worse than no rule -- it
# reads like coverage.
@pytest.mark.parametrize(
    "rule,sample",
    [
        ("deploy_path", "cd /usr/share/docker-" + "containers/Some-Project"),
        ("ssh_alias", "use the ssh-" + "docker MCP server"),
        ("ssh_alias", "tail logs over ssh-" + "orin"),
        ("device_serial", "adb -s " + "ABCD1234EF logcat -c"),
        ("device_serial", "adb --serial " + "ABCD1234EF shell"),
        ("windows_home", r"C:\Users\someone\Documents\notes.md"),
    ],
)
def test_the_added_rules_match_what_they_are_meant_to_catch(rule, sample):
    assert _PERSONAL_DATA_RULES[rule].search(sample), (
        f"the {rule} rule no longer matches {sample!r}"
    )


def test_the_added_rules_do_not_fire_on_ordinary_text():
    """A rule that matches everything gets allow-listed into uselessness."""
    innocuous = (
        "docker compose up -d\n"
        "ssh into the server and run adb devices\n"
        "the path /usr/share/doc is fine\n"
    )
    assert _personal_data_hits(innocuous, ".md") == []
