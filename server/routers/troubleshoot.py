"""
Troubleshoot router — library health issues and fixes.

Surfaces all detected problems (corrupt audio, DRM/unreadable ebooks, missing
files, zero-byte/tiny files, unsupported formats, failed transcriptions, failed
ACSM imports) and provides fixes: delete (single + bulk), replace-file, convert,
and re-queue. Backed by the `library_verify` background scan for the expensive
integrity checks.
"""

import asyncio
import logging
import os
import re
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel
from rapidfuzz import fuzz
from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database import get_db
from models.book import AudioBook, BookPair, EBook, PairStatus
from models.library_issue import LibraryCheckResult, MultiFileAudiobookFolder
from models.progress import UserProgress
from models.sync_map import SyncMap
from models.transcript import AudioTranscript
from services.file_hash import hash_file
from services.uploads import stream_upload_to_path
from utils import safe_join
from models.transcription_queue import TranscriptionQueueItem
from models.user import User
from rate_limit import expensive_reads
from routers.auth import get_current_user, get_editor_user, rate_limited
from schemas import (
    ActionResult, BulkChapterRepairResult, ChapterRepairResult, DeletedCount,
    LibraryScanProgress, MultiFileDismissResult, MultiFileRemoveTracksResult,
    ReplaceFileResult, RequeueResult, SyncMapAuditResponse, TroubleshootIssues,
)
from services import chapter_repair, library_verify, pair_plausibility
from services import sync_map_audit as sync_map_audit_service
from services.audio_change import invalidate_audiobook_transcripts
from services.position_service import (
    demote_pair_positions,
    invalidate_parse_coordinates_for_ebook,
    release_standalone_positions,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/troubleshoot", tags=["troubleshoot"])

# A real audiobook is always well over this; an ebook over this. Anything
# smaller (but non-zero) is almost certainly a truncated/empty download.
_TINY_AUDIO_BYTES = 1 * 1024 * 1024     # 1 MB
_TINY_EBOOK_BYTES = 1 * 1024            # 1 KB
EBOOK_EXTENSIONS = {".epub", ".pdf", ".mobi", ".azw3"}
AUDIOBOOK_EXTENSIONS = {".mp3", ".m4a", ".m4b", ".flac", ".ogg", ".wav", ".aac", ".wma"}


def _is_track_of(file_path: Optional[str], folder: str, extension: str) -> bool:
    """Whether `file_path` is one of a flagged group's tracks: directly in
    `folder` (not a same-prefix sibling folder) AND of the group's extension —
    a merged `Book.m4b` beside leftover MP3 tracks is not a track."""
    if not file_path:
        return False
    if os.path.normpath(os.path.dirname(file_path)) != os.path.normpath(folder):
        return False
    return os.path.splitext(file_path)[1].lower() == extension.lower()


def _item_dict(item, item_type: str, detail: str = "", pair_id: Optional[int] = None) -> dict:
    return {
        "item_type": item_type,
        "item_id": item.id,
        "title": item.title,
        "author": item.author,
        "filename": item.filename,
        "file_path": item.file_path,
        "format": item.format,
        "file_size": item.file_size,
        "detail": detail,
        "pair_id": pair_id,
    }


# ---------------------------------------------------------------------------
# Possible duplicates (issue #692) — content-similarity candidates
# ---------------------------------------------------------------------------
#
# The `duplicate` category above only catches byte-identical files (same
# `file_hash`). Real duplicates almost never are: a remux, a re-tag, or
# Tandem's own EPUB metadata write-back (#533) changes every byte while
# leaving the same work behind, so the exact-hash check stays near-empty while
# genuine duplicates accumulate. This section adds two weaker signals —
# audiobook duration, ebook file size — but neither is trusted alone.
#
# Checked against a real library (several hundred audiobooks, all with
# distinct whole-second durations): dozens of pairs of genuinely *different*
# books still land within a few seconds of each other purely by chance, and a
# same-size-different-hash ebook pair can just as easily be two unrelated
# books that happen to share a byte count. On a library that size, even exact
# duration equality alone would eventually collide by the birthday effect.
#
# So every candidate here needs a second, independent signal already on the
# row — title, author, narrator, ASIN/ISBN — before it is reported, on top of
# the length/size match. This is report-only: no delete/bulk-delete action is
# offered for it anywhere (see `web/src/pages/TroubleshootPage.jsx`), because
# a false positive (two different books that happen to be the same size) is
# expected, not exceptional, and the operator has to judge each one.

# Below this an audiobook's duration is not trusted as a duplicate signal at
# all: a few minutes is too short a length for a coincidental match to carry
# any weight, and folding these out costs almost no real candidates (very few
# genuine duplicates are this short).
_DUP_MIN_AUDIO_DURATION_SECONDS = 30 * 60  # 30 minutes

# Duration tolerance: the looser (max) of a relative and an absolute bound,
# and even a match within this window still needs a corroborating signal
# (above) before it is reported — see the module comment for why duration
# alone is not enough on a library of any size.
#
# The originating case (issue #692) is a remux 9 s apart over ~33.8 h of
# audio: 9 / (33.8 * 3600) ≈ 0.0074%. 0.01% relative gives comfortable margin
# over that without opening the window wide enough to catch the unrelated
# same-length-by-chance pairs found in the check above. The 10 s floor exists
# only so short-ish candidates (just above the minimum, where 0.01% is under
# a fifth of a second) aren't held to an unreasonably tight absolute window.
_DUP_AUDIO_RELATIVE_TOLERANCE = 0.0001  # 0.01%
_DUP_AUDIO_ABSOLUTE_FLOOR_SECONDS = 10

# A dramatization and an unabridged reading of the same title differ hugely in
# length (typically a fraction vs. the full running time), so this tolerance
# — well under a tenth of a percent — never groups them; no separate rule is
# needed to keep them apart.


def _audio_duration_tolerance(duration_seconds: float) -> float:
    return max(duration_seconds * _DUP_AUDIO_RELATIVE_TOLERANCE, _DUP_AUDIO_ABSOLUTE_FLOOR_SECONDS)


# Trailing series index ("Axis Test: 2", "Axis Test - Book 3") or a
# dramatization/edition annotation ("(Unabridged)", "(Dramatized)"). Stripped
# before comparison so two releases of the same title don't score low purely
# on the suffix — this signal has no author gate the way auto-pairing does, so
# the suffix has to come off up front rather than just costing a few fuzzy
# points.
_DUP_TITLE_SUFFIX_RE = re.compile(
    r"""
    \s*[:\-–—]\s*(?:book|vol(?:ume)?|part)?\s*\d+(?:\.\d+)?\s*$
    |
    \s*\(\s*(?:unabridged|abridged|dramati[sz]ed?|dramati[sz]ation|full[\s-]cast[^)]*)\)\s*$
    """,
    re.IGNORECASE | re.VERBOSE,
)

_DUP_TITLE_OVERLAP_THRESHOLD = 85  # rapidfuzz token_set_ratio, 0-100

_DUP_AUTHOR_SUFFIX_TOKENS = {"jr", "sr", "ii", "iii", "iv", "v"}


def _dup_strip_title_suffix(title: str) -> str:
    prev, t = None, title
    while t != prev:
        prev = t
        t = _DUP_TITLE_SUFFIX_RE.sub("", t).strip()
    return t


def _dup_normalize_title(title: Optional[str]) -> str:
    """Lowercase, drop a trailing series/edition suffix, strip punctuation,
    drop a leading article."""
    if not title:
        return ""
    t = _dup_strip_title_suffix(title)
    t = t.lower()
    t = re.sub(r"[^\w\s]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    for article in ("the ", "a ", "an "):
        if t.startswith(article):
            t = t[len(article):]
            break
    return t


def _dup_titles_overlap(t1: Optional[str], t2: Optional[str]) -> bool:
    n1, n2 = _dup_normalize_title(t1), _dup_normalize_title(t2)
    if not n1 or not n2:
        return False
    return n1 == n2 or fuzz.token_set_ratio(n1, n2) >= _DUP_TITLE_OVERLAP_THRESHOLD


def _dup_author_surname(author: Optional[str]) -> str:
    """Last significant name token — tolerant of initials spacing ("J. Q.
    Sampleton" and "J.Q. Sampleton" both end in "sampleton": removing periods
    merges unspaced initials into one token but never merges two
    already-spaced ones) and of a generational suffix ("J. Q. Sampleton" vs
    "J. Q. Sampleton Jr." both end in "sampleton")."""
    if not author:
        return ""
    t = re.sub(r"[.,]", "", author.lower())
    tokens = [tok for tok in t.split() if tok not in _DUP_AUTHOR_SUFFIX_TOKENS]
    return tokens[-1] if tokens else ""


def _dup_authors_overlap(a1: Optional[str], a2: Optional[str]) -> bool:
    s1, s2 = _dup_author_surname(a1), _dup_author_surname(a2)
    return len(s1) >= 2 and s1 == s2


def _dup_values_match(v1, v2) -> bool:
    return bool(v1) and bool(v2) and str(v1).strip().lower() == str(v2).strip().lower()


def _dup_audio_corroboration(a, b) -> List[str]:
    """Independent signals (beyond duration) that `a` and `b` are the same
    recording. Order is the display order in `detail`."""
    signals = []
    if _dup_values_match(a.asin, b.asin):
        signals.append("same ASIN")
    if _dup_values_match(a.isbn, b.isbn):
        signals.append("same ISBN")
    if _dup_values_match(a.narrators, b.narrators):
        signals.append("same narrator")
    if _dup_titles_overlap(a.title, b.title):
        signals.append("matching title")
    if _dup_authors_overlap(a.author, b.author):
        signals.append("overlapping author")
    return signals


def _dup_ebook_corroboration(a, b) -> List[str]:
    signals = []
    if _dup_titles_overlap(a.title, b.title):
        signals.append("matching title")
    if _dup_authors_overlap(a.author, b.author):
        signals.append("overlapping author")
    return signals


class _UnionFind:
    """Minimal union-find over a fixed id set — connected components become
    the possible-duplicate groups."""

    def __init__(self, ids):
        self._parent = {i: i for i in ids}

    def find(self, x):
        while self._parent[x] != x:
            self._parent[x] = self._parent[self._parent[x]]
            x = self._parent[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self._parent[ra] = rb


# id -> {other_id: (extra_info, [signal, ...])} for the direct pairwise
# matches that produced a group — `extra_info` is the duration gap in seconds
# for audiobooks, unused (None) for ebooks.
_DupEdges = Dict[int, Dict[int, Tuple[Optional[int], List[str]]]]


def _group_possible_duplicate_audiobooks(audiobooks) -> Tuple[list, _DupEdges]:
    """Audiobooks whose duration matches within tolerance AND share a second,
    independent signal (see the module comment above). Returns
    ``(groups, edges)``: `groups` is a list of item-lists (size >= 2, at least
    one direct edge each); `edges` carries the per-pair (diff_seconds,
    signals) used to build `detail`.

    Pure in-memory comparisons over rows already loaded by the caller — no
    query, no file access. Candidates are sorted by duration first (O(n log
    n)); since duration only increases through the sort, the inner loop
    breaks out as soon as it passes the loosest tolerance any later candidate
    could have, so the common case (few matches) is a single pass rather than
    the full O(n^2) grid. A library where most items cluster within the
    tolerance is the pathological case and still runs in memory only.
    """
    candidates = sorted(
        (ab for ab in audiobooks
         if ab.duration_seconds and ab.duration_seconds >= _DUP_MIN_AUDIO_DURATION_SECONDS),
        key=lambda ab: ab.duration_seconds,
    )
    if not candidates:
        return [], {}

    global_cap = _audio_duration_tolerance(candidates[-1].duration_seconds)
    uf = _UnionFind(ab.id for ab in candidates)
    edges: _DupEdges = defaultdict(dict)

    for i, a in enumerate(candidates):
        for b in candidates[i + 1:]:
            diff = b.duration_seconds - a.duration_seconds
            if diff > global_cap:
                break  # sorted ascending — nothing further can be closer
            tol = max(_audio_duration_tolerance(a.duration_seconds),
                      _audio_duration_tolerance(b.duration_seconds))
            if diff > tol:
                continue
            if a.file_hash and b.file_hash and a.file_hash == b.file_hash:
                continue  # exact duplicate — already the certain `duplicate` category
            signals = _dup_audio_corroboration(a, b)
            if not signals:
                continue
            edges[a.id][b.id] = (diff, signals)
            edges[b.id][a.id] = (diff, signals)
            uf.union(a.id, b.id)

    clusters = defaultdict(list)
    for ab in candidates:
        if ab.id in edges:
            clusters[uf.find(ab.id)].append(ab)
    return [g for g in clusters.values() if len(g) > 1], edges


def _group_possible_duplicate_ebooks(ebooks) -> Tuple[list, _DupEdges]:
    """Ebooks sharing an exact `file_size`, not all sharing `file_hash`, and
    corroborated by title or author overlap (see the module comment above). A
    same-size group whose members *all* share one hash is a certain duplicate
    already, so it is never unioned here and never appears twice."""
    by_size = defaultdict(list)
    for eb in ebooks:
        if eb.file_size:
            by_size[eb.file_size].append(eb)

    uf = _UnionFind(eb.id for eb in ebooks if eb.file_size)
    edges: _DupEdges = defaultdict(dict)

    for group in by_size.values():
        if len(group) < 2:
            continue
        for i, a in enumerate(group):
            for b in group[i + 1:]:
                if a.file_hash and b.file_hash and a.file_hash == b.file_hash:
                    continue  # exact duplicate — already the certain `duplicate` category
                signals = _dup_ebook_corroboration(a, b)
                if not signals:
                    continue
                edges[a.id][b.id] = (None, signals)
                edges[b.id][a.id] = (None, signals)
                uf.union(a.id, b.id)

    clusters = defaultdict(list)
    for eb in ebooks:
        if eb.file_size and eb.id in edges:
            clusters[uf.find(eb.id)].append(eb)
    return [g for g in clusters.values() if len(g) > 1], edges


def _dup_hms(seconds: int) -> str:
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}"


def _dup_audio_detail(item, peers: Dict[int, Tuple[int, List[str]]]) -> str:
    other_count = len(peers)
    plural = "s" if other_count != 1 else ""
    max_diff = max(diff for diff, _ in peers.values())
    signals = sorted({s for _, sigs in peers.values() for s in sigs})
    hms = _dup_hms(item.duration_seconds)
    if max_diff == 0:
        length_phrase = f"same duration to the second as {other_count} other audiobook{plural} ({hms})"
    else:
        length_phrase = f"duration within {max_diff}s of {other_count} other audiobook{plural} (~{hms})"
    return (f"Possible duplicate — {length_phrase}, matched on {', '.join(signals)}. "
            f"Candidate only; review before deleting.")


def _dup_ebook_detail(item, peers: Dict[int, Tuple[None, List[str]]]) -> str:
    other_count = len(peers)
    plural = "s" if other_count != 1 else ""
    signals = sorted({s for _, sigs in peers.values() for s in sigs})
    return (f"Possible duplicate — same file size as {other_count} other ebook{plural}, "
            f"different content hash, matched on {', '.join(signals)}. "
            f"Candidate only; review before deleting.")


def _dup_order_pair_aware(group, paired_ids: set) -> List[Tuple[object, bool]]:
    """Paired members first (never marked); unpaired members after, marked
    `likely_redundant=True` only when the group also has a paired member to
    prefer instead — an all-paired or all-unpaired group has no relative
    preference to report."""
    paired = [it for it in group if it.id in paired_ids]
    unpaired = [it for it in group if it.id not in paired_ids]
    mark_unpaired = bool(paired) and bool(unpaired)
    return [(it, False) for it in paired] + [(it, mark_unpaired) for it in unpaired]


def _possible_dup_item_dict(item, item_type: str, detail: str, likely_redundant: bool) -> dict:
    d = _item_dict(item, item_type, detail)
    d["likely_redundant"] = likely_redundant
    return d


def _build_possible_duplicate_category(ebooks, audiobooks, all_pairs) -> list:
    """The `possible_duplicate` category: content-similarity candidates,
    pair-aware ordered (paired copy first, unpaired copy marked
    `likely_redundant` when the group has both)."""
    paired_ebook_ids = {p.ebook_id for p in all_pairs}
    paired_audiobook_ids = {p.audiobook_id for p in all_pairs}

    possible_duplicate = []

    audio_groups, audio_edges = _group_possible_duplicate_audiobooks(audiobooks)
    for group in audio_groups:
        for item, likely_redundant in _dup_order_pair_aware(group, paired_audiobook_ids):
            detail = _dup_audio_detail(item, audio_edges[item.id])
            if likely_redundant:
                detail += (" Not paired to any ebook — the paired copy in "
                           "this group is likely the one to keep.")
            possible_duplicate.append(
                _possible_dup_item_dict(item, "audiobook", detail, likely_redundant))

    ebook_groups, ebook_edges = _group_possible_duplicate_ebooks(ebooks)
    for group in ebook_groups:
        for item, likely_redundant in _dup_order_pair_aware(group, paired_ebook_ids):
            detail = _dup_ebook_detail(item, ebook_edges[item.id])
            if likely_redundant:
                detail += (" Not paired to any audiobook — the paired copy "
                           "in this group is likely the one to keep.")
            possible_duplicate.append(
                _possible_dup_item_dict(item, "ebook", detail, likely_redundant))

    return possible_duplicate


# ---------------------------------------------------------------------------
# Issues
# ---------------------------------------------------------------------------

def _scan_library_files(ebook_rows: List[dict], audiobook_rows: List[dict]) -> dict:
    """Stat every library file and check every audiobook's chapter atom.

    **Blocking, and the reason this endpoint had to change (issue #208):** one
    `os.path.isfile` plus one `os.path.getsize` per row, plus a mutagen atom
    parse per audiobook, all against a NAS mount. On the event loop — which is
    where it used to run — that stalled every other request for its duration,
    on a server with a single uvicorn worker.

    Takes plain dicts rather than ORM rows so nothing here can touch the
    session from a worker thread: the caller snapshots the columns first.
    """
    missing, zero_byte, unsupported, chapter_encoding_bad = [], [], [], []

    for row in ebook_rows:
        path = row.get("file_path")
        if not path or not os.path.isfile(path):
            missing.append({**row, "detail": "File not found on storage"})
        else:
            sz = os.path.getsize(path)
            if sz < _TINY_EBOOK_BYTES:
                zero_byte.append({**row, "detail": f"File is only {sz} bytes"})
        fmt = (row.get("format") or "").lower()
        if fmt in ("mobi", "azw3"):
            converted = Path(path).with_suffix(".epub").exists() if path else False
            if not converted:
                unsupported.append(
                    {**row, "detail": f"{fmt.upper()} needs conversion to EPUB"}
                )

    for row in audiobook_rows:
        path = row.get("file_path")
        if not path or not os.path.isfile(path):
            missing.append({**row, "detail": "File not found on storage"})
        else:
            sz = os.path.getsize(path)
            if sz < _TINY_AUDIO_BYTES:
                zero_byte.append(
                    {**row, "detail": f"File is only {sz} bytes — likely truncated"}
                )
            ok, detail = chapter_repair.check_chapter_encoding_cached(path)
            if not ok:
                chapter_encoding_bad.append(
                    {**row, "detail": detail or "Non-UTF-8 chapter title"}
                )

    return {
        "missing": missing,
        "zero_byte": zero_byte,
        "unsupported": unsupported,
        "chapter_encoding_bad": chapter_encoding_bad,
    }


@router.get("/issues", response_model=TroubleshootIssues)
async def get_issues(
    db: AsyncSession = Depends(get_db, scope="function"),
    # Editor: this page's own fix controls are editor-gated, so gating its data
    # at admin would lock out exactly the role it was built for (issue #283).
    #
    # Rate limited on top of that (issue #208). The role check bounds who may
    # ask; nothing bounded how often, and this is two full-table loads plus a
    # stat and an atom parse per row.
    _: User = Depends(rate_limited(expensive_reads, get_editor_user)),
):
    """Return all current issues grouped by category. Cheap categories are
    computed live; audio/ebook integrity come from the last scan."""
    ebooks = (await db.execute(select(EBook))).scalars().all()
    audiobooks = (await db.execute(select(AudioBook))).scalars().all()

    # Snapshot the columns the scan needs before handing them to a thread: ORM
    # instances are bound to this request's session, and an expired attribute
    # touched off-loop would issue IO on it.
    scan = await asyncio.to_thread(
        _scan_library_files,
        [_item_dict(eb, "ebook") for eb in ebooks],
        [_item_dict(ab, "audiobook") for ab in audiobooks],
    )
    missing = scan["missing"]
    zero_byte = scan["zero_byte"]
    unsupported = scan["unsupported"]
    chapter_encoding_bad = scan["chapter_encoding_bad"]

    # Expensive integrity results from the last scan.
    audio_corrupt, ebook_drm, ebook_unreadable = [], [], []
    ab_by_id = {a.id: a for a in audiobooks}
    eb_by_id = {e.id: e for e in ebooks}
    rows = (await db.execute(
        select(LibraryCheckResult).where(LibraryCheckResult.ok == False)  # noqa: E712
    )).scalars().all()
    for r in rows:
        if r.check_type == "audio_integrity" and r.item_id in ab_by_id:
            audio_corrupt.append(_item_dict(ab_by_id[r.item_id], "audiobook", r.detail or "Corrupt audio"))
        elif r.check_type == "ebook_integrity" and r.item_id in eb_by_id:
            d = (r.detail or "").lower()
            bucket = ebook_drm if "drm" in d or "encrypt" in d else ebook_unreadable
            bucket.append(_item_dict(eb_by_id[r.item_id], "ebook", r.detail or "Unreadable ebook"))

    # Failed transcriptions: pairs in ERROR + latest queue error.
    failed_transcription = []
    err_pairs = (await db.execute(
        select(BookPair).where(BookPair.status == PairStatus.ERROR)
    )).scalars().all()
    for pair in err_pairs:
        q = (await db.execute(
            select(TranscriptionQueueItem)
            .where(TranscriptionQueueItem.book_pair_id == pair.id)
            .order_by(TranscriptionQueueItem.id.desc())
        )).scalars().first()
        eb = eb_by_id.get(pair.ebook_id)
        ab = ab_by_id.get(pair.audiobook_id)
        failed_transcription.append({
            "pair_id": pair.id,
            "ebook_id": pair.ebook_id,
            "audiobook_id": pair.audiobook_id,
            "title": (ab.title if ab else None) or (eb.title if eb else f"Pair {pair.id}"),
            "author": (ab.author if ab else None) or (eb.author if eb else None),
            "detail": (q.error_message if q and q.error_message else "Transcription failed"),
        })

    # Failed ACSM imports (quarantined files).
    failed_acsm = []
    try:
        from services.import_sources.acsm import _failed_dir
        fdir = _failed_dir()
        if fdir.exists():
            for p in sorted(fdir.iterdir()):
                if p.is_file():
                    failed_acsm.append({
                        "filename": p.name,
                        "file_path": str(p),
                        "file_size": p.stat().st_size,
                        "detail": "Import failed — see import logs (expired ACSM, device limit, or decryption error)",
                    })
    except Exception as e:
        logger.debug(f"failed_acsm listing skipped: {e}")

    # Synced pairs that are missing their sync map.
    sync_map_missing = []
    all_pairs = (await db.execute(select(BookPair))).scalars().all()
    syncmap_pair_ids = set(
        (await db.execute(select(SyncMap.book_pair_id))).scalars().all()
    )
    for pair in all_pairs:
        if pair.status == PairStatus.SYNCED and pair.id not in syncmap_pair_ids:
            eb = eb_by_id.get(pair.ebook_id)
            ab = ab_by_id.get(pair.audiobook_id)
            sync_map_missing.append({
                "pair_id": pair.id,
                "ebook_id": pair.ebook_id,
                "audiobook_id": pair.audiobook_id,
                "title": (eb.title if eb else None) or (ab.title if ab else f"Pair {pair.id}"),
                "author": (eb.author if eb else None) or (ab.author if ab else None),
                "detail": "Marked synced but has no sync map — re-queue to rebuild it",
            })

    # Pairs whose audio length cannot account for the ebook's text (issue #458).
    # Recorded at pair creation rather than computed here: the verdict depends on
    # the files as they were when paired, and recomputing it on every page load
    # would re-derive the same answer for a whole library on a rate-limited read.
    implausible_pair = []
    pair_by_id = {p.id: p for p in all_pairs}
    plausibility_rows = (await db.execute(
        select(LibraryCheckResult).where(
            LibraryCheckResult.item_type == "pair",
            LibraryCheckResult.check_type == pair_plausibility.CHECK_TYPE,
            LibraryCheckResult.ok == False,  # noqa: E712
        )
    )).scalars().all()
    for r in plausibility_rows:
        pair = pair_by_id.get(r.item_id)
        if pair is None:
            continue            # pair deleted since the check ran
        eb = eb_by_id.get(pair.ebook_id)
        ab = ab_by_id.get(pair.audiobook_id)
        implausible_pair.append({
            "pair_id": pair.id,
            "ebook_id": pair.ebook_id,
            "audiobook_id": pair.audiobook_id,
            "title": (eb.title if eb else None) or (ab.title if ab else f"Pair {pair.id}"),
            "author": (eb.author if eb else None) or (ab.author if ab else None),
            "detail": r.detail or "Audio length does not fit this ebook",
        })

    # Duplicate files (same content hash) within each media type.
    duplicate = []
    by_hash = defaultdict(list)
    for eb in ebooks:
        if eb.file_hash:
            by_hash[("ebook", eb.file_hash)].append(eb)
    for ab in audiobooks:
        if ab.file_hash:
            by_hash[("audiobook", ab.file_hash)].append(ab)
    for (itype, h), group in by_hash.items():
        if len(group) > 1:
            for it in group:
                duplicate.append(_item_dict(it, itype, f"{len(group)} copies share hash {h[:12]}…"))

    # Possible duplicates (issue #692): content-similarity candidates the
    # exact-hash check above cannot see — see the module comment near
    # `_build_possible_duplicate_category` for the corroboration rule.
    # `all_pairs` was already loaded above for `sync_map_missing`.
    possible_duplicate = _build_possible_duplicate_category(ebooks, audiobooks, all_pairs)

    # Covers: missing (broken/absent) and orphaned (file with no owner).
    covers_dir = settings.covers_dir
    missing_cover = []
    referenced = set()

    def _cover_filename(cover_path: Optional[str]) -> Optional[str]:
        if not cover_path:
            return None
        return os.path.basename(cover_path.split("?")[0])

    for item, itype in [(e, "ebook") for e in ebooks] + [(a, "audiobook") for a in audiobooks]:
        fn = _cover_filename(item.cover_path)
        if fn:
            referenced.add(fn)
            if not os.path.isfile(os.path.join(covers_dir, fn)):
                missing_cover.append(_item_dict(item, itype, "Cover reference set but file is missing"))
        else:
            missing_cover.append(_item_dict(item, itype, "No cover image"))

    orphaned_cover = []
    if os.path.isdir(covers_dir):
        for f in sorted(os.listdir(covers_dir)):
            fp = os.path.join(covers_dir, f)
            if os.path.isfile(fp) and f not in referenced:
                orphaned_cover.append({
                    "filename": f,
                    "file_path": fp,
                    "file_size": os.path.getsize(fp),
                    "detail": "Cover file not referenced by any book",
                })

    # Multi-file audiobook folders the scanner refused to import (issue #63).
    # Persisted by the scan; dismissed rows stay hidden until the folder's
    # contents change. `imported_track_count` is live: rows that predate the
    # detection (one AudioBook per track) can be removed from here.
    multi_file = []
    folder_rows = (await db.execute(
        select(MultiFileAudiobookFolder)
        .where(MultiFileAudiobookFolder.dismissed == False)
        .order_by(MultiFileAudiobookFolder.folder_path)
    )).scalars().all()
    for row in folder_rows:
        multi_file.append({
            "item_type": "folder",
            "item_id": row.id,
            "title": row.guessed_title or os.path.basename(row.folder_path),
            "author": row.guessed_author,
            "filename": None,
            "file_path": row.folder_path,
            "format": row.extension.lstrip("."),
            "file_size": row.total_size,
            "file_count": row.file_count,
            "extension": row.extension,
            "imported_track_count": sum(
                1 for ab in audiobooks
                if _is_track_of(ab.file_path, row.folder_path, row.extension)),
            "detail": (f"{row.file_count} {row.extension} files — multi-file audiobooks "
                       f"aren't supported; merge to one .m4b in Audiobookshelf and rescan"),
            "pair_id": None,
        })

    categories = {
        "missing": missing,
        "zero_byte": zero_byte,
        "chapter_encoding_bad": chapter_encoding_bad,
        "audio_corrupt": audio_corrupt,
        "ebook_drm": ebook_drm,
        "ebook_unreadable": ebook_unreadable,
        "unsupported_format": unsupported,
        "multi_file_audiobook": multi_file,
        "sync_map_missing": sync_map_missing,
        "implausible_pair": implausible_pair,
        "duplicate": duplicate,
        "possible_duplicate": possible_duplicate,
        "missing_cover": missing_cover,
        "orphaned_cover": orphaned_cover,
        "failed_transcription": failed_transcription,
        "failed_acsm": failed_acsm,
    }
    return {
        "categories": categories,
        "counts": {k: len(v) for k, v in categories.items()},
        "total": sum(len(v) for v in categories.values()),
    }


# ---------------------------------------------------------------------------
# Scan
# ---------------------------------------------------------------------------

@router.post("/scan", status_code=202, response_model=ActionResult)
async def start_scan(_: User = Depends(get_editor_user)):
    started = await library_verify.start_scan()
    if not started:
        raise HTTPException(status_code=409, detail="A verification scan is already running")
    return {"status": "started"}


@router.get("/scan/progress", response_model=LibraryScanProgress)
async def scan_progress(_: User = Depends(get_current_user)):
    return library_verify.get_progress()


@router.post("/scan/cancel", response_model=ActionResult)
async def scan_cancel(_: User = Depends(get_editor_user)):
    library_verify.request_cancel()
    return {"status": "cancel_requested"}


# ---------------------------------------------------------------------------
# Fixes: delete (single + bulk), replace, requeue, acsm-dismiss
# ---------------------------------------------------------------------------

async def _delete_item_core(db: AsyncSession, item_type: str, item_id: int, delete_file: bool) -> None:
    model = EBook if item_type == "ebook" else AudioBook
    item = (await db.execute(select(model).where(model.id == item_id))).scalar_one_or_none()
    if not item:
        return
    file_path = item.file_path

    pair_col = BookPair.ebook_id if item_type == "ebook" else BookPair.audiobook_id
    pairs = (await db.execute(select(BookPair).where(pair_col == item_id))).scalars().all()
    for pair in pairs:
        # Sibling of library.delete_ebook/_audiobook: demote each user's
        # pair-scoped position onto the surviving medium before the pair
        # cascade takes it (issue #155).
        await demote_pair_positions(
            db, pair.id,
            keep_ebook=item_type != "ebook",
            keep_audiobook=item_type != "audiobook",
        )
        await db.execute(sa_delete(TranscriptionQueueItem).where(TranscriptionQueueItem.book_pair_id == pair.id))
        await db.execute(sa_delete(AudioTranscript).where(AudioTranscript.pair_id == pair.id))

    if item_type == "ebook":
        await release_standalone_positions(db, ebook_id=item_id)
        await db.execute(sa_delete(UserProgress).where(UserProgress.ebook_id == item_id))
    else:
        await release_standalone_positions(db, audiobook_id=item_id)
        await db.execute(sa_delete(UserProgress).where(UserProgress.audiobook_id == item_id))

    await db.execute(sa_delete(LibraryCheckResult).where(
        LibraryCheckResult.item_type == item_type, LibraryCheckResult.item_id == item_id))

    await db.delete(item)
    await db.commit()

    if delete_file and file_path:
        try:
            os.unlink(file_path)
        except OSError as e:
            logger.warning(f"Could not delete source file {file_path}: {e}")


class BulkDeleteItem(BaseModel):
    item_type: str
    item_id: int


class BulkDeleteRequest(BaseModel):
    items: List[BulkDeleteItem]


@router.post("/bulk-delete", response_model=DeletedCount)
async def bulk_delete(
    req: BulkDeleteRequest,
    delete_file: bool = Query(True),
    db: AsyncSession = Depends(get_db, scope="function"),
    _: User = Depends(get_editor_user),
):
    deleted = 0
    for entry in req.items:
        if entry.item_type not in ("ebook", "audiobook"):
            continue
        await _delete_item_core(db, entry.item_type, entry.item_id, delete_file)
        deleted += 1
    return {"deleted": deleted}


@router.post("/replace/{item_type}/{item_id}", response_model=ReplaceFileResult)
async def replace_file(
    item_type: str,
    item_id: int,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db, scope="function"),
    _: User = Depends(get_editor_user),
):
    """Replace an item's underlying file in place (keeps the DB row and its
    pairing). Metadata from the new file wins; gaps are filled from the old row."""
    if item_type not in ("ebook", "audiobook"):
        raise HTTPException(status_code=400, detail="item_type must be 'ebook' or 'audiobook'")
    model = EBook if item_type == "ebook" else AudioBook
    allowed = EBOOK_EXTENSIONS if item_type == "ebook" else AUDIOBOOK_EXTENSIONS

    item = (await db.execute(select(model).where(model.id == item_id))).scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail=f"{item_type} not found")

    new_ext = Path(file.filename).suffix.lower()
    if new_ext not in allowed:
        raise HTTPException(status_code=400, detail=f"Unsupported {item_type} format: {new_ext}")

    old_path = item.file_path
    default_dir = settings.ebook_dir if item_type == "ebook" else settings.audiobook_dir
    dest_dir = os.path.dirname(old_path) if old_path else default_dir
    os.makedirs(dest_dir, exist_ok=True)
    base = Path(old_path).stem if old_path else Path(safe_join(dest_dir, file.filename).name).stem
    dest_path = str(safe_join(dest_dir, base + new_ext))

    new_size = await stream_upload_to_path(file, Path(dest_path), settings.max_upload_file_bytes)

    # Remove the old file if its path differs from the new one.
    if old_path and os.path.abspath(old_path) != os.path.abspath(dest_path) and os.path.isfile(old_path):
        try:
            os.unlink(old_path)
        except OSError as e:
            logger.warning(f"Could not remove old file {old_path}: {e}")

    # Merge metadata: new file's values win; otherwise keep what the row has.
    from routers.library import extract_metadata
    try:
        new_meta = await extract_metadata(dest_path, item_type, db, library_root=default_dir)
    except Exception as e:
        logger.warning(f"replace: metadata extract failed for {dest_path}: {e}")
        new_meta = {}

    item.file_path = dest_path
    item.filename = base + new_ext
    item.file_size = new_size
    # hash_file matches hash_bytes exactly (composite scheme) — see test_file_hash.py.
    item.file_hash = hash_file(dest_path)
    item.format = new_ext.lstrip(".")
    for field in ("title", "author", "series", "series_index"):
        val = new_meta.get(field)
        if val:
            setattr(item, field, val)
    if not item.title:
        item.title = base
    # The file is the authority on its own length (issue #127) — carrying the
    # replaced file's duration forward would leave a silently wrong end zone.
    if item_type == "audiobook" and new_meta.get("duration_seconds"):
        item.duration_seconds = new_meta["duration_seconds"]

    # Replacing an ebook keeps the row id, so every position still points at
    # it — but its `epub_sentence_index`, its `sync_map_version` and every
    # device hint describe the file that was just overwritten (issue #303).
    # Same transaction as the swap: a commit that stores the new file must not
    # leave the old coordinates behind.
    if item_type == "ebook":
        await invalidate_parse_coordinates_for_ebook(db, item_id)

    # Replacing an audiobook invalidates the cached transcript & sync
    # (issue #588: shared with the scan-side detector so both react the
    # same way to a file that no longer matches what a pair was synced to).
    if item_type == "audiobook":
        await invalidate_audiobook_transcripts(db, item_id)

    # Re-run the integrity check so the issue clears (or re-flags) immediately.
    import asyncio as _asyncio
    if item_type == "audiobook":
        from services.audio_integrity import check_audio_integrity
        ok, det = await _asyncio.to_thread(check_audio_integrity, dest_path)
        check_type = "audio_integrity"
    else:
        from services.ebook_integrity import check_ebook_integrity
        ok, det = await _asyncio.to_thread(check_ebook_integrity, dest_path)
        check_type = "ebook_integrity"

    row = (await db.execute(select(LibraryCheckResult).where(
        LibraryCheckResult.item_type == item_type,
        LibraryCheckResult.item_id == item_id,
        LibraryCheckResult.check_type == check_type,
    ))).scalar_one_or_none()
    if row is None:
        row = LibraryCheckResult(item_type=item_type, item_id=item_id, check_type=check_type)
        db.add(row)
    st = os.stat(dest_path)
    row.file_path = dest_path
    row.file_size = st.st_size
    row.file_mtime = st.st_mtime
    row.ok = ok
    row.detail = det

    await db.commit()
    return {"status": "replaced", "integrity_ok": ok, "detail": det, "item_id": item_id}


