"""
Docs index contract (issues #239, #241 — pre-publication review).

`docs/` had no index of its own and the root README kept a hand-maintained
table listing 8 of 10 files, which is how a merged-branch handoff note and five
shipped implementation plans under `docs/superpowers/` sat in the tree with
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
# Issue #183: CLAUDE.md documented env vars that do not exist.
#
# `REMOTE_TRANSCRIPTION_URL`, `EBOOKS_PATH`, `AUDIOBOOKS_PATH` and
# `APP_DATA_PATH` were all read by nothing. `Settings` is configured with
# `extra = "ignore"`, so setting one is not an error — it is silently discarded,
# and the operator gets the default with no clue why. Every agent session and
# most contributors read this file first, so a wrong name here propagates.
# ---------------------------------------------------------------------------

_CONFIG_SECTION_HEADING = "### Key Config Variables"
_ENV_TOKEN_RE = re.compile(r"`([A-Z][A-Z0-9_]{2,})`")


def _claude_md_config_section() -> str:
    with open(os.path.join(_REPO_ROOT, "CLAUDE.md"), encoding="utf-8") as fh:
        text = fh.read()
    start = text.find(_CONFIG_SECTION_HEADING)
    assert start != -1, (
        f"CLAUDE.md no longer has a '{_CONFIG_SECTION_HEADING}' section. If it "
        "moved, point this test at the new heading; if the names moved into "
        "README.md, this test should follow them there."
    )
    rest = text[start + len(_CONFIG_SECTION_HEADING):]
    end = rest.find("\n#")
    return rest if end == -1 else rest[:end]


def _settings_env_names() -> set[str]:
    """Every name `Settings` will actually read: aliases, plus field names."""
    from config import Settings

    names = set()
    for field_name, field in Settings.model_fields.items():
        names.add(field_name.upper())
        if field.alias:
            names.add(field.alias.upper())
    return names


def test_claude_md_config_names_are_real_settings_aliases():
    documented = set(_ENV_TOKEN_RE.findall(_claude_md_config_section()))
    assert documented, "no env var names found in the CLAUDE.md config section"
    unknown = sorted(documented - _settings_env_names())
    assert not unknown, (
        f"CLAUDE.md documents env vars that `Settings` does not read: {unknown}. "
        "config.py uses `extra = \"ignore\"`, so setting one of these does "
        "nothing at all and reports nothing. Use the `alias=` value from "
        "server/config.py."
    )


# ---------------------------------------------------------------------------
# Issue #240: docs/testing.md quoted coverage numbers that had drifted from the
# files that enforce them.
#
# The Android floor was documented as 7% while the gradle file enforced 29 — a
# contributor bumping the ratchet after a PR would have set the next floor far
# below where it already was. It drifted again before this test existed (40 in
# the doc, 45 in gradle). Prose that restates a number in a config file will
# always drift; the fix is to make the drift fail the build.
#
# Each test below reads the enforcing file, not a constant, so the doc has to
# follow the gate rather than the other way round.
# ---------------------------------------------------------------------------

_TESTING_DOC = os.path.join(_REPO_ROOT, "docs", "testing.md")
_WORKFLOW = os.path.join(_REPO_ROOT, ".github", "workflows", "tests.yml")
_ANDROID_GRADLE = os.path.join(_REPO_ROOT, "android", "app", "build.gradle.kts")
_VITE_CONFIG = os.path.join(_REPO_ROOT, "web", "vite.config.js")


def _read(path: str) -> str:
    assert os.path.isfile(path), f"{path} is missing — repoint this test or restore the file"
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _testing_doc() -> str:
    return _read(_TESTING_DOC)


def test_testing_doc_quotes_current_android_floor():
    """The Kover `minValue` and the "currently **N% lines**" in the doc agree."""
    gradle = _read(_ANDROID_GRADLE)
    match = re.search(r"minValue\s*=\s*(\d+)", gradle)
    assert match, (
        "no `minValue = N` in android/app/build.gradle.kts. If the Kover verify "
        "block moved or was renamed, point this test at it."
    )
    floor = match.group(1)

    quoted = re.findall(r"\*\*(\d+)% lines\*\*", _testing_doc())
    assert quoted, (
        "docs/testing.md no longer states the Android floor as `**N% lines**`. "
        "Keep that spelling or update this test with the new one."
    )
    assert floor in quoted, (
        f"android/app/build.gradle.kts enforces a {floor}% line floor, but "
        f"docs/testing.md quotes {quoted}. Update the Android 'Coverage floor' "
        "section — a contributor ratcheting from the documented number sets the "
        "next floor below where it already is, which is how this was found."
    )


def test_testing_doc_quotes_current_web_thresholds():
    """All four vitest thresholds, not just lines."""
    config = _read(_VITE_CONFIG)
    thresholds = {}
    for metric in ("lines", "statements", "functions", "branches"):
        match = re.search(rf"\b{metric}\s*:\s*(\d+)\s*,", config)
        assert match, f"no `{metric}: N` threshold found in web/vite.config.js"
        thresholds[metric] = match.group(1)

    doc = _testing_doc()
    for metric, value in thresholds.items():
        assert f"{value}% {metric}" in doc, (
            f"web/vite.config.js enforces {value}% {metric}, which docs/testing.md "
            f"does not quote. The doc states the four thresholds as "
            f"'N% lines / N% statements / N% functions / N% branches'."
        )


def test_testing_doc_quotes_the_server_coverage_floor():
    workflow = _read(_WORKFLOW)
    match = re.search(r"--cov-fail-under=(\d+)", workflow)
    assert match, "no `--cov-fail-under=N` in .github/workflows/tests.yml"
    floor = match.group(1)
    assert f"**{floor}%**" in _testing_doc(), (
        f"CI fails the build under {floor}% total coverage; docs/testing.md does "
        f"not quote that number as `**{floor}%**`."
    )


# --- the diff-cover exclude list ------------------------------------------

_DOC_EXCLUDE_HEADING = "### diff-cover exclude list"


def _exclude_key(entry: str) -> str:
    """Canonical form for one exclude glob, comparable across the two spellings.

    The workflow writes `*/routers/files.py` (fnmatch against a full path) and
    the doc writes `server/routers/files.py`, so neither prefix is common. The
    trailing filename is, except for directory globs, where it is the directory
    plus the `*`.
    """
    parts = [p for p in entry.strip().split("/") if p]
    assert parts, f"empty exclude entry {entry!r}"
    if parts[-1] == "*" and len(parts) >= 2:
        return "/".join(parts[-2:])
    return parts[-1]


def _workflow_exclude_globs() -> set:
    workflow = _read(_WORKFLOW)
    # `--exclude` followed by a quote: the step's own comment mentions the flag
    # by name a few lines earlier, and that prose contains quoted `'*'`.
    flag = re.search(r"--exclude\s+'", workflow)
    assert flag, "no `--exclude '…'` in the diff-cover step of tests.yml"
    start = flag.start()
    # The globs run to the `|| diff_exit=$?` that ends the diff-cover command.
    end = workflow.find("|| diff_exit", start)
    assert end != -1, "could not find the end of the diff-cover command"
    return {
        _exclude_key(g) for g in re.findall(r"'([^']+)'", workflow[start:end])
    }


def _doc_exclude_globs() -> set:
    doc = _testing_doc()
    start = doc.find(_DOC_EXCLUDE_HEADING)
    assert start != -1, (
        f"docs/testing.md no longer has a '{_DOC_EXCLUDE_HEADING}' section."
    )
    fence = doc.find("```", start)
    assert fence != -1, "no fenced list under the diff-cover exclude heading"
    body_start = doc.find("\n", fence) + 1
    body_end = doc.find("```", body_start)
    entries = set()
    for line in doc[body_start:body_end].splitlines():
        # Trailing `# why this is excluded` comments are documentation, not glob.
        line = line.split("#", 1)[0].strip()
        if line:
            entries.add(_exclude_key(line))
    return entries


def test_diff_cover_exclude_list_matches_workflow():
    """The doc says "keep this list in sync"; this is what keeps it.

    Both directions matter. A module dropped from the workflow but left in the
    doc reads as ungated when it is gated; the reverse silently exempts a module
    from the 80% patch bar with nothing written down about why.
    """
    workflow = _workflow_exclude_globs()
    doc = _doc_exclude_globs()
    assert workflow, "parsed no globs out of the workflow — the step's shape changed"
    assert doc == workflow, (
        f"diff-cover exclude list drift.\n"
        f"  only in docs/testing.md: {sorted(doc - workflow)}\n"
        f"  only in .github/workflows/tests.yml: {sorted(workflow - doc)}"
    )


def test_ci_installs_requirements_without_filtering_them():
    """Issue #240: CI filtered `torch|openai-whisper` out of requirements.txt long
    after they moved to requirements-local.txt, while the doc called the filter a
    no-op. One of the two had to go; the filter did. If the heavy stack ever moves
    back into requirements.txt, this test is the reminder that the doc, the
    workflow and the split all have to move together."""
    assert "grep -viE" not in _read(_WORKFLOW), (
        "tests.yml is filtering requirements.txt again. torch and openai-whisper "
        "live in requirements-local.txt and are not installed by default, so the "
        "filter matches nothing — see docs/testing.md."
    )
