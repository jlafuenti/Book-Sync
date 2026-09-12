"""
CI and public-repo scaffolding contract (issues #189, #190 — pre-publication review).

Two things are pinned here, both of which are invisible at review time and only
bite once the repo is public:

**CI hardening (#189).** Workflows that declare no `permissions:` inherit the
repository default, which a settings change can widen from read to write without
touching a file in this repo. Actions referenced by a moving tag (`@v4`) execute
whatever the upstream owner force-pushes onto that tag — the standard supply-chain
path into a public repo's CI. Both are one-line regressions to reintroduce and
neither shows up in a diff review, so they are asserted rather than remembered.

**Public-repo scaffolding (#190).** A stranger who finds a vulnerability needs a
private channel (SECURITY.md + GitHub private vulnerability reporting) rather than
a public issue that discloses a live bug; a contributor needs the workflow written
down (CONTRIBUTING.md). These tests assert the files exist and carry the two
decisions that are easy to undo by accident: blank issues stay off, and the
security contact link points at the advisories form.

Line scans, not YAML parsing: PyYAML is not a declared server dependency, and the
same pragmatic approach is already used by test_compose_contract.py. Everything
here reads files that ship in the repo, so no test needs git or the network.
"""

import os
import re

import pytest

_SERVER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_REPO_ROOT = os.path.dirname(_SERVER_DIR)
_WORKFLOW_DIR = os.path.join(_REPO_ROOT, ".github", "workflows")
_ISSUE_TEMPLATE_DIR = os.path.join(_REPO_ROOT, ".github", "ISSUE_TEMPLATE")


def _workflow_names() -> list[str]:
    """Workflow filenames, sorted. Empty if the directory is missing."""
    if not os.path.isdir(_WORKFLOW_DIR):
        return []
    return sorted(
        name
        for name in os.listdir(_WORKFLOW_DIR)
        if name.endswith((".yml", ".yaml"))
    )


def _read(rel_path: str) -> str:
    with open(os.path.join(_REPO_ROOT, *rel_path.split("/")), encoding="utf-8") as fh:
        return fh.read()


def _workflow_lines(name: str) -> list[str]:
    with open(os.path.join(_WORKFLOW_DIR, name), encoding="utf-8") as fh:
        return fh.read().splitlines()


# ---------------------------------------------------------------------------
# #189 — workflow hardening
# ---------------------------------------------------------------------------


def test_the_workflow_directory_is_actually_populated():
    """A missing directory would make every parametrized test below vacuous."""
    names = _workflow_names()
    assert len(names) >= 4, f"expected the CI workflows, found {names}"


def _top_level_permissions(name: str) -> list[str] | None:
    """The entries of the column-0 `permissions:` block, or None if absent.

    Column 0 is what makes it top-level: `permissions:` also appears indented
    under individual jobs, and a job-level grant does not constrain the other
    jobs in the file.
    """
    lines = _workflow_lines(name)
    for idx, line in enumerate(lines):
        if re.match(r"^permissions:\s*\{", line):
            return [line.split(":", 1)[1].strip()]
        if re.match(r"^permissions:\s*(#.*)?$", line):
            entries = []
            for follower in lines[idx + 1 :]:
                if not follower.strip() or follower.lstrip().startswith("#"):
                    continue
                if not follower[:1].isspace():
                    break
                entries.append(follower.strip())
            return entries
    return None


@pytest.mark.parametrize("name", _workflow_names())
def test_every_workflow_declares_top_level_permissions(name):
    entries = _top_level_permissions(name)
    assert entries is not None, (
        f".github/workflows/{name} declares no top-level `permissions:`, so its "
        "GITHUB_TOKEN inherits whatever the repository default happens to be. "
        "Add `permissions:\\n  contents: read` (plus only what a specific job "
        "needs, declared on that job)."
    )
    assert any("contents: read" in entry for entry in entries), (
        f".github/workflows/{name} declares `permissions:` but not "
        f"`contents: read`; found {entries}"
    )


# ---------------------------------------------------------------------------
# #451 — every job has a timeout
# ---------------------------------------------------------------------------
#
# A hung step holds a runner for GitHub's default of six hours. With a handful
# of concurrent runners, one hang (pytest-cov 7 on PR #405) stalls every other
# PR's checks behind it. The longest job on main runs about nine minutes;
# nothing needs more than an hour.

_JOB_RE = re.compile(r"^  ([A-Za-z0-9_-]+):\s*$")
_TIMEOUT_RE = re.compile(r"^    timeout-minutes:\s*(\d+)\s*$")


