# Testing & coverage policy

The backend test suite lives in `server/tests/` and runs on SQLite (no Docker needed) plus a
small Postgres migration-smoke job. See issue #46 for the history. Web, Android, and Jetson
suites are covered in their own sections below.

## Running the tests

```bash
cd server && ./setup-testenv.sh          # one-time per clone or worktree
.venv/Scripts/python.exe -m pytest -q    # or .venv/bin/python on macOS/Linux
```

`setup-testenv.sh` is idempotent — re-run it after pulling dependency changes. It reads
`server/.python-version` (committed, currently `3.12`, matching CI) and provisions that
interpreter with [uv](https://docs.astral.sh/uv/), downloading it if the machine doesn't
have it. Because the version is committed rather than remembered, every new branch and
worktree gets the right one. Each worktree keeps its own `server/.venv`; uv hardlinks from
its global cache, so the second and later ones cost seconds and little disk. The script
**fails loudly** rather than falling back to whatever `python` resolves to — a venv built
on the wrong version is the exact problem it exists to prevent.

Notes:
- `conftest.py` points `DATABASE_URL` at a throwaway SQLite file, so `get_db` and direct
  `async_session()` calls both hit the test DB. No Postgres required.
- **That SQLite file is per-process** (`booksync_test_<pid>.db`) and is deleted at session
  end. It used to be one fixed path shared by every run on the machine, which the autouse
  `_fresh_schema` fixture — it drops and recreates the whole schema before *each test* —
  turned into a hazard: two suites running at once (one per worktree, say) tore down each
  other's tables mid-test, producing a storm of `no such table` / `table already exists`
  errors in files unrelated to whatever you changed. A crashed run could also leave the
  shared file locked on Windows, breaking every later run. Pinned by
  `tests/test_harness_db_isolation.py`.
- The Postgres migration test (`test_migrations_postgres.py`) self-skips unless
  `RUN_PG_TESTS=1` and a Postgres `DATABASE_URL` are set (it runs in its own CI job).
  It drives the **Alembic** migrations (issue #53): `alembic upgrade head` builds the
  full schema, downgrade→upgrade proves reversibility, and `alembic check` is the
  model↔migration drift gate. Schema is Alembic-managed (`server/alembic/`), not built
  at app startup — the container runs `alembic upgrade head` via `entrypoint.sh` before
  uvicorn; an existing pre-Alembic DB must be `alembic stamp head`ed once.
- On Windows, if a venv fails to build under a long path, create it at a short path
  (e.g. `C:\bst`) — pip's dist-info paths can exceed `MAX_PATH`.
- **Match CI, not your global Python** — that is what `setup-testenv.sh` is for. Running
  against a global interpreter with unpinned pytest or missing prod deps (e.g. `audible`)
  produces failures that don't exist in CI. Tests that need optional heavy deps should
  `pytest.importorskip(...)` so a missing dep skips instead of breaking collection.
  `requirements.txt` no longer carries the heavy transcription stack (torch /
  openai-whisper live in `requirements-local.txt`), so no filtering is needed — CI's
  `grep -viE '^(torch|openai-whisper)'` is a historical no-op.
- Don't create virtualenvs under `server/tests/`. They sit inside `testpaths`, so
  collection crawls their site-packages; `norecursedirs` in `pytest.ini` guards against it
  and `.gitignore` keeps them untracked, but `server/.venv` is the right home.

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

## Dependency audit gates (CI)

Both CI pipelines fail on known vulnerabilities in production dependencies (issue #157):

- **Server** — the `audit` job in `tests.yml` runs `pip-audit -r requirements.txt --strict`.
  Advisories that are justified-unreachable can be allow-listed in `server/audit-ignore.txt`
  (one ID per line, with a comment explaining why).
- **Web** — `web-tests.yml` runs `audit-ci` (npm audit with `--omit=dev` semantics plus an
  allow-list, which npm lacks natively). The allow-list and its per-advisory justifications
  live in `web/audit-ci.jsonc`.

Every allow-list entry must carry a written justification and gets re-checked whenever the
owning dependency is next touched.

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

### Android

Same rule again. After any PR that raises the Android total, bump `minValue` in the `kover`
block of `android/app/build.gradle.kts` to `new_total − 3`, whole percent. Milestone targets:
**10 → 20 → 30 → 40** and up (a lower ladder than server/web — the module started further
back). Reached 40 on 2026-09-03; next rung is 50.

Highest-leverage backfill targets, by **missed** lines as of the 2026-09-03 run. The list is
no longer "all at 0%": `BookSyncRepository`, `PlayerViewModel` and `DownloadedViewModel` are
part-covered, and `AuthInterceptor` has dropped off it entirely (#143/#218). What is left is
mostly the ViewModels:

| Class (with its lambdas) | Missed | Covered |
|---|---|---|
| `BookSyncRepository` | 316 | 605 |
| `PlayerViewModel` | 291 | 98 |
| `LibraryViewModel` | 227 | 0 |
| `SearchViewModel` | 152 | 0 |
| `HomeViewModel` | 142 | 0 |
| `BookDetailsViewModel` | 114 | 0 |
| `DownloadWorker` | 110 | 0 |
| `DiagnosticsViewModel` | 72 | 0 |
| `NewItemsViewModel` | 61 | 0 |
| `DownloadedViewModel` | 57 | 23 |
| `DiagnosticLogger` | 57 | 0 |
| `ReaderViewModel` | 46 | 0 |

The ViewModels are the cheapest of these — mockk + `Dispatchers.setMain` already work here,
see the Android section below. Regenerate the table with
`./gradlew :app:koverXmlReportDebug` and total the `LINE` counters per class in
`app/build/reports/kover/reportDebug.xml`.

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

The key tests are the two parity suites, which pin the Kotlin matcher to the server's for
every golden vector in `server/tests/fixtures/sync_parity/` (fixtures are copied onto the
test classpath by the `copySyncParityFixtures` Gradle task):

| Suite | Asserts | Against |
|---|---|---|
| `SyncMatcherParityTest` | `SyncMatcher.normalizeForSearch` | `normalize_cases.json` |
| `MatchParityTest` | `SyncMatcher.match` | `match_cases.json` |

Since #41 both platforms run the *same* algorithm — exact substring pass, then a fuzzy
bigram (Dice) pass, then the interpolated-point nudge — implemented in
`server/services/sync_matcher.py` and mirrored in
`android/app/src/main/java/com/booksync/sync/SyncMatcher.kt`. **Any matcher change must
update both implementations and the shared fixtures in the same PR**, or one of the two
suites will fail.

`SyncMatcherInternalsTest` covers the fuzzy-pass edge cases the golden vectors can't reach
through the public `match` entry point (short needle, over-long needle, sub-threshold score,
the ±3 window of the interpolation nudge). It mirrors the corresponding assertions in
`server/tests/test_sync_matching.py` — keep the two in step.

`AccountViewModelLogoutTest` pins the logout wiring added in #39: revoke on the server
*before* clearing local tokens (AuthInterceptor needs the still-stored bearer token to
authenticate that call), and clear the local tokens unconditionally so an offline or
already-expired session can still log out.

The HTTP stack is pinned with **`okhttp3:mockwebserver`** rather than a mocked
`Interceptor.Chain`, because both bugs it guards are about *how many requests actually
leave*: `TokenRefreshTest` asserts that a rejected refresh does not recurse, that five
concurrent 401s produce exactly one refresh POST, and that a redirect before the 401 does not
suppress it (#143); `RetryInterceptorTest` asserts that a persistent 5xx is returned rather
than disguised as an IOException, that a 4xx is not retried at all, and that the backoff
between attempts doubles (#218). A mocked chain can express none of those.

The Media3 media-id wire format has exactly one owner, `player/MediaId.kt`, so its
`pair_N` / `audiobook_N` dispatch is a pure function `MediaIdTest` can pin end to end even
though `AudioPlayerService` itself is excluded below.

The same trick carries the service's *decisions* out of the excluded class and into small
pure ones: `HeartbeatThrottle` (when a heartbeat may push, #65), `ContinuousPlaybackLog`
(when a 30-min history entry is due) and `PauseSavePolicy` (whether the stop being saved
right now may claim the format, #226) each have their own test. What is left inside the
service is only the wiring, and that is pinned by reading the source —
`BoundarySaveWiringTest`, `SeekFlushWiringTest` and `PauseOwnershipWiringTest` assert that
the call sites exist and that no second owner has appeared, e.g. that `CMD_USER_PAUSE` is
declared, registered and handled, and that `PlayerViewModel`'s poll loop writes no position
of its own.

The module has **mockk** and **kotlinx-coroutines-test** (`testOptions.unitTests
.isReturnDefaultValues = true`), so ViewModels are testable off-device: mock the
collaborators, `Dispatchers.setMain(UnconfinedTestDispatcher())` so `viewModelScope.launch`
runs eagerly, and assert straight after the call. Pin `kotlinx-coroutines-test` to the same
version `kotlinx-coroutines-core` resolves to — a mismatch breaks `Dispatchers.setMain`.

### Coverage floor

Android has **one** gate, not two: a global line-coverage floor (anti-backslide). There is no
patch-coverage equivalent — no mature Kotlin diff-cover exists, and the repo isn't going to
grow one just for this. Coverage is measured with **Kover** (`org.jetbrains.kotlinx.kover`,
pinned in `android/build.gradle.kts`) rather than JaCoCo: it is Kotlin/AGP-native, so nothing
has to hand-wire the unit-test `.exec` file or per-variant class dirs on AGP 9, and it
attributes Kotlin inline functions correctly. Note the version floor — Kover **≥ 0.9.9** is
required; earlier 0.9.x releases don't see AGP 9's build variants and silently report
"No sources".

```bash
cd android
./gradlew :app:koverVerifyDebug            # the gate CI runs
./gradlew :app:koverLogDebug               # print the current total
./gradlew :app:koverHtmlReportDebug        # browsable line-by-line report
```

The floor lives in the `kover { reports { verify { ... } } }` block of
`android/app/build.gradle.kts`, currently **40% lines** — measured total was **43.86%**
(1722/3926 lines) on 2026-09-03, floor set a few points under, exactly like the server's
`--cov-fail-under`. `.github/workflows/android-tests.yml` runs
`koverXmlReportDebug` + `koverVerifyDebug` after the test step (Kover reuses the test run; it
does not re-execute the suite) and uploads `reportDebug.xml` as the `android-coverage`
artifact.

Scope is **JVM unit tests only** — there are no instrumented/Espresso tests in CI, so the
number is not a whole-app figure. Three buckets are excluded from the denominator:

1. **Generated code** — Hilt (`*_Factory`, `Hilt_*`, `*_HiltModules*`, the aggregated-deps
   packages), Room `*_Impl`, kotlinx-serialization `$$serializer`, Compose
   `ComposableSingletons`, `BuildConfig`, `R`.
2. **Compose UI** — `*Screen*`, `ui.components.*`, `ui.theme.*`, navigation, `ReaderActivity`,
   the sheets and `UnifiedAudioPlayer`, `MainActivity`, `BookSyncApp`. Untestable without an
   emulator or Robolectric today; revisit if Robolectric is adopted.
3. **Framework/service glue with no JVM-testable surface** — `AudioPlayerService`,
   `LocalCastHttpServer`, `cast.*`, `auto.*`, `di.*`, `BookSyncDatabase`. Same reasoning as
   the server excluding its ffmpeg/hardware glue.

**ViewModels are deliberately *not* excluded** even though they live under `ui/`. They are
plain JVM classes, several are already tested, and they remain the largest untested logic
surface in the app — excluding them would make the floor look better while hiding the thing
most worth fixing. Because they're in the denominator the starting floor was low; that's the
point of the ratchet.

Like the web thresholds, the floor applies to whatever actually ran — a filtered test run
will fail it spuriously. Only the full `koverVerifyDebug` is the gate.

## Jetson (`jetson/`)

`jetson/test_server.py` covers the shared-secret auth guard, the oversized-chunk
decision logic (`_is_chunk_oversized` / `_shrink_chunk_size` in `jetson/server.py`),
and the off-hours control surface from #106: lazy model load, the idle-unload decision
table, and the pause → checkpoint → resume cycle. It **is wired into CI**
(`.github/workflows/jetson-tests.yml`, path-filtered to `jetson/**`) —
`jetson/conftest.py` stubs `nltk` and `faster_whisper` in `sys.modules` so the suite
runs with only `fastapi`/`uvicorn`/`httpx`/`pytest` installed, no GPU or network access
needed. Since #106 the startup event no longer loads the model, so `TestClient` is
usable here; `_transcribe_file` is driven with a fake model plus stubbed ffmpeg helpers
(`_get_audio_duration`, `load_audio_chunk`). Run it locally with:

```bash
cd jetson && python -m pytest test_server.py -v
```

Real ffmpeg chunk loading and real faster-whisper transcription still have no automated
test — those need real audio and a GPU, which is out of scope for this suite. The client-side instance_id retry logic is
covered in `server/tests/test_remote_transcription_provider.py`.
