# Library conventions

How Tandem reads your ebook and audiobook folders, where each piece of metadata comes from, and
the rules it uses to pair an ebook with its audiobook.

Everything here is implemented in [`server/routers/library.py`](../server/routers/library.py);
the editable defaults live in [`server/routers/settings.py`](../server/routers/settings.py).

## Supported formats

| Kind | Extensions |
|---|---|
| Ebooks | `.epub`, `.pdf`, `.mobi`, `.azw3` |
| Audiobooks | `.mp3`, `.m4a`, `.m4b`, `.flac`, `.ogg`, `.wav`, `.aac`, `.wma` |

**Only EPUB is fully usable.** Chapter extraction, text alignment and the readers all work on
EPUB; `.mobi` and `.azw3` are indexed but flagged as unsupported until converted (below), and
PDFs can be stored and paired but not aligned.

The scanner walks `EBOOK_DIR` and `AUDIOBOOK_DIR` recursively. Folder depth is up to you — the
patterns decide what a path means.

## Where metadata comes from

For each file, in this order, later steps overriding earlier ones field by field:

1. **Filename and path patterns** (below) — always attempted.
2. **Embedded metadata** — EPUB OPF for ebooks, tags via mutagen for audio. Any field present
   here wins over the pattern's guess.
3. **Audiobookshelf** — if `ABS_URL`/`ABS_API_TOKEN` are configured, audiobook rows are enriched
   from the matching ABS item (and the enriched values can be written back into the file's tags).

Nothing else runs automatically. **External metadata providers are a manual step**: a book's
detail page lets you search Google Books, Open Library, Audible or Hardcover and apply a result
(including its cover) to that book. `GOOGLE_BOOKS_API_KEY` is optional even for that — it only
raises the rate limit on the Google provider.

Each row records which of these it ended up using in `metadata_source` (`filename`, `pattern`,
`embedded`, `embedded+pattern`), shown as **Metadata Source** on a book's detail page — so when
something lands with the wrong title you can see which step produced it.

## Filename and path patterns

Patterns are plain strings with angle-bracket tags, tried in order until one matches. A pattern
containing `/` is matched against the file's path relative to the library root; one without is
matched against the filename alone (extension stripped).

| Tag | Matches |
|---|---|
| `<Author>` | Anything up to a `/` |
| `<Series>` | Anything up to a `/` |
| `<Title>` (or `<Book Title>`) | Anything up to a `/` |
| `<Book Number>` (or `<Series Index>`) | A number, optionally decimal (`3`, `3.5`) |

Defaults for ebooks:

```
<Author> - [<Series> <Book Number>] - <Title>
[<Series> <Book Number>] <Title>
<Author>/<Series>/<Book Number> - <Title>
<Author>/<Title>
<Title>
```

Audiobooks use the same list plus `<Author>/<Series>/<Title>`.

So `Jim Butcher/The Dresden Files/17 - Battle Ground.m4b` parses as author *Jim Butcher*, series
*The Dresden Files*, book 17, title *Battle Ground*. Common audiobook suffixes
(` - Audiobook`, ` (Unabridged)`, ` [Audiobook]`, …) are stripped before matching.

Edit the lists under **System → Library Settings** — the stored value replaces the defaults
entirely, so keep a catch-all `<Title>` at the end.

## Auto-pairing

After every scan, unpaired ebooks are compared against unpaired audiobooks
(`auto_match_books`). A candidate pair must clear all of these:

- **Not previously unpaired by hand.** Manually unpairing records each side's file hash in the
  other's `auto_pair_excluded_hashes`, so the scanner won't re-suggest that combination.
- **Series compatible.** If both sides carry a series name they must match at ≥85 (fuzzy, articles
  stripped); if both also carry an index, the whole numbers must be equal. A series on only one
  side is treated as missing metadata, not as a conflict.
- **Author gate.** If both sides have an author they must match at ≥70. Initials and suffixes are
  normalized first, so `L.E. Modesitt Jr.` and `L. E. Modesitt, Jr.` compare equal.
- **Title score ≥75.** Fuzzy token-sort comparison with leading `the`/`a`/`an` stripped. An author
  match of ≥80 adds a +10 bonus to the title score.

Each ebook takes its single best-scoring audiobook, and each audiobook is claimed at most once.
Pairs created this way get status `AUTO_MATCHED` — review them; the scanner is deliberately
conservative but not infallible. Anything it won't touch shows up on the Unpaired page for manual
pairing.

With `AUTO_TRANSCRIBE_ENABLED` (or the **System → Transcription Settings** toggle) on, newly
created pairs are queued for transcription immediately.

## Converting unsupported ebooks

`.mobi` and `.azw3` files are surfaced as *unsupported format* under **System → Unsupported** and
in **System → Troubleshoot Library**. Converting one runs Calibre's `ebook-convert` in the server
container, writing an `.epub` sibling next to the original (5-minute timeout). Failures are
reported specifically for the two cases worth acting on:

- **DRM-protected** — cannot be converted; you need a DRM-free copy.
- **Corrupt or an unsupported MOBI variant** — the source file needs replacing.

If the server image was built without Calibre, conversion reports that `ebook-convert` is missing
rather than failing silently.