def _jobs_with_timeouts(name: str) -> dict[str, int | None]:
    """Map each job id in a workflow to its `timeout-minutes`, or None."""
    jobs: dict[str, int | None] = {}
    current = None
    in_jobs = False
    for line in _workflow_lines(name):
        if line.startswith("jobs:"):
            in_jobs = True
            continue
        if not in_jobs:
            continue
        if line and not line.startswith(" "):
            in_jobs = False
            continue
        match = _JOB_RE.match(line)
        if match:
            current = match.group(1)
            jobs[current] = None
            continue
        match = _TIMEOUT_RE.match(line)
        if match and current is not None:
            jobs[current] = int(match.group(1))
    return jobs


@pytest.mark.parametrize("name", _workflow_names())
def test_every_job_has_a_bounded_timeout(name):
    jobs = _jobs_with_timeouts(name)
    assert jobs, f".github/workflows/{name} defines no jobs"
    for job, minutes in jobs.items():
        assert minutes is not None, (
            f".github/workflows/{name} job `{job}` has no `timeout-minutes:`; a hung "
            "step would hold a runner for six hours and stall every other PR."
        )
        assert 0 < minutes <= 60, (
            f".github/workflows/{name} job `{job}` sets timeout-minutes={minutes}; "
            "keep it at three times the job's normal duration, and under an hour."
        )


# ---------------------------------------------------------------------------
# Test suites run once per change, not twice
# ---------------------------------------------------------------------------
#
# `push: branches: ["**"]` plus `pull_request` ran every suite twice for each
# push to a PR branch — one run per event — which doubled runner load and, with
# a handful of hosted runners, pushed the nine-minute server suite past its
# thirty-minute timeout during a Dependabot wave. The pull_request run is the
# one the ruleset's required checks read, so the push trigger only needs main
# (the post-merge run). gitleaks is the exception and keeps every branch push:
# a branch push is how a secret usually lands, and it costs seconds.

_PUSH_BRANCHES_RE = re.compile(
    r"^on:\s*\n(?:.*\n)*?\s+push:\s*\n\s+branches:\s*\[([^\]]*)\]",
    re.MULTILINE,
)


# Workflows that are deliberately not test suites, and why. This is a denylist
# rather than an allowlist on purpose: a new *suite* that forgot its triggers
# would slip through an allowlist silently, whereas a new non-suite workflow
# fails here until someone writes down why it is exempt.
_NOT_TEST_SUITES = {
    # A branch push is how a secret usually lands, and the scan costs seconds,
    # so this one keeps every push rather than just main's.
    "gitleaks.yml",
    # Publishes container images. Running it on `pull_request` would build and
    # push images from code that has not merged — anyone who can open a PR could
    # publish an image the demo stack then pulls (#147).
    "publish-images.yml",
    # Triggers a rebuild of the public website, which serves privacy.md,
    # terms.md and account-deletion.md. Running it on `pull_request` would
    # publish those pages from a branch that has not merged — the same hazard as
    # publish-images.yml, and worse in kind: the privacy policy and the
    # account-deletion URL are the two pages Play reads, so an unmerged edit
    # would be live on a legally meaningful page.
    "publish-site.yml",
}


# ---------------------------------------------------------------------------
# The site rebuild must not leak its hook, and must not fire from a branch
# ---------------------------------------------------------------------------
#
# The website is built elsewhere and pulls the three shared documents from this
# repository at build time, so an edit here does not reach the published page
# until that build runs again. The workflow closes that gap. Two properties are
# worth pinning rather than remembering:
#
# The hook URL is a bare POST with no authentication beyond being unguessable —
# anyone holding it can trigger unlimited builds — so it lives in a secret and
# must never be interpolated anywhere it would be echoed. `curl` is given the
# URL through an environment variable rather than on the command line, because
# `set -x`, a non-zero exit or a future `echo` would otherwise put it in a log
# that is public on this repository.
#
# And it must never run on `pull_request`; see _NOT_TEST_SUITES above.

_SITE_WORKFLOW = "publish-site.yml"


def test_the_site_rebuild_never_runs_from_an_unmerged_branch():
    assert _SITE_WORKFLOW in _workflow_names(), (
        f".github/workflows/{_SITE_WORKFLOW} is missing; it is what keeps the "
        "published privacy policy from drifting behind docs/privacy.md."
    )
    text = "\n".join(_workflow_lines(_SITE_WORKFLOW))
    assert not re.search(r"^\s+pull_request:", text, re.MULTILINE), (
        f".github/workflows/{_SITE_WORKFLOW} runs on pull_request, which would "
        "publish the privacy policy and account-deletion pages from a branch "
        "that has not merged."
    )
    match = _PUSH_BRANCHES_RE.search(text)
    assert match, f".github/workflows/{_SITE_WORKFLOW} has no `push:` branches list"
    branches = [b.strip().strip("\"'") for b in match.group(1).split(",") if b.strip()]
    assert branches == ["main"], (
        f".github/workflows/{_SITE_WORKFLOW} pushes run on {branches}; only main "
        "has merged content."
    )


