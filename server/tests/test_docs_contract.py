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


# ---------------------------------------------------------------------------
# Issue #150: the privacy policy is a shipped artefact, not a Play Console field.
#
# Google Play will not accept a listing without a publicly reachable privacy
# policy, and the Data safety answers are enforced retroactively. Because Tandem
# is self-hosted, boilerplate is wrong in the way that gets listings pulled: the
# app author receives nothing, but the reader's "Define" action does send the
# selected word to a third party. The policy has to say so, and it has to keep
# saying so after someone edits it.
#
# These tests pin the three claims that would be expensive to get wrong, plus the
# repo-hygiene rule that a public document must not name anybody's actual server.
# The matching code-side guard — that `api.dictionaryapi.dev` stays the *only*
# hard-coded third-party host in the app — lives in
# test_android_no_personal_hosts.py, so the policy and the code cannot drift
# apart silently.
# ---------------------------------------------------------------------------

_PRIVACY_REL = "docs/privacy.md"

# The one third-party destination the app reaches on its own. If this ever
# changes, the policy, the Data safety form and the Android host guard all change
# with it.
_DECLARED_THIRD_PARTY_HOST = "api.dictionaryapi.dev"


def _privacy_text() -> str:
    path = os.path.join(_REPO_ROOT, *_PRIVACY_REL.split("/"))
    assert os.path.isfile(path), (
        "docs/privacy.md is missing. Play requires a publicly reachable privacy "
        "policy URL for every submission (issue #150), and it is published from "
        "this file via GitHub Pages."
    )
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def test_privacy_policy_exists_and_is_linked():
    """The policy has to be reachable from the front page and the Android guide."""
    _privacy_text()  # existence

    for rel in ("README.md", "docs/android.md"):
        with open(os.path.join(_REPO_ROOT, *rel.split("/")), encoding="utf-8") as fh:
            text = fh.read()
        assert "privacy.md" in text, (
            f"{rel} does not link docs/privacy.md. A policy nobody can find from "
            "the front page is not a published policy."
        )


def test_privacy_policy_names_the_dictionary_lookup():
    """The one flow a user would not predict must be named, not paraphrased.

    `AppModule.provideDictionaryRetrofit` sends the selected word to
    api.dictionaryapi.dev as a GET path segment. Play's Data safety form asks who
    the third party is; the policy is where that answer is written down.
    """
    assert _DECLARED_THIRD_PARTY_HOST in _privacy_text(), (
        f"docs/privacy.md must name {_DECLARED_THIRD_PARTY_HOST} — it is the only "
        "third party the app sends anything to, and the Data safety form's "
        "'shared with third parties' answer depends on it."
    )


def test_privacy_policy_states_there_is_no_analytics():
    """The 'no analytics' claim is graded against the form — say it in words.

    Pinned as a phrase because a policy that merely omits the subject reads, to a
    reviewer, as an undeclared SDK rather than as an absent one.
    """
    assert "no analytics" in _privacy_text().lower(), (
        "docs/privacy.md must state 'no analytics' explicitly. The absence of an "
        "analytics/ads/crash SDK is what makes the short Data safety answers "
        "truthful; leaving it implied wastes the simplification."
    )


def test_privacy_policy_names_no_real_host_or_private_address():
    """The policy is published to the world — it must not name anyone's server.

    Reuses the Android host guard's detectors (issues #58/#311) rather than
    keeping a second copy: private-range IPs and `*.lafuenti.com` are exactly as
    wrong in a public document as they are in the app's sources. Write
    `<your-server>` or an RFC 2606 example host instead.
    """
    from tests.test_android_no_personal_hosts import scan_plain_text

    offenders = scan_plain_text(_privacy_text())
    assert not offenders, (
        "docs/privacy.md names a private address or a personal hostname: "
        + ", ".join(f"line {ln} -> {lit}" for ln, lit in offenders)
        + ". Write `<your-server>` or an example.com host — this file is "
        "published publicly and outlives any single deployment."
    )
