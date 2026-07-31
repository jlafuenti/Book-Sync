# Sync-matcher parity fixtures

These JSON files are the **cross-platform parity contract** for the reading-position
matcher that is hand-duplicated between the Python server and the Android client.

| Fixture | Exercises (Python) | Android counterpart |
|---|---|---|
| `normalize_cases.json` | `services/sync_matcher.py::normalize_for_search` | `SyncMatcher.normalizeForSearch` |
| `match_cases.json` | `services/sync_matcher.py::match_text_to_sync_points` | `SyncMatcher.match` |
| `restore_cases.json` | `services/position_resolver.py::plan_restore` | `PositionResolver.planRestore` |

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