def test_the_site_rebuild_keeps_its_hook_out_of_the_logs():
    lines = _workflow_lines(_SITE_WORKFLOW)
    text = "\n".join(lines)
    assert "secrets." in text, (
        f".github/workflows/{_SITE_WORKFLOW} names no secret; the hook URL must "
        "not be written into the file."
    )
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#") or "secrets." not in stripped:
            continue
        assert re.match(r"^[A-Z_]+:\s*\$\{\{\s*secrets\.[A-Z_]+\s*\}\}$", stripped), (
            f".github/workflows/{_SITE_WORKFLOW} interpolates a secret outside an "
            f"`env:` assignment: {stripped!r}. Pass it to curl through the "
            "environment — a secret on a command line reaches the log on `set -x` "
            "or a non-zero exit, and this repository's logs are public."
        )
    assert not re.search(r"curl[^\n]*\$\{\{", text), (
        f".github/workflows/{_SITE_WORKFLOW} interpolates a secret directly into "
        "the curl command line; read it from the environment instead."
    )


@pytest.mark.parametrize(
    "name", [n for n in _workflow_names() if n not in _NOT_TEST_SUITES]
)
def test_test_suites_run_on_pull_request_and_only_main_pushes(name):
    text = "\n".join(_workflow_lines(name))
    assert re.search(r"^\s+pull_request:", text, re.MULTILINE), (
        f".github/workflows/{name} must run on pull_request; that run is what the "
        "main ruleset's required checks read."
    )
    match = _PUSH_BRANCHES_RE.search(text)
    assert match, (
        f".github/workflows/{name} has no `push:` trigger with a `branches:` list; "
        "use `push:\n  branches: [main]` so a PR push runs each suite once."
    )
    branches = [b.strip().strip("\"'") for b in match.group(1).split(",") if b.strip()]
    assert branches == ["main"], (
        f".github/workflows/{name} pushes run on {branches}; restrict to `[main]` — "
        "the pull_request event already covers every PR branch, and running both "
        "doubles runner load for no extra signal."
    )


# `uses: owner/repo@ref` — the ref is what we care about. Local (`./…`) and
# docker (`docker://…`) references have no tag to pin and are exempt.
_USES_RE = re.compile(r"^\s*-?\s*uses:\s*([^\s#]+)")
_SHA_PINNED_RE = re.compile(r"@[0-9a-f]{40}$")


@pytest.mark.parametrize("name", _workflow_names())
def test_every_action_reference_is_pinned_to_a_commit_sha(name):
    unpinned = []
    for lineno, line in enumerate(_workflow_lines(name), 1):
        match = _USES_RE.match(line)
        if not match:
            continue
        ref = match.group(1).strip("\"'")
        if ref.startswith(("./", "docker://")):
            continue
        if not _SHA_PINNED_RE.search(ref):
            unpinned.append(f"line {lineno}: {ref}")
    assert not unpinned, (
        f".github/workflows/{name} references actions by a moving tag: {unpinned}. "
        "A tag can be repointed by its owner at any time and CI would run the new "
        "code with no diff here. Pin the 40-character commit SHA and put the "
        "version in a trailing comment (Dependabot's github-actions ecosystem "
        "keeps the pin current)."
    )


# ---------------------------------------------------------------------------
# #189 — Dependabot
# ---------------------------------------------------------------------------

_DEPENDABOT_REL = ".github/dependabot.yml"

# Ecosystem -> the directory it must watch. Four manifests, four ecosystems:
# missing one means that half of the tree silently stops getting security PRs.
_EXPECTED_ECOSYSTEMS = {
    "pip": "/server",
    "npm": "/web",
    "gradle": "/android",
    "github-actions": "/",
}


def test_dependabot_config_exists():
    path = os.path.join(_REPO_ROOT, *_DEPENDABOT_REL.split("/"))
    assert os.path.isfile(path), (
        f"{_DEPENDABOT_REL} is missing. Without it nothing tells the maintainer "
        "that a shipped dependency has a published advisory."
    )


