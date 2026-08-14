# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Book Sync** is a multi-platform system for synchronizing reading position between ebooks and audiobooks using AI transcription. Core components:

- `server/` — Python FastAPI backend with PostgreSQL
- `web/` — React + Vite frontend
- `jetson/` — Lightweight FastAPI transcription server for Jetson Orin Nano (faster-whisper)
- `android/` — Kotlin/Compose Android app

## Live UI Access

A production web UI exists for verification; its URL and login are in `CLAUDE.local.md`
(gitignored — auto-loaded by Claude Code alongside this file).

### Playwright MCP — Token Budget Rules

The Playwright MCP's `browser_snapshot` and `browser_click` tools return full accessibility trees that routinely exceed the maximum token limit on a rich page. **Default to logs and source code** for debugging and analysis. Only use Playwright when the user explicitly asks to interact with or inspect the UI.

When Playwright is necessary:
- Use `browser_take_screenshot` (returns an image) instead of `browser_snapshot` (returns a large text tree) whenever you only need to *observe* the page state — screenshots are far cheaper in tokens.
- Reserve `browser_snapshot` for cases where you need element `ref` values to drive further interactions (fill, click, etc.).
- If a `browser_snapshot` or `browser_click` result exceeds the token limit, take a screenshot to confirm the current state instead of re-snapshotting.

## Remote Hosts

The running services are on separate hosts, accessible via MCP SSH servers:

| Service | MCP Server | Path |
|---|---|---|
| Book server (FastAPI + PostgreSQL + web) | `ssh-docker` | `/usr/share/docker-containers/Book-Sync` |
| Calibre (ebook conversion) | `ssh-docker` | `/usr/share/docker-containers/calibre` |
| Transcriber (Jetson Orin Nano) | `ssh-orin` | `~/ai-services/booksync-transcriber` |

Use the `ssh-docker` MCP server to inspect or manage the book server (e.g., restart containers, check logs, edit `.env`). Use the `ssh-orin` MCP server for the transcription service.

**Important:** Only use SSH MCP servers to check logs or runtime state. Always read and edit code locally from this repository — do not read source files over SSH.

## Development Process

- Always follow TDD (superpowers:test-driven-development): write a failing test before implementation code, for every feature and bugfix. **This applies to web UI changes exactly as much as server changes** — history shows web fixes repeatedly landed code-first and needed follow-up "satisfy the coverage gate" commits. A PR should never need one.
- Testing policy, fixtures, coverage gates, and the ratchet plan live in `docs/testing.md`. Run the affected suite(s) locally before pushing (commands below).
- Sync-matching logic exists on both server and Android. Any change to it must update the golden vectors in `server/tests/fixtures/sync_parity/` — both platforms assert the same contract from those fixtures. Never change matcher behavior on one platform only.
- Security fixes: grep for sibling endpoints with the same vulnerable pattern (every place a user-supplied URL/filename/path is used) and fix them in the same PR or file an issue. If a path is deliberately left open (e.g. admin-gated SSRF via `allow_private=True`), record that decision in the issue and pin it with a test.
- Before starting any non-trivial feature or behavior change, use superpowers:brainstorming to clarify scope and design before writing code.
- Work on feature branches in an isolated git worktree (superpowers:using-git-worktrees) rather than directly in the main workspace, per [feedback_always_branch.md] — never commit directly to `main`.
- Before claiming a fix or feature is complete, verify it actually works (superpowers:verification-before-completion) — run the relevant tests/checks and, per [feedback_no_commit_until_working.md], wait for explicit user confirmation before committing/pushing.
- When debugging, use superpowers:systematic-debugging to find root causes rather than guessing at fixes.
- Do not run `npm run build`, Docker, or deploy commands — the user pushes to the server manually ([feedback_no_build.md]).

## Development Commands

### Full Stack (Docker)
```bash
docker compose up                                    # server + PostgreSQL + web (nginx)
docker compose -f docker-compose.jetson.yml up -d   # GPU transcription service on port 9000
docker compose -f docker-compose.jetson.yml logs -f # tail Jetson logs
```

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
cd server && python -m pytest -q            # backend (SQLite, no Docker); fast loop: ptw -- tests/test_foo.py
cd web && npx vitest run                    # web (watch mode: npm test; coverage: npm run coverage)
cd android && ./gradlew :app:testDebugUnitTest   # Android JVM unit tests
cd jetson && python -m pytest test_server.py -v  # Jetson auth tests — NOT in CI, run manually when touching jetson/server.py
```
CI gates: server = 30% global floor + ≥80% patch coverage; web = ≥80% patch coverage; Android = parity tests. Local Python should mirror CI (3.12, pinned dev deps) — a bare global interpreter may miss prod deps like `audible` (those tests skip).

## Architecture

### Data Flow
1. User uploads EPUB + audio file via Library API
2. Metadata extracted (EPUB parser, filename regex, optional Google Books API)
3. Auto-match by title/series or manual pairing creates a `BookPair`
4. Transcription job queued (`TranscriptionQueueItem`)
5. `queue_manager.py` processes one job at a time:
   - Tries remote Jetson if `TRANSCRIPTION_PROVIDER=remote` or `remote_with_fallback`
   - Falls back to local OpenAI Whisper
   - NLTK tokenizes output into sentences
6. Result cached as `AudioTranscript` in DB
7. Alignment service generates `SyncMap` (sentence ↔ chapter position)
8. Frontend/Android uses sync points to jump between ebook and audiobook positions

### Key Server Files
- `server/main.py` — App entrypoint, registers 9 routers, lifespan startup
- `server/config.py` — Pydantic Settings; all env vars loaded here
- `server/database.py` — Async SQLAlchemy session factory + superadmin bootstrap
- `server/alembic/` — Alembic migrations (schema management); `env.py` derives a sync psycopg2 URL from `DATABASE_URL`, `versions/` holds the revisions
- `server/models/` — ORM models: `User`, `EBook`, `AudioBook`, `BookPair`, `SyncMap`, `SyncPoint`, `AudioTranscript`, `TranscriptionQueueItem`, `Bookmark`, `UserProgress`
- `server/routers/library.py` — Largest file (~93KB); handles directory scanning, file uploads, auto-matching, metadata extraction
- `server/services/queue_manager.py` — Background async job processor with cancellation and retry
- `server/services/transcription_providers/` — Pluggable provider pattern: `local.py`, `remote.py`, `__init__.py` (factory + fallback logic)
- `server/services/alignment.py` — Sentence-level sync point generation
- `server/services/epub_parser.py` — EPUB metadata extraction

### Key Config Variables (`server/config.py`)
- `DATABASE_URL` — PostgreSQL async URL (asyncpg)
- `TRANSCRIPTION_PROVIDER` — `local` | `remote` | `remote_with_fallback`
- `REMOTE_TRANSCRIPTION_URL` — URL to Jetson server (default port 9000)
- `WHISPER_MODEL`, `WHISPER_DEVICE` — Local Whisper model and device (cpu/cuda)
- `EBOOKS_PATH`, `AUDIOBOOKS_PATH`, `APP_DATA_PATH` — File system paths
- `GOOGLE_BOOKS_API_KEY` — Optional; enables metadata enrichment

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
- `server/routers/auth.py` handles login, registration, token refresh

### Frontend Structure (`web/src/`)
- `api.js` — Centralized API client with automatic token refresh
- `App.jsx` — React Router root with auth guard
- `pages/` — LibraryPage, PairsPage, SeriesPage, TranscriptionPage, TranscriptionQueuePage, TranscriptionEditorPage, SystemPage, BookDetailPage
