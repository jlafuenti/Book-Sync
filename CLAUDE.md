# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Tandem** (repo/folder name: Book-Sync; the product was renamed from BookSync) is a multi-platform system for synchronizing reading position between ebooks and audiobooks using AI transcription. User-visible strings say Tandem; identifiers (`com.booksync`, the `booksync` Postgres role/db, `booksync_*` volumes) deliberately still say BookSync — see the naming note in `README.md`. Core components:

- `server/` — Python FastAPI backend with PostgreSQL
- `web/` — React + Vite frontend
- `jetson/` — Lightweight FastAPI transcription server for Jetson Orin Nano (faster-whisper)
- `android/` — Kotlin/Compose Android app

## Personal information stays out of everything external-facing

This repository is public. Nothing that identifies the owner, the owner's network, or the
owner's accounts may appear anywhere a stranger can read: code, tests, fixtures, docs,
comments, commit messages, branch names, pull-request titles and bodies, issue titles and
bodies, review comments, CI logs, container images, screenshots, or the changelog. That means:

- **No real hostnames or domains** other than the project's own public brand. Use
  `tandem.example.com` and friends. The production hostname lives only in the gitignored
  `CLAUDE.local.md`; refer to it as "the production host" or "the docker host".
- **No LAN or private addresses**: nothing from the three RFC 1918 private blocks (the ones
  starting 10., 172.16-31. and 192.168.). Examples use the documentation ranges
  (`192.0.2.x`, `198.51.100.x`, `203.0.113.x`).
- **No e-mail addresses** except the project's public support address; **no names** beyond the
  GitHub username that is already public; **no device serials, MAC addresses, account ids,
  ticket numbers, or Play/Cloudflare/GitHub Support identifiers.**
- **No secrets or secret-shaped values**, real or "obviously fake": tokens, keys, passwords,
  the Jetson API key, `.env` contents. Placeholders only, and only in docs that explain how to
  create them.
- **No personal reading data**: no exported databases, no real library filenames, no real
  bookmark or progress rows in fixtures. Fixtures are synthetic (`Axis Test`, `Bartleby`-style
  public-domain titles are fine).
- **No screenshots or logs that contain any of the above.** Redact before attaching, or
  describe instead.

Enforced, not just requested: `server/tests/test_repo_hygiene.py`,
`server/tests/test_android_no_personal_hosts.py` and `server/tests/personal_identifiers.py`
fail the build on the personal domain, private-range addresses and e-mail addresses in the
tree, with a small allow-list for documentation examples. They cannot scan an issue body or a
commit message, so those are on you: read anything you are about to post to GitHub as if you
were a stranger, and if a value came from the live system (a log line, a `docker inspect`,
a `psql` result, a path), mask it before it leaves the machine. When a mistake does land,
say so immediately; history that has already been pushed needs a rewrite and GitHub
Support's help to purge, which is far more expensive than not posting it.

## Live UI Access

Optional and machine-local: if you have a running instance to verify against, put its
URL and login in `CLAUDE.local.md` (gitignored, auto-loaded alongside this file), along
with anything else specific to your machine or network — hosts, paths, remote-access
details. Nothing of that kind belongs in this file, which is tracked and public
(pinned by `server/tests/test_repo_hygiene.py`).

### Playwright MCP — Token Budget Rules

The Playwright MCP's `browser_snapshot` and `browser_click` tools return full accessibility trees that routinely exceed the maximum token limit on a rich page. **Default to logs and source code** for debugging and analysis. Only use Playwright when the user explicitly asks to interact with or inspect the UI.

When Playwright is necessary:
- Use `browser_take_screenshot` (returns an image) instead of `browser_snapshot` (returns a large text tree) whenever you only need to *observe* the page state — screenshots are far cheaper in tokens.
- Reserve `browser_snapshot` for cases where you need element `ref` values to drive further interactions (fill, click, etc.).
- If a `browser_snapshot` or `browser_click` result exceeds the token limit, take a screenshot to confirm the current state instead of re-snapshotting.

