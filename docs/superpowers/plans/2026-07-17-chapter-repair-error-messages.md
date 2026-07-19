# Chapter Repair Error Message Improvements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When the Troubleshoot Library "chapter repair" action fails, show a short, readable error (last 2-3 stderr lines, not the full ffmpeg banner) and, when the failure looks like genuine media corruption rather than a chapter-title issue, point the user at the existing "Corrupt audiobooks" / Replace workflow instead.

**Architecture:** `services/chapter_repair.py`'s `repair_chapter_encoding` currently returns the raw, undecoded `stderr` bytes as the error string on ffmpeg failure — this can be several KB of version/build banner. Two changes: (1) truncate to the last few non-empty stderr lines, mirroring the existing pattern in `services/audio_integrity.py`; (2) reuse `audio_integrity`'s corruption-marker detection (promoted from private to public names) to decide whether to append a guidance suffix.

**Tech Stack:** Python, pytest, subprocess (ffmpeg).

## Global Constraints
- Follow TDD: write the failing test before the implementation change, for every step below.
- Run the affected test file after each change; don't move to the next task with a red test.
- No behavior change to `check_audio_integrity`'s public contract — only its two private helpers become public names.

---

### Task 1: Promote `audio_integrity`'s corruption-marker helpers to public names

**Files:**
- Modify: `server/services/audio_integrity.py:29-53`
- Test: `server/tests/test_chapter_repair.py` (new tests added in Task 2 will import from `audio_integrity`, proving the public names work — no separate test file needed for this rename since `audio_integrity.py` has no existing dedicated unit test file to update)

**Interfaces:**
- Produces: `services.audio_integrity.CORRUPTION_MARKERS: tuple[str, ...]` and `services.audio_integrity.stderr_indicates_corruption(stderr: str) -> bool` — both importable by `services/chapter_repair.py` in Task 2.

- [ ] **Step 1: Rename the private constant and function to public names**

In `server/services/audio_integrity.py`, replace lines 28-53:

```python
# Substrings in ffmpeg/ffprobe stderr that indicate genuine media corruption.
_CORRUPTION_MARKERS = (
    "invalid data found",
    "error submitting packet",
    "error reading header",
    "moov atom not found",
    "could not find codec parameters",
    "partial file",
    "truncat",
)

# A small number of decode warnings can occur even on healthy files; require a
# few before declaring the file corrupt. The confirmed-bad files in the wild
# produced tens of thousands, so this threshold is comfortably safe.
_MAX_DECODE_ERRORS = 5

# ffprobe should be near-instant; the full decode of a ~26h book runs at
# thousands-of-x realtime but we still cap it generously to avoid hanging the
# queue on a pathological input.
_PROBE_TIMEOUT_SEC = 120
_DECODE_TIMEOUT_SEC = 1800


def _stderr_has_corruption(stderr: str) -> bool:
    low = stderr.lower()
    return any(marker in low for marker in _CORRUPTION_MARKERS)
```

with:

```python
# Substrings in ffmpeg/ffprobe stderr that indicate genuine media corruption.
# Public: also used by services.chapter_repair to distinguish "ffmpeg can't
# open this file at all" (deeper corruption) from other repair failures
# (missing binary, disk full, permissions) when a chapter-title repair fails.
CORRUPTION_MARKERS = (
    "invalid data found",
    "error submitting packet",
    "error reading header",
    "moov atom not found",
    "could not find codec parameters",
    "partial file",
    "truncat",
)

# A small number of decode warnings can occur even on healthy files; require a
# few before declaring the file corrupt. The confirmed-bad files in the wild
# produced tens of thousands, so this threshold is comfortably safe.
_MAX_DECODE_ERRORS = 5

# ffprobe should be near-instant; the full decode of a ~26h book runs at
# thousands-of-x realtime but we still cap it generously to avoid hanging the
# queue on a pathological input.
_PROBE_TIMEOUT_SEC = 120
_DECODE_TIMEOUT_SEC = 1800


def stderr_indicates_corruption(stderr: str) -> bool:
    low = stderr.lower()
    return any(marker in low for marker in CORRUPTION_MARKERS)
```

- [ ] **Step 2: Update the one internal call site**

In the same file, find `_stderr_has_corruption(stderr)` (inside `check_audio_integrity`, around the `_MAX_DECODE_ERRORS` check) and replace with `stderr_indicates_corruption(stderr)`.

