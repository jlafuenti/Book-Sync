"""
Audio runtime, read from the container header with ffprobe (issue #127).

`AudioBook.duration_seconds` matters beyond display: the server-side audio
auto-complete rule (issue #56, `position_service._in_audio_end_zone`) uses it
to decide when a listening position has reached the end of the book. A wrong
length is therefore worse than a missing one — "unknown length" cleanly
disables the end zone, while a length that is too short makes *every* position
look like the end.

Why ffprobe rather than mutagen's `info.length`, which the scanner already has
in hand while reading tags — measured across the full 308-book production
library, every file cross-checked:

  - 290 files: the two agreed exactly.
  - 17 files: `mutagen.File()` raised `MP4MetadataError` on a legacy Nero
    `chpl` chapter atom and produced nothing; ffprobe read all 17 fine.
  - 1 file: an MP3 whose header made mutagen report 12 seconds for a 9.9-hour
    book. Nothing about that answer looks wrong on its own.

mutagen stays as the caller's fallback for environments without ffmpeg on
PATH. ffmpeg is already a hard dependency here (services.audio_integrity,
services.chapter_repair).
"""

import logging
import subprocess
from typing import Optional

logger = logging.getLogger(__name__)

# Reading a container header is near-instant even for a 26-hour book; the cap
# only exists so a pathological input cannot stall a library scan.
_PROBE_TIMEOUT_SEC = 120


def probe_duration_seconds(path: str) -> Optional[float]:
    """The file's runtime in seconds, or None if ffprobe can't supply one.

    None is not an error — it means "no opinion", and the caller should fall
    back to mutagen rather than treat the file as broken. Every failure mode
    collapses to None: ffprobe absent, timed out, non-zero exit, or output that
    isn't a positive number (it prints `N/A` for a container it opened but
    could not measure).
    """
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        path,
    ]
    try:
        probe = subprocess.run(
            cmd, capture_output=True, text=True, timeout=_PROBE_TIMEOUT_SEC
        )
    except subprocess.TimeoutExpired:
        logger.warning("[audio_duration] ffprobe timed out on %s", path)
        return None
    except FileNotFoundError:
        logger.warning(
            "[audio_duration] ffprobe not found; falling back to mutagen for %s", path
        )
        return None
    except OSError as e:
        logger.warning("[audio_duration] ffprobe could not run on %s: %s", path, e)
        return None

    if probe.returncode != 0:
        detail = (probe.stderr or "").strip().splitlines()
        logger.info(
            "[audio_duration] ffprobe failed on %s: %s",
            path, detail[-1] if detail else "unreadable container header",
        )
        return None

    raw = (probe.stdout or "").strip()
    try:
        duration = float(raw)
    except ValueError:
        logger.info("[audio_duration] ffprobe gave no usable duration (%r) for %s", raw, path)
        return None

    if duration <= 0:
        return None
    return duration
