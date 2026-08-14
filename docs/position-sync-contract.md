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

Standalone media had no reset endpoint at all before this; the web client faked
one by PUTting zeros through the legacy progress adapter, which left the
canonical record in place for the next write to resurrect — the same failure
mode the pair reset fixed (issue #6).

The "hints are never deleted" rule above governs position **writes**; an
explicit user reset is the one sanctioned deletion path.

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

## Percent scale

`epub_progress_percent` is **0–100** on the wire. epub.js reports 0–1
(`location.start.percentage`) and Readium reports 0–1
(`locator.locations.totalProgression`); both clients multiply by 100. Sending
either raw makes the two disagree by 100×.
