# Dependency updates

How Tandem takes dependency updates, from the weekly Dependabot run to the deploy that proves
one. The enforcement lives in CI (`.github/dependabot.yml`, the audit jobs, the changelog gate,
the contract tests); this page is the human half — what a maintainer does with the PRs, and why.

The short version: **an update is merged the same way as any other change.** It goes through
the seven required checks, it gets the verification its ecosystem needs (below), and if it
changes what an operator deploys it is recorded in the release notes. Dependabot writes the PR;
it does not decide.

## What Dependabot opens

`.github/dependabot.yml` runs every Monday and watches:

| Ecosystem | Directory | Grouped as |
|---|---|---|
| pip | `/server`, `/jetson` | `server-minor-and-patch`, `jetson-minor-and-patch` |
| npm | `/web` | `web-minor-and-patch` |
| gradle | `/android` | `android-minor-and-patch` |
| github-actions | `/` | `actions-minor-and-patch` |
| docker | `/server`, `/web`, `/jetson` | one PR per base image |
| docker-compose | `/` (`docker-compose.example.yml`, `docker-compose.demo.yml`) | one PR per image; Postgres majors never proposed |

Minor and patch updates arrive as one grouped PR per ecosystem; **a major arrives on its own**.
Security advisories come through GitHub's Dependabot alerts (a repository setting, not this
file) and open PRs regardless of the schedule.

**Read a grouped PR's body, not its title.** "android-minor-and-patch" has carried a Kotlin
major, a media3 minor that removed API, and a Readium minor. The group name describes the
version arithmetic, not the blast radius.

## Taking them

- **Waves of at most five PRs at a time.** Comment `@dependabot rebase` on each and arm
  auto-merge (`gh pr merge N --merge --auto`). Rebasing all of them at once has queued more than
  sixty CI runs on a handful of hosted runners and starved every other PR; five keeps the queue
  short and lets a failure be seen before the next wave.
- **Dependabot rebases about one PR a minute.** Until it has, a PR's checks are the *old*
  head's, so read results on the post-rebase commit only.
- **A PR opened before a workflow change carries the old workflows.** A check that did not
  exist when the PR was created never reports on it and auto-merge sits at `BLOCKED` with no
  message. `gh pr update-branch N` fixes it.
- **A grouped PR that conflicts** (pip's `ResolutionImpossible`, a lockfile that moved) is
  recreated from the current `main` with `@dependabot recreate` after the single PRs it collides
  with have merged. Dependabot also closes a grouped PR on its own when a merge elsewhere makes
  part of it redundant, and opens a replacement — look for the new PR before reviving the old.
- **Pushing your own commit onto a Dependabot branch is fine** — regenerating
  `docs/openapi.json` after a pydantic bump, say. It stops Dependabot rebasing that PR, which is
  what you want once it carries a hand-made change.
- **Holding a major:** comment `@dependabot ignore this major version` and it closes the PR and
  stops proposing that major. The way back is `@dependabot unignore <group:artifact> dependency`
  on the closed PR (the `unignore this major version` form is not a command Dependabot knows).
  Say why in the PR and file an issue for the prerequisite, so the ignore is a decision and not
  a forgotten comment.
- **Dependabot PRs are exempt from the changelog gate** because the bot cannot write the line.
  The promise that goes with the exemption is kept in `docs/releasing.md`: cutting a release
  lists the Dependabot PRs merged since the last tag and records them in one line.

## What each ecosystem has to pass before it merges

The seven required checks run on every PR. They are necessary, not sufficient, for these:

| Ecosystem | Beyond CI |
|---|---|
| **Server (pip)** | A pydantic or FastAPI bump usually moves `docs/openapi.json`; regenerate it with `python server/scripts/export_openapi.py` under the bumped venv and push it onto the branch. A SQLAlchemy or Alembic bump: run `alembic check` locally against the bumped venv. After it merges, deploy `server` and smoke the API (`docs/operations.md`). |
| **Web (npm)** | Anything that runs at *build* time — vite, esbuild, a Vite plugin, epub.js — is not exercised by CI, because `vite build` runs only when the image is built. After it merges, rebuild `web` on the deployment host and open a book in the reader before calling it done. React, react-router and epub.js majors are hand-made PRs with a reader regression pass, not bot merges. |
| **Android (gradle)** | `unit-tests` and the Kover floor run in CI. A group that touches Compose, media3, Readium, Hilt or Kotlin also gets an emulator pass: sign in, open a book in both formats, download, play. A `checkDebugAarMetadata` failure means a library now needs a newer `compileSdk` — that is a project change (`android/app/build.gradle.kts`), not a reason to force the bump. `kotlinx-coroutines-test` stays at the version of the `coroutines-core` it patches. |
| **Jetson (pip)** | `jetson-tests` covers the auth guard and the queue logic with the ML libraries stubbed. After it merges, rebuild the worker on the Jetson and run one transcription through it — the model libraries are the part the tests cannot see. |
| **GitHub Actions** | Actions are pinned to commit SHAs (a contract test enforces it); Dependabot updates both the SHA and the version comment. A `setup-*` major occasionally changes a default (Node version, cache behaviour) — read the release notes it links. |
| **Docker base images** | The web image's Node line and CI's `node-version` must match (contract test). A Python minor bump for the server image is a normal PR; check the image builds on the deployment host. The Jetson base (`dustynv/faster-whisper`) is tied to the JetPack release on the device; do not take a bump that changes the `r36`/CUDA part without checking the device first. Postgres majors are never proposed; a major there is a planned migration with a backup. |

## Two things that are not Dependabot

- **The update check** (`server/services/update_check.py`, System → Updates): a running server
  tells its operator when a newer *release* is published, and whether the transcription worker
  it is configured to use is behind the server's version. It is how operators learn about
  updates; it does not apply them.
- **Watchtower on the demo stack** pulls `ghcr.io/jlafuenti/tandem-{server,web}:main` on a
  timer. That is the demo following `main`, not a release process — nobody deploys the demo by
  hand, and nothing on a real server should run `:main`.

## Where the pins are enforced

| Pin | Test |
|---|---|
| Every workflow action referenced by commit SHA | `server/tests/test_ci_contract.py` |
| Dependabot watches every ecosystem and directory above | `server/tests/test_ci_contract.py` |
| Web image Node line supported and equal to CI's | `server/tests/test_ci_contract.py` |
| `@xmldom/xmldom` override present, epub.js on 0.3.x | `web/src/dependency-pins.test.js` |
| Release version strings agree across server, web, Android | `server/tests/test_docs_contract.py` |
| Server and web audits clean (`pip-audit`, `audit-ci`) | `tests.yml` `audit` job, `web-tests.yml` |
