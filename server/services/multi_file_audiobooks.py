"""
Multi-file audiobook detection (issue #63).

`AudioBook` has a single `file_path`. A book ripped as `01.mp3 … 30.mp3` is not
representable, and importing every track as its own audiobook polluted the
library and auto-pairing. Merging is deliberately *not* done here — Audiobookshelf
already does it well — so the job of this module is to recognise such folders at
scan time, keep their files out of the import, and remember them so Troubleshoot
Library can show the remediation.

The classifier is pure (filenames + sizes + an injected tag reader) so it can be
tested without audio files; `read_audio_tags` is the mutagen-backed default.

The rule, per folder, per extension group of ≥ 2 audio files:

* all files carry the same non-empty **album** tag → one book, flagged;
* files carry **different** albums → distinct books in a flat folder, not flagged;
* no file carries an album → flagged only if **every** filename looks like a
  track (`01.mp3`, `Track 07`, `Part 3`, `CD1 - 05`, `Chapter 12`, `Title - 07`).

Other extension groups in the same folder are judged separately, so a merged
`book.m4b` sitting beside leftover MP3 tracks imports normally while the MP3
group stays flagged.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
from dataclasses import dataclass
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.library_issue import MultiFileAudiobookFolder
from utils import utcnow

logger = logging.getLogger(__name__)

# Mirrors AUDIOBOOK_EXTENSIONS in routers/library.py (kept here too so this
# module has no router import).
AUDIO_EXTENSIONS = {".mp3", ".m4a", ".m4b", ".flac", ".ogg", ".wav", ".aac", ".wma"}

# A 1–3 digit number that *leads* the name ("01 - Intro"), *ends* it after a
# separator ("Dune - 07", "Dune_03"), or follows an explicit track/part/disc/
# chapter marker ("Track 07", "CD1 - 05", "ch03"). A number in the middle of a
# title ("The 39 Steps") or glued to letters ("Catch22", "1984") doesn't count.
_TRACK_TOKEN = re.compile(
    r"^\d{1,3}(?![0-9])"
    r"|(?<=[\s\-_.])\d{1,3}$"
    r"|(?<![A-Za-z])(?:track|part|pt|cd|disc|disk|chapter|ch)\s*\.?\s*-?\s*\d+",
    re.IGNORECASE,
)


def looks_like_track_name(filename: str) -> bool:
    """Whether a filename reads as one track of a multi-file book."""
    stem = os.path.splitext(os.path.basename(filename))[0].strip()
    return _TRACK_TOKEN.search(stem) is not None


@dataclass(frozen=True)
class AudioFileInfo:
    name: str
    size: int


@dataclass(frozen=True)
class FolderGroup:
    """One flagged (folder, extension) group."""
    folder_path: str
    extension: str
    files: Tuple[AudioFileInfo, ...]
    guessed_title: Optional[str]
    guessed_author: Optional[str]
    fingerprint: str

    @property
    def file_count(self) -> int:
        return len(self.files)

    @property
    def total_size(self) -> int:
        return sum(f.size for f in self.files)

    @property
    def paths(self) -> List[str]:
        return [os.path.join(self.folder_path, f.name) for f in self.files]


TagReader = Callable[[str], Dict[str, Optional[str]]]


def read_audio_tags(path: str) -> Dict[str, Optional[str]]:
    """album / albumartist / artist from the file's tags, best effort, via mutagen."""
    try:
        import mutagen
        audio = mutagen.File(path, easy=True)
    except Exception:
        return {}
    if not audio or not getattr(audio, "tags", None):
        return {}

    def first(key: str) -> Optional[str]:
        try:
            values = audio.tags.get(key)
        except Exception:
            return None
        if not values:
            return None
        value = values[0] if isinstance(values, (list, tuple)) else values
        text = str(value).strip()
        return text or None

    return {"album": first("album"), "albumartist": first("albumartist"), "artist": first("artist")}


def _fingerprint(files: Iterable[AudioFileInfo]) -> str:
    h = hashlib.sha1()
    for f in sorted(files, key=lambda f: f.name):
        h.update(f"{f.name}\0{f.size}\n".encode("utf-8", "surrogateescape"))
    return h.hexdigest()


