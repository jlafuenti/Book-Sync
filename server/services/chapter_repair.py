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

from services.audio_duration import probe_duration_seconds
from config import settings
from services.audio_integrity import stderr_indicates_corruption
from services.cache import TTLMemo

logger = logging.getLogger(__name__)

# Shared with services.abs_metadata, which imports this to detect the same
# failure mode while writing tags.
CHAPTER_TITLE_ERROR_RE = re.compile(r"chapter \d+ title:")

# Suffix for the pre-remux backup (issue #243). Deliberately not a media
# extension, so a stray one left by a crashed run is never picked up as a
# library item.
BACKUP_SUFFIX = ".tandem-repair.bak"

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


#: Verdicts from :func:`check_chapter_encoding`, one entry per audiobook file
#: (issue #208). "Cheap" above is relative: it still opens and parses atom
#: headers over a NAS mount, and Troubleshoot runs it for *every* audiobook on
#: every page load.
encoding_check_cache = TTLMemo(lambda: settings.chapter_encoding_cache_seconds)


def check_chapter_encoding_cached(filepath: str) -> Tuple[bool, Optional[str]]:
    """:func:`check_chapter_encoding`, memoized on ``(path, mtime, size)``.

    Keyed on content and not on the path alone, deliberately: `repair_chapter_
    encoding` rewrites the file in place, and a path-keyed memo would keep
    reporting a problem that no longer exists until the TTL happened to lapse.
    Conversely an untouched library re-parses nothing, so two page loads inside
    the TTL cost one parse per file rather than two.

    A file that cannot be stat'ed is not cached at all — it is about to be
    reported as missing anyway, and a stat failure is not a verdict worth
    remembering.
    """
    try:
        st = os.stat(filepath)
    except OSError:
        return check_chapter_encoding(filepath)
    key = (filepath, st.st_mtime_ns, st.st_size)
    # Resolved through the module global on every miss, so tests (and any future
    # wrapper) that replace `check_chapter_encoding` are still honoured.
    return encoding_check_cache.get(key, lambda: check_chapter_encoding(filepath))


def _patch_chpl_fourcc(filepath: str) -> Optional[Tuple[int, bytes]]:
    """Overwrite the moov.udta.chpl atom's fourcc with 'free' so every parser
    skips it. A 4-byte in-place patch: atom sizes and all other bytes are
    untouched.

    Returns ``(offset, original_fourcc)`` — the undo record the caller needs to
    put the file back byte-for-byte (issue #243) — or ``None`` (without
    writing) when no chpl atom exists.

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
            return None
        offset = chpl.offset

    with open(filepath, "r+b") as f:
        # The fourcc always sits 4 bytes after the atom start (after the
        # 32-bit size field), including for 64-bit extended-size atoms.
        f.seek(offset + 4)
        original = f.read(4)
        f.seek(offset + 4)
        f.write(b"free")
    logger.info(f"[chapter_repair] Neutralized chpl atom at offset {offset} in {filepath}")
    return offset, original


def neutralize_chpl(filepath: str) -> bool:
    """Thin bool-returning wrapper over :func:`_patch_chpl_fourcc` for callers
    that do not need the undo record. False means "no chpl atom, nothing
    written"."""
    return _patch_chpl_fourcc(filepath) is not None


def _restore_fourcc(filepath: str, patch: Tuple[int, bytes]) -> None:
    """Undo a :func:`_patch_chpl_fourcc` write."""
    offset, original = patch
    with open(filepath, "r+b") as f:
        f.seek(offset + 4)
        f.write(original)


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


# ---------------------------------------------------------------------------
# The one chapter write-back remux (issue #192)
# ---------------------------------------------------------------------------

#: How far the remuxed file's runtime may differ from the original's before we
#: refuse to install it. A codec-copy remux reproduces the same samples, so any
#: real difference means a truncated or mis-muxed output; the slack is only for
#: container rounding.
DURATION_TOLERANCE_SEC = 1.0

#: The iTunes freeform namespace. mutagen exposes these atoms as
#: ``----:com.apple.iTunes:NAME`` keys; ffmpeg's mov muxer never writes them.
FREEFORM_PREFIX = "----"