@router.post("/repair-chapter-encoding/{item_id}", response_model=ChapterRepairResult)
async def repair_chapter_encoding_endpoint(
    item_id: int,
    db: AsyncSession = Depends(get_db, scope="function"),
    _: User = Depends(get_editor_user),
):
    """Repair a single audiobook's non-UTF-8 chapter titles in place."""
    ab = (await db.execute(select(AudioBook).where(AudioBook.id == item_id))).scalar_one_or_none()
    if not ab:
        raise HTTPException(status_code=404, detail="Audiobook not found")
    if not ab.file_path or not os.path.isfile(ab.file_path):
        raise HTTPException(status_code=404, detail="Audiobook file not found on disk")

    import asyncio
    ok, error = await asyncio.to_thread(chapter_repair.repair_chapter_encoding, ab.file_path)
    if ok:
        return {"status": "repaired", "detail": None, "item_id": item_id}
    _, recheck_detail = await asyncio.to_thread(chapter_repair.check_chapter_encoding, ab.file_path)
    return {"status": "failed", "detail": error or recheck_detail, "item_id": item_id}


class BulkRepairChapterEncodingRequest(BaseModel):
    item_ids: List[int]


@router.post("/bulk-repair-chapter-encoding", response_model=BulkChapterRepairResult)
async def bulk_repair_chapter_encoding(
    req: BulkRepairChapterEncodingRequest,
    db: AsyncSession = Depends(get_db, scope="function"),
    _: User = Depends(get_editor_user),
):
    import asyncio
    repaired = 0
    failures = []
    for item_id in req.item_ids:
        ab = (await db.execute(select(AudioBook).where(AudioBook.id == item_id))).scalar_one_or_none()
        if not ab or not ab.file_path or not os.path.isfile(ab.file_path):
            failures.append({"item_id": item_id, "title": ab.title if ab else None, "error": "File not found on disk"})
            continue
        ok, error = await asyncio.to_thread(chapter_repair.repair_chapter_encoding, ab.file_path)
        if ok:
            repaired += 1
        else:
            failures.append({"item_id": item_id, "title": ab.title, "error": error})
    return {"repaired": repaired, "failures": failures}


