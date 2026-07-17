"""
Audio integrity validation.

Audiobook files that arrive via flaky imports (interrupted downloads, Audible
CLI returning HTML error pages instead of audio, etc.) can be truncated or have
corrupt media streams. Such files cannot be transcribed and, without an early
check, waste a full upload + several retries against the remote Whisper server
before failing with a cryptic error.

`check_audio_integrity` performs a two-stage, full-decode validation:

  1. ffprobe the container header / duration. Catches truncated or unreadable
     containers (e.g. a `moov` atom that extends past EOF) instantly.
  2. Full decode via `ffmpeg -v error -i <file> -f null -`. Catches mid-stream
     corruption (e.g. an AAC stream riddled with invalid packets) that a header
     probe alone would miss.

Designed to run on the server, where audiobook files are locally mounted and
ffmpeg/ffprobe are available, BEFORE anything is sent to the remote transcriber.
"""

import logging
import subprocess
from typing import Tuple

logger = logging.getLogger(__name__)

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


def check_audio_integrity(path: str) -> Tuple[bool, str]:
    """
    Validate that an audio file is fully decodable.

    Returns (ok, detail). When ok is False, `detail` is a short human-readable
    reason suitable for surfacing to the user (e.g. in a queue error message).
    This function never raises for media problems — only the caller decides how
    to react.
    """
    # Stage 1: header / duration probe.
    probe_cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        path,
    ]
    try:
        probe = subprocess.run(
            probe_cmd, capture_output=True, text=True, timeout=_PROBE_TIMEOUT_SEC
        )
    except subprocess.TimeoutExpired:
        return False, "ffprobe timed out reading the file header"
    except FileNotFoundError:
        # ffprobe missing from the environment — treat as a config error, not a
        # corrupt file. Pass the gate so we don't block on a tooling problem.
        logger.warning("ffprobe not found; skipping audio integrity check for %s", path)
        return True, "integrity check skipped (ffprobe unavailable)"

    if probe.returncode != 0:
        reason = (probe.stderr or "").strip().splitlines()
        reason_str = reason[-1] if reason else "unreadable container header"
        return False, f"ffprobe failed: {reason_str}"

    duration_str = (probe.stdout or "").strip()
    try:
        duration = float(duration_str)
        if duration <= 0:
            return False, "reported audio duration is zero"
    except ValueError:
        return False, f"could not parse audio duration ({duration_str!r})"

    # Stage 2: full decode. `-v error` keeps stderr to genuine problems; counting
    # those lines distinguishes a fully-decodable file from a corrupt one.
    decode_cmd = [
        "ffmpeg", "-nostdin", "-v", "error",
        "-i", path,
        "-ac", "1", "-ar", "16000",
        "-f", "null", "-",
    ]
    try:
        decode = subprocess.run(
            decode_cmd, capture_output=True, text=True, timeout=_DECODE_TIMEOUT_SEC
        )
    except subprocess.TimeoutExpired:
        return False, f"full decode exceeded {_DECODE_TIMEOUT_SEC}s (likely corrupt)"

    stderr = decode.stderr or ""
    error_lines = [ln for ln in stderr.splitlines() if ln.strip()]

    if decode.returncode != 0:
        last = error_lines[-1] if error_lines else "decode failed"
        return False, f"decode failed (exit {decode.returncode}): {last}"

    if stderr_indicates_corruption(stderr) and len(error_lines) >= _MAX_DECODE_ERRORS:
        return False, (
            f"{len(error_lines)} decode errors during full decode "
            f"(e.g. {error_lines[0][:160]})"
        )

    return True, f"ok ({duration:.0f}s, fully decoded)"