def snapshot_freeform_tags(filepath: str) -> dict:
    """Every iTunes freeform (``----``) atom on this file, as mutagen sees it.

    These are the tags a remux destroys. ffmpeg's mov *muxer* emits only its own
    fixed list of ilst atoms and never a ``----`` atom, so ``-map_metadata 0``
    does not save them — with ``-movflags use_metadata_tags`` it writes them as
    ``mdta`` keys instead, which neither mutagen nor the scanner reads back as
    iTunes freeform. The only way to keep them is to read them before the remux
    and write them onto the result. ``routers/library.py`` reads NARRATOR,
    SERIES, SERIES-PART and publisher straight out of these atoms, so losing
    them quietly un-tags the book.

    Raises whatever mutagen raises; the caller decides whether that is fatal.
    """
    import mutagen.mp4

    audio = mutagen.mp4.MP4(filepath)
    tags = audio.tags or {}
    return {k: v for k, v in tags.items() if k.startswith(FREEFORM_PREFIX)}


def restore_freeform_tags(filepath: str, tags: dict) -> None:
    """Write a `snapshot_freeform_tags` result back onto `filepath`."""
    if not tags:
        return
    import mutagen.mp4

    audio = mutagen.mp4.MP4(filepath)
    if audio.tags is None:
        audio.add_tags()
    for key, value in tags.items():
        audio.tags[key] = value
    audio.save()


def _ffmetadata_without_chapters(raw: bytes) -> list:
    """The exported FFMETADATA text with every ``[CHAPTER]`` block removed.

    Everything else — the global tags ffmpeg *does* understand — is kept
    verbatim. The bytes are decoded leniently because ffmpeg copies chapter
    title bytes through without validating their encoding, so a title another
    tool wrote in Windows-1252 would crash a hard-coded utf-8 read.
    """
    lines = decode_lenient(raw).splitlines(keepends=True)
    kept, in_chapter = [], False
    for line in lines:
        if line.strip() == "[CHAPTER]":
            in_chapter = True
            continue
        if in_chapter and line.startswith("["):
            in_chapter = False  # a new section begins; keep this line
        if not in_chapter:
            kept.append(line)
    return kept


def _chapter_block(start_seconds: float, end_seconds: float, title: str) -> list:
    timebase = 1000  # ms precision
    return [
        "[CHAPTER]\n",
        "TIMEBASE=1/{}\n".format(timebase),
        "START={}\n".format(int(start_seconds * timebase)),
        "END={}\n".format(int(end_seconds * timebase)),
        "title={}\n".format(ffmetadata_escape(title)),
    ]


def _duration_is_plausible(original: str, produced: str) -> Tuple[bool, Optional[str]]:
    """Does the remuxed file still describe the same recording?

    An unmeasurable *original* is not a failure: `probe_duration_seconds`
    returns None for "no opinion", and a file we could never measure has no
    expectation to compare against. An unmeasurable *output* is: ffprobe read
    the original fine, so a replacement it cannot read is one we must not
    install.
    """
    expected = probe_duration_seconds(original)
    if expected is None:
        return True, None
    actual = probe_duration_seconds(produced)
    if actual is None:
        return False, ("The remuxed file could not be read back — "
                       "leaving the original in place.")
    if abs(actual - expected) > DURATION_TOLERANCE_SEC:
        return False, (
            f"The remuxed file's duration ({actual:.1f}s) does not match the "
            f"original's ({expected:.1f}s) — leaving the original in place."
        )
    return True, None


