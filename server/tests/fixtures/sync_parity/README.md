# Sync-matcher parity fixtures

These JSON files are the **cross-platform parity contract** for the reading-position
matcher that is hand-duplicated between the Python server and the Android client.

| Fixture | Exercises (Python) | Android counterpart |
|---|---|---|
| `normalize_cases.json` | `routers/sync.py::_normalize_for_search` | `normalizeForSearch` |
| `match_cases.json` | `routers/sync.py::_match_text_to_sync_points` | `getSyncPointForEpubText` |

Because the two implementations are maintained by hand, they can drift silently (this is
exactly what issue #46 calls out). The Python suite `tests/test_sync_matching.py` loads
these vectors and asserts the server matcher's output. When Android JUnit tests are added
(Phase 2), they should load **these same files** and assert the Kotlin matcher produces
identical results — that makes parity mechanically enforceable on both sides.

## Rules for editing
- Treat these as golden vectors: change them only when the *intended* algorithm changes,
  and update **both** platforms in the same PR.
- `normalize_cases.json`: `[{ "input": str, "expected": str }]`.
- `match_cases.json`: `[{ "name", "sync_points": [{chapter, sentence_index, preview}],
  "epub_text", "chapter_hint", "expected_chapter": int|null,
  "expected_sentence_index": int|null }]`.
  `expected_chapter` / `expected_sentence_index` identify the matched point, or both `null`
  when no match is expected.
