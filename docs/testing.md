# Testing & coverage policy

The backend test suite lives in `server/tests/` and runs on SQLite (no Docker needed) plus a
small Postgres migration-smoke job. See issue #46 for the history. Web, Android, and Jetson
suites are covered in their own sections below.

## Running the tests

```bash
cd server
python -m venv .venv && . .venv/Scripts/activate   # or .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
pytest -v
```

Notes:
- `conftest.py` points `DATABASE_URL` at a throwaway SQLite file, so `get_db` and direct
  `async_session()` calls both hit the test DB. No Postgres required.
- The Postgres migration test (`test_migrations_postgres.py`) self-skips unless
  `RUN_PG_TESTS=1` and a Postgres `DATABASE_URL` are set (it runs in its own CI job).
  It drives the **Alembic** migrations (issue #53): `alembic upgrade head` builds the
  full schema, downgrade→upgrade proves reversibility, and `alembic check` is the
  model↔migration drift gate. Schema is Alembic-managed (`server/alembic/`), not built
  at app startup — the container runs `alembic upgrade head` via `entrypoint.sh` before
  uvicorn; an existing pre-Alembic DB must be `alembic stamp head`ed once.
- On Windows, if a venv fails to build under a long path, create it at a short path
  (e.g. `C:\bst`) — pip's dist-info paths can exceed `MAX_PATH`.
- **Match CI, not your global Python.** CI runs Python 3.12 with the pinned dev deps and
  installs `requirements.txt` minus the heavy transcription stack:
  `grep -viE '^(torch|openai-whisper)' requirements.txt > req-ci.txt && pip install -r req-ci.txt -r requirements-dev.txt`.
  Running against a global interpreter with unpinned pytest or missing prod deps (e.g.
  `audible`) produces failures that don't exist in CI. Tests that need optional heavy deps
  should `pytest.importorskip(...)` so a missing dep skips instead of breaking collection.

### Fast red-green loop

`pytest-watch` reruns the suite on save — the core TDD loop. Focus on the file you're
working on for the fastest feedback:

```bash
cd server
ptw -- tests/test_users.py        # rerun just this file on every save
```

Tests use a low bcrypt cost (set in `conftest.py`) so auth/user-heavy suites stay fast.

## Writing a test (server)

Write the failing test first, watch it fail for the right reason, then make it pass.

**Fixtures & helpers** (from `conftest.py` / `tests/factories.py`):
- `db` — an `AsyncSession` on the SQLite test DB (schema is fresh per test).
- `make_user(username=…, role=…, password=…, is_active=…)` — insert a `User`.
- `auth_header(user)` — `Authorization: Bearer …` for that user.
- `make_client(*routers)` — an `httpx.AsyncClient` for a minimal app mounting just the
  router(s) under test (avoids the heavy full app). Async context manager.
- `tests.factories.make_book_pair(db, …)` / `make_sync_map(db, …)` — seed common rows.

**Pattern — a router endpoint test:**

```python
from routers import users
from tests.factories import make_book_pair  # if you need domain rows

async def test_admin_can_list_users(make_client, make_user, auth_header):
    admin = await make_user(username="admin1", role="admin")
    async with make_client(users.router) as client:
        r = await client.get("/api/users/", headers=auth_header(admin))
    assert r.status_code == 200
```

**Pattern — pure logic (golden vectors):** put inputs/expected in a small data list or a
JSON fixture under `tests/fixtures/` and `@pytest.mark.parametrize` over them (see
`test_metadata_utils.py` and `tests/fixtures/sync_parity/`). For logic mirrored on the
Android client, add the vectors to `sync_parity/` so both platforms assert the same contract.

Cross-request DB state: the app commits per request via its own session, so read back with a
**fresh** `async_session()` (not the `db` fixture, which holds its own snapshot) — see
`test_queue_manager.py::_get`.

## Coverage gates (CI)

Coverage is measured with `pytest-cov` and enforced by **two independent gates** in the
`pytest` job of `.github/workflows/tests.yml`:

1. **Global floor (anti-backslide)** — `pytest --cov=. --cov-fail-under=30`. Fails the build if
   total coverage drops below **30%**. Runs on every push and PR. This only stops backsliding;
   it is deliberately a few points under the actual total.

2. **Patch coverage (stop-the-bleeding, PRs only)** — `diff-cover` requires **≥80%** coverage of
   the lines a PR *adds or changes* (compared to the base branch). This is the gate that keeps
   new business logic tested without demanding retroactive coverage of legacy glue.

### Why not one high global number?

~2/3 of the statements are intentionally-untested integration/hardware glue (ffmpeg,
torch/whisper, Audible/ABS, Calibre, real EPUB/audio parsing). A high global target is neither
reachable nor meaningful, and a single global % is dominated by churn in big untested files.
Patch coverage targets exactly what a change touches, which is the useful signal.

### diff-cover exclude list

These modules are **excluded from patch coverage** because they are external-tool /
hardware / environment-specific and are covered by manual verification or the dedicated
Postgres job, not unit tests. Keep this list in sync with the `--exclude` args in
`.github/workflows/tests.yml`:

```
server/database.py                       # migrations exercised by the Postgres job
server/services/transcription.py         # torch/whisper (lazy-imported)
server/services/transcription_providers/*
server/services/import_sources/*         # Audible / Calibre / ACSM externals
server/services/import_scheduler.py
server/services/abs_metadata.py          # Audiobookshelf HTTP API
server/services/audio_integrity.py       # ffmpeg
server/services/ebook_integrity.py
server/services/epub_parser.py           # needs real EPUBs
server/services/library_verify.py
server/routers/chapters.py               # ffmpeg
server/routers/files.py                  # file serving / byte-range streaming
server/routers/import_sources.py
server/routers/troubleshoot.py
server/scripts/*
```

Everything else is **in scope** and must meet the 80% patch bar: the auth / sync / users /
settings / stats / match routers, `library.py`, and the core services (`sync_engine`,
`metadata_utils`, `credentials`, `queue_manager`, `library_writer`, `alignment`).

## The ratchet (raise coverage over time)

The floor is a starting point, not the goal. As coverage grows:

1. **Raise the floor.** After any PR that increases total coverage, bump `--cov-fail-under` in
   `.github/workflows/tests.yml` to `floor = new_total − 3` (whole percent). Milestone targets:
   **30 → 40 → 50** and up.
2. **Backfill untested modules**, then **remove them from the diff-cover exclude list** so future
   changes to them are gated too. Suggested order (most unit-testable first):
   - `library.py` endpoints — pairing, metadata edit/write-back, cover upload.
   - `match` provider fetches (`fetch_google_books` / `fetch_open_library`) — mock `httpx`.
   - `abs_metadata` — mock `httpx` against the Audiobookshelf API shapes.
   - `files` — serving + byte-range streaming with temp files.
   - `chapters` — mock the ffmpeg/ffprobe subprocess calls.
   - `epub_parser` — add a tiny fixture `.epub` and assert sentence extraction.
   - `library_verify`.
3. Genuinely untestable branches (hardware/external) can be marked `# pragma: no cover` rather
   than excluding a whole file.

### Web

Same rule, different knob. After any PR that raises the web total, bump each entry in
`coverage.thresholds` (`web/vite.config.js`) to `metric_total − 3`, whole percent — all four
metrics, not just lines. Milestone targets for lines: **30 → 40 → 50** and up.

Highest-leverage backfill targets are the large, near-zero pages (percentages as of the
2026-07-27 run): `ImportSourcesPage.jsx` (0.2%), `TranscriptionPage.jsx` (1.1%),
`PairsPage.jsx` (1.4%), `NewPairsPage.jsx` (4.3%), `LibraryPage.jsx` (6.2%),
`UserManagementPage.jsx` (6.7%), `UnpairedPage.jsx` (6.3%). Component-level gaps worth
closing first because they are small: `FilterPill.jsx` (1.9%), `MobileTopBar.jsx` (6.3%),
`MobileDrawer.jsx` (19%).

## Web (`web/`)

Vitest + React Testing Library on jsdom; config lives in the `test` block of
`web/vite.config.js`, shared setup in `web/src/test/setup.js` (jest-dom matchers, `cleanup()`,
a `setViewport(width)` matchMedia mock, and HTMLMediaElement `play`/`pause` stubs — jsdom has
no media engine).

```bash
cd web
npm test          # watch mode — the web red-green loop
npx vitest run    # one-shot
npm run coverage  # what CI runs
```

Write the failing test first here too. Web changes have repeatedly landed code-first and
needed follow-up commits when the patch-coverage gate failed — the gate is a backstop, not
the workflow. Tests live next to the code (`src/**/*.test.jsx`); mock the API at the
`fetch`/module boundary as the existing page tests do.

CI (`.github/workflows/web-tests.yml`) enforces the same **two independent gates** as the
server:

1. **Global floor (anti-backslide)** — vitest `coverage.thresholds` in the `test.coverage`
   block of `web/vite.config.js`, currently **28% lines / 28% statements / 28% functions /
   64% branches**. `npm run coverage` exits non-zero below any of them, and CI runs that on
   every push (not just PRs), so no separate workflow step is needed.
2. **Patch coverage (stop-the-bleeding, PRs only)** — `diff-cover` requires **≥80%** coverage
   of the lines a PR adds or changes.

Thresholds apply to whatever actually ran, so a *filtered* run
(`npx vitest run src/pages/HomePage.test.jsx --coverage`) will fail the floor spuriously —
that is expected. Only the full `npm run coverage` is the gate; use `npm test` for the
red-green loop.

## Android (`android/`)

JVM unit tests only (no emulator in CI).

```bash
cd android
./gradlew :app:testDebugUnitTest
```

The key test is `SyncMatcherParityTest`, which asserts `SyncMatcher.normalizeForSearch`
matches the server's `_normalize_for_search` for every golden vector in
`server/tests/fixtures/sync_parity/` (fixtures are copied in by the
`copySyncParityFixtures` Gradle task). **Any matcher change must update the shared fixtures**
so both platforms stay pinned to the same contract. The `match_cases.json` half
(`getSyncPointForEpubText` ↔ `_match_text_to_sync_points`) is not yet enforced on Android —
see issue #41.

## Jetson (`jetson/`)

`jetson/test_server.py` covers the shared-secret auth guard and the oversized-chunk
decision logic (`_is_chunk_oversized` / `_shrink_chunk_size` in `jetson/server.py`).
It **is wired into CI** (`.github/workflows/jetson-tests.yml`, path-filtered to
`jetson/**`) — `jetson/conftest.py` stubs `nltk` and `faster_whisper` in `sys.modules`
so the suite runs with only `fastapi`/`uvicorn`/`pytest` installed, no GPU or network
access needed. Run it locally with:

```bash
cd jetson && python -m pytest test_server.py -v
```

The rest of the transcription pipeline (actual ffmpeg chunk loading, faster-whisper
transcription, checkpointing) still has no automated test — that needs real audio and
a GPU, which is out of scope for this suite. The client-side instance_id retry logic is
covered in `server/tests/test_remote_transcription_provider.py`.
