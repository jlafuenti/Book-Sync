# Sync-matcher parity fixtures

These JSON files are the **cross-platform parity contract** for the reading-position
matcher that is hand-duplicated between the Python server and the Android client.

| Fixture | Exercises (Python) | Android counterpart |
|---|---|---|
| `normalize_cases.json` | `services/sync_matcher.py::normalize_for_search` | `SyncMatcher.normalizeForSearch` |
| `match_cases.json` | `services/sync_matcher.py::match_text_to_sync_points` | `SyncMatcher.match` |
| `restore_cases.json` | `services/position_resolver.py::plan_restore` | `PositionResolver.planRestore` |
| `pair_open_target.json` | — (client-side rule) | `BookSyncRepository.resolvePairOpenTarget` |
| `audio_to_epub_cases.json` | `services/sync_engine.py::audio_to_epub` | `SyncMatcher.pointForAudioPosition` (via `BookSyncRepository.audioToEpubText`) |

Because the two implementations are maintained by hand, they can drift silently (this is
exactly what issues #46 and #41 call out). Both sides are now enforced: the Python suite
`tests/test_sync_matching.py` and the Kotlin suites `SyncMatcherParityTest` /
`MatchParityTest` load **these same files** and must agree, which makes parity mechanically
unbreakable. (Phase 2 — the Android `match_cases.json` half — landed with #41.)

## Rules for editing
- Treat these as golden vectors: change them only when the *intended* algorithm changes,
  and update **both** platforms in the same PR.
- `restore_cases.json`: `[{ "name", "why", "position": obj|null, "context": {spine_count,
  device_id, hint_kind}, "expected": [step kind, ...] }]`. `expected` is the ordered list of
  restore-step kinds (`hint`/`text`/`chapter`/`percent`/`audio`) the ladder should try.
  **An `expected` of `[]` is only ever correct when the position holds no anchor at all** —
  every other empty plan is the bug that opened a book at page one and let the next autosave
  overwrite a real position with chapter 0. `why` states what each case defends; keep it
  filled in.
- `pair_open_target.json`: `[{ "name", "why", "source": "ebook"|"audiobook"|null,
  "available": {"ebook": bool, "audiobook": bool}, "expected":
  "ebook"|"audiobook"|"details" }]`. Which format a tap on a pair opens, keyed on
  `bookmarks.source` per the contract's § "Who may claim `source`". This one has **no
  Python half** — the rule is client-side — so the pair is Android's
  `ResolvePairOpenTargetTest` and the web's `lib/pairOpenTarget.test.js`.
  `available` means "openable by this client": *downloaded* on Android, *present on the
  pair* on the web. The web keyed on the two `user_progress` rows' `updated_at` until
  issue #215; a pair-scoped write stamps both in one loop, so that comparison always tied
  and every pair opened in the reader.
- `audio_to_epub_cases.json`: `[{ "name", "why", "points": [{chapter, sentence_index,
  audio_start_ms}], "audio_position_ms", "expected_point_index": int|null }]`. Which point an
  audio position lands on — `expected_point_index` indexes `points` **as written**, so a case can
  deliberately list them out of audio order. `null` means "no point at all", which is only ever
  correct for an empty `points`; each platform expresses that in its own shape (Python falls back
  to `(0, 0)`, Kotlin returns `null`).
  **A position earlier than every point resolves to the *first* point, never to chapter 0**
  (issue #200): a map may legitimately start well into the audio, and a silent origin is
  indistinguishable from a real hit on the opening sentence — which is how a re-map used to
  relocate a bookmark to the start of the book.
- `normalize_cases.json`: `[{ "input": str, "expected": str }]`.
- `match_cases.json`: `[{ "name", "sync_points": [{chapter, sentence_index, preview,
  confidence?}], "epub_text", "chapter_hint", "expected_chapter": int|null,
  "expected_sentence_index": int|null }]`.
  `expected_chapter` / `expected_sentence_index` identify the matched point, or both `null`
  when no match is expected.
  `confidence` (0..1) is **optional** and defaults to `1.0` — set it only for cases that
  exercise the interpolated-point nudge, where `0` means "interpolated, not directly
  matched" and the matcher prefers a neighbour above `0.5` within ±3 list positions.
- Cases named `fuzzy_*` / `best_fuzzy_*` deliberately have **no common substring of 30+
  chars** with any preview, so the exact pass cannot fire and the fuzzy pass is what is
  under test. If you edit their text, re-check that.
