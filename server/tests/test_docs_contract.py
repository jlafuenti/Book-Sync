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
# Issue #182: the README's environment tables are the only place most operators
# look, and they had drifted from `server/config.py` in both directions — names
# the server does not read, and settings it does read that nothing documented.
# Same repo-contract shape as
# `test_compose_contract.py::test_server_service_env_vars_are_settings_aliases`,
# applied to the README instead of the compose template.
# ---------------------------------------------------------------------------

# A backticked, SCREAMING_SNAKE token — how every env var is written in the
# first cell of the README's tables.
_ENV_NAME_RE = re.compile(r"`([A-Z][A-Z0-9_]{2,})`")

# README-documented names that are deliberately NOT fields on config.Settings.
# One line of reason each; anything else is a typo or a stale row.
_README_ENV_NOT_SETTINGS = {
    "PUID": (
        "compose interpolation only: fills the `user:` key of the server and web "
        "services (issue #180); the server process never reads it"
    ),
    "PGID": "compose interpolation only, the group half of PUID (issue #180)",
}

# Settings fields the README may leave undocumented, with the reason. Empty on
# purpose: everything config.Settings reads is settable by an operator, so
# everything belongs in a table. Adding an entry here is a decision to hide a
# knob, not a way to skip writing a row.
_UNDOCUMENTED_SETTINGS: dict[str, str] = {}


def _readme_text() -> str:
    with open(os.path.join(_REPO_ROOT, "README.md"), encoding="utf-8") as fh:
        return fh.read()


def _readme_table_env_names() -> set[str]:
    """Env var names in the first cell of any markdown table row in README.md.

    Only the first cell, so a variable merely *mentioned* in a description does
    not count as documented — a row of its own is the point.
    """
    names: set[str] = set()
    for line in _readme_text().splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        first_cell = stripped.strip("|").split("|")[0]
        names.update(_ENV_NAME_RE.findall(first_cell))
    return names


def _settings_env_names() -> set[str]:
    """Every env name config.Settings reads.

    pydantic-settings resolves a field from its alias when it has one and from
    the (case-insensitive) field name when it does not, so `jwt_algorithm` is
    genuinely settable as `JWT_ALGORITHM` even with no alias declared.
    """
    from config import SECRET_FILE_ENV_VARS, SECRET_FILE_SUFFIX, Settings

    names = {
        (field.alias or name).upper()
        for name, field in Settings.model_fields.items()
    }
    # The `<NAME>_FILE` variants are read by config.SecretFileSettingsSource
    # rather than declared as fields (issue #180), and they are the *preferred*
    # way to pass a secret — so they need rows of their own just as much.
    names |= {name + SECRET_FILE_SUFFIX for name in SECRET_FILE_ENV_VARS}
    return names


def test_readme_env_parser_finds_the_tables():
    """Guard the parser: a silently-empty parse makes both tests below vacuous."""
    names = _readme_table_env_names()
    assert {"JWT_SECRET_KEY", "CORS_ORIGINS", "BACKUPS_DIR"} <= names, (
        f"README env-table parser found {sorted(names)} — it is not reading the "
        "environment tables any more. Fix the parser or the table format."
    )


def test_readme_documents_only_variables_the_server_reads():
    """A name in the table that is not a setting is a lie the operator acts on."""
    unknown = sorted(
        _readme_table_env_names()
        - _settings_env_names()
        - set(_README_ENV_NOT_SETTINGS)
    )
    assert not unknown, (
        f"README.md documents {unknown}, but they are neither aliases nor field "
        "names on config.Settings — the server would ignore them. Fix the name, "
        "drop the row, or add it to _README_ENV_NOT_SETTINGS with a reason."
    )


def test_readme_documents_every_setting_the_server_reads():
    """The reverse: an undocumented setting is a knob nobody can find."""
    missing = sorted(
        _settings_env_names()
        - _readme_table_env_names()
        - set(_UNDOCUMENTED_SETTINGS)
    )
    assert not missing, (
        f"config.Settings reads {missing}, but README.md has no table row for "
        "them. Add a row (name in backticks in the first cell), or delete the "
        "setting if nothing reads it."
    )


# ---------------------------------------------------------------------------
# Issue #186: no changelog, and the release version is written out by hand in
# three files. Single-sourcing it would mean a build step in each of three
# toolchains; pinning the three literals equal costs one test and fails on the
# first unsynchronised bump, which is the whole risk.
# ---------------------------------------------------------------------------

_CHANGELOG_REL = "CHANGELOG.md"
_VERSION_NAME_RE = re.compile(r'versionName\s*=\s*"([^"]+)"')


def _changelog_text() -> str:
    path = os.path.join(_REPO_ROOT, _CHANGELOG_REL)
    assert os.path.isfile(path), (
        "CHANGELOG.md is missing from the repo root. It is what a bug reporter "
        "and a self-hoster read to find out what changed between two versions — "
        "see docs/releasing.md."
    )
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _app_version() -> str:
    from version import APP_VERSION

    return APP_VERSION


