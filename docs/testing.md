# Testing & coverage policy

The backend test suite lives in `server/tests/` and runs on SQLite (no Docker needed) plus a
small Postgres migration-smoke job. See issue #46 for the history.

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
- On Windows, if a venv fails to build under a long path, create it at a short path
  (e.g. `C:\bst`) — pip's dist-info paths can exceed `MAX_PATH`.

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
