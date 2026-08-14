# Handoff — cross-device reading position

Branch: `claude/book-sync-issues-61-40-6944a0`
Deployed on the server as of writing: `969eed1` (schema at `0004_canonical_position`)
Status: **§4 diagnosed and fixed on-branch (2026-07-31; see §4 for the real root
cause — it was not the gate). Known issues 2 and 6 fixed in the same pass.**

---

## 1. What we set out to fix

GitHub #61 — the Android reader never wrote ebook `UserProgress`, so phone
reading was invisible in the web Home/Continue lists.
GitHub #40 — the exact ebook position didn't hand off between devices.

Both were treated as isolated bugs. The first fix attempt **caused production
data loss**: a real position (chapter 39, 46.8%) was overwritten with chapter 0.
That forced a redesign, which is what this branch actually contains.

### The three root causes underneath

**a. Two competing position records.** `bookmarks` and `user_progress` both
stored an ebook chapter and an audio position. The Android reader restored from
`bookmarks`; the web reader restored from `user_progress.epub_cfi`. Every save
was *two independent PUTs* with two independent staleness verdicts, so one could
be accepted while the other was rejected — leaving two records describing
different positions for the same book, with nothing to reconcile them.

**b. Two chapter coordinate systems.** `server/services/epub_parser.py` built its
own chapter ordinal — an index into *manifest* documents that produced sentences
— while both readers position by **spine index**. The two axes drift whenever a
document produces no sentences (cover pages, blank pages). Both clients also
wrote *both* axes into the same `epub_chapter` column depending on whether the
text matcher hit.

**c. Deleting a hint to express staleness.** The first fix made the server clear
`epub_locator` when an ebook write moved the anchor without supplying one. The
Android reader read "no locator" as "no position", opened at the title page, and
its 5-second autosave persisted chapter 0 over the real position. Two failures
compounding: a restore that resolved to nothing, and a save path with nothing
stopping it writing that nothing.

---

## 2. What changed

### Server

- **`bookmarks` is now the one canonical position record.** New columns:
  `epub_text_preview`, `epub_progress_percent`, `is_completed`,
  `anchor_revision`, plus `ebook_id`/`audiobook_id` scopes with `book_pair_id`
  nullable so standalone media gets a record. Three partial unique indexes give
  one row per user per book.
- **`user_progress` is now a projection**, written in the same transaction. It
  still backs the Home/Continue lists. Clients never write it directly.
- **`position_hints`** — one row per `(bookmark, device, kind)`;
  `readium_locator` (Android) or `epubjs_cfi` (web). A hint is *current* iff
  `hint.anchor_revision == bookmark.anchor_revision`. **Hints are never deleted.**
  A stale hint stops being current and becomes current again when its device
  re-captures. `anchor_revision` bumps on chapter/sentence/percent changes but
  **not** on audio movement alone.
- **`GET`/`PUT /api/sync/position/{scope}/{id}`** — one request, one staleness
  verdict, one transaction. A stale write returns 409 and changes *nothing*.
  `GET` returns 204 for "never opened" and never creates a row.
