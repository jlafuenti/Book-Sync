# Changelog

All notable changes to Tandem are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project aims at
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

One version number covers the whole repository — server, web and Android move together. It is
written out in three files by hand; [docs/releasing.md](docs/releasing.md) says which, and a test
fails if they drift apart. `API_VERSION` in [`server/version.py`](server/version.py) is a
*different* number with its own rule: it is the client-compatibility contract and moves only on a
breaking API change.

**Upgrade notes** belong in the version's own section, under an `### Upgrade notes` heading —
irreversible migrations, removed endpoints, a minimum app version for a given server, anything an
operator must do by hand rather than read about afterwards.

## [Unreleased]

### Upgrade notes

- **Secrets move out of compose `environment:` and into files.** An existing install has to create
  `secrets/jwt_secret_key`, `secrets/postgres_password` and `secrets/credential_enc_keys` holding
  **the values it already uses** — not new ones — and switch the compose file to the `*_FILE`
  variables. A regenerated `credential_enc_keys` makes every stored import-source credential
  undecryptable; a regenerated `jwt_secret_key` signs every device out. The step-by-step migration
  (copying the values without printing them), the verification and the rollback are in
  [docs/operations.md](docs/operations.md#secrets). The plain-`environment:` path still works if
  you prefer it (#180).

### Added

- Security headers at the edge: `Caddyfile.example` is now the complete internet-facing proxy
  recipe — TLS, HSTS, `nosniff`, frame denial, a minimal `Permissions-Policy`, no version
  advertising, and a Content-Security-Policy derived from the web sources, shipped Report-Only
  first. The web nginx hides its version and repeats the cheap headers for deployments without
  Caddy; tests pin the header set and the inline-script hash (#178).
- API-version handshake: the server reports `app_version`/`api_version` from `/api/health`, and the
  Android app compares it against the version it was built for and shows a banner on a mismatch
  (#349).
- Android: first-run setup screen, Request Access flow, and externalised string resources (#342).
- Android: audio streams when a book is not downloaded, and the ebook auto-downloads on first open
  (#361).
- Per-device sign-out — one device's logout no longer ends every other session — plus in-place
  transcript replacement for re-transcribes (#362).
- Retry for failed queue jobs, series-level Mark Complete / Reset, and a pending-registration badge
  for admins (#356).
- Audit-log retention setting with background pruning, and a cap on stored failed-login detail
  (#347).
- Web: a switch-to-ebook control in the mini-player, and batched media tokens so a page of covers
  costs one request (#352).
- Web: URL-driven Library search on mobile, with a clear control (#344).
- Repo: this changelog, a release runbook ([docs/releasing.md](docs/releasing.md)), and a
  public-facing README top — what Tandem is, what it needs, its status and its limitations.

### Changed

- The server refuses to boot when the environment asks for more than one worker: the transcription
  queue, cancellation state and the import/backup schedulers are single-process by construction
  (#357).
- Transcription: exponential retry ladder, non-destructive chapter repair, and throttled progress
  writes (#355).
- NLTK data is baked into the image with a fixed `NLTK_DATA`, and `file_path`/foreign-key indexes
  were added (#343).
- Android: the player service is the sole owner of pause-position writes (#339), and NEW/
  acknowledged state syncs with the server (#341).
- Web: route-level code splitting and a separate epub.js vendor chunk; two dead pages removed
  (#358). Defined CSS tokens, scrollable admin tables, mobile card actions, and a pinned manifest
  `start_url` (#350).
- Repo: compose-template drift fixed, `docs/README.md` added as the single docs index, stale Jetson
  documentation and shipped implementation plans removed (#348).
- Tests: golden vectors for auto-matching and the real pipeline in the queue-manager tests (#353);
  Android test backfill with the Kover floor raised to 40 (#337).

### Fixed

- Server: future `captured_at` values are clamped, auto-transcribe is queued in the request
  session, and user deletion cascades instead of leaving orphan rows (#340).
- Server: the integrity gate reports what it actually checked, the remote transcription timeout is
  honoured, and unattributed sync hints are no longer written (#346).
- Web: handoff resume rewind, visible stream failures instead of a silent stall, and source-driven
  pair routing (#351).

### Security

- Secrets are read from files instead of the container environment. `JWT_SECRET_KEY`,
  `CREDENTIAL_ENC_KEYS` and the Postgres password were visible to `docker inspect` and in
  `/proc/1/environ`; every secret setting now also accepts `<NAME>_FILE`, which wins over the plain
  variable and is a startup error rather than a silent fallback when the file is missing,
  unreadable or empty. The compose template ships file-backed `secrets:`, and the server assembles
  `DATABASE_URL` from the Postgres password so one secret feeds both containers (#180).
- Editor-only pair actions are gated by role, and the two overlapping delete controls collapsed
  into one (#360).
- Allow-listed GHSA-6gmq-8vp8-gcm6 (xmldom, via epubjs) so the web audit gate reflects a real
  decision rather than blocking every merge (#345).

### Removed

- `SERVER_HOST` / `SERVER_PORT` settings. Nothing outside `server/config.py` ever read them — the
  listen address is fixed on uvicorn's command line in `server/entrypoint.sh` — so they advertised
  a knob that did nothing (#182).

## [0.1.0]

The version string the code has carried since the beginning, and the number the first tag will
publish. **No `v0.1.0` tag exists yet**; when it is cut, the Unreleased section above folds into
this one with its date. Everything from the start of the project up to the entries above is
covered by it: the transcription queue and alignment engine, the sentence-level position contract,
the web app and PWA, the Android reader/player, multi-user accounts and roles, library scanning
and auto-pairing, the import sources, and the backup/restore system.