@router.post("/requeue/{pair_id}", response_model=RequeueResult)
async def requeue_pair(
    pair_id: int,
    db: AsyncSession = Depends(get_db, scope="function"),
    _: User = Depends(get_editor_user),
):
    pair = (await db.execute(select(BookPair).where(BookPair.id == pair_id))).scalar_one_or_none()
    if not pair:
        raise HTTPException(status_code=404, detail="Pair not found")
    from services.queue_manager import add_to_queue
    await add_to_queue([pair_id])
    return {"status": "queued", "pair_id": pair_id}


# ---------------------------------------------------------------------------
# Sync-map drift audit (issue #295)
# ---------------------------------------------------------------------------

@router.get("/sync-map-audit", response_model=SyncMapAuditResponse)
async def sync_map_audit(
    sample_size: int = Query(sync_map_audit_service.DEFAULT_SAMPLE_SIZE, ge=0, le=100),
    pair_id: Optional[int] = Query(None, ge=1),
    limit: Optional[int] = Query(None, ge=1, le=1000),
    flagged_only: bool = Query(False),
    db: AsyncSession = Depends(get_db, scope="function"),
    _: User = Depends(get_editor_user),
):
    """Which pairs' sync maps no longer describe the ebook on disk (issue #295).

    A map is only meaningful against the file it was aligned from. Pair 260's
    resolved audio positions to sentences that occur nowhere in its current
    EPUB, and nothing could tell an operator that — or which other pairs were in
    the same state. Two signals per pair: the ebook hash recorded at alignment
    time versus the file's hash now, and the share of a sampled set of the map's
    stored sentences that can still be found in the book's whole-spine text.
    See `services/sync_map_audit.py` for how the verdict is reached.

    Read-only. A flagged pair is re-aligned through the endpoint that already
    does that — `POST /api/transcription/{pair_id}/realign` — which each stale
    row names in `realign_path`; a pair with no cached transcript is flagged
    `retranscribe` instead, because re-alignment has nothing to rebuild from.

    `sample_size=0` skips EPUB parsing for a fast provenance-only pass;
    `pair_id` audits one pair. Editor-gated like its troubleshoot siblings: the
    full audit hashes and parses every paired ebook on disk.
    """
    rows = await sync_map_audit_service.audit_sync_maps(
        db, sample_size=sample_size, pair_id=pair_id, limit=limit
    )
    # "degraded" (issue #586, out-of-order audio) is as actionable as "stale"
    # (wrong file/edition) — both need an operator's attention, just with a
    # different fix (`suggested_action` names which).
    flagged = [r for r in rows if r["status"] in ("stale", "degraded")]
    return {
        "sample_size": sample_size,
        "checked": len(rows),
        "flagged": len(flagged),
        "realign_endpoint": sync_map_audit_service.REALIGN_ENDPOINT,
        "pairs": flagged if flagged_only else rows,
    }


