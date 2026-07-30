# Cross-device position contract

How a reading position travels between the web reader, the Android reader, and
the server. Settled in issues #40 and #61.

## The anchor and the hints

**`epub_chapter` + `epub_sentence_index` is the portable anchor.** It is the
sync-map key, every client can produce it, and it is the only representation
both readers understand. `epub_text_preview` accompanies it as the searchable
form of the same point.

**Everything more precise is a device-local hint:**

| Field | Written by | Format |
|---|---|---|
| `bookmarks.epub_locator` | Android | Readium `Locator` JSON |
| `bookmarks.locator_audio_ms` | Android | audio position (ms) the locator was captured at |
| `user_progress.epub_cfi` | web | epub.js CFI |

Neither client can read the other's hint, so a hint is only meaningful while it
still agrees with the anchor.

## The rule

> **A hint is trustworthy only if it arrived with the write that set the
> current anchor.**

The server enforces it (`server/routers/sync.py`):

- `PUT /api/sync/bookmark/{pair_id}` — a hint sent with the write is stored. An
  **ebook**-source write that moves the anchor without one clears
  `epub_locator` and `locator_audio_ms`. Anything else preserves them. An
  **audiobook**-source write never clears the locator: audio drift doesn't
  invalidate the page, and `locator_audio_ms` lets a client judge that itself.
- `PUT /api/sync/progress/ebook/{id}` — same rule for `epub_cfi`.

Clients don't rely on the server alone, because a stale hint can also survive
locally:

- Android (`ReaderActivity.getInitialLocator`) discards a stored locator whose
  href doesn't resolve to the bookmark's chapter, and resolves from the anchor
  instead. `BookmarkResponse.toEntity` drops the local locator when an
  ebook-source response comes back without one, rather than resurrecting the
  value the server just invalidated.
- Web (`resolveInitialDisplayTarget` in `EbookReader.jsx`) uses `epub_cfi` only
  when `book.spine.get(cfi).index` matches the chapter anchor.

Falling back to the anchor costs some within-chapter precision. Following a
stale hint puts the reader on the wrong page entirely, which is worse.

## Progress metrics

`UserProgress` (percent + completion) is separate from `Bookmark` (resume
position) and drives the web Home/Continue lists. Both readers write it on
every throttled position save.

**`epub_progress_percent` is 0–100 on the wire.** epub.js reports 0–1
(`location.start.percentage`) and Readium reports 0–1
(`locator.locations.totalProgression`); both clients multiply by 100. Sending
either raw would make the two disagree by 100×.