def remux_with_chapters(
    filepath: str, chapters: list, timeout: int = 60
) -> Tuple[bool, Optional[str]]:
    """Replace `filepath`'s chapters with `chapters`, keeping everything else.

    `chapters` is a list of mappings with ``start`` and ``end`` in **seconds**
    and a ``title``. Returns ``(True, None)`` or ``(False, message)``.

    The single implementation behind both
    `routers.chapters.update_audiobook_chapters` and `_rebuild_chapters`
    (issue #192). They used to carry byte-identical copies of this, each with
    the same three defects:

    * **Freeform tags survive.** ffmpeg cannot write ``----`` atoms, so they are
      snapshotted with mutagen before the remux and written back onto the
      result — onto the *staged* file, before it takes the library's name, so a
      concurrent scan never sees an untagged copy.
    * **The staging file lives next to the target**, which makes the final step
      `os.replace`, an atomic rename within one filesystem, rather than a
      `shutil.move` out of the system temp dir — across filesystems that is
      copy-then-delete, and a full disk or a crash mid-copy leaves the original
      truncated and the replacement half-written.
    * **The output is checked before it is installed.** A non-empty file whose
      runtime still matches the original's, or the original stays.

    On every failure path the original is left byte-identical and the staged
    file is removed. This is a purchased media file and there is no second copy.
    """
    ext = os.path.splitext(filepath)[1]
    directory = os.path.dirname(os.path.abspath(filepath))

    fd_meta, temp_meta_path = tempfile.mkstemp(suffix=".txt")
    os.close(fd_meta)
    # Next to the target, not in the system temp dir — see the docstring.
    fd_out, temp_out_path = tempfile.mkstemp(dir=directory, suffix=ext)
    os.close(fd_out)

    try:
        # mutagen raises on exactly the files this module exists to repair.
        # Losing tags we could not read is not a reason to refuse the edit.
        try:
            freeform = snapshot_freeform_tags(filepath)
        except Exception as e:
            logger.warning(
                "[chapter_repair] Could not snapshot freeform tags on %s "
                "(they will be lost by the remux): %s", filepath, e
            )
            freeform = {}

        export = subprocess.run(
            ["ffmpeg", "-y", "-i", filepath, "-f", "ffmetadata", temp_meta_path],
            capture_output=True, timeout=timeout,
        )
        if export.returncode != 0:
            return False, _format_ffmpeg_error("Failed to export metadata", export.stderr)

        with open(temp_meta_path, "rb") as f:
            raw = f.read()
        lines = _ffmetadata_without_chapters(raw)
        for ch in chapters:
            lines.extend(_chapter_block(ch["start"], ch["end"], ch["title"]))
        with open(temp_meta_path, "w", encoding="utf-8") as f:
            f.writelines(lines)

        reinject = subprocess.run(
            # -map_metadata alone does NOT map chapters — that needs the
            # separate -map_chapters flag, which defaults to input 0 (the
            # unedited original) if omitted.
            ["ffmpeg", "-y", "-i", filepath, "-i", temp_meta_path,
             "-map_metadata", "1", "-map_chapters", "1", "-codec", "copy",
             temp_out_path],
            capture_output=True, timeout=timeout,
        )
        if reinject.returncode != 0:
            return False, _format_ffmpeg_error("Failed to write chapters", reinject.stderr)

        if os.path.getsize(temp_out_path) == 0:
            return False, "The remux produced an empty file — leaving the original in place."

        ok, why = _duration_is_plausible(filepath, temp_out_path)
        if not ok:
            return False, why

        # Onto the staged file: one that appeared under the real name and only
        # then grew its tags would be visible to a scan in between.
        try:
            restore_freeform_tags(temp_out_path, freeform)
        except Exception as e:
            # The chapters are correct and the audio is intact; refusing the
            # whole edit over the tags would be the worse trade.
            logger.warning(
                "[chapter_repair] Could not restore freeform tags onto the "
                "remux of %s: %s", filepath, e
            )

        os.replace(temp_out_path, filepath)
        return True, None
    except Exception as e:
        return False, str(e)
    finally:
        for path in (temp_meta_path, temp_out_path):
            if os.path.exists(path):
                os.remove(path)


def _rebuild_chapters(filepath: str, chapters: list, timeout: int) -> Tuple[bool, Optional[str]]:
    """Write `chapters` back into the file after neutralizing the chpl atom
    removed the file's sole chapter source.

    A thin adapter over `remux_with_chapters` — the same implementation the
    editor's chapter write-back uses (issue #192). These were two byte-identical
    ffmpeg invocations maintained separately, which is how they both ended up
    losing freeform tags and replacing the original non-atomically.

    The snapshot rows use ffprobe's `start`/`end` keys already, in seconds,
    which is exactly what the helper takes.
    """
    return remux_with_chapters(filepath, chapters, timeout=timeout)


def _make_backup(filepath: str) -> str:
    """Copy `filepath` alongside itself so a whole-file rewrite can be undone.

    The copy lives in the same directory, so the restore is a same-filesystem
    rename rather than a second multi-GB copy. The name is unique per call:
    a backup left behind by an earlier crashed run is the true original and
    must never be clobbered.
    """
    directory = os.path.dirname(os.path.abspath(filepath))
    prefix = os.path.basename(filepath) + "."
    fd, backup_path = tempfile.mkstemp(dir=directory, prefix=prefix, suffix=BACKUP_SUFFIX)
    os.close(fd)
    shutil.copy2(filepath, backup_path)
    return backup_path


