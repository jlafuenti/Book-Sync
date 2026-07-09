# BookSync

Synchronize your reading position between ebooks and audiobooks — switch seamlessly between
reading and listening. Transcribes an audiobook, aligns it to the ebook text, and keeps a
single position in sync across devices.

## Components

| Path | Stack | What it is |
|------|-------|------------|
| `server/` | Python · FastAPI · SQLAlchemy (async) · Postgres | API, auth/RBAC, transcription queue, sync engine, import sources |
| `web/` | Vite · React 18 | Web app (responsive — same codebase serves desktop and mobile) |
| `android/` | Kotlin · Jetpack Compose | Android reader/listener client |
| `jetson/` | Python | Remote transcription worker |

## Running

Use Docker Compose for the full stack (server + Postgres + web). Copy the template once, then
customize your paths/secrets — `docker-compose.yml` is gitignored so your local copy never
conflicts with future pulls:

```bash
cp docker-compose.example.yml docker-compose.yml
# edit docker-compose.yml: ebook/audiobook dirs, JWT_SECRET_KEY, CREDENTIAL_ENC_KEYS
docker compose up --build
```

The Jetson Orin Nano remote transcription worker deploys separately, on its own host — see
[jetson/README.md](jetson/README.md) for the sparse-clone-and-deploy walkthrough
(`docker-compose.jetson.example.yml` is the template, same gitignored-copy pattern as above).

## Development & tests

Tests run in CI on every push/PR. **Write a failing test first**, then make it pass.

- **Server:** `cd server && pip install -r requirements.txt -r requirements-dev.txt && pytest`.
  Runs on SQLite — no Docker needed. Use `ptw` for the auto-rerun TDD loop.
- **Coverage gates:** a global floor plus per-PR **patch coverage** (changed lines must be
  ≥80% covered). Full policy, the fixtures/helpers available, and how to write a test:
  **[docs/testing.md](docs/testing.md)**.
- **Web / Android:** test setup is being established per stack (see `docs/testing.md`).

Contributions should include tests for new/changed behavior — the PR template has the checklist.
