"""
Repairs m4b files whose legacy Nero `chpl` chapter atom mutagen cannot parse.

Some audiobook files carry a malformed or variant `moov.udta.chpl` atom.
mutagen's MP4Chapters._parse_chpl assumes one fixed binary layout and raises
"chapter N title: <decode error>" when the bytes don't line up — often
landing on binary timestamp bytes it mistakes for title text — which aborts
the whole mutagen.mp4.MP4(filepath) call before any tags can be written
(services.abs_metadata.write_metadata_to_file). ffmpeg/ffprobe, by contrast,
read these same files' chapters fine, via their own more tolerant chpl
parser and/or the QuickTime chapter text track.

Earlier revisions of this module tried to regenerate the atom by exporting
FFMETADATA text, patching it, and reinjecting with ffmpeg. That proved
unreliable in production: ffmpeg's rewritten atom still didn't match the
layout mutagen expects, and the text round-trip could corrupt titles that
contained FFMETADATA escape sequences. The current strategy neutralizes the
unparseable atom instead: its 4-byte fourcc `chpl` is overwritten in place
with `free` (the standard padding atom), which every parser skips — no
remux, no size recomputation, near-instant even on multi-GB files. If the
file's chapters lived only in that atom (nothing left for ffprobe to read
afterwards), they are rebuilt from an ffprobe snapshot taken before the
patch, using the same ffmpeg ffmetadata reinject as
routers.chapters.update_audiobook_chapters.
"""

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
from typing import Optional, Tuple

from services.audio_integrity import stderr_indicates_corruption

logger = logging.getLogger(__name__)

# Shared with services.abs_metadata, which imports this to detect the same
# failure mode while writing tags.
CHAPTER_TITLE_ERROR_RE = re.compile(r"chapter \d+ title:")

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
    the narrower chapter-atom issue this module fixes."""
    text = stderr_bytes.decode(errors="replace")
    lines = [ln for ln in text.splitlines() if ln.strip()]
    tail = "\n".join(lines[-n:])
    message = f"{prefix}: {tail}"
    if stderr_indicates_corruption(text):
        message += _CORRUPTION_GUIDANCE
    return message


def decode_lenient(raw: bytes) -> str:
    """Decode bytes that are supposed to be UTF-8 but may actually be
    Windows-1252/Latin-1 (common for metadata written by other tools).
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


def ffmetadata_escape(value: str) -> str:
    """Escape a value for an FFMETADATA1 file. ffmpeg treats '=', ';', '#',
    '\\' and newlines as special within key/value lines; each must be
    backslash-escaped. Backslash first so existing ones aren't re-escaped."""
    for ch in ("\\", "=", ";", "#", "\n"):
        value = value.replace(ch, "\\" + ch)
    return value


def check_chapter_encoding(filepath: str) -> Tuple[bool, Optional[str]]:
    """
    Cheap, live detector for Troubleshoot Library: does mutagen fail to open
    this m4b because of an unparseable chpl chapter atom? No full decode —
    just parses atom headers.
    Returns (True, None) when the file is fine or this check doesn't apply
    (wrong extension, or an unrelated error that's another check's concern).
    Returns (False, detail) when the file has this specific, repairable
    problem (see repair_chapter_encoding).
    """
    ext = os.path.splitext(filepath)[1].lower()
    if ext not in (".m4b", ".m4a", ".mp4"):
        return True, None

    import mutagen.mp4

    try:
        mutagen.mp4.MP4(filepath)
    except Exception as e:
        if CHAPTER_TITLE_ERROR_RE.search(str(e)):
            return False, str(e)
        return True, None
    return True, None


def neutralize_chpl(filepath: str) -> bool:
    """Overwrite the moov.udta.chpl atom's fourcc with 'free' so every
    parser skips it. A 4-byte in-place patch: atom sizes and all other bytes
    are untouched. Returns False (without writing) when no chpl atom exists.

    The atom is located with mutagen's structural Atoms walk — atom offsets
    are reliable even when the atom's *content* is the very thing mutagen's
    chapter parser chokes on.
    """
    import mutagen.mp4

    with open(filepath, "rb") as f:
        atoms = mutagen.mp4.Atoms(f)
        try:
            chpl = atoms[b"moov.udta.chpl"]
        except KeyError:
            return False
        offset = chpl.offset

    with open(filepath, "r+b") as f:
        # The fourcc always sits 4 bytes after the atom start (after the
        # 32-bit size field), including for 64-bit extended-size atoms.
        f.seek(offset + 4)
        f.write(b"free")
    logger.info(f"[chapter_repair] Neutralized chpl atom at offset {offset} in {filepath}")
    return True


