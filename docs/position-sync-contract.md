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

The older `PUT /sync/bookmark/{pair}` and `PUT /sync/progress/{type}/{id}`
endpoints still exist as thin adapters over the same service, so app builds that
predate the position endpoint keep working and converge on the same row.

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

`bookmarks.epub_locator`, `bookmarks.locator_audio_ms` and
`user_progress.epub_cfi` remain as mirrors of the current-anchor hint, purely so
older app builds still resume.

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

A client must not write a position until it knows where the reader is:

- **`positionEstablished`** — saving is blocked until a rung has landed, or the
  record was genuinely empty. Without it, a failed restore sitting on page one
  gets persisted over a real position.
- **Full position per write** — anchors are never inherited from a stale local
  row; omission on the wire means "leave alone", and a write carrying no anchor
  never clears one.
- **Resume paths refresh first.** The reader, the player and Android Auto all
  pull the server position (bounded, then fall back to cache) *before* seeking.
  Refreshing afterwards meant a position set elsewhere always arrived too late.

## Percent scale

`epub_progress_percent` is **0–100** on the wire. epub.js reports 0–1
(`location.start.percentage`) and Readium reports 0–1
(`locator.locations.totalProgression`); both clients multiply by 100. Sending
either raw makes the two disagree by 100×.