## Deployed Instances

A deployment is typically two hosts: the server stack (FastAPI + PostgreSQL + web, and
Calibre for conversion) and, optionally, a Jetson running the remote transcription worker
(`jetson/README.md`). Connection details for a specific deployment are machine-local —
keep them in `CLAUDE.local.md`.

**If you have remote access to a running instance, it is read-only.** Use it for logs and
runtime state; always read and edit code locally from this repository, never over SSH, and
never build or deploy from a session — that is the operator's step.

## Development Process

- Always follow TDD (superpowers:test-driven-development): write a failing test before implementation code, for every feature and bugfix. **This applies to web UI changes exactly as much as server changes** — history shows web fixes repeatedly landed code-first and needed follow-up "satisfy the coverage gate" commits. A PR should never need one.
- Testing policy, fixtures, coverage gates, and the ratchet plan live in `docs/testing.md`. Run the affected suite(s) locally before pushing (commands below).
- **Record server and web changes in `CHANGELOG.md`.** A PR that changes `server/` or `web/` outside their tests adds a line under `## [Unreleased]`, in the matching section — Added, Changed, Fixed, Security — plus `### Upgrade notes` for anything an operator has to do when deploying it. **Do not bump `APP_VERSION`** or the other version strings in a feature PR: the version changes only when a release is cut (`docs/releasing.md`), because the System page's update check compares a running server against published releases, and a version that was never tagged means nothing to it. Bumping per PR would also collide between parallel branches and force a patch/minor/major choice before anyone can see what the release contains. Enforced by the `changelog` CI job (`.github/scripts/changelog_gate.py`). Label a PR `skip-changelog` only when it has no operator- or user-visible effect — e.g. a test-timeout tweak in `web/vite.config.js`, which the gate counts because that file also drives the production build. Dependabot PRs are exempt — the bot cannot write the line, and stalling a security bump on it would defeat Dependabot — so dependency updates are summarised in the release notes when a release is cut. Android is out of scope: Play carries its own release notes.
- Sync-matching logic exists on both server and Android. Any change to it must update the golden vectors in `server/tests/fixtures/sync_parity/` — both platforms assert the same contract from those fixtures. Never change matcher behavior on one platform only.
- Security fixes: grep for sibling endpoints with the same vulnerable pattern (every place a user-supplied URL/filename/path is used) and fix them in the same PR or file an issue. If a path is deliberately left open (e.g. admin-gated SSRF via `allow_private=True`), record that decision in the issue and pin it with a test.
- **Nothing blocking inside an `async def`.** No filesystem read/write, hashing, archive or XML parse, media-container read, or subprocess call may run directly on the event loop — wrap it in `await asyncio.to_thread(...)`. One uvicorn worker serves every request, so a synchronous file read stalls position sync, audio streaming, login and `/api/health` for its whole duration, and the symptom (unrelated endpoints timing out) never points at the culprit. The models are `library.py`'s `_read_embedded_metadata`, `_hash_and_size` and `_walk_tree`: put the blocking work in a plain sync helper and cross to a thread once, rather than hopping per item (issue #203).
- **Long library jobs commit in bounded batches and take the job guard.** Anything that walks the whole library (`/library/scan`, `/rescan-all`, `/rehash`, `/enrich-abs`) runs for minutes; one request-long transaction means a crash discards all of it and Postgres holds a write transaction open throughout. Commit every `SCAN_COMMIT_BATCH` items via `library._Batch` (unless the job has a cross-pass invariant, as `/rehash` does), and wrap the endpoint in `_library_job(name)` so a second one gets 409 instead of interleaving — overlapping walks both miss on the same `file_path` and fight over the unique index (issue #202).
- Before starting any non-trivial feature or behavior change, use superpowers:brainstorming to clarify scope and design before writing code.
- Work on feature branches in an isolated git worktree (superpowers:using-git-worktrees) rather than directly in the main workspace — never commit directly to `main`.
- Before claiming a fix or feature is complete, verify it actually works (superpowers:verification-before-completion): run the relevant tests/checks, and wait for explicit confirmation that the fix works before committing or pushing.
- When debugging, use superpowers:systematic-debugging to find root causes rather than guessing at fixes.
- Do not run `npm run build`, Docker, or deploy commands — building and deploying is the operator's step, done manually.

## Development Commands

### Full Stack (Docker)
```bash
cp docker-compose.example.yml docker-compose.yml   # once; the real file is gitignored
docker compose up                                  # server + PostgreSQL + web (nginx)
```
The GPU transcription service is a separate deployment on its own host — its compose file
is `jetson/docker-compose.example.yml` and its commands run from inside `jetson/` with no
`-f` flag. See `jetson/README.md`.

### Backend (server/)
```bash
pip install -r server/requirements.txt
cd server && alembic upgrade head          # apply DB migrations (schema is Alembic-managed)
cd server && uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```
Schema is managed by **Alembic** (`server/alembic/`), not by app startup. In the
container this runs automatically (`server/entrypoint.sh` → `alembic upgrade head`
before uvicorn); locally you run it yourself. To change the schema: edit the ORM
model → `alembic revision --autogenerate -m "..."` → review the generated script →
commit. CI fails if models and migrations drift (`alembic check`). An existing DB
that predates Alembic must be stamped once: `alembic stamp head`.

### Frontend (web/)
```bash
cd web && npm install
npm run dev     # dev server on port 3000, proxies /api → localhost:8000
npm run build   # production build
```

### Android
```bash
cd android
./gradlew assembleDebug    # debug APK
./gradlew assembleRelease  # release APK
```

### Tests (run the affected suite before pushing — see docs/testing.md)
```bash
cd server && ./setup-testenv.sh             # once per clone/worktree — provisions the CI Python (3.12) via uv
cd server && .venv/Scripts/python.exe -m pytest -q   # backend (SQLite, no Docker); fast loop: ptw -- tests/test_foo.py
cd web && npx vitest run                    # web (watch mode: npm test; coverage: npm run coverage)
cd android && ./gradlew :app:testDebugUnitTest   # Android JVM unit tests
cd jetson && python -m pytest test_server.py -v  # Jetson auth tests — in CI, path-filtered to jetson/**
```
CI gates: server = 30% global floor + ≥80% patch coverage; web = vitest thresholds + ≥80% patch coverage; Android = parity tests + a Kover line-coverage floor (`koverVerifyDebug`). Local Python must mirror CI (3.12, pinned dev deps) — `setup-testenv.sh` handles this; a bare global interpreter may miss prod deps like `audible` (those tests skip). Never run the server suite with a global `python`.

## Architecture

### Data Flow
1. User uploads EPUB + audio file via Library API
2. Metadata extracted (EPUB parser, filename regex, optional Google Books API)
3. Auto-match by title/series or manual pairing creates a `BookPair`
4. Transcription job queued (`TranscriptionQueueItem`)
5. `queue_manager.py` processes one job at a time:
   - Tries remote Jetson if `TRANSCRIPTION_PROVIDER=remote` or `remote_with_fallback`
   - Falls back to local Whisper — but only in an image built with `--build-arg INSTALL_LOCAL_WHISPER=1`; the default image installs no local model and the fallback raises instead
   - NLTK tokenizes output into sentences
6. Result cached as `AudioTranscript` in DB
7. Alignment service generates `SyncMap` (sentence ↔ chapter position)
8. Frontend/Android uses sync points to jump between ebook and audiobook positions

### Key Server Files
- `server/main.py` — App entrypoint, registers every router in `server/routers/`, lifespan startup
- `server/config.py` — Pydantic Settings; all env vars loaded here
- `server/database.py` — Async SQLAlchemy session factory + superadmin bootstrap
- `server/alembic/` — Alembic migrations (schema management); `env.py` derives a sync psycopg2 URL from `DATABASE_URL`, `versions/` holds the revisions
- `server/models/` — ORM models, one module per area (`book.py`, `bookmark.py`, `progress.py`, `sync_map.py`, `transcript.py`, `transcription_queue.py`, `user.py`, `refresh_token.py`, `audit_log.py`, `library_issue.py`, `import_source.py`, `settings.py`); read the directory rather than trusting a list here
- `server/routers/library.py` — Largest file (~155 KB / 3,800 lines); handles directory scanning, file uploads, auto-matching, metadata extraction
- `server/services/queue_manager.py` — Background async job processor with cancellation and retry.
  **Single-process only** (issue #252): the queue claim has no row lock, cancel/pause state is in
  module globals, startup recovery re-queues every `in_progress` row, and the import/backup
  schedulers start per process. `config.check_single_process()` refuses to boot on
  `WEB_CONCURRENCY`/`UVICORN_WORKERS`/`GUNICORN_WORKERS` > 1; don't add `--workers` or a second
  replica without doing the redesign in docs/operations.md, "Single process only"
- `server/services/transcription_providers/` — Pluggable provider pattern: `local.py`, `remote.py`, `__init__.py` (factory + fallback logic)
- `server/services/alignment.py` — Sentence-level sync point generation
- `server/services/epub_parser.py` — EPUB metadata extraction

### Key Config Variables (`server/config.py`)
Names below are the env-var **aliases** — what `Settings` actually reads. `extra = "ignore"`
(`config.py`) means a misspelled variable is silently discarded, so check the alias in
`config.py` (or the env tables in `README.md`) rather than guessing one.
- `DATABASE_URL` — PostgreSQL async URL (asyncpg). Optional: assembled from
  `POSTGRES_USER`/`POSTGRES_PASSWORD`/`POSTGRES_HOST`/`POSTGRES_PORT`/`POSTGRES_DB` when not set
- `JWT_SECRET_KEY`, `CREDENTIAL_ENC_KEYS` — Token signing and at-rest credential encryption
- `EBOOK_DIR`, `AUDIOBOOK_DIR`, `APP_DATA_DIR`, `COVERS_DIR`, `IMPORTS_DIR`, `BACKUPS_DIR` — File system paths
- `WHISPER_MODEL`, `WHISPER_DEVICE` — Local Whisper model and device (cpu/cuda)
- `TRANSCRIPTION_PROVIDER`, `TRANSCRIPTION_REMOTE_URL` — Provider mode and remote worker URL
- `GOOGLE_BOOKS_API_KEY` — Optional; enables metadata enrichment

**Secrets never go in compose `environment:`** (issue #180). Every secret setting also reads a
`<NAME>_FILE` variable naming a file — the Docker secrets convention, `/run/secrets/<name>` in the
shipped template. `<NAME>_FILE` wins over `<NAME>`, and a path that is missing, unreadable or empty
is a startup error naming the variable, never a silent fallback. The list of names is
`config.SECRET_FILE_ENV_VARS`; adding a secret setting without adding it there fails
`server/tests/test_secret_files.py`. Runbook: `docs/operations.md`, "Secrets".

**Much of the runtime configuration is DB-backed, not env-backed.** The transcription
provider/URL/timeout, the Audiobookshelf connection, the off-hours window, backups and
retention live in the `system_settings` table (defaults in `routers/settings.py`) and are
edited from the System page. Grepping the filesystem for a host or a mode answers the
wrong question — read the table.

### Request Transactions

`get_db` commits once, after the handler returns — **one request, one transaction, and the
dependency owns it**. Handlers do not commit; helpers never commit (they may `flush()`); background
work opens its own `async_session()` and says so in its docstring. A handler may commit by hand in
exactly two cases, and must say which in a comment: to persist state before an external side effect
that can fail or cannot be undone (a file rewrite, an unlink), or to leave progress behind inside a
long library job, which commits in batches on purpose. The remaining trailing commits in
`routers/library.py` duplicate `get_db`'s and come out opportunistically, one endpoint at a time,
never as a sweep. Read `docs/request-transactions.md` before adding a `db.commit()` — or before
deleting one that looks redundant.

### Cross-Device Position Sync
Chapter + sentence index is the portable anchor; a Readium locator (Android) and
an epub.js CFI (web) are device-local hints, stored per device in
`position_hints` and marked stale — never deleted — when they no longer match
the anchor. Everything writes `PUT /api/sync/position/{scope}/{ident}`; there is
no other write path. Read `docs/position-sync-contract.md` before touching
bookmark/progress writes or reader restore logic — the rules are non-obvious
and the failure mode (reopening at the wrong page) is silent.

### Authentication
- JWT (HS256), access token 24h, refresh token 30 days
- Stored in `localStorage` on the frontend
- `server/routers/auth.py` handles login, registration, token refresh, logout

**Sign-out is per device** (issue #250). Every login opens a session — a
`refresh_tokens` row holding the refresh token's `jti` plus the client's
`device_id`. The access and media tokens minted from it carry the same value as
`sid`, and both `get_current_user` and the media resolver in `routers/files.py`
refuse a token whose session has been revoked. `POST /api/auth/logout` revokes
one row, so the browser signing out leaves the phone signed in — which matters
because the phone is usually the device holding unsynced reading positions
(`docs/position-sync-contract.md`).

`users.token_version` is still the account-wide kill switch and still bumped by
password change, admin reset, and the explicit `POST /api/auth/logout-all`.
Do not reintroduce that bump into `logout`.

Two rules that keep the deploy safe, both pinned by tests in
`server/tests/test_auth.py`:

- **A token with no `jti`/`sid` predates sessions and must keep working** on its
  `ver` alone, and a `logout` that can name no session at all must keep falling
  back to the global bump. That is what lets a client that has not been updated
  carry on across the deploy.
- **`/auth/refresh` re-issues for the same session rather than replacing it.**
  The presented refresh token stays valid until its own expiry; revocation, not
  use, is what ends a session. This is deliberately *not* rotation — see below.

**The localStorage choice depends on the web having zero HTML-injection sinks**
(issue #286). A 30-day refresh token in `localStorage` is readable by any script
on the origin, so one injected script is full account takeover, not a defaced
page. That trade is only sound while nothing can inject.

So these are **banned in `web/src/**`**: `dangerouslySetInnerHTML`, `innerHTML`,
`outerHTML`, `insertAdjacentHTML`, `document.write`, and `rehype-raw`. Pinned by
`web/src/no-html-sinks.test.js`, which fails the build rather than relying on
anyone remembering. Test files are exempt — they build DOM fixtures.

The realistic way this breaks is not malice: ABS book descriptions render their
literal `<p>` tags today, and the obvious fix is raw HTML. `BookDetailPage`
renders them through react-markdown, whose escaping and `javascript:` URL
filtering are load-bearing, not incidental — `BookDetailPage.test.jsx` pins both.

**If you need one of these**, move the tokens to httpOnly cookies with CSRF
protection first, in the same change that relaxes the rule.

Related: refresh is single-flighted client-side (`web/src/api.js`,
`refreshSession`; Android's `TokenAuthenticator`) and the server does not rotate
refresh tokens — issue #250 added sessions but deliberately kept the presented
token valid, because a refresh whose response is lost would otherwise strand the
device with a dead token and no way to renew it. If rotation is ever added, the
single-flight is what stops concurrent 401s from logging users out at random —
do not remove it without replacing that guarantee.

### Frontend Structure (`web/src/`)
- `api.js` — Centralized API client with automatic token refresh
- `App.jsx` — React Router root with auth guard
- `pages/` — one component per route; read the directory for the full set. The ones you will
  touch most: HomePage (landing), LibraryPage, BookDetailPage, PairsPage, SystemPage,
  TranscriptionPage/TranscriptionQueuePage/TranscriptionEditorPage
