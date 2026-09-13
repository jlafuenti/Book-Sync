"""Fail a pull request that changes what an operator deploys without saying so.

A PR that touches `server/` or `web/` outside their tests must add a line under
`## [Unreleased]` in the root `CHANGELOG.md`. Releases are built from that
section (docs/releasing.md), and the System page's update check points
operators at those release notes, so a change that skips it is a change an
operator upgrades into blind.

Deliberately *not* checked: the version number. It moves once, when a release
is cut — never per PR.

Usage (from the workflow):

    git diff --name-only BASE...HEAD | python .github/scripts/changelog_gate.py --labels "a,b"

Tested by server/tests/test_changelog_gate.py.
"""

from __future__ import annotations

import argparse
import sys

# The trees an operator deploys. Android ships through Play with its own
# "What's new" text and is out of scope; so are docs, CI and the Jetson worker.
_DEPLOYED_PREFIXES = ("server/", "web/")

# Changes confined to tests alter nothing that runs in production. Demanding a
# changelog line for them would teach people to apply the skip label by reflex.
_TEST_PREFIXES = ("server/tests/", "web/src/test/")
_TEST_SUFFIXES = (".test.js", ".test.jsx", ".test.ts", ".test.tsx")

SKIP_LABEL = "skip-changelog"
CHANGELOG = "CHANGELOG.md"

# Bots that open PRs against deployable files and cannot write a changelog line.
# Found by running this gate over real history before it was made required: nine
# Dependabot bumps to requirements/package files would each have stalled until a
# human intervened — for security updates, exactly the delay Dependabot removes.
# Dependency updates are summarised in the release notes when a release is cut.
# The exact identity only: `dependabot` alone is an ordinary account name.
EXEMPT_AUTHORS = frozenset({"dependabot[bot]"})


def _is_deployable(path: str) -> bool:
    path = path.strip()
    if not path.startswith(_DEPLOYED_PREFIXES):
        return False
    if path.startswith(_TEST_PREFIXES) or path.endswith(_TEST_SUFFIXES):
        return False
    return True


def changelog_required(changed: list[str]) -> bool:
    """Whether any changed file is something an operator deploys."""
    return any(_is_deployable(p) for p in changed)


def passes(changed: list[str], labels: list[str], author: str | None = None) -> bool:
    """The verdict for a PR.

    Only the root `CHANGELOG.md` counts, and only the exact label or bot identity
    skips: a near-miss either way would disable the gate without anyone noticing.
    """
    if not changelog_required(changed):
        return True
    if author in EXEMPT_AUTHORS:
        return True
    if SKIP_LABEL in labels:
        return True
    return CHANGELOG in (p.strip() for p in changed)


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - CI glue
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--labels", default="", help="comma-separated PR labels")
    parser.add_argument("--author", default="", help="login of the PR's author")
    args = parser.parse_args(argv)

    changed = [line for line in sys.stdin.read().splitlines() if line.strip()]
    labels = [label.strip() for label in args.labels.split(",") if label.strip()]

    author = args.author.strip() or None

    if passes(changed, labels, author):
        if not changelog_required(changed):
            print("No server/ or web/ change outside tests — no changelog entry needed.")
        elif author in EXEMPT_AUTHORS:
            print(f"Opened by {author}; dependency updates are summarised at release.")
        elif SKIP_LABEL in labels:
            print(f"Skipped by the `{SKIP_LABEL}` label.")
        else:
            print(f"{CHANGELOG} is updated.")
        return 0

    deployable = sorted(p for p in changed if _is_deployable(p))
    print("::error title=Changelog entry missing::"
          f"This PR changes what an operator deploys but not {CHANGELOG}.")
    print()
    print("Deployable files changed:")
    for path in deployable:
        print(f"  {path}")
    print()
    print(f"Add a line under `## [Unreleased]` in {CHANGELOG} — Added, Changed, Fixed or")
    print("Security, plus `### Upgrade notes` for anything an operator must do when deploying.")
    print("Do not bump the version: it changes only when a release is cut (docs/releasing.md).")
    print(f"If the change genuinely has no operator- or user-visible effect, label the PR "
          f"`{SKIP_LABEL}`.")
    return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
