"""
The changelog gate: a PR that changes what an operator deploys must say so.

`CHANGELOG.md` has an `## [Unreleased]` section that is meant to collect every
change between releases, and `docs/releasing.md` builds each release's notes
from it. Nothing enforced that. On one evening eleven issues landed —
server, web and Android — and not one of them reached the changelog, which is
also how `APP_VERSION` sat at `0.1.0` while all of it merged.

It matters more now than it did: the System page's update check compares a
running server against published GitHub Releases, and a release whose notes
miss most of what changed tells an operator nothing about what they are
upgrading into.

**The version number is deliberately not what this gate checks.** It moves
once, when a release is cut. Bumping it per PR would collide between parallel
branches, stamp servers with versions that were never tagged, and force a
patch/minor/major decision before anyone can see what the release contains.

The decision lives in `.github/scripts/changelog_gate.py` rather than inline in
the workflow so that it can be tested here; the workflow only feeds it a diff.
"""

import importlib.util
import os
import re

import pytest

_SERVER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_REPO_ROOT = os.path.dirname(_SERVER_DIR)
_SCRIPT = os.path.join(_REPO_ROOT, ".github", "scripts", "changelog_gate.py")
_WORKFLOW = os.path.join(_REPO_ROOT, ".github", "workflows", "changelog.yml")


def _gate():
    """Load the script by path — it deliberately lives outside `server/`.

    Were it under `server/`, the PR that introduced the gate would be the first
    PR required to log itself, for a change no operator deploys.
    """
    assert os.path.isfile(_SCRIPT), (
        ".github/scripts/changelog_gate.py is missing — the changelog workflow "
        "has nothing to run."
    )
    spec = importlib.util.spec_from_file_location("changelog_gate", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# What counts as a deployable change
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", [
    "server/routers/library.py",
    "server/services/position_resolver.py",
    "server/requirements.txt",
    "server/alembic/versions/0042_something.py",
    "web/src/pages/SystemPage.jsx",
    "web/src/lib/pairOpenTarget.js",
    "web/package.json",
])
def test_a_server_or_web_change_needs_an_entry(path):
    assert _gate().changelog_required([path]) is True


@pytest.mark.parametrize("path", [
    # Android is out of scope by decision: Play has its own "What's new" text,
    # and the update check this exists for is about what an operator deploys.
    "android/app/src/main/java/com/booksync/ui/home/HomeViewModel.kt",
    "docs/android.md",
    "README.md",
    ".github/workflows/tests.yml",
    "jetson/server.py",
    "CLAUDE.md",
])
def test_a_change_nobody_deploys_does_not(path):
    assert _gate().changelog_required([path]) is False


@pytest.mark.parametrize("path", [
    "server/tests/test_library.py",
    "server/tests/fixtures/sync_parity/restore_cases.json",
    "web/src/test/setup.js",
    "web/src/pages/SystemPage.test.jsx",
    "web/src/lib/pairOpenTarget.test.js",
])
def test_a_change_confined_to_tests_does_not(path):
    """A test-only change alters nothing an operator runs.

    Without this exception the gate would demand a changelog line for, say, a
    fix to flaky async timeouts — exactly the noise that teaches people to
    reach for the skip label by reflex, after which the gate means nothing.
    """
    assert _gate().changelog_required([path]) is False


def test_shared_build_and_test_config_counts_as_deployable():
    """`web/vite.config.js` configures the test runner *and* the production build.

    Run against real history, the gate flagged the PR that fixed flaky web test
    timeouts: its only non-test change was `testTimeout` inside this file. That
    is a false positive, and it is the right one to accept. The gate cannot tell
    which half of the file changed, and excluding it would let a genuine build
    change — a new `define`, a changed output path — merge unlogged. A test-only
    edit here is exactly what the `skip-changelog` label is for.
    """
    assert _gate().changelog_required(["web/vite.config.js"]) is True


def test_one_deployable_file_among_tests_still_needs_an_entry():
    """The exceptions are per file, not per PR — a real change can't hide in a test-heavy diff."""
    changed = [
        "server/tests/test_a.py",
        "server/tests/test_b.py",
        "server/services/update_check.py",
    ]
    assert _gate().changelog_required(changed) is True


def test_an_empty_diff_needs_nothing():
    assert _gate().changelog_required([]) is False


# ---------------------------------------------------------------------------
# Pass / fail
# ---------------------------------------------------------------------------


def test_a_deployable_change_with_a_changelog_entry_passes():
    assert _gate().passes(["server/routers/library.py", "CHANGELOG.md"], labels=[]) is True


def test_a_deployable_change_without_one_fails():
    assert _gate().passes(["server/routers/library.py"], labels=[]) is False


def test_the_skip_label_is_an_escape_hatch():
    assert _gate().passes(["server/routers/library.py"], labels=["skip-changelog"]) is True


def test_only_the_exact_label_skips():
    """A near-miss label must not quietly disable the gate."""
    gate = _gate()
    assert gate.passes(["web/src/App.jsx"], labels=["skip changelog"]) is False
    assert gate.passes(["web/src/App.jsx"], labels=["no-changelog"]) is False


def test_touching_a_nested_changelog_does_not_count():
    """Only the root `CHANGELOG.md` is the one releases are built from."""
    assert _gate().passes(["web/src/App.jsx", "web/CHANGELOG.md"], labels=[]) is False


# ---------------------------------------------------------------------------
# The workflow — the two mistakes that would make the gate silently inert
# ---------------------------------------------------------------------------


def _workflow() -> str:
    """The workflow with comments stripped.

    Every assertion below reads code, never prose. The workflow explains at
    length why it avoids `pull_request_target` and a `paths:` filter, so a raw
    substring check would fail on that explanation — or, worse, a comment
    mentioning `labeled` would satisfy the trigger check while the real `types:`
    list lacked it.
    """
    assert os.path.isfile(_WORKFLOW), ".github/workflows/changelog.yml is missing"
    with open(_WORKFLOW, encoding="utf-8") as fh:
        return "\n".join(
            line.split("#", 1)[0].rstrip()
            for line in fh.read().splitlines()
            if not line.lstrip().startswith("#")
        )


def test_the_workflow_reruns_when_a_label_changes():
    """Without `labeled`/`unlabeled`, adding `skip-changelog` never re-runs the check.

    The PR would sit red with the escape hatch already applied, and the only way
    out would be pushing an empty commit.
    """
    match = re.search(r"^\s*types:\s*\[([^\]]*)\]", _workflow(), re.MULTILINE)
    assert match, "changelog.yml must list pull_request `types:` explicitly"
    types = {t.strip() for t in match.group(1).split(",")}
    for event in ("opened", "synchronize", "reopened", "labeled", "unlabeled"):
        assert event in types, f"changelog.yml pull_request types must include `{event}`"


def test_the_workflow_has_no_paths_filter():
    """A required check that is path-filtered never reports on an unrelated PR.

    GitHub then waits on it forever and the PR cannot merge. The job must always
    run and decide from the diff itself — which is why the decision is in the
    script and not in the trigger.
    """
    text = _workflow()
    assert not re.search(r"^\s*paths(-ignore)?:", text, re.MULTILINE), (
        "changelog.yml must not use a `paths:` / `paths-ignore:` filter"
    )


def test_the_workflow_never_uses_pull_request_target():
    """`pull_request_target` runs with a write token and secrets against fork code.

    This check needs neither, so it must not be able to acquire them.
    """
    assert "pull_request_target" not in _workflow()


def test_the_workflow_runs_the_script():
    assert "changelog_gate.py" in _workflow()
