"""
Audio integrity validation.

Audiobook files that arrive via flaky imports (interrupted downloads, Audible
CLI returning HTML error pages instead of audio, etc.) can be truncated or have
corrupt media streams. Such files cannot be transcribed and, without an early
check, waste a full upload + several retries against the remote Whisper server
before failing with a cryptic error.

`check_audio_integrity` performs a two-stage, full-decode validation:

  1. ffprobe the container header / duration. Catches truncated or unreadable
     containers (e.g. a `moov` atom that extends past EOF) instantly. The same
     probe reads the audio stream's frame count, which catches a *padded*
     file: one whose container states far more time than its stream holds
     (issue #796). ffmpeg's decode below follows the stream's timestamps
     across the gaps and so "fully decodes" a padded file to its stated
     length; only the sample count gives it away.
  2. Full decode via `ffmpeg -v error -i <file> -f null -`. Catches mid-stream
     corruption (e.g. an AAC stream riddled with invalid packets) that a header
     probe alone would miss.

Designed to run on the server, where audiobook files are locally mounted and
ffmpeg/ffprobe are available, BEFORE anything is sent to the remote transcriber.
"""

import json
import logging
import subprocess
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

# A file whose header sample count accounts for less than this fraction of its
# stated duration is padded (issue #796). Measured on a real library of 446
# audiobooks: 413 could be measured this way (the 33 MP3s carry no frame count
# in their header), with a median ratio of 1.000, 1st percentile 0.998, minimum
# 0.929 (a file whose transcript nevertheless covers 99.9% of its stated length,
# so noise rather than padding) and maximum 1.050. The padded file that
# prompted this measured 0.26. 0.80 sits well clear of both.
PADDED_AUDIO_MAX_RATIO = 0.80

# Samples per frame, for turning a header frame count into seconds. AAC-LC
# (and the rarer Main/LTP profiles) code 1024 samples per frame. HE-AAC and
# HE-AACv2 code 1024 at the core rate, but ffprobe reports the doubled SBR
# output rate, so 2048 at the reported rate — the three HE-AAC files in the
# measurement above agreed. Any other AAC profile (LD/ELD use 480/512), a
# missing profile, or another codec: not judged.
_AAC_SAMPLES_PER_FRAME = {
    "LC": 1024,
    "Main": 1024,
    "LTP": 1024,
    "HE-AAC": 2048,
    "HE-AACv2": 2048,
}
# MPEG-1 Layer III (32-48 kHz) codes 1152 samples per frame; MPEG-2 and 2.5
# (below 32 kHz) code 576. MP3 headers seldom carry a frame count at all, in
# which case the file is not judged.
_MP3_SAMPLES_PER_FRAME_MPEG1 = 1152
_MP3_SAMPLES_PER_FRAME_LOW_RATE = 576
_MP3_MPEG1_MIN_RATE = 32000

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

# Prefix of the detail returned when the file was actually decoded end to end.
# Every other passing detail means "we could not check", not "this file is
# fine" — see `is_unverified`.
_VERIFIED_DETAIL_PREFIX = "ok"


def is_unverified(detail: str) -> bool:
    """Whether a passing result came from a real decode or from a skipped one.

    `check_audio_integrity` returns ok=True both when it decoded the whole
    file and when it could not check at all (no ffmpeg/ffprobe, decode
    timeout). The caller must not silently treat the second as a clean bill of
    health: it logs and surfaces the detail instead (issue #245).
    """
    return not detail.startswith(_VERIFIED_DETAIL_PREFIX)


def stderr_indicates_corruption(stderr: str) -> bool:
    low = stderr.lower()
    return any(marker in low for marker in CORRUPTION_MARKERS)


def _run_header_probe(path: str) -> subprocess.CompletedProcess:
    """ffprobe the container duration and the first audio stream's frame count.

    Header only: nothing is decoded. Raises `subprocess.TimeoutExpired` or
    `FileNotFoundError` (no ffprobe) for the caller to handle.
    """
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "a:0",
        "-show_entries",
        "format=duration:stream=codec_name,profile,sample_rate,nb_frames",
        "-of", "json",
        path,
    ]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=_PROBE_TIMEOUT_SEC)