- [ ] **Step 3: Confirm nothing else references the old private names**

Run: `grep -rn "_CORRUPTION_MARKERS\|_stderr_has_corruption" server/`
Expected: no matches (the rename is complete).

- [ ] **Step 4: Run the existing audio_integrity-dependent tests to confirm no regression**

Run: `cd server && python -m pytest -q tests/ -k audio_integrity`
Expected: PASS (or "no tests collected" if none exist yet — either is fine, this is a pure rename with no behavior change)

- [ ] **Step 5: Commit**

```bash
git add server/services/audio_integrity.py
git commit -m "Promote audio_integrity corruption-marker helpers to public names

Needed by services.chapter_repair to distinguish genuine media
corruption from other repair failures."
```

---

### Task 2: Truncate ffmpeg stderr and add corruption guidance in `repair_chapter_encoding`

**Files:**
- Modify: `server/services/chapter_repair.py:88-129`
- Test: `server/tests/test_chapter_repair.py:156-...` (existing `TestRepairChapterEncoding` class)

**Interfaces:**
- Consumes: `services.audio_integrity.stderr_indicates_corruption(stderr: str) -> bool` (Task 1).
- Produces: `services.chapter_repair.repair_chapter_encoding(filepath, timeout=60) -> tuple[bool, Optional[str]]` — same signature as before, only the error string content changes on failure. No other module needs to change since callers (`routers/troubleshoot.py`, `services/abs_metadata.py`) only branch on the boolean and pass the string through untouched.

- [ ] **Step 1: Write the failing tests**

In `server/tests/test_chapter_repair.py`, replace the existing `test_export_failure_returns_error` and `test_reinject_failure_returns_error` methods (lines 156-... through the end of `TestRepairChapterEncoding`) with:

```python
    def test_export_failure_truncates_to_last_lines(self, monkeypatch, tmp_path):
        filepath = str(tmp_path / "book.m4b")
        banner = "\n".join(f"banner line {i}" for i in range(20))
        real_error = "error reading header\nInvalid data found when processing input"

        def fake_run(cmd, capture_output=True, timeout=None):
            return _FakeCompletedProcess(
                returncode=1, stderr=f"{banner}\n{real_error}".encode()
            )

        monkeypatch.setattr(subprocess_module, "run", fake_run)

        ok, error = chapter_repair.repair_chapter_encoding(filepath)

        assert ok is False
        assert "banner line" not in error
        assert "error reading header" in error
        assert "Invalid data found when processing input" in error

    def test_export_failure_with_corruption_signature_adds_guidance(self, monkeypatch, tmp_path):
        filepath = str(tmp_path / "book.m4b")

        def fake_run(cmd, capture_output=True, timeout=None):
            return _FakeCompletedProcess(
                returncode=1,
                stderr=b"Invalid data found when processing input",
            )

        monkeypatch.setattr(subprocess_module, "run", fake_run)

        ok, error = chapter_repair.repair_chapter_encoding(filepath)

        assert ok is False
        assert "Corrupt audiobooks" in error

    def test_export_failure_without_corruption_signature_has_no_guidance(self, monkeypatch, tmp_path):
        filepath = str(tmp_path / "book.m4b")

        def fake_run(cmd, capture_output=True, timeout=None):
            return _FakeCompletedProcess(returncode=1, stderr=b"Permission denied")

        monkeypatch.setattr(subprocess_module, "run", fake_run)

        ok, error = chapter_repair.repair_chapter_encoding(filepath)

        assert ok is False
        assert "Corrupt audiobooks" not in error

    def test_reinject_failure_truncates_and_adds_guidance(self, monkeypatch, tmp_path):
        filepath = str(tmp_path / "book.m4b")
        banner = "\n".join(f"banner line {i}" for i in range(20))

        def fake_run(cmd, capture_output=True, timeout=None):
            if "-f" in cmd and "ffmetadata" in cmd:
                export_path = cmd[-1]
                with open(export_path, "wb") as f:
                    f.write(b";FFMETADATA1\n[CHAPTER]\ntitle=Fine\n")
                return _FakeCompletedProcess(returncode=0)
            return _FakeCompletedProcess(
                returncode=1,
                stderr=f"{banner}\nmoov atom not found".encode(),
            )

        monkeypatch.setattr(subprocess_module, "run", fake_run)

        ok, error = chapter_repair.repair_chapter_encoding(filepath)

        assert ok is False
        assert "banner line" not in error
        assert "moov atom not found" in error
        assert "Corrupt audiobooks" in error
```