@pytest.mark.parametrize("ecosystem,directory", sorted(_EXPECTED_ECOSYSTEMS.items()))
def test_dependabot_watches_every_ecosystem(ecosystem, directory):
    text = _read(_DEPENDABOT_REL)
    assert re.search(rf'package-ecosystem:\s*"?{re.escape(ecosystem)}"?', text), (
        f"{_DEPENDABOT_REL} declares no `{ecosystem}` update block"
    )
    assert re.search(rf'directory:\s*"?{re.escape(directory)}"?', text), (
        f"{_DEPENDABOT_REL} has no update block watching `{directory}` "
        f"(expected for {ecosystem})"
    )


def test_dependabot_runs_weekly_and_groups_minor_and_patch():
    """Ungrouped daily PRs are the reason people turn Dependabot off."""
    text = _read(_DEPENDABOT_REL)
    assert re.search(r'interval:\s*"?weekly"?', text), (
        f"{_DEPENDABOT_REL} must schedule updates weekly"
    )
    assert "groups:" in text, f"{_DEPENDABOT_REL} must group updates"
    assert '"minor"' in text and '"patch"' in text, (
        f"{_DEPENDABOT_REL} must group minor and patch update-types so a routine "
        "week is one PR per ecosystem, not twenty"
    )


# ---------------------------------------------------------------------------
# #189 — secret scanning
# ---------------------------------------------------------------------------


def _gitleaks_workflow() -> tuple[str, str]:
    """(name, text) of the workflow that runs gitleaks. Fails if there is none."""
    for name in _workflow_names():
        text = "\n".join(_workflow_lines(name))
        if "gitleaks" in text:
            return name, text
    pytest.fail(
        "No workflow runs gitleaks. A public repo needs secret scanning in CI: "
        "one pushed credential is permanent, because deleting it in the next "
        "commit leaves it in the history."
    )


def test_a_workflow_runs_gitleaks_on_push_and_pull_request():
    name, text = _gitleaks_workflow()
    assert re.search(r"^on:", text, re.MULTILINE), f"{name} declares no triggers"
    assert re.search(r"^\s+push:", text, re.MULTILINE), (
        f"{name} must run on push — branch pushes are how a secret usually lands"
    )
    assert re.search(r"^\s+pull_request:", text, re.MULTILINE), (
        f"{name} must run on pull_request"
    )


def test_gitleaks_checks_out_the_full_history():
    """`fetch-depth: 0`: a shallow clone hides the commit that added the secret."""
    name, text = _gitleaks_workflow()
    assert "fetch-depth: 0" in text, (
        f"{name} must check out with `fetch-depth: 0`; gitleaks scans history, "
        "and the default shallow clone gives it one commit to look at"
    )


# ---------------------------------------------------------------------------
# #190 — public-repo scaffolding
# ---------------------------------------------------------------------------

_PUBLIC_REPO_FILES = [
    "LICENSE",
    "SECURITY.md",
    "CONTRIBUTING.md",
    ".github/ISSUE_TEMPLATE/bug_report.yml",
    ".github/ISSUE_TEMPLATE/feature_request.yml",
    ".github/ISSUE_TEMPLATE/config.yml",
]


@pytest.mark.parametrize("rel_path", _PUBLIC_REPO_FILES)
def test_public_repo_files_exist_and_are_not_empty(rel_path):
    path = os.path.join(_REPO_ROOT, *rel_path.split("/"))
    assert os.path.isfile(path), f"{rel_path} is missing"
    assert os.path.getsize(path) > 0, f"{rel_path} is empty"


def test_a_pull_request_template_exists():
    """GitHub accepts either casing; the repo has historically used the shouty one.

    Asserted case-insensitively on purpose: adding the second spelling would
    collide with the first on a case-insensitive filesystem (Windows, macOS)
    while looking like two files to git.
    """
    github_dir = os.path.join(_REPO_ROOT, ".github")
    names = {name.lower() for name in os.listdir(github_dir)}
    assert "pull_request_template.md" in names, (
        ".github/PULL_REQUEST_TEMPLATE.md (either casing) is missing"
    )


_CONFIG_REL = ".github/ISSUE_TEMPLATE/config.yml"


def test_issue_template_config_disables_blank_issues():
    """Blank issues bypass the templates, including the 'not for security' routing."""
    text = _read(_CONFIG_REL)
    assert re.search(r"^blank_issues_enabled:\s*false\s*$", text, re.MULTILINE), (
        f"{_CONFIG_REL} must set `blank_issues_enabled: false`"
    )


