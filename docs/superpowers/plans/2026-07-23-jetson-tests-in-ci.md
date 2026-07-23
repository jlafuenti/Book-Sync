# Jetson Tests in CI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire `jetson/test_server.py` into CI on every PR touching `jetson/**`, and add failing-test-first coverage for the oversized-chunk shrink/retry/clean-failure decision logic in `jetson/server.py::_transcribe_file`.

**Architecture:** Stub the two hardware/network-dependent imports (`nltk`, `faster_whisper`) in a new `jetson/conftest.py` so `server.py` can be imported with only `fastapi`/`uvicorn` installed. Add a path-filtered GitHub Actions workflow that installs those light deps and runs `pytest`. Extract the chunk-size shrink/abort decision inside `_transcribe_file` into two pure, side-effect-free functions and unit-test them directly, then re-point the existing inline logic (duplicated across the OoM and oversized-chunk except-blocks) at those functions.

**Tech Stack:** Python 3.12, pytest, FastAPI, GitHub Actions.

## Global Constraints

- CI must install **only** `fastapi`, `uvicorn`, and `pytest` for the jetson job — never `faster-whisper`, `torch`, or a real `nltk` data corpus (issue #80, matches the existing pattern in `.github/workflows/tests.yml` of excluding the heavy transcription stack).
- The new workflow must be path-filtered to `jetson/**` (issue #80 acceptance criteria: "CI job runs jetson tests on every PR touching `jetson/**`").
- TDD: write the failing test before the implementation for every step (per `CLAUDE.md`).
- Do not change `_transcribe_file`'s observable behavior — the extraction must be behavior-preserving (same retry counts, same log messages, same exception propagated on abort).
- Do not run `npm run build`, Docker, or deploy commands.

---

### Task 1: Stub hardware/network deps so `server.py` imports without the Jetson stack

**Files:**
- Create: `jetson/conftest.py`
- Test: `jetson/test_server.py` (existing — verifies the stub works by continuing to pass)

**Interfaces:**
- Produces: `sys.modules["nltk"]` and `sys.modules["faster_whisper"]` pre-populated with minimal stand-ins before any test module imports `server`. Later tasks' tests (Task 3) rely on `import server as jetson_server` succeeding under this stub with no other setup.

- [ ] **Step 1: Confirm current behavior without the stub (sanity check, not a real test-first step since this is infra, not logic)**

Run: `cd jetson && python -m pytest test_server.py -v`
Expected: `ModuleNotFoundError: No module named 'nltk'` (or similar) unless `nltk`/`faster_whisper` happen to already be installed in your environment. This confirms the suite currently depends on the Jetson stack being present.

- [ ] **Step 2: Write `jetson/conftest.py`**

```python
"""
Pytest bootstrap for the jetson test suite.

CI installs only the light deps (fastapi, uvicorn, pytest) — not the
Jetson-only stack: faster-whisper needs a GPU/CTranslate2 build that only
exists on the device, and nltk's sentence-tokenizer data (punkt_tab) is
fetched over the network on first use. Stub both modules in sys.modules
before any test imports `server`, so the import succeeds without either.
"""
import sys
import types

if "nltk" not in sys.modules:
    nltk_stub = types.ModuleType("nltk")
    nltk_data_stub = types.ModuleType("nltk.data")
    nltk_data_stub.find = lambda *args, **kwargs: None
    nltk_stub.data = nltk_data_stub
    nltk_stub.download = lambda *args, **kwargs: None
    nltk_stub.sent_tokenize = lambda text: [text]
    sys.modules["nltk"] = nltk_stub
    sys.modules["nltk.data"] = nltk_data_stub

if "faster_whisper" not in sys.modules:
    faster_whisper_stub = types.ModuleType("faster_whisper")
    faster_whisper_stub.WhisperModel = object
    sys.modules["faster_whisper"] = faster_whisper_stub
```

- [ ] **Step 3: Run the existing suite in an environment without `nltk`/`faster_whisper` installed to verify the stub works**

Run: `cd jetson && python -m pytest test_server.py -v`
Expected: all 6 existing tests PASS (auth guard tests + route-metadata test), with no `ModuleNotFoundError`.

- [ ] **Step 4: Commit**

```bash
git add jetson/conftest.py
git commit -m "test(jetson): stub nltk/faster_whisper so server.py imports without the GPU stack"
```

---

### Task 2: Wire jetson tests into CI, path-filtered to `jetson/**`

**Files:**
- Create: `.github/workflows/jetson-tests.yml`

**Interfaces:**
- Consumes: `jetson/conftest.py` (Task 1) and `jetson/test_server.py`, run as-is via `pytest`.

- [ ] **Step 1: Write `.github/workflows/jetson-tests.yml`**

```yaml
name: Jetson tests

on:
  push:
    branches: ["**"]
    paths:
      - "jetson/**"
  pull_request:
    paths:
      - "jetson/**"

jobs:
  pytest:
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: jetson

    steps:
      - uses: actions/checkout@v4

      - name: Set up Python 3.12
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: pip

      - name: Install light dependencies
        # Only fastapi/uvicorn/pytest — NOT faster-whisper (GPU-only build,
        # not pip-installable off-device) or nltk's data corpus (network
        # download). conftest.py stubs both modules so server.py can still
        # be imported for unit testing.
        run: |
          python -m pip install --upgrade pip
          pip install "fastapi>=0.110.0" "uvicorn>=0.27.0" pytest

      - name: Run tests
        run: python -m pytest test_server.py -v
```

- [ ] **Step 2: Verify the workflow is valid YAML and matches the repo's existing style**

Run: `cd "$(git rev-parse --show-toplevel)" && python -c "import yaml; yaml.safe_load(open('.github/workflows/jetson-tests.yml'))"`
Expected: no output (parses cleanly). Compare structure against `.github/workflows/tests.yml` and `.github/workflows/web-tests.yml` for consistency (already done in the step above — same `defaults.run.working-directory` + `actions/setup-python` pattern).

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/jetson-tests.yml
git commit -m "ci: run jetson tests on PRs touching jetson/**"
```

- [ ] **Step 4: Push the branch and confirm the new workflow actually runs and passes on GitHub**

Run: `git push -u origin HEAD`
Expected: after pushing, the "Jetson tests" check appears on the PR (or commit) and passes. This is the real verification that the path filter and light-dependency install work end-to-end in Actions, not just locally.

---

### Task 3: Extract the oversized-chunk decision logic into pure functions, with failing-test-first coverage

**Files:**
- Modify: `jetson/server.py` (add two functions near the `OversizedChunkError` class around line 198–207; rewire `_transcribe_file`'s two except-blocks around lines 490–505, 572–591, and 593–619)
- Modify: `jetson/test_server.py` (append tests)

**Interfaces:**
- Produces:
  - `_is_chunk_oversized(actual_chunk_sec: float, requested_chunk_size: int) -> bool` — `True` when `actual_chunk_sec > requested_chunk_size * OVERSIZED_CHUNK_TOLERANCE`.
  - `_shrink_chunk_size(current_chunk_size: int) -> Optional[int]` — returns the halved size (`current_chunk_size // 2`), or `None` once that halved size would fall below `MIN_CHUNK_SIZE_SEC` (signals "give up, don't retry again").
- Consumes: existing module constants `OVERSIZED_CHUNK_TOLERANCE` (1.5) and `MIN_CHUNK_SIZE_SEC` (112), and `DEFAULT_CHUNK_SIZE_SEC` (900) for the retry-sequence test.

- [ ] **Step 1: Write the failing tests in `jetson/test_server.py`**

Append to the end of the file:

```python
def test_is_chunk_oversized_true_when_ffmpeg_returns_excess_audio():
    # OVERSIZED_CHUNK_TOLERANCE is 1.5x; 200s actual for a 100s request exceeds it
    assert jetson_server._is_chunk_oversized(actual_chunk_sec=200, requested_chunk_size=100) is True


def test_is_chunk_oversized_false_within_tolerance():
    assert jetson_server._is_chunk_oversized(actual_chunk_sec=140, requested_chunk_size=100) is False


def test_shrink_chunk_size_halves_the_value():
    assert jetson_server._shrink_chunk_size(900) == 450


def test_shrink_chunk_size_retry_sequence_reaches_clean_failure():
    """Simulates the retry loop: keep shrinking until the pure function signals
    abort (None), matching the oversized -> shrink/retry -> clean failure path."""
    sizes = []
    size = jetson_server.DEFAULT_CHUNK_SIZE_SEC  # 900
    while True:
        size = jetson_server._shrink_chunk_size(size)
        if size is None:
            break
        sizes.append(size)
    # 900 -> 450 -> 225 -> 112 (still >= MIN_CHUNK_SIZE_SEC=112, so one more retry)
    # -> 56 (< 112, abort)
    assert sizes == [450, 225, 112]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd jetson && python -m pytest test_server.py -v -k "oversized or shrink"`
Expected: 4 FAILED with `AttributeError: module 'server' has no attribute '_is_chunk_oversized'` (and similarly for `_shrink_chunk_size`).

- [ ] **Step 3: Add the two pure functions to `jetson/server.py`**

Insert immediately after the `OversizedChunkError` class definition (after line 207, before the `# --- Checkpoint system` comment block):

```python
def _is_chunk_oversized(actual_chunk_sec: float, requested_chunk_size: int) -> bool:
    """True if ffmpeg returned far more audio than requested (see OversizedChunkError)."""
    return actual_chunk_sec > requested_chunk_size * OVERSIZED_CHUNK_TOLERANCE


def _shrink_chunk_size(current_chunk_size: int) -> Optional[int]:
    """Halve the chunk size after an oversized-chunk or OoM failure.

    Shared by both recovery paths in `_transcribe_file`. Returns the halved
    size to retry with, or None once that size would drop below
    MIN_CHUNK_SIZE_SEC — the caller should treat None as a signal to stop
    retrying and propagate the original failure instead.
    """
    new_size = current_chunk_size // 2
    if new_size < MIN_CHUNK_SIZE_SEC:
        return None
    return new_size
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd jetson && python -m pytest test_server.py -v -k "oversized or shrink"`
Expected: 4 PASSED.

- [ ] **Step 5: Rewire `_transcribe_file` to use the new functions (behavior-preserving refactor)**

In `jetson/server.py`, replace the inline oversized check inside the chunk-processing `try` block:

```python
                actual_chunk_sec = len(audio_array) / 16000
                if actual_chunk_sec > current_chunk_size * OVERSIZED_CHUNK_TOLERANCE:
                    del audio_array
                    raise OversizedChunkError(
                        f"ffmpeg returned {actual_chunk_sec:.1f}s of audio for a "
                        f"{current_chunk_size}s request at {start_sec}s — likely a "
                        f"timestamp discontinuity in a malformed/corrupt source file"
                    )
```

with:

```python
                actual_chunk_sec = len(audio_array) / 16000
                if _is_chunk_oversized(actual_chunk_sec, current_chunk_size):
                    del audio_array
                    raise OversizedChunkError(
                        f"ffmpeg returned {actual_chunk_sec:.1f}s of audio for a "
                        f"{current_chunk_size}s request at {start_sec}s — likely a "
                        f"timestamp discontinuity in a malformed/corrupt source file"
                    )
```

Then replace the `except OversizedChunkError as e:` block:

```python
            except OversizedChunkError as e:
                logger.error(f"  Oversized chunk at {start_sec}s: {e}")

                # No transcribe() call happened, so there's nothing to reload —
                # just shrink and retry at the same position.
                current_chunk_size = current_chunk_size // 2
                if current_chunk_size < MIN_CHUNK_SIZE_SEC:
                    logger.error(
                        f"  Chunk size {current_chunk_size}s below minimum "
                        f"({MIN_CHUNK_SIZE_SEC}s) and ffmpeg is still returning "
                        f"oversized audio at {start_sec}s. Aborting — source file "
                        f"is likely corrupt/malformed and needs to be re-imported."
                    )
                    raise
                logger.info(
                    f"  Retrying @ {start_sec}s with reduced chunk size "
                    f"{current_chunk_size}s"
                )
                consecutive_small_successes = 0
                # Do NOT advance start_sec — retry same position
```

with:

```python
            except OversizedChunkError as e:
                logger.error(f"  Oversized chunk at {start_sec}s: {e}")

                # No transcribe() call happened, so there's nothing to reload —
                # just shrink and retry at the same position.
                new_size = _shrink_chunk_size(current_chunk_size)
                if new_size is None:
                    logger.error(
                        f"  Chunk size below minimum ({MIN_CHUNK_SIZE_SEC}s) and "
                        f"ffmpeg is still returning oversized audio at {start_sec}s. "
                        f"Aborting — source file is likely corrupt/malformed and "
                        f"needs to be re-imported."
                    )
                    raise
                current_chunk_size = new_size
                logger.info(
                    f"  Retrying @ {start_sec}s with reduced chunk size "
                    f"{current_chunk_size}s"
                )
                consecutive_small_successes = 0
                # Do NOT advance start_sec — retry same position
```

Then replace the shrink logic inside the OoM `except Exception as e:` block:

```python
                    # Halve chunk size and retry
                    current_chunk_size = current_chunk_size // 2
                    if current_chunk_size < MIN_CHUNK_SIZE_SEC:
                        logger.error(
                            f"  Chunk size {current_chunk_size}s below minimum "
                            f"({MIN_CHUNK_SIZE_SEC}s). Cannot recover."
                        )
                        raise
                    logger.info(
                        f"  Retrying @ {start_sec}s with reduced chunk size "
                        f"{current_chunk_size}s"
                    )
                    consecutive_small_successes = 0
                    # Do NOT advance start_sec — retry same position
```

with:

```python
                    # Halve chunk size and retry
                    new_size = _shrink_chunk_size(current_chunk_size)
                    if new_size is None:
                        logger.error(
                            f"  Chunk size below minimum ({MIN_CHUNK_SIZE_SEC}s). "
                            f"Cannot recover."
                        )
                        raise
                    current_chunk_size = new_size
                    logger.info(
                        f"  Retrying @ {start_sec}s with reduced chunk size "
                        f"{current_chunk_size}s"
                    )
                    consecutive_small_successes = 0
                    # Do NOT advance start_sec — retry same position
```

- [ ] **Step 6: Run the full jetson suite to confirm nothing broke**

Run: `cd jetson && python -m pytest test_server.py -v`
Expected: all tests PASS (6 original + 4 new = 10).

- [ ] **Step 7: Commit**

```bash
git add jetson/server.py jetson/test_server.py
git commit -m "test(jetson): extract oversized-chunk shrink/abort logic into pure functions"
```

---

### Task 4: Update `docs/testing.md`

**Files:**
- Modify: `docs/testing.md:189-201` (the "## Jetson (`jetson/`)" section)

**Interfaces:**
- None (documentation only).

- [ ] **Step 1: Replace the Jetson section**

Replace:

```markdown
## Jetson (`jetson/`)

`jetson/test_server.py` covers the shared-secret auth guard. It is **not wired into CI**
(the service's faster-whisper/ffmpeg stack isn't installed there), so run it manually
whenever `jetson/server.py` changes:

```bash
cd jetson && python -m pytest test_server.py -v
```

The transcription pipeline itself (chunking, oversized-chunk OOM guard) has no automated
tests; the client-side retry logic is covered in
`server/tests/test_remote_transcription_provider.py`.
```

with:

```markdown
## Jetson (`jetson/`)

`jetson/test_server.py` covers the shared-secret auth guard and the oversized-chunk
decision logic (`_is_chunk_oversized` / `_shrink_chunk_size` in `jetson/server.py`).
It **is wired into CI** (`.github/workflows/jetson-tests.yml`, path-filtered to
`jetson/**`) — `jetson/conftest.py` stubs `nltk` and `faster_whisper` in `sys.modules`
so the suite runs with only `fastapi`/`uvicorn`/`pytest` installed, no GPU or network
access needed. Run it locally with:

```bash
cd jetson && python -m pytest test_server.py -v
```

The rest of the transcription pipeline (actual ffmpeg chunk loading, faster-whisper
transcription, checkpointing) still has no automated test — that needs real audio and
a GPU, which is out of scope for this suite. The client-side instance_id retry logic is
covered in `server/tests/test_remote_transcription_provider.py`.
```

- [ ] **Step 2: Commit**

```bash
git add docs/testing.md
git commit -m "docs: update jetson testing section for CI wiring"
```

---

## Self-Review Notes

- **Spec coverage:** Acceptance criterion 1 ("CI job runs jetson tests on every PR touching `jetson/**`") → Task 2. Criterion 2 ("Failing-test-first coverage for the oversized-chunk shrink/retry/fail path") → Task 3. The "extract into a pure function" instruction from the issue body → Task 3, Steps 3 & 5. The "stub faster_whisper/nltk via sys.modules shims in a conftest" instruction → Task 1.
- **Type consistency:** `_shrink_chunk_size` is used identically in Task 3 Steps 1 (test), 3 (definition), and 5 (call sites) — `Optional[int]` return, `None` sentinel for abort. `_is_chunk_oversized` signature (`actual_chunk_sec: float, requested_chunk_size: int) -> bool`) matches across test and call site.
- **Behavior preservation:** The rewired except-blocks in Task 3 Step 5 keep the same log messages (trimmed to remove the now-stale `{current_chunk_size}s below minimum` phrasing that referenced a value the pure function no longer exposes at the call site) and the same bare `raise` (re-raising whatever exception is currently being handled) once `_shrink_chunk_size` returns `None`.
