# Contributing to Tandem

Thanks for looking. This project has a few conventions that are stricter than average and that
**fail in CI rather than at review** — a PR that ignores them bounces before a human reads it.
They are all written down here, so please skim this before you start rather than after.

If anything below is wrong or out of date, that is a bug; say so in an issue.

## Before you start

- **Open an issue first** for anything beyond a typo or an obvious one-line fix. It is a small
  project with opinions about scope (see [Non-goals](#non-goals)); agreeing on the shape first
  saves you writing code that gets declined.
- **Do not report security problems here.** They go through the private channel in
  [SECURITY.md](SECURITY.md).
- Work on a branch. `main` is the canonical copy and is protected; PRs are the only way in.

## Setup

The repo holds four independently-buildable pieces. You only need the ones you are changing.

### Server (`server/`) — Python 3.12, FastAPI

```bash
cd server
./setup-testenv.sh                        # once per clone or worktree
.venv/Scripts/python.exe -m pytest -q     # Windows
.venv/bin/python -m pytest -q             # macOS / Linux
```

`setup-testenv.sh` reads the committed `server/.python-version` (3.12, matching CI) and
provisions exactly that interpreter with [uv](https://docs.astral.sh/uv/), downloading uv if the
machine does not have it. It is idempotent — re-run it after pulling dependency changes.

**Run the suite through that venv, not a global `python`.** The script fails loudly instead of
falling back to whatever `python` resolves to, because a venv on the wrong version (or missing an
optional prod dependency such as `audible`) produces failures that do not exist in CI — and,
worse, silent skips. Tests are SQLite-backed: no Docker and no Postgres needed.

### Web (`web/`) — React + Vite

```bash
cd web
npm install
npm test          # watch mode
npx vitest run    # one-shot
npm run coverage  # what CI runs
```

### Android (`android/`) — Kotlin / Compose

```bash
cd android
./gradlew :app:testDebugUnitTest    # JVM unit tests, no emulator needed
```

A fresh checkout may need `local.properties` pointing `sdk.dir` at your Android SDK.

### Jetson worker (`jetson/`)

```bash
cd jetson
python -m pytest test_server.py -v
```

The full testing policy — fixtures, helpers, how to write a test for each platform, and the
coverage ratchet — lives in **[docs/testing.md](docs/testing.md)**.

## Test-driven development is the expectation

**Write the failing test first, watch it fail for the right reason, then make it pass.** This
applies to web UI changes exactly as much as to server changes; history in this repo shows web
fixes repeatedly landing code-first and needing a follow-up commit purely to satisfy the coverage
gate. A PR should never need one.

This is not ceremony. Most of the interesting failure modes here are silent — a reader that
reopens at the wrong page, a matcher that drifts on one platform, a migration that works forward
but not back — and a test written after the fact tends to assert what the code does rather than
what it should do.

## The CI gates

| Suite | What runs | Gates |
|---|---|---|
| Server | `pytest` + `audit` + `migrations` | 30% global coverage floor; **≥80% patch coverage** on PRs; `pip-audit --strict`; `alembic check` drift gate |
| Web | `vitest` | per-metric coverage thresholds in `web/vite.config.js`; **≥80% patch coverage** on PRs; `audit-ci` |
| Android | `unit-tests` | Kover coverage floor; sync-parity tests; a release (R8) build |
| Jetson | `pytest` | unit tests (path-filtered to `jetson/**`) |
| All | `gitleaks` | secret scan over the history |

**Patch coverage** is the one that surprises people. `diff-cover` compares the coverage report
against the lines your PR adds or changes and requires ≥80% of them to be covered — it does not
ask you to backfill coverage for code you did not touch. When it fails, the run's step summary
lists the file and the exact uncovered line numbers; add tests for those lines. A small set of
intentionally-manual modules (hardware, ffmpeg, third-party integrations) is excluded, and that
exclude list is kept in sync between `.github/workflows/tests.yml` and `docs/testing.md`.

The global floors are anti-backslide ratchets, not targets: if your PR raises the total, bump the
floor, as described in `docs/testing.md`.

## Changes that must touch more than one place

These are the traps. Each one is a place where changing the obvious file alone leaves the system
inconsistent in a way that no single suite catches:

- **Sync-matching logic.** It exists on both the server and Android, and both platforms assert
  the same contract from the golden vectors in `server/tests/fixtures/sync_parity/`. A matcher
  change must update the fixtures and both implementations in the same PR. Never change matcher
  behaviour on one platform only.
- **Playback-offset constants.** They exist on server, web and Android; drift makes the platforms
  disagree about where "here" is.
- **Position and bookmark writes.** Read `docs/position-sync-contract.md` *before* touching
  bookmark/progress writes or reader-restore logic. Chapter + sentence index is the portable
  anchor; the Readium locator and epub.js CFI are per-device hints. The rules are non-obvious and
  the failure mode — reopening at the wrong page — is silent.
- **Schema changes.** An ORM model change without a matching Alembic revision fails CI's
  `alembic check`. See below.
- **Security fixes.** Grep for sibling endpoints with the same vulnerable shape (every place a
  user-supplied URL, filename or path is used) and fix them in the same PR, or file an issue. If a
  path is deliberately left open, say so in the PR and pin the decision with a test.

## Changing the database schema

Schema is Alembic-managed (`server/alembic/`), never built at app startup:

```bash
# 1. edit the ORM model in server/models/
cd server
alembic revision --autogenerate -m "add whatever"
# 2. READ the generated script — autogenerate misses renames, server defaults
#    and data migrations, and happily writes a destructive downgrade
# 3. commit the model change and the revision together
```

CI runs the migrations against real Postgres, checks that downgrade→upgrade is reversible, and
fails on any drift between the models and the migrations.

## Branches, commits and PRs

- Branch off `main`; never commit to `main` directly.
- One logical change per PR. A refactor plus a behaviour change in one diff is hard to review and
  harder to revert.
- Reference the issue in the PR body. Use one closing keyword **per issue** — `Resolves #12,
  resolves #13`; a comma-separated list closes only the first.
- Fill in the PR template checklist honestly. "Tests added" means tests that fail without your
  change.
- Keep the history readable: no merge commits from `main` into your branch mid-review if a rebase
  will do.

## A note on the `booksync` names

The product was renamed from BookSync to **Tandem**, but the rename stopped at the surface on
purpose. User-visible strings say Tandem; identifiers do not — the Android package is
`com.booksync`, the Postgres role and database are `booksync`, and the Docker volumes are
`booksync_*`. Renaming those would mean a data migration and a reinstall for every existing
deployment, in exchange for nothing a user can see. **Do not "fix" them.** New user-facing
strings should say Tandem.

## Non-goals

Declined on purpose, so nobody writes them speculatively:

- **Multi-file audiobooks** (one book split across many files) — the alignment model assumes a
  single audio timeline.
- **iOS** — no Apple toolchain in this project.
- **DRM circumvention.** The server can convert unprotected formats through Calibre; DRM plugins
  are strictly opt-in at build time, never shipped, and no key material belongs in this repo.
- **Multi-tenant hosting.** See the threat model in [SECURITY.md](SECURITY.md): all accounts on
  one instance are people the operator chose.

## Licence

Tandem is **AGPL-3.0-only** (see [LICENSE](LICENSE)) — the licence follows the server's AGPL/GPL
dependencies. By contributing you agree that your contribution is licensed under the same terms.
There is no CLA.

Under §13, anyone who runs a modified Tandem server for other users must offer those users the
corresponding source. Keep that in mind if you are packaging this for someone else.

---

## Branch protection (maintainer)

Branch protection cannot live in a file in the repo, so the intended settings are recorded here.
On a private free repo the API returns 403; the settings become available the moment the repo is
public, which is exactly when nobody will remember what they were.

**Settings → Branches → `main`:**

- Require a pull request before merging — **0 required approvals** (single maintainer; the point
  is to stop direct pushes, not to invent a reviewer).
- Dismiss stale approvals when new commits are pushed.
- Require conversation resolution before merging.
- Require linear history.
- **Block force pushes** and **block deletions**. This is the one that makes a mistyped
  `git push --force` recoverable.
- Do not enforce for administrators — leaves an emergency path; everything above still applies to
  the normal flow.
- Required status checks: **`pytest`**, **`audit`**, **`migrations`** (Server tests) and
  **`gitleaks`** (Secret scan).

One wrinkle worth knowing before you type that: **the Server and Jetson workflows both have a job
called `pytest`**, and a required status check is matched by job name alone, not by workflow. The
server job runs on every PR so the requirement is always satisfiable, but if that ambiguity ever
matters, give one of them an explicit `name:` first and require that instead.

**Do not require `vitest`, `unit-tests` or the Jetson `pytest`.** All three workflows are
path-filtered (`web/**`, `android/**`, `jetson/**`), so on a PR that touches none of those paths
the check never reports and GitHub blocks the merge on a status that will never arrive. Either
leave them advisory, or convert a workflow to always-run with a no-op guard job first and then
require it.

```bash
gh api -X PUT repos/jlafuenti/Book-Sync/branches/main/protection --input - <<'JSON'
{
  "required_status_checks": {
    "strict": false,
    "contexts": ["pytest", "audit", "migrations", "gitleaks"]
  },
  "enforce_admins": false,
  "required_pull_request_reviews": {
    "required_approving_review_count": 0,
    "dismiss_stale_reviews": true,
    "require_code_owner_reviews": false
  },
  "restrictions": null,
  "required_linear_history": true,
  "allow_force_pushes": false,
  "allow_deletions": false,
  "required_conversation_resolution": true
}
JSON
```

`"strict": false` on purpose: `strict` requires every branch to be up to date with `main` before
merging, which on a one-person repo means rebasing after each merge for no added safety.

Two settings that are not branch protection but belong to the same pass, both under
**Settings → Code security**: turn on **private vulnerability reporting** (SECURITY.md and the
issue-template contact link both point at that form, and it 404s until it is enabled), and turn on
**Dependabot alerts and security updates** (`.github/dependabot.yml` only opens version-update
PRs; the security half needs the setting).