def test_issue_template_config_routes_security_reports_privately():
    text = _read(_CONFIG_REL)
    assert "contact_links:" in text, f"{_CONFIG_REL} declares no contact_links"
    urls = re.findall(r"url:\s*(\S+)", text)
    assert any(url.rstrip("\"'").endswith("/security/advisories/new") for url in urls), (
        f"{_CONFIG_REL} must offer a contact link ending in "
        "`/security/advisories/new`, so a vulnerability report goes to a private "
        f"advisory instead of a public issue. Found: {urls}"
    )


def test_security_policy_points_at_private_vulnerability_reporting():
    text = _read("SECURITY.md")
    assert "/security/advisories/new" in text, (
        "SECURITY.md must link the private advisory form. There is no security "
        "email address for this project, so that link is the whole channel."
    )


def test_contributing_documents_the_branch_protection_settings():
    """Branch protection cannot be committed, so the settings live in prose.

    They are only applicable once the repo is public (the API 403s on a private
    free repo), which is exactly when nobody will remember what they were.
    """
    text = _read("CONTRIBUTING.md")
    assert "Branch protection (maintainer)" in text, (
        "CONTRIBUTING.md must carry a 'Branch protection (maintainer)' section"
    )
    assert "gh api" in text, (
        "the branch-protection section must include the runnable `gh api` command"
    )


def test_contributing_names_every_test_suite():
    """A contributor should not have to read CLAUDE.md to find the suites."""
    text = _read("CONTRIBUTING.md")
    for needle in ("setup-testenv.sh", "pytest", "vitest", "gradlew"):
        assert needle in text, f"CONTRIBUTING.md never mentions `{needle}`"


# ---------------------------------------------------------------------------
# #147 — container images for the public demo stack
# ---------------------------------------------------------------------------
#
# The demo server exists because a Play reviewer who cannot get past the login
# screen is an "app not functional" rejection. It is kept current by Watchtower
# pulling images this workflow publishes, so if publishing silently stops, the
# demo drifts from `main` and nobody finds out until a reviewer meets a stale
# build. These pin the parts that would fail quietly.

_PUBLISH_WORKFLOW = "publish-images.yml"


def _publish_workflow_text() -> str:
    path = os.path.join(_WORKFLOW_DIR, _PUBLISH_WORKFLOW)
    assert os.path.isfile(path), (
        f".github/workflows/{_PUBLISH_WORKFLOW} is missing. The demo stack (#147) "
        "updates itself by pulling images built from main; without this workflow "
        "there is nothing to pull and the demo silently pins to whatever was "
        "deployed by hand."
    )
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def test_the_publish_workflow_builds_both_deployed_images():
    """A demo missing either half is not a demo: no API, or no web UI."""
    text = _publish_workflow_text()
    for image in ("server", "web"):
        assert re.search(rf"^\s+.*\b{image}\b", text, re.MULTILINE), (
            f"{_PUBLISH_WORKFLOW} does not appear to build the `{image}` image. "
            "The demo stack runs both; publishing one leaves the other pinned to "
            "whatever the host last built by hand."
        )


def test_the_publish_workflow_runs_on_a_schedule_and_on_demand():
    """
    Watchtower pulls on its own timer, so the images have to be rebuilt on one
    too — a push-only trigger means a quiet week leaves the demo on a stale base
    image with whatever CVEs it accumulated. `workflow_dispatch` is what makes a
    fix reachable without waiting for the next tick.
    """
    text = _publish_workflow_text()
    assert re.search(r"^\s+schedule:", text, re.MULTILINE), (
        f"{_PUBLISH_WORKFLOW} has no `schedule:` trigger; the demo would only "
        "rebuild when someone happens to push to main."
    )
    assert re.search(r"^\s+workflow_dispatch:", text, re.MULTILINE), (
        f"{_PUBLISH_WORKFLOW} has no `workflow_dispatch:`; there would be no way "
        "to publish a fix without waiting for the schedule."
    )


def test_the_publish_workflow_asks_for_package_write_only_on_the_job():
    """
    Top-level `permissions: contents: read` is the repo-wide rule (#189). Pushing
    to GHCR needs `packages: write`, and that widening belongs on the one job
    that pushes — a token that can write packages for the length of the whole
    workflow is a wider blast radius than the task needs.
    """
    text = _publish_workflow_text()
    assert "packages: write" in text, (
        f"{_PUBLISH_WORKFLOW} never requests `packages: write`, so the push to "
        "GHCR will be denied."
    )
    top_level = text.split("jobs:")[0]
    assert "packages: write" not in top_level, (
        f"{_PUBLISH_WORKFLOW} grants `packages: write` at the top level. Declare "
        "it on the publishing job instead, so every other job in the file keeps "
        "a read-only token."
    )