def repair_chapter_encoding(filepath: str, timeout: int = 60) -> Tuple[bool, Optional[str]]:
    """
    Repair a file flagged by check_chapter_encoding: snapshot its chapters
    via ffprobe, neutralize the unparseable chpl atom in place, verify with
    mutagen, and rebuild the chapters via ffmpeg only if they lived solely
    in the neutralized atom.
    Returns (True, None) on success, (False, error_message) on failure —
    success is only reported after mutagen actually opens the file cleanly.

    Every failure path leaves the audiobook byte-identical to how it was found
    (issue #243). This is a purchased media file, and until this function
    returns True there is no reason to believe the edit helped, so each write
    carries its own undo:

    * the 4-byte fourcc patch is undone from the bytes it overwrote — no copy
      of a multi-GB file for a 4-byte change;
    * the ffmpeg remux, which replaces the whole file, is covered by a real
      backup taken immediately before it.

    The returned `detail` says which way it went, so the Troubleshoot UI and
    the ABS log line can tell the operator whether their file was touched.
    """
    snapshot = read_chapters_ffprobe(filepath, timeout=timeout)

    try:
        patch = _patch_chpl_fourcc(filepath)
    except Exception as e:
        # Nothing was written — the failure was in locating the atom.
        return False, f"Failed to patch the chpl atom: {e}"
    if patch is None:
        return False, (
            "No chpl chapter atom found to repair — the file's structure "
            "doesn't match this issue."
        )

    # Set once the remux is about to replace the whole file; from then on the
    # backup, not the fourcc undo, is what restores the original.
    backup_path: Optional[str] = None

    def _fail(detail: Optional[str]) -> Tuple[bool, Optional[str]]:
        message = detail or "File is still unreadable after neutralizing the chpl atom"
        try:
            # Unwind in reverse order. The backup is a copy of the *already
            # patched* file (that is the point — it exists to undo the remux,
            # not the 4-byte edit), so the fourcc still has to be put back
            # afterwards, at the same offset.
            if backup_path is not None:
                shutil.move(backup_path, filepath)
            _restore_fourcc(filepath, patch)
        except Exception as restore_error:
            logger.error(
                f"[chapter_repair] Could not restore {filepath} after a failed "
                f"repair: {restore_error}"
            )
            note = (
                " — WARNING: the audiobook was modified and could not be "
                f"restored ({restore_error})"
            )
            if backup_path is not None:
                note += f"; a copy of the original is at {backup_path}"
            return False, message + note
        logger.info(f"[chapter_repair] Restored {filepath} after a failed repair")
        return False, message + " — the audiobook was left unmodified."

    ok, detail = check_chapter_encoding(filepath)
    if not ok:
        return _fail(detail)

    # If ffprobe saw chapters before but sees none now, they lived only in
    # the chpl atom (no QuickTime chapter track) — rebuild them from the
    # snapshot. `remaining == []` deliberately excludes None (probe failure),
    # where rebuilding would risk acting on bad information.
    remaining = read_chapters_ffprobe(filepath, timeout=timeout)
    if snapshot and remaining == []:
        try:
            backup_path = _make_backup(filepath)
        except Exception as e:
            return _fail(f"Could not back up the file before rebuilding chapters: {e}")

        rebuilt, err = _rebuild_chapters(filepath, snapshot, timeout)
        if not rebuilt:
            return _fail(err)
        ok, detail = check_chapter_encoding(filepath)
        if not ok:
            # ffmpeg's freshly written chpl is *also* unparseable by mutagen
            # — drop it too; the chapters remain in the track form ffmpeg
            # wrote alongside it. Best-effort: the remuxed file may not even
            # be walkable, and the backup covers us either way.
            try:
                neutralize_chpl(filepath)
            except Exception as e:
                logger.warning(
                    f"[chapter_repair] Could not neutralize the rebuilt chpl atom "
                    f"in {filepath}: {e}"
                )
            ok, detail = check_chapter_encoding(filepath)
            if not ok:
                return _fail(detail)

    if backup_path is not None and os.path.exists(backup_path):
        os.remove(backup_path)
    logger.info(f"[chapter_repair] Repaired unparseable chapter atom in {filepath}")
    return True, None