def _web_version() -> str:
    import json

    with open(
        os.path.join(_REPO_ROOT, "web", "package.json"), encoding="utf-8"
    ) as fh:
        return json.load(fh)["version"]


def _android_version_name() -> str:
    path = os.path.join(_REPO_ROOT, "android", "app", "build.gradle.kts")
    with open(path, encoding="utf-8") as fh:
        match = _VERSION_NAME_RE.search(fh.read())
    assert match, f'no `versionName = "..."` found in {path}'
    return match.group(1)


def test_the_three_version_strings_agree():
    """server/version.py, web/package.json and build.gradle.kts must match.

    They are bumped by hand, in that order, per docs/releasing.md. Nothing
    derives one from another, so this test *is* the wiring: it fails the moment
    a release bumps two of the three, which is how a server would otherwise end
    up reporting a version no client build ever had.
    """
    versions = {
        "server/version.py APP_VERSION": _app_version(),
        "web/package.json version": _web_version(),
        "android/app/build.gradle.kts versionName": _android_version_name(),
    }
    assert len(set(versions.values())) == 1, (
        f"Release version strings disagree: {versions}. Bump all three (see "
        "docs/releasing.md, 'Bump the version') — they are one release number, "
        "written out three times because no build step shares them."
    )


def test_changelog_has_an_unreleased_section():
    """Keep a Changelog's `## [Unreleased]` is where work lands between tags."""
    assert "## [Unreleased]" in _changelog_text(), (
        "CHANGELOG.md has no `## [Unreleased]` section. Merged work is recorded "
        "there and renamed to the version heading at release time."
    )


def test_changelog_records_the_current_version():
    """The version the server advertises must have a changelog entry."""
    version = _app_version()
    assert f"## [{version}]" in _changelog_text(), (
        f"CHANGELOG.md has no `## [{version}]` heading, but that is the version "
        "server/version.py advertises. Rename the Unreleased section when you "
        "cut the release (docs/releasing.md)."
    )


# ---------------------------------------------------------------------------
# Issue #185: the README gained a screenshots section. The images themselves are
# pending, so this passes vacuously today and starts biting the moment someone
# embeds one — a broken image on the front page of a public repo.
# ---------------------------------------------------------------------------

_IMAGE_EMBED_RE = re.compile(r"!\[[^\]]*\]\(([^)\s]+)\)")