Also delete the old `test_export_failure_returns_error` and `test_reinject_failure_returns_error` methods entirely (they're superseded by the four tests above, which cover the same scenarios plus truncation/guidance).

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `cd server && python -m pytest -q tests/test_chapter_repair.py::TestRepairChapterEncoding -v`
Expected: FAIL — `test_export_failure_truncates_to_last_lines` and `test_reinject_failure_truncates_and_adds_guidance` fail because `error` still contains `"banner line"` (no truncation yet); `test_export_failure_with_corruption_signature_adds_guidance` and `test_reinject_failure_truncates_and_adds_guidance` fail because `"Corrupt audiobooks"` isn't in `error` yet.

- [ ] **Step 3: Implement truncation and guidance**

In `server/services/chapter_repair.py`, add the import and helper function after the existing imports (after line 25's `logger = logging.getLogger(__name__)`):

```python
from services.audio_integrity import stderr_indicates_corruption

_CORRUPTION_GUIDANCE = (
    " — this looks like deeper file corruption, not just a chapter-title "
    "issue. Check 'Corrupt audiobooks' in Troubleshoot Library or replace "
    "the file."
)


def _format_ffmpeg_error(prefix: str, stderr_bytes: bytes, n: int = 3) -> str:
    """Build a short error message from ffmpeg stderr: the last `n` non-empty
    lines (ffmpeg's real error is always at the end; everything before it is
    the version/build banner, which is useless noise to show the user), plus
    guidance when the failure looks like genuine media corruption rather than
    the narrower chapter-title-encoding issue this module fixes."""
    text = stderr_bytes.decode(errors="replace")
    lines = [ln for ln in text.splitlines() if ln.strip()]
    tail = "\n".join(lines[-n:])
    message = f"{prefix}: {tail}"
    if stderr_indicates_corruption(text):
        message += _CORRUPTION_GUIDANCE
    return message
```

Then replace the two failure branches inside `repair_chapter_encoding` (currently lines 104-105 and 118-119):

```python
        if export.returncode != 0:
            return False, f"Failed to export metadata: {export.stderr.decode(errors='replace')}"
```
becomes:
```python
        if export.returncode != 0:
            return False, _format_ffmpeg_error("Failed to export metadata", export.stderr)
```

and:
```python
        if reinject.returncode != 0:
            return False, f"Failed to reinject metadata: {reinject.stderr.decode(errors='replace')}"
```
becomes:
```python
        if reinject.returncode != 0:
            return False, _format_ffmpeg_error("Failed to reinject metadata", reinject.stderr)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd server && python -m pytest -q tests/test_chapter_repair.py -v`
Expected: PASS — all tests in the file, including the untouched `TestDecodeLenient`, `TestFixFfmetadataBytes`, and `TestCheckChapterEncoding` classes.

- [ ] **Step 5: Commit**

```bash
git add server/services/chapter_repair.py server/tests/test_chapter_repair.py
git commit -m "Truncate ffmpeg error messages and guide toward Corrupt audiobooks

repair_chapter_encoding was returning ffmpeg's full stderr on failure,
including the entire version/build banner (a Winter's Heart repair
attempt surfaced ~2KB of banner text as the error). Now shows just the
last few real error lines, and when the failure signature indicates
genuine container corruption (ffmpeg couldn't open the file at all,
as with Winter's Heart) rather than the narrower non-UTF-8
chapter-title issue this module targets, points the user at the
existing Corrupt audiobooks / Replace workflow instead."
```

---

### Task 3: Full regression check

**Files:** none (verification only)

- [ ] **Step 1: Run the full backend suite**

Run: `cd server && python -m pytest -q`
Expected: PASS, same pass count as before this plan plus the 2 net-new tests (4 new − 2 removed from Task 2's Step 1), 0 failed.

- [ ] **Step 2: Confirm no frontend changes are needed**

This plan only touches `server/services/audio_integrity.py` and `server/services/chapter_repair.py` — no API response shape changed (still `tuple[bool, Optional[str]]`), so `web/` needs no changes and its test suite doesn't need re-running. State this explicitly rather than re-running it, to avoid an unnecessary step.

- [ ] **Step 3: Push and let CI confirm**

```bash
git push
```

Then check `gh pr checks <PR number>` once CI finishes, confirming all jobs (pytest, vitest, migrations) still pass.