def classify_folder(
    folder_path: str,
    filenames: Sequence[str],
    read_tags: TagReader = read_audio_tags,
    *,
    library_root: Optional[str] = None,
    file_size: Callable[[str], int] = os.path.getsize,
) -> List[FolderGroup]:
    """The multi-file groups among `filenames` (basenames) in `folder_path`.

    Returns an empty list when nothing in the folder is a multi-file book.
    Non-audio names are ignored; a size that can't be read counts as 0.
    """
    by_ext: Dict[str, List[str]] = {}
    for name in filenames:
        ext = os.path.splitext(name)[1].lower()
        if ext in AUDIO_EXTENSIONS:
            by_ext.setdefault(ext, []).append(name)

    groups: List[FolderGroup] = []
    for ext, names in sorted(by_ext.items()):
        if len(names) < 2:
            continue
        tags = [read_tags(os.path.join(folder_path, n)) or {} for n in names]
        albums = [(t.get("album") or "").strip() for t in tags]
        distinct = {a for a in albums if a}
        if len(distinct) > 1:
            continue                                   # different books, flat folder
        if len(distinct) == 1 and all(albums):
            title = next(iter(distinct))
        elif not distinct and all(looks_like_track_name(n) for n in names):
            title = None
        else:
            continue

        author = next((t.get("albumartist") or t.get("artist") for t in tags
                       if t.get("albumartist") or t.get("artist")), None)
        folder_name = os.path.basename(os.path.normpath(folder_path))
        parent = os.path.dirname(os.path.normpath(folder_path))
        if title is None:
            title = folder_name or None
        if author is None:
            root = os.path.normpath(library_root) if library_root else None
            if parent and os.path.normpath(parent) != root and os.path.normpath(folder_path) != root:
                author = os.path.basename(parent) or None

        files = []
        for n in sorted(names):
            try:
                size = int(file_size(os.path.join(folder_path, n)))
            except OSError:
                size = 0
            files.append(AudioFileInfo(name=n, size=size))
        files_t = tuple(files)
        groups.append(FolderGroup(
            folder_path=folder_path, extension=ext, files=files_t,
            guessed_title=title, guessed_author=author,
            fingerprint=_fingerprint(files_t),
        ))
    return groups


async def sync_folder_flags(
    db: AsyncSession, groups: Iterable[FolderGroup], *, prune: bool = True
) -> int:
    """Bring `multi_file_audiobook_folders` in line with what a scan saw.

    Upserts every group (a changed fingerprint clears `dismissed` — the folder
    is worth a second look). With `prune`, rows for folders the scan did *not*
    see are deleted: the tracks were merged or removed, so the issue is gone.
    A targeted scan passes `prune=False` since it only looked at some folders.

    Returns the number of flagged rows after the sync (dismissed included).
    Flushes; the caller owns the transaction.
    """
    seen = {(g.folder_path, g.extension): g for g in groups}
    existing = {
        (row.folder_path, row.extension): row
        for row in (await db.execute(select(MultiFileAudiobookFolder))).scalars().all()
    }
    now = utcnow()

    for key, g in seen.items():
        row = existing.get(key)
        if row is None:
            db.add(MultiFileAudiobookFolder(
                folder_path=g.folder_path, extension=g.extension,
                file_count=g.file_count, total_size=g.total_size,
                guessed_title=g.guessed_title, guessed_author=g.guessed_author,
                fingerprint=g.fingerprint, dismissed=False,
                first_seen_at=now, last_seen_at=now,
            ))
            continue
        if row.fingerprint != g.fingerprint:
            row.dismissed = False
        row.fingerprint = g.fingerprint
        row.file_count = g.file_count
        row.total_size = g.total_size
        row.guessed_title = g.guessed_title
        row.guessed_author = g.guessed_author
        row.last_seen_at = now

    if prune:
        for key, row in existing.items():
            if key not in seen:
                await db.delete(row)

    await db.flush()
    if prune:
        return len(seen)
    return len(set(existing) | set(seen))