def test_readme_image_embeds_resolve():
    broken = [
        target
        for target in _IMAGE_EMBED_RE.findall(_readme_text())
        if not target.startswith(("http://", "https://"))
        and not os.path.exists(os.path.join(_REPO_ROOT, *target.split("/")))
    ]
    assert not broken, (
        f"README.md embeds images that do not exist: {broken}. Commit the file "
        "under docs/images/ (the .gitignore `*.png` rule has an exception for "
        "that folder) or describe the screenshot instead of linking it."
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
        "policy URL for every submission (issue #150), and the public site (#418) "
        "publishes this very file."
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
    keeping a second copy: private-range IPs and `*.example.com` are exactly as
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


# ---------------------------------------------------------------------------
# Issue #180: the non-root switch is the one change in this repo that can stop
# an *existing* install from booting — the server creates `logs/` under the
# app-data mount at import time, so a mount still owned by root is a fatal
# PermissionError before anything else runs. The runbook for that is the whole
# mitigation, and README.md links straight at its anchor.
# ---------------------------------------------------------------------------

_OPERATIONS_DOC = os.path.join(_REPO_ROOT, "docs", "operations.md")
_NON_ROOT_HEADING = "## Running as a non-root user"


def test_operations_doc_has_the_non_root_runbook():
    text = _read(_OPERATIONS_DOC)
    assert _NON_ROOT_HEADING in text, (
        "docs/operations.md lost its non-root section, and README.md's "
        "#running-as-a-non-root-user link now points at nothing."
    )
    section = text.split(_NON_ROOT_HEADING, 1)[1].split("\n## ", 1)[0]
    for needle in ("PUID", "PGID", "chown", "jetson", "roll back"):
        assert needle.lower() in section.lower(), (
            f"docs/operations.md's non-root runbook never mentions {needle!r}."
        )


def test_readme_links_at_the_non_root_runbook():
    """The env table's PUID/PGID row is where an operator meets this first."""
    readme = _read(os.path.join(_REPO_ROOT, "README.md"))
    assert "PUID" in readme and "PGID" in readme, (
        "README.md's environment table does not document PUID/PGID."
    )
    assert "operations.md#running-as-a-non-root-user" in readme, (
        "README.md does not link at the non-root runbook, so the one-time "
        "ownership change an existing install needs is undiscoverable from it."
    )


# ---------------------------------------------------------------------------
# Issue #180, second half: the secrets runbook.
#
# Moving JWT_SECRET_KEY, CREDENTIAL_ENC_KEYS and the Postgres password out of
# `environment:` and into files is a change an operator performs on a live
# install, and the two ways it goes wrong are both silent-looking: a changed
# CREDENTIAL_ENC_KEYS means every stored import-source credential stops
# decrypting, and a changed JWT_SECRET_KEY signs every existing session out.
# The migration therefore has to say, in the doc, that the values are copied
# rather than regenerated — and it has to have a rollback.
# ---------------------------------------------------------------------------

_SECRETS_HEADING = "## Secrets"


def test_operations_doc_has_the_secrets_runbook():
    text = _read(_OPERATIONS_DOC)
    assert _SECRETS_HEADING in text, (
        "docs/operations.md has no '## Secrets' section, and README.md's "
        "#secrets link now points at nothing."
    )
    section = text.split(_SECRETS_HEADING, 1)[1].split("\n## ", 1)[0]
    for needle in (
        "umask 077",
        "/run/secrets/",
        "_FILE",
        "docker inspect",
        "roll back",
    ):
        assert needle.lower() in section.lower(), (
            f"docs/operations.md's secrets runbook never mentions {needle!r}."
        )
    for phrase in ("same value", "re-encrypt"):
        assert phrase.lower() in section.lower(), (
            "docs/operations.md's secrets runbook does not say that migrating "
            "an existing install copies the existing values rather than "
            f"generating new ones (looked for {phrase!r}). A regenerated "
            "CREDENTIAL_ENC_KEYS silently orphans every stored credential."
        )


def test_readme_links_at_the_secrets_runbook():
    readme = _read(os.path.join(_REPO_ROOT, "README.md"))
    assert "operations.md#secrets" in readme, (
        "README.md does not link at the secrets runbook, so an operator "
        "meeting `JWT_SECRET_KEY_FILE` in the env table has nowhere to go."
    )


# ---------------------------------------------------------------------------
# Issue #178: the internet-facing proxy — TLS, security headers, CSP — is
# documented in one place and shipped as Caddyfile.example. The section is
# what an operator follows before exposing the stack, and README.md's
# reverse-proxy paragraph links straight at its anchor.
# ---------------------------------------------------------------------------

_EDGE_PROXY_HEADING = "## Edge proxy"


def test_operations_doc_has_the_edge_proxy_section():
    text = _read(_OPERATIONS_DOC)
    assert _EDGE_PROXY_HEADING in text, (
        "docs/operations.md lost its edge-proxy section, and README.md's "
        "#edge-proxy link now points at nothing."
    )
    section = text.split(_EDGE_PROXY_HEADING, 1)[1].split("\n## ", 1)[0]
    for needle in (
        "Caddyfile.example",
        "Strict-Transport-Security",
        "Report-Only",
        "frame-ancestors",
        "curl",
        "/api/health",
        "server_tokens",
        "sha256",
    ):
        assert needle in section, (
            f"docs/operations.md's edge-proxy section never mentions {needle!r}."
        )


def test_readme_links_at_the_edge_proxy_section():
    assert "docs/operations.md#edge-proxy" in _read(os.path.join(_REPO_ROOT, "README.md")), (
        "README.md's reverse-proxy paragraph does not link at "
        "docs/operations.md#edge-proxy."
    )


# ---------------------------------------------------------------------------
# #418 — the public website
# ---------------------------------------------------------------------------
#
# The Play Console will not accept a submission without a reachable privacy
# policy and account-deletion URL. Those pages are published from this
# repository's Markdown, so the text the tests below guard is the text that
# reaches the public page.


def test_no_doc_still_claims_the_policy_is_published_via_github_pages():
    """
    GitHub Pages was never set up; #418 chose Cloudflare Pages. A doc that names
    the wrong publisher sends the next person looking for a setting that does not
    exist — and, worse, implies the policy is already reachable when it is not.
    """
    offenders = []
    for name in sorted(os.listdir(os.path.join(_REPO_ROOT, "docs"))):
        if not name.endswith(".md"):
            continue
        with open(os.path.join(_REPO_ROOT, "docs", name), encoding="utf-8") as fh:
            if re.search(r"github pages", fh.read(), re.IGNORECASE):
                offenders.append(f"docs/{name}")
    assert not offenders, f"these still claim GitHub Pages publishing: {offenders}"


def test_the_privacy_policy_gives_a_real_contact():
    """
    Play requires a contact on the privacy policy. The file shipped with a
    placeholder, which would have been published verbatim.
    """
    text = _privacy_text()
    assert "to be filled in" not in text.lower(), (
        "docs/privacy.md still carries the placeholder contact address."
    )
    assert re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", text), (
        "docs/privacy.md names no contact address at all."
    )
