"""
Docs index contract (issues #239, #241 — pre-publication review).

`docs/` had no index of its own and the root README kept a hand-maintained
table listing 8 of 10 files, which is how `docs/handoff-position-sync.md` and
five shipped implementation plans under `docs/superpowers/` sat in the tree with
nothing pointing at them and nobody noticing they had gone stale. GitHub renders
`docs/` as a bare file list, so on a public repo an unindexed file is just a
filename.

`docs/README.md` is now the single index, and these tests make it the source of
truth in both directions: every tracked doc is listed, and every link in the
index resolves. A future orphan — or a future subfolder of working notes — fails
here rather than shipping.

Like test_repo_hygiene.py, this reads the tracked file list via `git ls-files`
and skips cleanly when git isn't available.
"""

import os
import re
import subprocess

import pytest

_SERVER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_REPO_ROOT = os.path.dirname(_SERVER_DIR)
_DOCS_DIR = os.path.join(_REPO_ROOT, "docs")
_INDEX_REL = "docs/README.md"

# Markdown inline links: [text](target). Anchors/queries are stripped later.
_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")


def _tracked_docs() -> list[str]:
    """Tracked `docs/**/*.md` paths (forward-slash, repo-relative), index excluded."""
    try:
        subprocess.run(
            ["git", "rev-parse", "--git-dir"],
            cwd=_REPO_ROOT,
            check=True,
            capture_output=True,
        )
        result = subprocess.run(
            ["git", "ls-files", "docs"],
            cwd=_REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("git unavailable or not a git checkout")

    return sorted(
        line.strip()
        for line in result.stdout.splitlines()
        if line.strip().endswith(".md") and line.strip() != _INDEX_REL
    )


def _index_text() -> str:
    path = os.path.join(_REPO_ROOT, *_INDEX_REL.split("/"))
    assert os.path.isfile(path), (
        "docs/README.md is missing. It is the single index for docs/ — the root "
        "README points at it instead of keeping a second, hand-maintained list."
    )
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _index_link_targets() -> list[str]:
    """Local link targets in docs/README.md (external URLs and anchors dropped)."""
    targets = []
    for raw in _LINK_RE.findall(_index_text()):
        if raw.startswith(("http://", "https://", "mailto:", "#")):
            continue
        targets.append(raw.split("#", 1)[0].split("?", 1)[0])
    return targets


def test_docs_index_lists_every_doc():
    """Every tracked doc must appear in the index — no orphans."""
    listed = set(_index_link_targets())
    missing = [
        doc
        for doc in _tracked_docs()
        # Links in docs/README.md are relative to docs/, so `docs/foo.md` is
        # written `foo.md` and `docs/sub/foo.md` as `sub/foo.md`.
        if doc[len("docs/"):] not in listed
    ]
    assert not missing, (
        f"Documents under docs/ that docs/README.md does not link: {missing}. "
        "Add a row for each — an unlisted file is invisible on GitHub's bare "
        "directory listing. If it is a historical artefact rather than current "
        "documentation, list it under the Historical heading (or delete it)."
    )


def test_docs_index_links_resolve():
    """The reverse: nothing in the index points at a file that isn't there."""
    broken = [
        target
        for target in _index_link_targets()
        if not os.path.exists(os.path.join(_DOCS_DIR, *target.split("/")))
    ]
    assert not broken, (
        f"docs/README.md links to files that do not exist: {broken}."
    )


def test_root_readme_points_at_the_docs_index():
    """One list to maintain, not two (issue #239).

    The root README used to carry its own table; it drifted. It now points here.
    """
    with open(os.path.join(_REPO_ROOT, "README.md"), encoding="utf-8") as fh:
        readme = fh.read()
    assert "docs/README.md" in readme, (
        "README.md no longer points at docs/README.md — the docs index has to be "
        "reachable from the front page or nobody finds it."
    )


def test_no_shipped_implementation_plans_under_docs():
    """Issue #241: `docs/` is documentation, not a plan archive.

    Five dated, checkbox-style implementation plans shipped under
    `docs/superpowers/`, describing work that had already landed — one of them
    telling contributors the opposite of the current testing rule. Keep working
    notes out of the published docs tree; the index test above catches the
    general case, this pins the specific directory that came back once.
    """
    offenders = [doc for doc in _tracked_docs() if doc.startswith("docs/superpowers/")]
    assert not offenders, (
        f"Implementation plans tracked under docs/: {offenders}. Plans and specs "
        "belong in the issue/PR or an untracked path, not in the published docs "
        "tree where a visitor can't tell them from current documentation."
    )