- **Legacy `PUT /sync/bookmark/{pair}` and `/sync/progress/{type}/{id}` are
  gone** (issue #102). They survived for a while as thin adapters over the same
  service so old app builds converged on the same record; every client now
  writes the canonical endpoint directly.
- **Parser emits true spine indices**; the zip/OPF spine walk is now primary and
  ebooklib the fallback (both spine-based). Migration 0004 re-bases existing sync
  points.
- **Duplicate-tolerant reads** (`latest_progress_row`) — `user_progress` has no
  unique constraint and an old `GET` created rows with flush-but-no-commit, so
  duplicates exist. `scalar_one_or_none()` used to raise on them, meaning the
  affected book 500'd on every write.

### Migration `0004_canonical_position`

Order matters and is load-bearing:

1. Add columns / indexes / `position_hints`.
2. **Rescue anchors first** — persist each bookmark's `epub_text_preview` from
   the sync map *while old chapter numbers still mean what the sync points mean*,
   plus percent from progress. Text is axis-independent, so these survive step 3.
   `bookmarks.epub_chapter` is deliberately **not** remapped: it is ambiguous per
   row (sync-map space when the matcher hit, spine space when it missed).
3. Re-base `sync_points.epub_chapter` onto spine indices by re-parsing each EPUB.
4. Backfill hints from legacy locator/CFI columns (only where `device_id` known).
5. Create canonical rows for standalone media, newest-wins on duplicates.

### Clients

Both readers now:

- Fetch the canonical position **at open** (not from a page-load snapshot).
- Walk one shared **restore ladder**, fixture-tested in Python, JS and Kotlin
  against `server/tests/fixtures/sync_parity/restore_cases.json`.
- Write the **whole position once** per save.
- Gate saving on `positionEstablished` — see §4, this is where the open bug is.

Player and Android Auto were migrated to the same single write
(`savePlaybackPosition`), and now await the server position before their first
seek instead of refreshing after it.

### The restore ladder

```
1. current hint for my kind/device (+ audio-drift check on audiobook positions)
2. epub_text_preview  -> text search -> in-chapter progression
3. epub_chapter       -> that spine item
4. epub_progress_percent -> content-weighted spine mapping
5. audio_position_ms  -> sync map -> preview -> retry (2)
```

**Invariant: a record holding any anchor never resolves to "start of book".**
Only a record with no anchor at all may do that. If every rung fails that is
*unresolved*, which is distinct from *unread*.

---

## 3. Where we are

**Verified working in production:**

| | |
|---|---|
| Migration | applied in ~50s; sync points re-based (pair 309 now chapters 1–83) |
| Axis correctness | `index_split_037.html` **is** spine 38, and the record says 38 ✓ |
| Preview rescue | Mad Ship carries a real text anchor from the migration |
| Restore | `plan=[text, chapter, audio]` → `restored via 'text'`, lands correctly |
| Percent | 44.91, matches reality (was being written as 0 — fixed) |
| Single write | server sees only `PUT /position/pair/309`, no legacy write beside it |
| Hint on anchor move | survives, marked `current: false` — the data-loss case, now correct |
| Stale write | 409, nothing changed |

**Test suites:** server 477 passed / 6 skipped, web 183 passed, Android unit
tests + Kover floor green. CI green on all three workflows.

**Commits on this branch (newest first):**

```
969eed1  player and Auto write the position once
6de35cb  stop the reader double-writing and reporting 0%
90a6d07  return the hint the write just stored
04998c1  tolerate duplicate user_progress rows
dac09c0  declare hintkind with create_type=False
2fa1d70  one canonical reading position across server, web and Android
885ce20  (superseded) the original #40/#61 patch — DO NOT deploy this revision alone
```

`885ce20` contains the clearing rule that caused the data loss. Squash on merge,
or at minimum never check out that revision on its own.

---

## 4. RESOLVED — the Android reader was not persisting

**Resolution (2026-07-31).** On-device logcat + DB inspection disproved the gate
hypothesis below: the restore landed (`restored via 'text'`), the gate was open,
and no "suppressed" line was ever logged. The bug was two mechanisms compounding:

1. **The close-flush save was cancelled.** `savePosition` launched its write
   into `lifecycleScope.launch`; a back-press destroyed the activity and
   cancelled the coroutine mid-network-call (`JobCancellationException`), and
   the Room write sat *after* the server call — so both writes died. Short
   sessions also produced no tracker save (5-second throttle), leaving zero
   reader writes: exactly the byte-identical `epub_progress_percent`.
2. **A background player save re-stamped the format.** ~4 s after the reader
   closed, a player teardown save wrote `source=audiobook`, so
   `resolvePairOpenTarget` reopened the audiobook even when a reader write had
   landed. (Known issue 2 was half of this bug, not a follow-up.)

Fixes: saves now run Room-first in an application-scoped `NonCancellable`
coroutine (`BookSyncRepository.saveReaderPosition`); the boolean gate became
`PositionSavePolicy` (`FullSave` / `LocalMetadataOnly` — the latter stamps only
local `source` so routing follows the session while anchors stay protected;
a user page-turn upgrades an unresolved session to `FullSave`); background
player saves no longer claim `source` (see the contract doc, "Who may claim
`source`"). The gate's latent defects (all-rungs-fail latching saves off
forever; the catch demoting a landed restore) were fixed in the same pass.

The original report and hypothesis are kept below for the record.

## The original report

**Reported flow (reproduced three times):**

1. Open app → tap Mad Ship → it opens the **audiobook**
2. Tap "Switch to Reader" → ebook opens correctly at the right position
3. Turn one page back
4. Close the reader, kill the app
5. Reopen → tap Mad Ship → **opens the audiobook again**

**Evidence:**

- `epub_progress_percent` is byte-identical (44.914135) across all three
  attempts — the reader never wrote.
- In the 11:03 session the reader was open 11:03:13–11:03:21. The server
  received writes at 11:03:11 and 11:03:19, both `source = audiobook` — the
  player's open and teardown saves. **Nothing from the reader.**
- `resolvePairOpenTarget` reads the **local Room** bookmark. If the reader had
  saved even locally, the next launch would open the reader regardless of
  network. It doesn't — so **the local write isn't happening either.**

**Therefore this is not a sync problem.** `savePosition` is being skipped
entirely, before it reaches either the server write or the Room write.

**Prime suspect: the `positionEstablished` gate.**
`ReaderActivity.savePosition` returns early when `positionEstablished` is false.
That flag is set only inside `getInitialLocator`. Something on the
player → "Switch to Reader" path is likely leaving it false. Worth checking:

- Does `getInitialLocator` run at all on that path, or does the activity reach
  the save loop by a route that skips it?
- Does it throw before setting the flag? The `catch` sets it to **false**.
- Secondary suspect: `savePosition`'s throttle. `lastSaveTime` is now
  initialised to `System.currentTimeMillis()` (to suppress echoing the restored
  position), so a session shorter than 5 seconds relies entirely on
  `saveCurrentPosition()` flushing on close. Verify that path still fires on the
  back gesture.

**How to diagnose:** clear logcat, repeat the flow with a ~10 second dwell in the
reader, then pull immediately (the buffer rotates fast on this device):

```bash
adb -s 48021FDAS009EF logcat -c
```

Then look for `getInitialLocator: plan=`, `restored via`, and
`savePosition: suppressed — position not established yet`. The presence or
absence of that last line decides between the two suspects.

**Design note for whoever picks this up:** the gate is correct in intent — a
restore that failed must not let a page-one view overwrite a real position — but
it currently blocks the *local* write too. Consider whether an unresolved
position should still be cached locally while being withheld from the server,
or whether the gate should only ever apply to the first write after open.

---

## 5. Intended behaviour

### Reading on the phone, online

- **Open:** `GET /position/pair/{id}` → the canonical record → `planRestore` →
  first rung that lands. With a current hint for this device that is the exact
  page; otherwise the preview search lands close within the right chapter.
- **Save** (5s throttle + flush on close): **one** `PUT /position/pair/{id}`
  carrying chapter (spine index), sentence + audio position when the sync matcher
  hits, text preview, percent, and this device's Readium locator as a hint. The
  Room row is updated locally and marked synced.
- **Server:** one staleness verdict; writes the bookmark, upserts the hint at the
  resulting `anchor_revision` and projects `user_progress` — all in one
  transaction.

### Reading on the phone, offline

- **Open:** the fetch fails, `PositionFetch.reachable = false`, and the reader
  falls back to the **local Room bookmark**, running the same ladder over it. The
  locally stored locator is offered as a hint at the local anchor revision, so it
  qualifies and the exact page is restored.
- **Save:** the canonical write fails and returns null, so the write falls
  through to the retry path — Room row updated, then queued in `pending_sync`
  with `createdAt` set to the **true capture moment**, not the enqueue time.
  `processPendingSync` drains that queue through the canonical endpoint too.
- **On reconnect:** `processPendingSync` replays queued writes with the original
  `captured_at`, so a stale replay is correctly rejected with 409 rather than
  clobbering a newer position from another device.
- **Nothing is lost by being offline** — this is the property currently broken by
  §4, since the skipped save never reaches the Room write either.

### Reading in the web browser

- **Open:** `getPosition(scope, id)` **at reader open**, not at page load — a
  position set elsewhere in the meantime is picked up. Same ladder;
  `epubjs_cfi` hints are portable between browsers, so another browser's CFI is
  usable, unlike Readium locators which are device-scoped.
- **Save:** 2s debounce → one `updatePosition` with chapter (spine index),
  sentence/audio when the matcher hits, preview, percent, and the CFI as a hint.
- **Gate:** nothing is written until the restore has landed, or the record was
  genuinely empty.

### Handoff between them

Both write the same record, so there is no reconciliation step — the last write
wins, adjudicated once by `captured_at`.

When device A moves the anchor, `anchor_revision` bumps and device B's hint stops
being *current*. B then restores via the text preview: close, chapter-accurate,
not page-exact. B's hint is **not deleted** — it becomes current again the moment
B re-captures at the live anchor. This is the central correction: precision
degrades gracefully instead of a position being destroyed.

Audio movement alone does **not** bump the anchor, so listening does not
invalidate the reading page.

---

## 6. Known issues and follow-ups

1. **The reader-not-persisting bug (§4)** — ~~blocking~~ **fixed** (see §4).
2. **"Which format opens" follows the last writer** — **fixed** (2026-07-31):
   the format follows actual consumption. Saves claim `source` only while
   playback is playing or on an explicit user playback command; background
   saves omit it and the server keeps the stored value. See the contract doc.
3. **Duplicate `user_progress` rows** for `(user 1, ebook 1726)`, two empty rows
   3ms apart from the old GET race. Left in place by choice; reads tolerate them,
   but they may show a phantom Continue entry.
4. **`.mobi` books** — the server parses the `.mobi` while the reader renders a
   Calibre-converted EPUB, so their axes cannot correspond. Those positions rely
   on the preview/percent rungs. Fix by parsing the artifact the reader renders.
5. **`is_completed` at ≥98%** — from #61, never implemented; needs both clients
   together or they disagree.
6. **`reset_pair_progress` doesn't delete the canonical bookmark** — **fixed**
   (2026-07-31): the DELETE now removes the bookmark rows (all scopes), their
   hints, and the projection; `GET /position` returns 204 afterwards. Client
   reset buttons that used to legacy-write zeros (ContinuePage, BookDetailPage,
   Android) were rewired to the DELETE. Standalone media kept the zero-write
   until issue #102 added `DELETE /api/sync/position/{scope}/{ident}`, which all
   reset buttons now use.
7. **Legacy endpoints and mirror columns** (`bookmarks.epub_locator`,
   `locator_audio_ms`, `user_progress.epub_cfi`) — **done** (issue #102): both
   legacy PUTs and `GET /sync/bookmark/{pair}` are removed, the columns dropped
   by migration 0007, and web and Android write only the canonical endpoint.

---

## 7. Where things live

| What | Where |
|---|---|
| Contract | `docs/position-sync-contract.md` |
| Ladder (server) | `server/services/position_resolver.py` |
| Ladder (web) | `web/src/lib/positionLadder.js` |
| Ladder (Android) | `android/.../data/sync/PositionResolver.kt` |
| Golden vectors | `server/tests/fixtures/sync_parity/restore_cases.json` |
| Write path | `server/services/position_service.py` |
| Migration | `server/alembic/versions/0004_canonical_position.py` |
| Reader (Android) | `android/.../ui/reader/ReaderActivity.kt` — `getInitialLocator`, `savePosition` |
| Reader (web) | `web/src/components/EbookReader.jsx` — `executeRestore`, `doSave` |

Server logs: `ssh-docker`, `/usr/share/docker-containers/Book-Sync`,
`docker compose logs server`. Phone: `adb -s 48021FDAS009EF logcat`.
