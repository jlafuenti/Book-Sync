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

### Changed

- Widened the rule that un-finishes a book when its position moves back out of the end stretch
  (#584): it used to clear `is_completed` only on the *transition* out of the end zone, so a book
  already sitting mid-book when it was marked finished — for example, re-listened from the middle
  on a build that predated that rule — had both its before and after positions outside the zone on
  the next write, and could never un-finish. Now any write that *moves* the stored position (the
  new value differs from what's stored) to somewhere outside the end zone clears the flag,
  regardless of which side of the boundary the previous position was already on. A write that
  doesn't move the position — a heartbeat, or a resend of the same value — still never clears it,
  which is what keeps a manual "mark finished" sticking. Applies to both the server rule (every
  client inherits it) and Android's local mirror for standalone books (#613).

## [0.3.0] - 2026-09-17

### Added

- The sync-map audit (`GET /api/troubleshoot/sync-map-audit`) checks timings as well as text. For a
  sample of 80 sync points it finds each sentence in the cached transcript and compares
  timestamps, and flags the pair when at least 10% of the located points are more than 2 minutes
  off *and* at least 3 of those are consecutive. That is the signature of an audiobook whose
  narration is reordered relative to the ebook, which the hash and text checks never caught.
  Flagged pairs report `status: "degraded"` with the suggested action `check_audio_order`:
  re-aligning cannot fix reordered audio, the file has to be corrected first. The web Troubleshoot
  page has a new on-demand "Sync-Map Audit" section (#586).
- Alignment marks a sync map as degraded (`sync_maps.degraded`, `degraded_reason`) when the anchor
  filter throws away a large, contiguous, consistently displaced run of matches — a reordered
  block that used to be papered over with interpolated timestamps. Scattered rejections from
  ordinary noise do not trip it (#586).

### Changed

- A finished book is un-finished when its position moves back out of the end stretch — the mirror
  of the rule that finishes it on entering. Re-listening or re-reading from the middle used to
  leave the book off every Continue Listening / Continue Reading list for good. Rewinding from the
  final minute to replay the ending now also un-finishes the book until it reaches the end again.
  The server applies the rule for every client; Android also applies it locally to standalone
  books so they reappear straight away (#584).
- The sync-map audit tells a map problem from an audio problem. When the cached transcript is in
  book order, a flagged pair now gets `suggested_action: "realign"` instead of
  `check_audio_order`, and the reason names the affected chapters. `check_audio_order` stays for
  transcripts that really are out of order, or when there is no transcript to check (#595).

### Fixed

- The transcript cache no longer trusts the audiobook's path alone. A transcript now records the
  fingerprint (file hash and duration) of the audio it was made from, and is reused only when that
  still matches. Tandem's own tag write-backs move the fingerprint forward, so they never look like
  a new file (#588).
- A library scan, rescan-all or single rescan notices when the file at a known audiobook path has
  changed. A duration change of more than 2 seconds is treated as a replaced recording: the cached
  transcript is dropped and a synced pair goes back to `manual_matched`, exactly as
  `POST /api/troubleshoot/replace/audiobook/{id}` already did. A changed hash with the same
  duration is treated as a tag edit made outside Tandem (Audiobookshelf, a tag editor): the stored
  hash and size are refreshed and the transcript is kept. Reading positions are untouched either
  way (#588).
- A transcript with no fingerprint yet is checked against the file's current duration before it is
  reused; one that ends well short of the file or runs past its end is re-transcribed instead of
  being trusted (#588).
- Android: Define and Sync to Audio read the selection from the page on screen. After moving to an
  adjacent chapter they used to read a neighbouring chapter's page, giving "Select a word to
  define", a definition for an earlier word, or a "select more text" error. Switch to Audio reads
  the visible chapter for the same reason. Curly apostrophes, possessives and invisible characters
  are cleaned out of a word before it is looked up (#582).
- Android Auto's Continue Listening and Library refresh while they are on screen, instead of
  showing a stale list until the app is reopened (#583).

- Alignment no longer lets one wrong match far ahead survive and drag the surrounding stretch of
  the map toward it — the "exact, then hours off, decaying back to exact" shape. Each kept match
  is judged against the trend its neighbours imply and outliers are dropped. On a real affected
  22-hour book the drift of up to 4 hours disappeared and more sentences matched directly, at
  about 1.3x the alignment time (#595).
- The sync-map audit flags maps whose chapter order disagrees with the EPUB's spine — maps built
  when chapters were ordered by file name (`part1, part10, part2 …`) — and suggests realigning
  them (#595).
- Android: a guided walkthrough introduces every screen, spotlighting the real control and having
  you tap through the app yourself, including the hop from a sentence to the matching moment in
  the audiobook. It can be quit at any time and replayed from Account (#597).

### Upgrade notes

- Two migrations run on start-up. `0022_sync_map_degraded` adds `sync_maps.degraded` (existing
  maps default to false) and `degraded_reason`. `0023_audio_fingerprint` adds the transcript
  fingerprint columns, empty for existing transcripts; they are filled in the next time each
  transcript is used, so nothing is re-transcribed on deploy.
- Existing sync maps are not re-examined automatically. Run **Sync-Map Audit** on the Troubleshoot
  page once. Pairs marked `realign` are fixed with **Realign** from their cached transcript, with
  no re-transcription; only `check_audio_order` needs the audio file itself looked at. Existing
  maps are not realigned automatically.

## [0.2.3] - 2026-09-16

Android only. Nothing in the server or the web app changed, so a deployment has nothing to do —
this release exists to ship the Android build. Android normally carries its notes in Play rather
than here (see the note at the top of this file); they are listed because a version with an empty
section reads like an oversight.

### Fixed

- Android Auto refuses to browse, search or play anything while no account is signed in. The
  browse tree is built from a local cache that signing out deliberately keeps, so a head unit went
  on listing the previous account's library and playing its downloaded books (#573).
- The local library cache is cleared when it belongs to a different server, including the
  downloaded files and cached covers, which were keyed by id alone and could show the wrong book's
  artwork after a switch. An account change on the same server keeps both, because a Tandem
  server's library is shared by its users (#575).
- Android Auto's Continue Listening is ordered by when each book was last played, across paired
  and standalone books, and the cap is applied after that ordering rather than before. The voice
  query with no title shared the bug and is fixed with it (#574).
- The phone's own media controls show cover art again: the artwork is published as image data
  rather than a file link the system UI has no permission to open (#570).

## [0.2.2] - 2026-09-15

### Fixed

- A cleared or changed audiobook description now sticks through the next library scan for every
  format. Clearing an M4B's description also removes its `©des` atom, which the scanner reads
  first, and a new description is written there too, so a stale `©des` no longer outranks it.
  Clearing a FLAC or Ogg description also removes `summary`, and a new MP3 description replaces
  any comment frame a ripper left behind rather than sitting beside it (#538).
- The book being transcribed appears once, not twice, under In Progress on the phone-width
  Transcription page, and its card shows the queue's live progress (#562).
- ACSM imports of Adobe-DRM books that also carry obfuscated fonts work again. The DeDRM plugin
  release the image pins (10.0.3) crashes on those books — it rewrites `META-INF/encryption.xml`
  for the leftover font entries from a value it never stored — and the import failed with a bare
  "ACSM conversion failed (exit 6)". The DRM image build now applies upstream's two-line fix, the
  same way it already patches DeACSM, and fails the build if the patch stops applying. A
  decryption failure also reports itself as one, naming the exception, instead of reading like an
  expired loan or a missing Adobe authorization (#566).
- EPUBs whose chapters are XHTML files without an `.xhtml` or `.html` name extract their text. The
  server picked content documents by file extension, but EPUB identifies them by the manifest
  media-type, and some publishers name them `chapter01.xml` or give them no extension at all. A
  book built that way throughout extracted no text, failed transcription at the integrity check and
  was listed under DRM in Troubleshoot; a book built that way in part got a sync map missing those
  chapters. Chapters that were already read keep their chapter and sentence numbers (#561).
- EPUBs that only obfuscate their embedded fonts are no longer reported as DRM-encrypted or refused
  for transcription. Every `META-INF/encryption.xml` uses the XML encryption vocabulary, and the
  check treated any such file as Adobe ADEPT, but font obfuscation leaves the text readable in
  every reader. Calibre writes these files when it converts a book with embedded fonts, so books
  from Tandem's own Convert flow were affected. A book is now DRM-encrypted only when the manifest
  encrypts something other than fonts. The ACSM import's post-decryption check uses the same
  rule (#560).

### Upgrade notes

- Migration `0020` deletes the cached "ebook produced almost no text" integrity failures again: a
  Library verify run after 0.2.1 re-cached them for books this bug still broke. A pair already
  synced from an EPUB that was only partly affected is missing those chapters until you realign it.
- Migration `0021` deletes the cached "EPUB is DRM-encrypted" integrity failures; books that really
  are encrypted fail again when re-checked.
- After upgrading, run Library verify once, then retry any transcription that was refused as
  DRM-encrypted or for "almost no text".
- The DeDRM fix lives in the image, so a deployment that imports ACSM files must **rebuild** with
  `docker compose build --build-arg INSTALL_DRM_PLUGINS=1` rather than only restarting; pulling a
  published image is enough only if it was built with that flag. Re-try any ACSM loan that failed
  with "ACSM conversion failed (exit 6)". The default image is unchanged: it still ships no DRM
  tooling.

## [0.2.1] - 2026-09-15

### Fixed

- EPUBs whose content files have spaces (or other escaped characters) in their names extract their
  text again. The server read each manifest href as a literal file name, but hrefs are URLs
  (`Text/Axis%20Test_1.html`), so those chapters came out empty. A book named that way throughout
  failed transcription at the integrity check and was listed under DRM in Troubleshoot. A book
  named that way only in part got a sync map missing those chapters (#554).

### Upgrade notes

- Migration `0019` deletes the cached "ebook produced almost no text" integrity failures, which
  this bug caused. Run Library verify once after upgrading to re-check those books.
- A transcription that failed with "Ebook failed integrity check — ebook produced almost no text"
  should be retried; it runs normally now.
- A pair already synced from an affected EPUB is missing the chapters that read as empty. Realign
  it (`POST /api/transcription/{pair_id}/realign`, or Realign on the pair) to add them. Existing
  positions and sync points keep their coordinates.

## [0.2.0] - 2026-09-14

### Added

- The System page says when the transcription worker is behind the server. The Jetson worker
  now reports the release it was built from (`worker_version` on `/v1/health`, the fourth of
  the version literals a release bumps together); the server asks its configured worker every
  few hours and on every change of the worker URL, and the Updates card shows "The transcription
  worker is on X — this server is Y" with the rebuild steps. A worker that predates the field is
  reported as not reporting a version, never as current. This is a call to the operator's own
  worker and is not gated by the GitHub update-check toggle.

### Changed

- Password hashing calls the `bcrypt` module directly instead of going through `passlib`,
  whose 1.7.4 backend self-test raises under bcrypt 5. Existing `$2b$` hashes and the
  72-byte password truncation both keep working unchanged (#515).
- The web image builds on Node 22 (`node:22-alpine`); Node 20 reached end of life in April 2026.
  CI tests the web app on the same Node line, and Dependabot now watches the three Dockerfiles
  and the compose templates so a base image cannot age out unnoticed again.
- A release tag publishes versioned images: `ghcr.io/jlafuenti/tandem-server:X.Y.Z` and
  `tandem-web:X.Y.Z`, plus `latest`. Pulling them is now a documented deploy route beside
  building from source (`docs/releasing.md`).
- Dependency updates since 0.1.0, all via Dependabot: numpy 2 (`>=2.5.3`), bcrypt 4.3.0,
  psycopg2-binary 2.9.13, audible-cli 0.6, tzdata 2026.3, torch `>=2.14` and tqdm `>=4.70`
  (server); mutagen 1.48 and python-multipart 0.0.32 (Jetson worker); jsdom 30 (web, dev
  only); Compose BOM 2026.09 and six minor/patch Android libraries; and the GitHub Actions
  majors `actions/checkout` 7, `actions/upload-artifact` 7, `gradle/actions/setup-gradle` 6
  plus `actions/setup-java` 6.0.1. Held back with issues: vite 8 / `@vitejs/plugin-react` 6
  (#516), pytest 9 (#517).
- Android app 0.2.0 (versionCode 200) ships with this release. Opening or switching a pair
  whose transcription has not finished now says so — not transcribed, in the queue at
  position n, transcribing, or failed — with "Open anyway" always available and a Transcribe
  button for editors; the sync map downloads on its own once transcription finishes, while
  the app is open. The app also maps the server's real transcription status values, so
  "transcribing" and "error" no longer display as "not transcribed" (#535, #536, #537).

### Fixed

- Database writes are committed before the response is sent. Under the pinned FastAPI the
  session dependency's commit had been running *after* the response, so a client that read
  straight after a write (the web app refreshing a list after an unpair, a phone re-syncing)
  saw the old row, and a failed commit was reported as success. Every route now declares the
  session with `scope="function"`, pinned by a test that walks the whole app (#524).
- `normalize_author` no longer treats every comma as a `Last, First` separator. A co-author
  list (`Ann Axis, Bob Bartleby`), a name with a suffix (`Ann Axis, Jr.`), an author with a
  narrator tacked on, or a value with two or more commas is now left as-is instead of being
  swapped into a fabricated name. This ran on every ingest and on `POST
  /api/library/normalize`, so a rescan or a library-wide normalize used to re-mangle values
  that had already been corrected by hand (issue #514).
- The library scan no longer descends into hidden (dot-prefixed) directories and files, or the
  Synology `@eaDir`/`#recycle` system folders — a hand-made `.recyclebin/`, a macOS `._*`
  resource fork or a hidden `.unimported-*` copy parked beside a real file could otherwise be
  imported as its own book row. The multi-file audiobook detector and the targeted (post-upload)
  scan use the same filter, so a hidden track can no longer make a normal folder look like a
  multi-file audiobook either.
- `PATCH /api/library/ebooks/{id}`, `PATCH /api/library/audiobooks/{id}` and
  `POST /api/library/pairs/{id}/resolve-discrepancies` now write metadata back to the EPUB or
  audio file in a worker thread (`asyncio.to_thread`) instead of blocking the event loop, so
  editing a book's metadata no longer stalls unrelated requests for the length of the file
  rewrite.
- Clearing an ebook's series (`PATCH /api/library/ebooks/{id}` with `{"series": ""}`) now
  actually sticks. `write_ebook_metadata` only removed the `calibre:series` /
  `calibre:series_index` OPF metas when a series was being *set*, so an empty series left the old
  tags in the file and the next library scan's fill-empty-fields step read them straight back
  onto the row. The EPUB writer now matches the audio writer's existing behavior for an empty
  string: delete both metas rather than leaving them untouched.
- Editing a paired ebook's metadata no longer marks its sync map `stale` in the drift audit.
  `PATCH /api/library/ebooks/{id}` and the ebook side of
  `POST /api/library/pairs/{id}/resolve-discrepancies` rewrite the EPUB's OPF in place, which
  changes the whole-file hash the audit compares even though the book's text is untouched; both
  now recompute and store the file's hash right after a successful write-back, on the ebook's own
  `file_hash` and on its sync map's recorded provenance hash. The audiobook equivalents refresh
  the audiobook's own `file_hash` the same way. A file replaced by something other than the
  server is unaffected and is still reported as drifted (#533).
- Clearing an audiobook's description now also survives the next library scan. `extract_metadata`
  falls back to a comment-shaped tag when no description tag is present — ID3 `COMM` frames and
  `TXXX:comment`, or MP4 `©cmt` — but `write_audiobook_metadata` only cleared the description
  tags, so a description that came from one of those was read straight back in on the next scan.
  The audio writer now removes the comment tags too when the description is cleared; writing a
  real description leaves them untouched (#538).

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