def read_chapters_ffprobe(filepath: str, timeout: int = 60) -> Optional[list]:
    """Read the file's chapters via ffprobe (whose parser tolerates the
    atom variants mutagen rejects). Returns a list of
    {"start": s, "end": s, "title": str} dicts, or None if ffprobe failed."""
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_chapters", filepath],
            capture_output=True, timeout=timeout,
        )
    except Exception as e:
        logger.warning(f"[chapter_repair] ffprobe failed for {filepath}: {e}")
        return None
    if probe.returncode != 0:
        return None
    try:
        data = json.loads(decode_lenient(probe.stdout))
    except ValueError:
        return None

    chapters = []
    for i, ch in enumerate(data.get("chapters", [])):
        tags = ch.get("tags") or {}
        chapters.append({
            "start": float(ch.get("start_time") or 0),
            "end": float(ch.get("end_time") or 0),
            "title": tags.get("title", f"Chapter {i + 1}"),
        })
    return chapters


def _rebuild_chapters(filepath: str, chapters: list, timeout: int) -> Tuple[bool, Optional[str]]:
    """Write `chapters` back into the file via ffmpeg's ffmetadata reinject
    (same mechanism as routers.chapters.update_audiobook_chapters). Used only
    when neutralizing the chpl atom removed the file's sole chapter source."""
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
            return False, _format_ffmpeg_error("Failed to export metadata", export.stderr)

        # Preserve the existing global tags verbatim (chapters are already
        # gone from this export — the chpl atom was neutralized) and append
        # the snapshotted chapters with proper FFMETADATA escaping.
        with open(temp_meta_path, "rb") as f:
            raw = f.read()
        lines = [decode_lenient(raw).rstrip("\n")]
        for ch in chapters:
            lines.append("[CHAPTER]")
            lines.append("TIMEBASE=1/1000")
            lines.append(f"START={int(ch['start'] * 1000)}")
            lines.append(f"END={int(ch['end'] * 1000)}")
            lines.append("title=" + ffmetadata_escape(ch["title"]))
        with open(temp_meta_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

        reinject = subprocess.run(
            # -map_metadata alone does NOT map chapters — that needs the
            # separate -map_chapters flag, which defaults to input 0 (the
            # chapterless original) if omitted.
            ["ffmpeg", "-y", "-i", filepath, "-i", temp_meta_path,
             "-map_metadata", "1", "-map_chapters", "1", "-codec", "copy",
             temp_out_path],
            capture_output=True, timeout=timeout,
        )
        if reinject.returncode != 0:
            return False, _format_ffmpeg_error("Failed to rebuild chapters", reinject.stderr)

        shutil.move(temp_out_path, filepath)
        return True, None
    except Exception as e:
        return False, str(e)
    finally:
        for p in (temp_meta_path, temp_out_path):
            if os.path.exists(p):
                os.remove(p)


def repair_chapter_encoding(filepath: str, timeout: int = 60) -> Tuple[bool, Optional[str]]:
    """
    Repair a file flagged by check_chapter_encoding: snapshot its chapters
    via ffprobe, neutralize the unparseable chpl atom in place, verify with
    mutagen, and rebuild the chapters via ffmpeg only if they lived solely
    in the neutralized atom.
    Returns (True, None) on success, (False, error_message) on failure —
    success is only reported after mutagen actually opens the file cleanly.
    """
    snapshot = read_chapters_ffprobe(filepath, timeout=timeout)

    try:
        had_chpl = neutralize_chpl(filepath)
    except Exception as e:
        return False, f"Failed to patch the chpl atom: {e}"
    if not had_chpl:
        return False, (
            "No chpl chapter atom found to repair — the file's structure "
            "doesn't match this issue."
        )

    ok, detail = check_chapter_encoding(filepath)
    if not ok:
        return False, detail or "File is still unreadable after neutralizing the chpl atom"

    # If ffprobe saw chapters before but sees none now, they lived only in
    # the chpl atom (no QuickTime chapter track) — rebuild them from the
    # snapshot. `remaining == []` deliberately excludes None (probe failure),
    # where rebuilding would risk acting on bad information.
    remaining = read_chapters_ffprobe(filepath, timeout=timeout)
    if snapshot and remaining == []:
        rebuilt, err = _rebuild_chapters(filepath, snapshot, timeout)
        if not rebuilt:
            return False, err
        ok, detail = check_chapter_encoding(filepath)
        if not ok:
            # ffmpeg's freshly written chpl is *also* unparseable by mutagen
            # — drop it too; the chapters remain in the track form ffmpeg
            # wrote alongside it.
            neutralize_chpl(filepath)
            ok, detail = check_chapter_encoding(filepath)
            if not ok:
                return False, detail

    logger.info(f"[chapter_repair] Repaired unparseable chapter atom in {filepath}")
    return True, None
