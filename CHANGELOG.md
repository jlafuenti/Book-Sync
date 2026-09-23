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

### Fixed

- A passing words-per-hour plausibility check could still be overruled by the imprecise
  bytes-per-hour check, permanently flagging heavily illustrated ebooks (picture books, comics,
  cookbooks) as `implausible_pair` even though their audio narrates them at a completely normal
  pace (#693). `check_pair_plausibility` now treats a passing word-count verdict as final and
  skips the byte check entirely when a word count is available; the byte check only ever runs on
  its own when no word count could be computed for the ebook, and its message now says so
  explicitly. Trade-off: a pair with a plausible word count is no longer checked by file size at
  all — the word count is trusted outright as the more precise signal, so it can no longer act as
  a second line of defense behind a coincidentally plausible-looking word count. Since that verdict
  is only ever recorded at pair creation, fixing the check alone could not clear a false positive
  already stored on a running server, so "Library verify" (Troubleshoot → Verify library) now also
  re-checks any pair whose stored verdict currently says implausible, and clears it if a freshly
  computed word count now passes; a pair whose stored verdict is already plausible is left alone.
  The added scan phase also exposed a small pre-existing display bug: the Troubleshoot page's
  progress bar guessed the total phase count before the server had reported one, so it briefly
  showed "phase 1 of 3" and then flipped once the real total came back. It no longer guesses — the
  phase total only renders once the server has actually reported it.

- The web reader left open in a tab now catches up with listening done elsewhere (#683, the web
  half of #682). Before, it kept showing the page from before, because it only worked out where to
  open when the book was first opened, and the first page turn from that stale page was saved as
  a reading position over the real, later listening position. Now, when the tab becomes visible
  again, the reader holds its saves, re-reads the position, and, if the audiobook moved on by 30
  seconds or more since the reader last knew, re-opens the book there the same way a fresh open
  would. Costs one position read each time the tab comes back. A page turned in the moment
  between returning to the tab and that read coming back is not saved.

### Upgrade notes

- Run Library verify (Troubleshoot → Verify library) after deploying this change to clear pairs
  that were flagged by file size despite a normal word count (#693).

## [0.5.1] - 2026-09-21

### Changed

- Dependency updates since 0.5.0: the Android reader moves to Readium 3.4 (a reading position saved
  under 3.3 was checked to restore to the identical locator), Media3 1.11.1 and the Android Gradle
  plugin 9.4.1; uvicorn 0.53 and alembic 1.20 on the server; and 12 further minor/patch updates
  across the server, web, Jetson and CI actions (#663, #664, #665, #666, #667, #668).

### Fixed

- Realigning a book no longer makes it look recently read. Clients rank "last read" by a
  position's `captured_at` and fall back to `updated_at` when it is empty, and a realign's
  bookmark remap bumps `updated_at`. Most positions written before `captured_at` existed have it
  empty, so every realigned book jumped to the front of Continue Reading and the phone fetched
  its sync map. Migration `0024_captured_at_backfill` copies `updated_at` into an empty
  `captured_at` on `bookmarks` and `user_progress`; the remap pins the old `updated_at` into
  `captured_at` if it finds one still empty; and the web's Continue Reading now sorts by
  `captured_at`, not `updated_at` (#679).
- The ebook could reopen on the page from before a period of listening — reported from a car,
  where Android Auto resumed the audiobook and disconnecting paused it, and the ebook then opened
  on the pre-drive page instead of where listening had stopped. A locator hint captured while
  reading, with no `audio_position_ms` stamped beside it, skipped the drift check that would
  otherwise have dropped it, and a drive that stayed within one chapter never bumped
  `anchor_revision` either — so the stale hint won the restore, online, offline, and for a reader
  left open across the trip. The restore ladder (server, web, Android) now treats a hint with no
  audio anchor as unusable whenever its record is audiobook-sourced, falling through to the audio
  rung instead. Android also re-anchors a reader that returns from the background once the
  canonical record's audio has moved on past a threshold, and no longer lets a page whose
  sync-point lookup missed inherit an older page's audio time (#682).

### Upgrade notes

- Migration `0024_captured_at_backfill` is a one-time data backfill and runs automatically on
  start. Its downgrade does nothing: the copied values stay, and they are the values clients were
  already using. Books realigned before this deploy keep the realign time as their last-read time.

## [0.5.0] - 2026-09-21

### Added

- `GET /api/sync/positions`: every position the caller has (pair, standalone ebook, standalone
  audiobook), paged (`page`/`limit`, default 100, cap 500, same convention as the library list
  endpoints), in the same shape `GET /api/sync/position/{scope}/{ident}` already returns —
  reuses `to_response_dict` so the two cannot drift. Read-only; `PUT /api/sync/position/{scope}/{ident}`
  remains the only write path. Exists because a fresh sign-in on Android pulled positions with
  one `GET /position/{scope}/{ident}` per pair bookmark plus one per paired audiobook, sequentially
  — several hundred round trips against the single-process server on a library of a few hundred
  pairs, with Home's Continue Reading filling in over minutes. Bounded query cost regardless of
  page size (one count, one page of rows, one apiece for the two eager-loaded relationships).
  Android's `PositionRepository.syncAllBookmarksAndProgress` now pulls this list once at sign-in
  instead of looping per pair, falling back to the old per-pair loop when the server predates this
  endpoint (#653).

### Fixed

- Sync maps no longer drift by hours through the middle of a book. The aligner pins its map to
  "anchors" — sentences found exactly once in the audio — and aligns the text between them. Its
  anchor search fuzzy-scored each EPUB sentence against individual transcript sentences with a
  scorer that rates a short fragment ~100 against any long sentence containing its words, and
  narration is full of fragments like "He said." Two of them far apart always tied, so nearly every
  candidate was thrown out as "ambiguous": a 31-hour book kept **2** anchors, and whole books were
  aligned by proportion between three fixed points. Measured against the real transcripts, maps
  were off by up to **+115 min** (a book where every chapter is narrated, #650) and **−155 min**
  (books whose EPUB carries back matter the audiobook never reads, #648). The search now locates a
  slice of each sentence in the whole transcript and anchors only where it occurs exactly once,
  and searches every sentence near the start and end so anchoring runs right up to where the
  narration stops. On the same books it keeps hundreds of anchors, every scored chapter lands
  within a minute of the narration, and aligning is roughly 40× faster. Realign was previously
  deterministic and reproduced the broken map byte for byte; it now produces a correct one (#648,
  #650).

- The SSRF guard no longer resolves DNS on the event loop. `assert_safe_url` looked the hostname
  up with a synchronous `socket.getaddrinfo()`, and three `async def` handlers called it directly —
  cover fetching during matching (reachable by editors, and run once per redirect hop) and the
  System page's Audiobookshelf and transcription-worker connection tests. One uvicorn worker serves
  every request, so for the length of each lookup nothing else was served: position sync,
  streaming, login and `/api/health` all stalled. Invisible with a resolver that answers in
  milliseconds; seconds long with one that has to time out, and the symptom — unrelated endpoints
  timing out — never pointed at the cause. Those callers now use `assert_safe_url_async`, which
  crosses to a worker thread once. What the guard accepts and rejects is unchanged. A source-guard
  test fails the build if any `async def` calls the synchronous guard again (#673).

- A server restart no longer makes an in-progress **remote** transcription look like it restarted
  from zero. `queue_manager.reset_stale_items()` used to re-stamp `started_at` to the restart
  moment and hand every recovered row back to the pipeline exactly like a brand-new item — which
  re-ran the integrity gates (decoding the whole audio file again on the server, for no reason)
  and briefly published `progress≈0.01`/`0.02` with an early-pipeline message before the worker's
  first post-restart status poll corrected it. None of that was true: the remote worker keeps
  running across a server restart and loses nothing (confirmed in production across four restarts
  of the same 13.5-hour job, which went on to complete normally) — only the server's picture of
  the job was wrong, and it was wrong in the one direction that looks like six hours of GPU time
  destroyed. `reset_stale_items()` now leaves `started_at` alone and flags the row instead
  (`_recovered_at_startup`), so `_process_next_item` treats it like a resume — skipping the
  integrity gates and the startup-sequence progress numbers, and writing "Reattaching to
  transcription after restart..." rather than a message that implies either a restart or a
  checkpointed resume, neither of which is what happened. A genuinely new or genuinely failed item
  is unaffected: the flag is one-shot, consumed the moment the row is claimed (#661).
- A position write that moves `epub_chapter` without carrying an `epub_sentence_index` now clears
  the stored index (and the `sync_map_version` attesting to it) instead of leaving the previous
  chapter's index beside the new chapter. A sentence index is a coordinate *within* a chapter, so
  a chapter change invalidates it; the stored pair is the portable cross-device anchor every client
  restores from, and a mismatched pair resolved to a position the reader was never at — confidently,
  because nothing marked it as suspect. Android's reader sends exactly this shape whenever its
  sync-point lookup misses (#644 fixed the client's own local row; this is the canonical record's
  half, and it also covers app builds predating that fix, which keep sending this shape for as long
  as they are installed). Deliberately narrow: only a write that *states* a different chapter clears
  the index. A write carrying no chapter at all — an audio heartbeat, a completion toggle — still
  leaves every anchor alone, which is what stops background saves eating a real position. Clearing
  the index moves the anchor, so hints captured against the old one correctly stop being served as
  current (#658).

### Upgrade notes

- **Sync maps built before this release keep their drift until they are realigned** — the fix is in
  how a map is built, not in the maps already stored. After deploying, realign the affected pairs;
  the sync-map audit's timing check flags them. Realigning bumps the map's version, which marks
  readers' saved positions for re-anchoring against the new map (#55, #116), so pairs with active
  readers are worth doing deliberately rather than in one sweep. Pairs aligned for the first time
  after deploying need nothing (#648, #650).

- The bulk positions endpoint (#653) needs no action. An app build that predates this release keeps
  working against a server that has it (it simply never calls the new endpoint), and an app build that has it falls back to the
  old per-pair loop against a server that predates it.

## [0.4.2] - 2026-09-18

### Fixed

- `_filter_chunked_segment_drift` (0.4.0, #620) demoted nearly every matched point in a large
  anchor-bounded gap instead of just the genuinely displaced ones, making realign strictly worse
  than doing nothing on exactly the pairs it was meant to repair — production re-realigns of the
  12 affected pairs all came back with fewer matched points and *more* audit timing mismatches
  than before (one pair: 12,268 matched -> 252; 8/74 mismatches -> 62/74). Root cause: the filter
  judges every point in a segment against one straight line drawn between its two bracketing
  anchors, but that line is only accurate when the epub:whisper density is uniform across the
  *whole* segment — and a segment large enough to need chunked alignment in the first place (up
  to a whole book, on the sparse-anchor fallback) is exactly where it is least likely to be. An
  ordinary pacing difference between two large stretches of a real book was then enough to put
  correct matches on the far side of the difference outside tolerance for the rest of the
  segment, and because they were all off in the same direction the run-length check did nothing
  to contain it. `CHUNK_DRIFT_MAX_DEMOTION_FRACTION` (30%) bounds the damage: if applying the
  filter would demote more than that share of a segment's matches, the reference itself is the
  more likely thing that's wrong, and the segment's chunked output is now left untouched instead
  (#635).
- Android: **Report a problem** attaches the Android Auto diagnostics log as well as the app's,
  when each exists, and the message names which logs it carries — plus how to switch on
  whichever is missing. A report about the car used to arrive with none of the evidence about
  the car (#636).

### Upgrade notes

- The 12 pairs from #635's production re-audit (and any others the sync-map audit flags
  `status: "realign"`) need to be realigned again after this deploys — the 0.4.1 realign that hit
  this bug left them with fewer matched sync points than before, and only a fresh realign under
  the fix restores them.
- This narrows what #620's chunked-drift fix (0.4.0) actually corrects: a genuinely displaced run
  that is a small minority of a large gap's points is still caught and repaired, but a gap whose
  matches are *mostly* correct and simply spread across a non-uniform pace is now left alone
  rather than being "corrected" into a straight-line interpolation that #635 showed is typically a
  worse estimate than the original chunked output. A smarter, locally-scoped reference (instead of
  one chord per segment) would let the filter safely re-cover that case; not attempted here in the
  interest of shipping the regression fix first.

## [0.4.1] - 2026-09-18

Android only. Nothing in the server or the web app changed since 0.4.0, so a deployment has
nothing to do. Android normally carries its notes in Play rather than here; this one is listed
because it repairs something 0.4.0 shipped broken.

### Fixed

- The reader's **Define** worked for no word at all in 0.4.0. The dictionary lookup moved to
  Wiktionary in that release, but the app identified itself to Wikimedia only by the HTTP
  library's default name, which Wikimedia's robot policy refuses with 403 — and the fallback to
  the previous dictionary ran only when a word was genuinely absent, so every lookup ended as
  "No definition available". The app now identifies itself properly (its name, version, site and
  support address — nothing about the device or the reader), and any failure of the first source
  falls back to the second. A lookup reports failure only when both sources fail.

## [0.4.0] - 2026-09-17

### Added

- `implausible_pair` (`GET /api/troubleshoot/library`) now also checks words-per-hour, not just
  bytes-per-hour: a real unabridged reading lands around 8,000-12,000 words/hour, and file size
  alone can miss an abridgement or excerpt whose EPUB happens to carry heavy images/fonts (two
  real pairs — an abridgement at ~29.3k words/h and a one-hour excerpt at ~80.8k words/h — slipped
  past the byte-based check for exactly that reason) (#620). The same check now runs *before*
  `auto_match_books` creates a pairing, not only after — a word-rate-implausible pair is no longer
  auto-matched (and, with auto-transcribe on, queued) in the first place; a manual pairing still
  only warns, unchanged from before.

### Changed

- Android app 0.4.0 (versionCode 400) ships a guided walkthrough: a five-minute tour over the
  real screens — Home, a book's menu and details, the sync map, the reader and the player
  (including hopping between them at a sentence), Library, Downloaded and Account — where the
  user taps the real controls, can quit at any time, and can replay it from Account → Help. It
  is offered once after the first sign-in. The book it opens is put back the way it was when
  the tour ends. The reader's selection-toolbar "Sync to Audio" now also works while
  streaming, not only with a downloaded audiobook (#597).
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
- Alignment's degraded-map classifier (#586/#595) no longer fires on a tiny anchor pool: with very
  few anchors surviving the outlier filter (2-25 raw, seen in production), the local trend a
  rejected run is judged against is too sparse to trust, and a small book's ordinary fuzzy-match
  noise could look exactly like a genuine reordered block. `MIN_KEPT_ANCHORS_FOR_DEGRADED` (20)
  gates the fraction/run rule on enough surviving anchors first; the sync-map audit
  (`GET /api/troubleshoot/sync-map-audit`) separately lets a clean, well-sampled timing check
  (`timing_status == "ok"` over 20+ checked points) override a leftover alignment-time flag instead
  of compounding with it (#620).
- Alignment could drift smoothly for hundreds of sentences (up to hours, on a real book) and then
  recover, inside a single large anchor-bounded gap whose local epub:whisper sentence-count ratio
  varies (footnotes, chapter headings or other content present in the epub but never spoken,
  clustered unevenly) — `_chunk_align`'s fixed-size windows assume one ratio for the whole gap, and
  a wrong-sized window can force a *confident* match to the wrong audio, with the error carrying
  into the next window until the true content reappears in range. `_filter_chunked_segment_drift`
  checks chunked output against the gap's own two bracketing anchors and demotes a sustained
  displaced run back to unmatched so it re-interpolates instead (#620).

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
