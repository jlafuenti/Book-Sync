# Changelog

All notable changes to Tandem are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project aims at
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

One version number covers the whole repository — server, web and Android move together. It is
written out in four files by hand; [docs/releasing.md](docs/releasing.md) says which, and a test
fails if they drift apart. `API_VERSION` in [`server/version.py`](server/version.py) is a
*different* number with its own rule: it is the client-compatibility contract and moves only on a
breaking API change.

**Upgrade notes** belong in the version's own section, under an `### Upgrade notes` heading —
irreversible migrations, removed endpoints, a minimum app version for a given server, anything an
operator must do by hand rather than read about afterwards.

## [Unreleased]

### Added

- The System page says when the transcription worker is behind the server. The Jetson worker
  now reports the release it was built from (`worker_version` on `/v1/health`, the fourth of
  the version literals a release bumps together); the server asks its configured worker every
  few hours and on every change of the worker URL, and the Updates card shows "The transcription
  worker is on X — this server is Y" with the rebuild steps. A worker that predates the field is
  reported as not reporting a version, never as current. This is a call to the operator's own
  worker and is not gated by the GitHub update-check toggle.

### Changed

- The web image builds on Node 22 (`node:22-alpine`); Node 20 reached end of life in April 2026.
  CI tests the web app on the same Node line, and Dependabot now watches the three Dockerfiles
  and the compose templates so a base image cannot age out unnoticed again.
- A release tag publishes versioned images: `ghcr.io/jlafuenti/tandem-server:X.Y.Z` and
  `tandem-web:X.Y.Z`, plus `latest`. Pulling them is now a documented deploy route beside
  building from source (`docs/releasing.md`).

### Fixed

- The library scan no longer descends into hidden (dot-prefixed) directories and files, or the
  Synology `@eaDir`/`#recycle` system folders — a hand-made `.recyclebin/`, a macOS `._*`
  resource fork or a hidden `.unimported-*` copy parked beside a real file could otherwise be
  imported as its own book row. The multi-file audiobook detector and the targeted (post-upload)
  scan use the same filter, so a hidden track can no longer make a normal folder look like a
  multi-file audiobook either.
- Clearing an ebook's series (`PATCH /api/library/ebooks/{id}` with `{"series": ""}`) now
  actually sticks. `write_ebook_metadata` only removed the `calibre:series` /
  `calibre:series_index` OPF metas when a series was being *set*, so an empty series left the old
  tags in the file and the next library scan's fill-empty-fields step read them straight back
  onto the row. The EPUB writer now matches the audio writer's existing behavior for an empty
  string: delete both metas rather than leaving them untouched.

## [0.1.0] - 2026-09-13

The first tagged release. `0.1.0` is the version string the code has carried since the beginning,
and everything from the start of the project is covered by it: the transcription queue and
alignment engine, the sentence-level position contract, the web app and PWA, the Android
reader/player, multi-user accounts and roles, library scanning and auto-pairing, the import
sources, and the backup/restore system. The entries below record what changed from the point this
changelog was introduced.

### Upgrade notes

- **The update check is off until an admin turns it on.** After upgrading, the System page asks
  "Check for updates automatically?". Enabling it makes the server ask `api.github.com` for the
  latest Tandem release every few hours; GitHub sees the server's address and nothing else is sent.
  It can be changed later under System → Updates (#463).
- **Secrets move out of compose `environment:` and into files.** An existing install has to create
  `secrets/jwt_secret_key`, `secrets/postgres_password` and `secrets/credential_enc_keys` holding
  **the values it already uses** — not new ones — and switch the compose file to the `*_FILE`
  variables. A regenerated `credential_enc_keys` makes every stored import-source credential
  undecryptable; a regenerated `jwt_secret_key` signs every device out. The step-by-step migration
  (copying the values without printing them), the verification and the rollback are in
  [docs/operations.md](docs/operations.md#secrets). The plain-`environment:` path still works if
  you prefer it (#180).

### Added

- Update check: the System page says when a newer Tandem release is published — "Tandem X.Y.Z is
  available", with a link to the release notes and the upgrade steps. It compares the running
  `APP_VERSION` with GitHub's latest *release* using semantic versioning, the way `docs/releasing.md`
  already defines releases, so it announces deliberate releases rather than every commit. Opt-in,
  asked once on the System page; notify-only, since updating from inside the app would need the
  Docker socket (#463).
- Troubleshoot: a pair whose audio cannot plausibly cover its ebook — two minutes of audio against
  a full-length novel, say — is flagged instead of matching, transcribing and reaching `synced`
  with a meaningless sync map. Judged from the EPUB's file size against the audio's duration, with a
  deliberately wide band, because nothing stores a book's word count (#458).
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
- Web: React 19 (#453).
- The shared "which format does a tap open" rule (`web/src/lib/pairOpenTarget.js`, mirrored on
  Android) separates a format that can be *opened* from one already *on the device*, so a book that
  was streamed resumes in the player. The web passes neither new field and behaves exactly as
  before (#486).
- Dependency updates from Dependabot: on the server, a group of 16 minor and patch updates and
  `aiofiles` 25.1, plus `openai-whisper` 20250625 for the opt-in local-Whisper image and the dev-only
  `aiosqlite` 0.22; on the web, `react-markdown` 10.1 — whose escaping the no-raw-HTML rule relies
  on, pinned by `BookDetailsPage.test.jsx` — and test tooling (#394, #395, #397, #406, #407, #408,
  #429, #442).
- Repo: a PR that changes `server/` or `web/` must now add an entry here, enforced by a `changelog`
  CI check; the version itself moves only when a release is cut. `CONTRIBUTING.md` now describes
  the branch ruleset actually in force.
- Tests: golden vectors for auto-matching and the real pipeline in the queue-manager tests (#353);
  Android test backfill with the Kover floor raised to 40 (#337); `PlayerViewModel`'s restore,
  poll loop and download mirror and the interceptor edges are driven directly, floor raised to
  56 (#217).

### Fixed

- Server: future `captured_at` values are clamped, auto-transcribe is queued in the request
  session, and user deletion cascades instead of leaving orphan rows (#340).
- Server: the integrity gate reports what it actually checked, the remote transcription timeout is
  honoured, and unattributed sync hints are no longer written (#346).
- Position restore: when the audiobook is the format being listened to, its position is tried before
  the ebook's chapter, text and percentage — which only a reader save refreshes — so a book listened
  to for hours no longer reopens at a page last read long before (#479).
- Web: the audio player clears its seek-flush and sleep timers when it unmounts, so neither can fire
  into a player that has gone (#469).
- EPUB write-back works again: tag editing takes its XML tree builders from the standard library
  (#428).
- ACSM import: an unreadable home directory — `/root` under a non-root container — counts as having
  no Calibre configuration instead of failing the import (#443).
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
- The reader's XML parser is patched: an npm override lifts `@xmldom/xmldom` to 0.8.15, fixing the
  serialization advisories reached through epub.js 0.3.x, and the audit allow-list that had been
  ignoring them is removed, so dropping the override now fails CI instead of passing silently.
  epub.js stays on 0.3.93 — 0.4.2 depends on the abandoned unscoped `xmldom`, which has a critical
  advisory and no fixed version (#345, #447).

### Removed

- `SERVER_HOST` / `SERVER_PORT` settings. Nothing outside `server/config.py` ever read them — the
  listen address is fixed on uvicorn's command line in `server/entrypoint.sh` — so they advertised
  a knob that did nothing (#182).