class DeleteCoversRequest(BaseModel):
    filenames: List[str]


@router.post("/delete-orphan-covers", response_model=DeletedCount)
async def delete_orphan_covers(req: DeleteCoversRequest, _: User = Depends(get_editor_user)):
    """Delete orphaned cover files. Paths are constrained to the covers dir."""
    covers_dir = os.path.abspath(settings.covers_dir)
    deleted = 0
    for name in req.filenames:
        fp = os.path.abspath(os.path.join(covers_dir, os.path.basename(name)))
        if os.path.dirname(fp) != covers_dir:
            continue
        if os.path.isfile(fp):
            try:
                os.unlink(fp)
                deleted += 1
            except OSError as e:
                logger.warning(f"Could not delete cover {fp}: {e}")
    return {"deleted": deleted}


class AcsmDismissRequest(BaseModel):
    filename: str


@router.post("/acsm-dismiss", response_model=ActionResult)
async def acsm_dismiss(req: AcsmDismissRequest, _: User = Depends(get_editor_user)):
    from services.import_sources.acsm import _failed_dir
    target = _failed_dir() / Path(req.filename).name
    if not target.is_file():
        raise HTTPException(status_code=404, detail="File not found in failed imports")
    try:
        target.unlink()
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Could not delete: {e}")
    return {"status": "dismissed"}


