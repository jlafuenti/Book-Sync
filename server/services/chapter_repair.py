"""
Repairs m4b files whose embedded Nero-style chapter titles were written in a
non-UTF-8 encoding (commonly Windows-1252/Latin-1 from another tagging tool).

mutagen's MP4Chapters._parse_chpl decodes chapter title bytes with no
encoding fallback and raises immediately, which aborts the whole
mutagen.mp4.MP4(filepath) call before any tags can be written
(services.abs_metadata.write_metadata_to_file). ffmpeg's mov demuxer, in
contrast, copies chapter title bytes through opaquely without validating
encoding, so exporting to FFMETADATA and re-decoding the title bytes
ourselves — rather than patching the file's raw MP4 atoms directly — gives
us a safe way to fix the encoding and reinject a corrected, guaranteed-valid
result via a lossless `-codec copy` remux. Mirrors the ffmpeg export/reinject
pattern already used by routers.chapters.update_audiobook_chapters.
"""

import logging
import os
import shutil
import subprocess
import tempfile
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


def decode_lenient(raw: bytes) -> str:
    """Decode bytes that are supposed to be UTF-8 but may actually be
    Windows-1252/Latin-1 (common for chapter titles written by other tools).
    Never raises: falls back to latin-1 with replacement as a last resort,
    since latin-1 maps every byte 0-255 to a codepoint.
    """
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        pass
    try:
        return raw.decode("windows-1252")
    except UnicodeDecodeError:
        return raw.decode("latin-1", errors="replace")


def fix_ffmetadata_bytes(raw: bytes) -> str:
    """Re-decode an exported FFMETADATA file's bytes into valid UTF-8 text,
    correcting the encoding of any `title=` line. Other lines are decoded
    leniently too (defensive), but only titles are expected to carry
    free-form text from an external source."""
    fixed_lines = []
    for line in raw.split(b"\n"):
        if line.startswith(b"title="):
            value = decode_lenient(line[len(b"title="):])
            fixed_lines.append("title=" + value)
        else:
            fixed_lines.append(decode_lenient(line))
    return "\n".join(fixed_lines)


def repair_chapter_encoding(filepath: str, timeout: int = 60) -> Tuple[bool, Optional[str]]:
    """
    Export the file's chapters/metadata via ffmpeg, correct the encoding of
    any non-UTF-8 chapter title, and reinject with a lossless -codec copy
    remux over the original file.
    Returns (True, None) on success, (False, error_message) on failure.
    """
    fd_meta, temp_meta_path = tempfile.mkstemp(suffix=".txt")
    os.close(fd_meta)
    fd_out, temp_out_path = tempfile.mkstemp(suffix=os.path.splitext(filepath)[1])
    os.close(fd_out)
    try:
        export = subprocess.run(
            ["ffmpeg", "-y", "-i", filepath, "-f", "ffmetadata", temp_meta_path],
            capture_output=True, timeout=timeout,
        )
        if export.returncode != 0:
            return False, f"Failed to export metadata: {export.stderr.decode(errors='replace')}"

        with open(temp_meta_path, "rb") as f:
            raw = f.read()
        fixed_text = fix_ffmetadata_bytes(raw)
        with open(temp_meta_path, "w", encoding="utf-8") as f:
            f.write(fixed_text)

        reinject = subprocess.run(
            ["ffmpeg", "-y", "-i", filepath, "-i", temp_meta_path,
             "-map_metadata", "1", "-codec", "copy", temp_out_path],
            capture_output=True, timeout=timeout,
        )
        if reinject.returncode != 0:
            return False, f"Failed to reinject metadata: {reinject.stderr.decode(errors='replace')}"

        shutil.move(temp_out_path, filepath)
        logger.info(f"[chapter_repair] Repaired chapter title encoding in {filepath}")
        return True, None
    except Exception as e:
        return False, str(e)
    finally:
        for p in (temp_meta_path, temp_out_path):
            if os.path.exists(p):
                os.remove(p)