def _parse_header_probe(stdout: str) -> Tuple[Optional[str], dict]:
    """`(format duration string or None, first audio stream's fields or {})`."""
    try:
        data = json.loads(stdout or "")
    except ValueError:
        return None, {}
    if not isinstance(data, dict):
        return None, {}
    duration = (data.get("format") or {}).get("duration")
    streams = data.get("streams") or []
    stream = streams[0] if streams and isinstance(streams[0], dict) else {}
    return duration, stream


def _positive_number(value) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def sample_derived_seconds(stream: dict) -> Optional[float]:
    """The audio the stream actually holds, from its header frame count:
    `nb_frames * samples_per_frame / sample_rate`.

    None — "not judged" — whenever that can't be worked out: no frame count
    (most MP3s), a missing or zero sample rate, or a codec/profile whose
    frame size isn't known here.
    """
    frames = _positive_number(stream.get("nb_frames"))
    rate = _positive_number(stream.get("sample_rate"))
    if frames is None or rate is None:
        return None
    codec = stream.get("codec_name")
    if codec == "aac":
        per_frame = _AAC_SAMPLES_PER_FRAME.get(stream.get("profile"))
    elif codec == "mp3":
        per_frame = (_MP3_SAMPLES_PER_FRAME_MPEG1 if rate >= _MP3_MPEG1_MIN_RATE
                     else _MP3_SAMPLES_PER_FRAME_LOW_RATE)
    else:
        per_frame = None
    if per_frame is None:
        return None
    return frames * per_frame / rate


def is_padded(real_seconds: Optional[float], stated_seconds: Optional[float]) -> bool:
    """Whether the stream holds well under the container's stated length
    (`PADDED_AUDIO_MAX_RATIO`). False when either side is unknown."""
    if not real_seconds or not stated_seconds:
        return False
    return real_seconds < stated_seconds * PADDED_AUDIO_MAX_RATIO


def probe_real_audio_seconds(path: str) -> Optional[float]:
    """The sample-derived length of `path`'s audio, or None if unknown.

    Blocking (one header-only ffprobe): call through `asyncio.to_thread`. Every
    failure — no ffprobe, a timeout, an unreadable file, a stream that can't be
    judged — collapses to None, which callers read as "use the stated length".
    """
    try:
        probe = _run_header_probe(path)
    except (subprocess.TimeoutExpired, OSError) as e:
        logger.info("[audio_integrity] header probe of %s failed: %s", path, e)
        return None
    if probe.returncode != 0:
        return None
    _duration, stream = _parse_header_probe(probe.stdout)
    return sample_derived_seconds(stream)


def _hours(seconds: float) -> str:
    return f"{seconds / 3600.0:.1f} h"


def check_audio_integrity(path: str) -> Tuple[bool, str]:
    """
    Validate that an audio file is fully decodable.

    Returns (ok, detail). When ok is False, `detail` is a short human-readable
    reason suitable for surfacing to the user (e.g. in a queue error message).
    This function never raises for media problems — only the caller decides how
    to react.
    """
    # Stage 1: header / duration probe.
    try:
        probe = _run_header_probe(path)
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

    duration_str, stream = _parse_header_probe(probe.stdout)
    try:
        duration = float(duration_str)
        if duration <= 0:
            return False, "reported audio duration is zero"
    except (TypeError, ValueError):
        return False, f"could not parse audio duration ({duration_str!r})"

    # Padding (issue #796): the stream holds far less than the container
    # states. Decided here, from the header alone, because the full decode
    # below cannot see it. A stream that can't be measured is not judged.
    real = sample_derived_seconds(stream)
    if is_padded(real, duration):
        return False, (
            f"padded: ~{_hours(real)} of audio in a file stating {_hours(duration)}"
        )

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
        # All this proves is that the host was slow or contended: the decode
        # normally runs at ~1000x realtime, and a truly corrupt file still
        # fails at the worker's own decode. Pass with a warning rather than
        # sending the operator off to re-import a good file (issue #245).
        logger.warning(
            "Full decode of %s did not finish within %ss; passing the integrity "
            "gate unverified", path, _DECODE_TIMEOUT_SEC,
        )
        return True, (
            f"full decode did not finish within {_DECODE_TIMEOUT_SEC}s "
            f"— could not verify"
        )
    except FileNotFoundError:
        # ffmpeg missing from the environment — a tooling problem, not a
        # corrupt file. Symmetrical with the ffprobe branch in stage 1.
        logger.warning("ffmpeg not found; skipping full-decode check for %s", path)
        return True, "integrity check skipped (ffmpeg unavailable)"

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
