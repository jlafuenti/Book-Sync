"""
Repo hygiene tests (issues #152, #153, #154 — pre-publication review).

The repo is about to go public. These tests pin the invariants that make that
safe: no tracked databases/keys/build outputs (#153), no oversized blobs
sneaking into the pack (#153), a LICENSE that matches the dependency tree
(#154), and no DRM-circumvention binaries in the tree with the Dockerfile's
DRM plugin install strictly opt-in (#152).

They run `git ls-files` via subprocess and skip cleanly when git or the .git
directory is unavailable (e.g. a tarball checkout).
"""

import fnmatch
import os
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
