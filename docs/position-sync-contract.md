# Cross-device position contract

How a reading position travels between the web reader, the Android reader/player,
and the server.

## One record

`bookmarks` is **the** canonical position record — one row per user per book,
scoped by `book_pair_id` or, for standalone media, `ebook_id`/`audiobook_id`.
`user_progress` is a **projection** of it, written in the same transaction, and
is what the Home/Continue lists read. Clients never write it directly.

Everything goes through `PUT /api/sync/position/{scope}/{id}`
(`services/position_service.apply_position`): **one request, one staleness
verdict, one transaction.** A stale write returns 409 and changes nothing — not
the record, not the hints, not the projection.

It is the **only** write path. The older `PUT /sync/bookmark/{pair}` and
`PUT /sync/progress/{type}/{id}` endpoints survived for a while as thin adapters
over the same service, so app builds predating the position endpoint kept
working; they are gone (issue #102).

> Why: these were once two independent writes with two independent staleness
> checks. Either could be accepted while the other was rejected, leaving two
> records that described different positions for the same book, permanently —
> and the two readers each restored from a different one.

## Anchors and hints

**Anchors** are portable. Every client can write and resolve them:

| Field | Meaning |
|---|---|
| `epub_chapter` | EPUB **spine index** — the axis both readers position by |
| `epub_sentence_index` | sentence within that document, from the sync map |
| `epub_text_preview` | the text at the position; survives re-parsing and re-alignment |
| `epub_progress_percent` | 0–100 book fraction |
| `audio_position_ms` | audio position |

`epub_chapter` is the spine index because that is what `book.spine.items`
(epub.js) and `publication.readingOrder` (Readium) both use. The parser emits it
directly (`services/epub_parser.py`); it used to build its own ordinal by
counting only documents that produced sentences, which matched neither reader.

**Hints** are precise but reader-specific, and live in `position_hints`, one row
per `(bookmark, device, kind)`:

| Kind | Written by | Portable between devices? |
|---|---|---|
| `readium_locator` | Android | No — encodes how that device rendered the page |
| `epubjs_cfi` | web | Yes — derived from the EPUB DOM |

A hint is **current** iff `hint.anchor_revision == bookmark.anchor_revision`.
`anchor_revision` bumps when a text anchor changes, and deliberately **not** on
audio movement alone — audio drifting on doesn't move the page.

> **Hints are never deleted when the anchor moves.** A stale hint simply stops
> being current, and becomes current again the moment its device re-captures.
> An earlier design cleared them instead. Android then found no locator, treated
> that as "no position", opened at the title page, and its autosave wrote
> chapter 0 over a real position. Staleness is a tag, not a deletion.

`position_hints` is the only place a precise position lives. It used to be
mirrored into `bookmarks.epub_locator`, `bookmarks.locator_audio_ms` and
`user_progress.epub_cfi` for app builds predating that table; those columns were
dropped by migration 0007 (issue #102).

## The restore ladder

Every client fetches the canonical position **at open** — not from a snapshot
taken earlier — then walks the same ladder, taking the first rung that lands:

```
1. current hint for my kind/device (+ audio-drift check on audiobook positions)
2. epub_text_preview  -> text search -> in-chapter progression
3. epub_chapter       -> that spine item
4. epub_progress_percent -> content-weighted spine mapping
5. audio_position_ms  -> sync map -> preview -> retry (2)
```

**Invariant: a record holding any anchor never resolves to "start of book".**
Only a record with no anchor at all may do that.

The decision layer is shared and fixture-tested in all three places —
`services/position_resolver.py`, `web/src/lib/positionLadder.js`,
`data/sync/PositionResolver.kt` — against
`server/tests/fixtures/sync_parity/restore_cases.json`. Executing a step needs
the EPUB, so that stays per-client; the ordering and eligibility, where the
clients actually drifted, does not.

If every rung fails, that is **unresolved**, which is not the same as unread.
The client shows something but sets no position as established.

## The write gate

A client must not write *anchors* until it knows where the reader is. On
Android this is `PositionSavePolicy`; the web reader keeps the boolean form.
Every save produces one of two verdicts — there is no "suppress everything":

- **`FullSave`** — the restore landed, the record was genuinely empty, or the
  user has deliberately navigated since open (a user page-turn makes the
  current position the truth). Writes the whole position: local row first,
  then the server.
- **`LocalMetadataOnly`** — the restore is *unresolved* (a non-empty ladder
  where no rung landed). Only the local row's `source` is stamped, so
  open-target routing still follows the session; anchor fields are left
  untouched locally and nothing goes to the server. As a hard backstop, an
  unresolved session still sitting at the start of the book stays
  `LocalMetadataOnly` even if navigation was detected — no navigation signal
  is trustworthy enough to let a start-of-book write replace a real anchor,
  and a genuine forward page-turn moves off the start anyway. Without this
  split, a failed restore sitting on page one either got persisted over a
  real position (no gate) or suppressed even the local write, so the app
  kept reopening the other format (gate too wide — the original §4 bug).
- **Full position per write** — anchors are never inherited from a stale local
  row; omission on the wire means "leave alone", and a write carrying no anchor
  never clears one.
- **Saves must survive teardown.** The final flush runs in an
  application-scoped, non-cancellable coroutine with the local write *before*
  the server call. Running it in the activity's lifecycle scope meant a
  back-press cancelled it mid-request and both writes were lost.
- **Client wall-clock is trusted only up to the skew bound.** A `captured_at`
  more than `MAX_CLOCK_SKEW` (120 s, `services/position_service.py`) ahead of
  the server is stamped with the server's own time instead — never rejected —
  so one device with a wrong clock cannot park a future timestamp that 409s
  every honest write until the wall clock catches up (issue #197).
- **Resume paths refresh first.** The reader, the player and Android Auto all
  pull the server position (bounded, then fall back to cache) *before* seeking.
  Refreshing afterwards meant a position set elsewhere always arrived too late.

## Who may claim `source`

`bookmarks.source` decides which format opens next (`resolvePairOpenTarget`
keys on it). **The format follows actual consumption:**

- A save claims the format (sends `source`) only when playback is actively
  playing at save time or the save came from an explicit user playback command
  (play/pause, seek, skip — including Android Auto/MediaSession commands).
  Reader saves always claim `ebook`: having the reader open is consumption.
- Background saves — service or ViewModel teardown while paused, screen-open
  saves, refreshes — omit `source`. The server treats an omitted `source` like
  any other omitted field: keep the stored value. They also leave the local
  Room `source` untouched, so local and server routing agree.

> Why: a player teardown save used to stamp `audiobook` seconds after the
> reader closed, so a reading session still reopened the audiobook.

## Re-transcription

`epub_sentence_index` is a **sync-map coordinate**: it only means anything
relative to the `sync_points` a particular `sync_maps.version` produced.
Re-transcribing a pair deletes that map and inserts a fresh one, so the stored
index can silently start naming different text — and the audio timestamp for a
given sentence moves too.

`save_sync_map` therefore re-maps every bookmark on the pair onto the new
coordinates before returning (`services/sync_engine.remap_bookmarks_for_pair`).
The position has not moved; only the coordinate system has:

| Bookmark `source` | Re-derived from | Left alone |
|---|---|---|
| `audiobook` | `audio_position_ms` through the new map | `audio_position_ms` — the audio file didn't change, so it is the truth |
| `ebook` | `epub_text_preview` (or the outgoing map's text at the old coordinate) through the shared matcher | the text anchor; its *derived* `audio_position_ms` is refreshed to the matched point |

**`anchor_revision` bumps only when the chapter moves.** A sentence-index shift
inside the same spine item is the same page, and a device's Readium locator /
epub.js CFI still describes it — marking every hint stale would drop each reader
to text-search restore for a page that never moved. A chapter change is a real
relocation, so there the hints must go stale.

`captured_at` is never touched and no `BookmarkLog` row is written: a re-map is a
server-side translation, not a device capture. Stamping `captured_at` would let it
beat a genuinely newer write from a phone, and the log is the history of moves the
*user* made. A bookmark with no usable anchor keeps its coordinates and its old
`sync_map_version`, so the drift stays visible instead of being papered over.

> **Clients must check the version before using a cached map.** Android caches
> sync points in Room; before this it never compared versions, so after a
> re-transcription `epubToAudioText` kept finding the right text and returning an
> audio second that no longer existed. `BookPairResponse.sync_map_version` carries
> the live version on the pair listing clients already poll.
>
> On a mismatch the cached points are **deleted**, not flagged — a map that is
> merely flagged can still be read — and the reader/player re-fetch at open
> (`ensureSyncMapCached`). A cache holding points but *no recorded version* (an
> app build predating the column) counts as a mismatch: it cannot be identified,
> so it is refetched once. A **null on the server side** is the opposite case —
> *unknown*, not "no map", since endpoints that don't eager-load the relationship
> report null — and must leave the cache alone. `bookmarks.sync_map_version`
> records which map a row's own coordinates belong to (issue #55).

**A write that carries `epub_sentence_index` carries `sync_map_version` too** —
the version the client resolved that index against (issue #116). Android sends
the version its cached points came from (`BookPairEntity.syncMapVersion`, kept on
the bookmark and pending-sync rows so a deferred push attests the version at
resolution time, not at push time); the web sends the `sync_map_version` returned
by `/sync/match-text`. The server records **what the client attests to**, never
the live version by itself:

| Attested version | Server does |
|---|---|
| absent | stamps NULL — "unknown". Claiming the live version for a coordinate nobody vouched for is the false claim that hid the drift. |
| equal to the live map | stamps it; coordinates taken as sent |
| trails the live map | re-anchors the write on the live map from its own evidence — the same rule as the re-map table above (audio position for `audiobook`-sourced writes, `epub_text_preview` otherwise). Success lands the re-expressed coordinates stamped with the live version; failure keeps the coordinates as sent and stamps the *attested* version, so the row visibly trails. |

The map's points are only loaded on the mismatch path; an ordinary write still
costs a single `version` lookup. `PositionResponse.sync_map_version` reports the
stored value so a client that pulls a position and later pushes it back attests
the right one.

## Map provenance and drift

A sync map is only meaningful against the ebook file it was aligned from.
Re-converting the book, replacing the file with another edition, or changing how
it parses leaves the map's coordinates naming text that is not in the document
the reader renders. `sync_maps.version` does not catch this: the map is still
internally consistent, just about a different file.

`sync_maps.epub_file_hash` records the composite hash
(`services/file_hash.py`) of the ebook at alignment time. `save_sync_map` stamps
it, so every producer — transcription, re-alignment, the Convert flow — records
provenance without having to remember to. **NULL means unknown**, not healthy:
maps written before issue #295 have no stamp.

`GET /api/troubleshoot/sync-map-audit` (editor-gated, read-only) reports drift
per pair from two signals — the stored hash against the file's hash now, and the
share of a sampled set of the map's stored previews that still occur in the
book's whole-spine text. Whole-spine on purpose: a front-matter offset shifts
every chapter number without invalidating anything, so checking a preview
against the chapter it *claims* would flag a healthy map. A flagged pair is
fixed through the endpoint that already rebuilds maps,
`POST /api/transcription/{pair_id}/realign`; a pair with no cached transcript
has nothing to rebuild from and is flagged for full re-transcription instead.

## Reset

`DELETE /api/sync/position/{scope}/{ident}` is a true reset: it deletes the
canonical bookmark row for that scope, its hints, and the `user_progress`
projection, in one transaction. After it, `GET /api/sync/position/...` returns
204 — the book is *unread* again, not "pinned at zero".

A **pair** reset also sweeps the standalone ebook/audiobook rows for the same
underlying media: a client can reach those scopes independently of the pair, and
a survivor would resurrect the position. A **standalone** reset is deliberately
not symmetric — it must not wipe the pair's record, and it leaves the shared
`user_progress` row alone when another scope still writes it.

`DELETE /api/sync/progress/pair/{pair_id}` is an alias for the pair form, kept
so existing clients did not have to change their reset call.

## Unpairing

Unpairing **demotes** pair-scoped positions to standalone; it never deletes
them. Deleting a pair (or a paired ebook/audiobook, which dissolves its pairs)
re-scopes every user's pair-scoped bookmark onto the surviving media rows —
`book_pair_id = NULL`, `ebook_id`/`audiobook_id` set — in the same transaction,
before the pair row goes (`services/position_service.demote_pair_positions`,
issue #155). Unpairing is the *normal* way to correct a mis-matched pair; the
cascade used to take the canonical record for every user with it. If a
standalone row for the same (user, medium) already exists, the newer
`captured_at` wins (None counts as oldest) and the loser is deleted.
`sync_map_version` is cleared on the demoted row — the sentence index is a
sync-map coordinate and the map dies with the pair — while chapter, percent,
audio position, hints and logs are kept: same media, still valid. The
`user_progress` rows are keyed by media and remain the projection of the
demoted bookmark, so they are unlinked from the pair, not deleted. An explicit
user reset (above) stays the only path that deletes a position.

Standalone media had no reset endpoint at all before this; the web client faked
one by PUTting zeros through the legacy progress adapter, which left the
canonical record in place for the next write to resurrect — the same failure
mode the pair reset fixed (issue #6).

**Converting an unsupported ebook re-points instead of releasing.** The
converted EPUB is registered as a new `EBook` row and the source is deleted, so
the pair is re-pointed at it — and every standalone position on the source is
carried over the same way (`repoint_standalone_positions_to_ebook`, issue #298):
`ebook_id` moves to the converted row, `epub_sentence_index` and
`sync_map_version` are cleared (both are coordinates of the old parse), and
`anchor_revision` is bumped so the hints go **stale, not deleted** — a locator
or CFI addresses the DOM of the file it was captured in. Chapter, percent,
preview, audio side and `captured_at` are kept: a chapter/percent anchor is
roughly right across a conversion and the restore ladder lands it precisely from
the preview. Collisions with a position already on the replacement resolve by
`captured_at` exactly as a demotion does, `user_progress` included. Force-delete
has no replacement to point at, so it still releases, as above.

The "hints are never deleted" rule above governs position **writes**; an
explicit user reset is the one sanctioned deletion path.

## Replacing a file

**A new file behind the same row invalidates that row's parse coordinates.**
`POST /troubleshoot/replace/ebook/{id}` swaps the EPUB and keeps the `EBook`
id, so every position still points at it — but the new file is a different
parse. `position_service.invalidate_parse_coordinates_for_ebook` runs in the
same transaction as the swap, over every position referencing the ebook
(standalone rows *and* the pair-scoped rows of every pair containing it):

| Field | What happens |
|---|---|
| `epub_sentence_index`, `sync_map_version` | **cleared** — coordinates of the outgoing parse and the map built from it; on the new file the same index names different text |
| `anchor_revision` | **bumped**, so every hint goes stale: a locator or CFI addresses the DOM of the file that is gone |
| `epub_chapter`, `epub_progress_percent`, `epub_text_preview`, audio side, `captured_at` | kept — a chapter/percent anchor is roughly right across a re-parse, and the reader's whole-spine text search lands the preview precisely |

Same reasoning as a conversion re-point (issue #298), minus the change of id:
the book is the same, the artifact describing it is not. Hints go **stale, not
deleted**, per the rule above. `captured_at` is untouched for the same reason a
re-map leaves it alone — this is a server-side invalidation, not a device
capture (issue #303).

An ebook replacement queues no re-alignment, so the live map keeps describing
the old parse until someone re-queues the pair. That ordering is what keeps the
two mechanisms from fighting: a later `remap_bookmarks_for_pair` re-derives
coordinates from the surviving text preview and the new map's points, so a
cleared row is *upgraded* onto the new map, never resurrected onto the old one.

The **audiobook** branch of the same endpoint is deliberately exempt: the ebook
parse and the live map are both unchanged, so the epub coordinates still mean
what they said. What a new recording invalidates is `audio_position_ms`, and
the re-transcription it forces (the cached transcript is dropped and a synced
pair returns to `manual_matched`) re-derives the epub side through the re-map.
Metadata rescans (`/library/{type}s/{id}/rescan`, `/library/rescan-all`) re-read
the *same* file and change no coordinate, so they invalidate nothing.

## Reads never create

Every position GET is side-effect free. `GET /api/sync/position/{scope}/{id}` and
`GET /api/sync/progress/{type}/{id}` both answer **204** when the user has no
position for that media. `GET /api/sync/bookmark/{pair}` used to answer 200 with
a fabricated start-of-book response for old app builds; it is gone (issue #102),
so 204 is now the only "no position" answer.

> Why: the progress GET used to INSERT on a miss. Two clients opening the same
> book at the same moment each left a row behind, and every later read *and*
> write for that media then raised `MultipleResultsFound` — that one book 500ed
> forever until a row was deleted by hand (issue #64). A fabricated chapter 0 is
> also indistinguishable from a real position at the start of a book, which makes
> "has this user read any of this?" unanswerable and gives a failed restore
> something to overwrite.

The schema now enforces what the code assumed: partial unique indexes on
`bookmarks` (`ux_bookmarks_user_pair` / `_user_ebook` / `_user_audiobook`) and on
`user_progress` (`ux_user_progress_user_ebook` / `_user_audiobook`). Because a
read-then-insert cannot be made race-free in the application, the write path
attempts its insert inside a savepoint and, on conflict, re-reads the row the
racing transaction won and applies on top of it.

## Completion

`is_completed` on the canonical record (projected onto `user_progress` like
everything else) is what puts a book on the Finished shelf and takes it off
Continue. The **server** decides when a position write finishes a book
(`position_service._auto_complete`, issue #56); clients may still send the flag
explicitly, and an explicit value always wins.

The rule: a write that **crosses into the end zone** completes the book.

| Media | End zone | Setting (`server/config.py`) |
|---|---|---|
| Ebook | `epub_progress_percent >= 98` | `auto_complete_epub_percent` |
| Audio | within 120 s of `AudioBook.duration_seconds` | `auto_complete_audio_tail_seconds` |

- **Crossing, not being in.** The flag flips when the stored position goes from
  outside the zone (or unset — a first write straight at the end counts) to
  inside it. A manual un-finish (`is_completed: false`) therefore sticks while
  the reader is still parked at the end; the next heartbeat at the same spot
  does not undo it. Leaving the zone and re-entering it completes the book again.
- **Never auto-cleared.** Re-reading chapter three of a finished book is not
  un-finishing it. Only an explicit `false` or a reset clears the flag.
- **Unknown audio length ⇒ no audio end zone.** The players' own end-of-stream
  write still finishes the book, because it sends `is_completed: true` itself.
  `AudioBook.duration_seconds` is filled by the library scan from the file's own
  container header (issue #127) — for new rows and for existing ones, so an
  ordinary scan backfills a library that predates it. The file always wins over
  a stored value; an unreadable length never clears one.
- **Where the length comes from.** `services/audio_duration.py` probes with
  **ffprobe**, falling back to mutagen's `info.length` only when ffmpeg isn't on
  PATH. That ordering is deliberate: measured across the full production library,
  the two agreed on 290 of 308 files, mutagen could not open 17 at all (legacy
  Nero `chpl` atom) that ffprobe read fine, and on one MP3 mutagen confidently
  reported 12 seconds for a 9.9-hour book. **A wrong length is worse than a
  missing one here** — unknown cleanly disables the end zone, while a too-short
  one makes every position look like the end and finishes the book on the first
  write. Files split into per-chapter tracks are skipped by the scanner
  entirely (they are flagged for Troubleshoot instead), so their rows keep
  whatever length they had; the per-book rescan endpoint still fills them.
- **Pairs complete as a pair.** A pair-scoped write projects one flag onto both
  `user_progress` rows. Android used to finish a pair with two standalone-scope
  PUTs (`ebook` + `audiobook`), which left the pair's own record un-finished
  while web wrote the pair scope; both now write `PUT /position/pair/{id}`.
  Standalone media keep their own scope.

> Why 98 %: EPUB back-matter (acknowledgements, previews, ads) means the reader
> rarely reaches 100 % of the spine while actually reading. The audio tail
> exists for the same reason — outros and credits.

## Percent scale

`epub_progress_percent` is **0–100** on the wire. epub.js reports 0–1
(`location.start.percentage`) and Readium reports 0–1
(`locator.locations.totalProgression`); both clients multiply by 100. Sending
either raw makes the two disagree by 100×.

## Playback offsets

Two numbers govern how far playback jumps, and both must be **identical on every
surface**. There is no config channel that reaches all three platforms, so each
keeps its own copy and they are kept equal by hand:

| | Value | Android | Web | Server |
|---|---|---|---|---|
| Skip back / forward | 30 s | `PlaybackOffsets.SKIP_MS` | `SKIP_SECONDS` | — |
| Resume rewind | 5 s | `PlaybackOffsets.RESUME_REWIND_MS` | `RESUME_REWIND_SECONDS` | `default_rewind_seconds` |

`PlaybackOffsetsTest` and `test_epub_to_audio_exact_match_applies_default_rewind`
assert the values literally, so a one-sided edit fails CI rather than shipping a
silent divergence. Before issue #42 there were five different numbers: 15 s on the
phone player, 10 s in Android Auto and on the notification, 15 s back / 30 s
forward on the web, a 2 s text→audio handoff, and a stated server default of 10 s.

**Skip** is symmetric. Asymmetric skip (back 15 / forward 30) reads as a bug
whichever way you're navigating.

**Resume rewind** exists because picking up mid-word after a pause is hard to
follow. It applies to any paused→playing transition and, deliberately, to the
reader's text→audio handoff as well — "switch to listening" and "unpause" should
land the same distance before where you were.

On Android it lives on the **session player** (`ResumeRewindPlayer`), not in a
button handler. It used to be inline in the phone player's play/pause handler,
which meant Android Auto, the notification, headset buttons, Bluetooth, and
audio-focus recovery after a nav prompt — all of which drive the MediaSession
player directly — got no rewind at all.

Three paths are exempt, each on purpose:

- **First play of a book.** Opening a book restores its saved position and starts
  playing; that is not a resume, and rewinding it would cost 5 s every launch.
  `ResumeRewindPlayer` gates on having heard playback since the last media-item
  transition; on the web, `play()` (which carries an explicit position from
  Home/Continue) does not rewind, only `togglePlayPause` does.
- **Cast.** `AudioPlayerService.switchToPlayer` branches on `newPlayer is
  CastPlayer`, so wrapping the CastPlayer would break the Cast handoff. The
  CastPlayer goes to the session unwrapped and gets no resume rewind.
- **Web stream-error recovery.** Re-minting an expired media token and resuming is
  a transparent refresh, not a user resume, so it restores the exact position.

> A rewind on resume means each pause/resume cycle moves the *saved* position
> backwards by 5 s, since the position heartbeat writes wherever playback actually
> is. That is inherent to the feature, not a sync bug.

## Save cadence

Two more numbers are kept equal by hand on the player surfaces (issue #65):

| | Value | Android | Web |
|---|---|---|---|
| Local heartbeat | 5 s | `AudioPlayerService.AUTO_SAVE_INTERVAL_MS` | `HEARTBEAT_TICK_MS` |
| Network push | 30 s | `AudioPlayerService.NETWORK_SAVE_INTERVAL_MS` (`HeartbeatThrottle`) | `NETWORK_SAVE_INTERVAL_MS` |

While audio plays, the heartbeat keeps the **local** position fresh every 5 s
(Room on Android; in-memory + the unload keepalive on the web) but only
**pushes to the server every 30 s**. Both players used to PUT on every 5 s tick
— ~720 `UPDATE`s an hour per device to record "position advanced 5 s", and a
radio wake-up every 5 s on the phone. The server copy only matters for
cross-device resume, where 30 s of staleness is imperceptible.

**Boundaries flush immediately, bypassing the throttle:** pause, seek/skip
(debounced ~1 s on the web so a slider scrub is one write), speed change, sleep-
timer stop, book change, stop/teardown, natural end, tab unload (keepalive), cast
switch and controller disconnect (Android). A device that stops listening leaves
an exact position; the 30 s only ever trails during uninterrupted playback.

A **failed** push does not advance the window — the next tick retries. On Android
a throttled tick writes its Room row **unsynced**, so if the app dies before the
next push the startup reconcile (`syncAllBookmarksAndProgress`) and the 15-minute
WorkManager sweep (`processPendingSync`, which now also pushes unsynced bookmark
rows) still deliver it. The 30-minute `append_to_log` history cadence is
orthogonal and unchanged; a log tick is itself a boundary and pushes.