# ---------------------------------------------------------------------------
# Multi-file audiobook folders (issue #63)
# ---------------------------------------------------------------------------

async def _folder_or_404(db: AsyncSession, folder_id: int) -> MultiFileAudiobookFolder:
    row = (await db.execute(
        select(MultiFileAudiobookFolder).where(MultiFileAudiobookFolder.id == folder_id)
    )).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Multi-file audiobook folder not found")
    return row


@router.post("/multi-file/{folder_id}/dismiss", response_model=MultiFileDismissResult)
async def multi_file_dismiss(
    folder_id: int,
    db: AsyncSession = Depends(get_db, scope="function"),
    _: User = Depends(get_editor_user),
):
    """Hide a flagged folder until its contents change (the next scan clears
    the dismissal when the folder's fingerprint moves)."""
    row = await _folder_or_404(db, folder_id)
    row.dismissed = True
    await db.commit()
    return {"status": "dismissed", "id": row.id}


@router.post("/multi-file/{folder_id}/remove-tracks",
             response_model=MultiFileRemoveTracksResult)
async def multi_file_remove_tracks(
    folder_id: int,
    db: AsyncSession = Depends(get_db, scope="function"),
    _: User = Depends(get_editor_user),
):
    """Delete the AudioBook rows that were imported one-per-track from this
    folder before multi-file detection existed. DB rows only — the files stay
    on disk for merging in Audiobookshelf; the folder stays flagged."""
    row = await _folder_or_404(db, folder_id)
    audiobooks = (await db.execute(select(AudioBook))).scalars().all()
    victims = [ab.id for ab in audiobooks
               if _is_track_of(ab.file_path, row.folder_path, row.extension)]
    for audiobook_id in victims:
        await _delete_item_core(db, "audiobook", audiobook_id, delete_file=False)
    return {"deleted": len(victims), "id": row.id}
