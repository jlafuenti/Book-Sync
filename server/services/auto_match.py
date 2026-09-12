"""
Automatic ebook/audiobook pairing.

Split out of `routers/library.py` (issue #255). `_score_candidate` and the
four rule helpers above it are pure functions over plain objects — no session
— and are pinned by the golden vectors in `tests/fixtures/auto_match_cases.json`;
`auto_match_books` is the DB-driving shell the scan and the uploads call after
ingesting. The rules themselves are documented inline below (issue #253).
"""

import re
from typing import Optional

from rapidfuzz import fuzz
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.book import AudioBook, BookPair, EBook, PairStatus
from models.settings import SystemSetting
from services.pair_plausibility import record_pair_plausibility
from utils import utcnow


# ---------------------------------------------------------------------------
# Auto-pairing rules (issue #253)
#
# These four helpers are the whole decision an automatic pairing makes. They
# live at module level, take plain objects and touch no session, so the rules
# can be pinned by golden vectors (`tests/fixtures/auto_match_cases.json`)
# without a database — `auto_match_books` below is only the DB-driving shell
# that walks the unpaired rows and applies `_score_candidate`.
#
# Four rules were tightened in issue #253 and their golden vectors flipped in
# the same commit: series indexes compare as floats (1 is not the #1.5
# novella), a missing author on either side raises the title bar instead of
# silently skipping the author gate, the unpair exclusion is keyed by row id as
# well as file hash, and an empty normalized title never pairs. Greedy
# scan-order assignment was reviewed and deliberately kept.
# ---------------------------------------------------------------------------

# A title similarity at or above this wins the pairing when nothing rejects it.
AUTO_MATCH_TITLE_THRESHOLD = 75
# ...but with no author on one side there is no second signal to corroborate a
# near miss, so the title alone has to be this good ("The Way of Kings" vs
# "The Way of Kings Prime" scores 80 — two different books).
AUTO_MATCH_TITLE_THRESHOLD_NO_AUTHOR = 90
# Below this the authors are "different people" and no title score can rescue it.
AUTO_MATCH_AUTHOR_GATE = 70
# At or above this the authors agree well enough to boost the title score.
AUTO_MATCH_AUTHOR_BOOST = 80
AUTO_MATCH_AUTHOR_BOOST_POINTS = 10
# Below this the two series names are different series.
AUTO_MATCH_SERIES_THRESHOLD = 85
# Two series indexes closer than this are the same book. Only float noise is
# meant to fit under it — 1 and 1.5 are different books, and so are 1 and 1.1.
AUTO_MATCH_SERIES_INDEX_TOLERANCE = 0.001


def _normalize_for_comparison(text: str) -> str:
    """Normalize text for fuzzy comparison: strip articles, lowercase."""
    if not text:
        return ""
    t = text.lower().strip()
    # Strip leading articles for comparison
    for article in ['the ', 'a ', 'an ']:
        if t.startswith(article):
            t = t[len(article):]
            break
    return t


def _normalize_author(text: str) -> str:
    """Normalize author name for comparison.
    Strips punctuation used in initials/suffixes (L.E. → le, Jr. → jr)
    so that 'L.E. Modesitt Jr.' and 'L. E. Modesitt, Jr.' compare as equal.
    """
    if not text:
        return ""
    t = text.lower()
    t = re.sub(r'[.,]', '', t)       # remove periods and commas
    t = re.sub(r'\s+', ' ', t).strip()
    return t


def _parse_series_index(value) -> Optional[float]:
    """Best-effort float for a series index, or None when it is not a number.

    The column is a Float, but indexes reach the matcher from EPUB metadata and
    filename patterns before anything coerces them, so "1", "1.0" and "01" all
    turn up and all mean book one. An index that is not a number at all ("II",
    "") is treated as *missing* metadata rather than as a mismatch — see
    `_series_compatible`, which only rejects when both sides parse.
    """
    if value is None:
        return None
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _series_compatible(eb_series, eb_idx, ab_series, ab_idx) -> bool:
    """Return False if series metadata indicates these are different books."""
    if not eb_series and not ab_series:
        return True
    if not eb_series or not ab_series:
        return True  # Only one has series — missing metadata is fine
    series_score = fuzz.token_sort_ratio(
        _normalize_for_comparison(eb_series),
        _normalize_for_comparison(ab_series),
    )
    if series_score < AUTO_MATCH_SERIES_THRESHOLD:
        return False  # Clearly different series
    # Same series — if both have a readable index they must be the same number.
    # Compared as floats, not truncated to whole numbers: a #1.5 novella is a
    # different book from #1, and pairing it with #1's ebook builds a sync map
    # between two different texts (issue #253).
    eb_num = _parse_series_index(eb_idx)
    ab_num = _parse_series_index(ab_idx)
    if eb_num is not None and ab_num is not None:
        if abs(eb_num - ab_num) > AUTO_MATCH_SERIES_INDEX_TOLERANCE:
            return False
    return True


def _auto_pair_excluded(ebook, audiobook) -> bool:
    """True if a manual unpair recorded these two as "never re-pair".

    Two independent keys, either of which blocks the pair:

    * the *row ids* — always recordable, so a row without a file hash (legacy;
      every ingest and upload path computes one now) keeps its memory of the
      unpair instead of being re-paired by the next scan (issue #253);
    * the *file hashes* — kept because they survive a row being deleted and
      re-ingested at a new id, and because `POST /rehash` remaps them.

    Each is checked against the other side's identity, so an exclusion naming
    audiobook 22 does not block audiobook 33.
    """
    eb_excluded_ids = ebook.auto_pair_excluded_ids or []
    ab_excluded_ids = audiobook.auto_pair_excluded_ids or []
    if (audiobook.id is not None and audiobook.id in eb_excluded_ids) or (
        ebook.id is not None and ebook.id in ab_excluded_ids
    ):
        return True

    eb_excluded = ebook.auto_pair_excluded_hashes or []
    ab_excluded = audiobook.auto_pair_excluded_hashes or []
    return bool(
        (audiobook.file_hash and audiobook.file_hash in eb_excluded)
        or (ebook.file_hash and ebook.file_hash in ab_excluded)
    )


def _score_candidate(ebook, audiobook) -> Optional[float]:
    """Score this ebook/audiobook as a pairing, or None if it is not viable.

    None means "rejected": an unpair exclusion, incompatible series metadata,
    authors too far apart, or a title score under the threshold. Otherwise the
    number returned is the title score plus the author-agreement boost, and the
    highest score across an ebook's candidates wins.
    """
    if _auto_pair_excluded(ebook, audiobook):
        return None

    # Reject if series metadata indicates these are different books
    if not _series_compatible(ebook.series, ebook.series_index,
                              audiobook.series, audiobook.series_index):
        return None

    eb_title = _normalize_for_comparison(ebook.title)
    ab_title = _normalize_for_comparison(audiobook.title)

    # An empty title is not a match, it is absent metadata: two of them score a
    # perfect 100 against each other, which used to pair every untitled row in
    # the library with the first other one the scan reached (issue #253).
    if not eb_title or not ab_title:
        return None

    eb_author_norm = _normalize_author(ebook.author)
    ab_author_norm = _normalize_author(audiobook.author)

    # Author gate: if both books have authors they must be similar.
    # token_set_ratio handles initials/punctuation variants well
    # (e.g. "L.E. Modesitt Jr." matches "L. E. Modesitt, Jr.").
    if eb_author_norm and ab_author_norm:
        author_score = fuzz.token_set_ratio(eb_author_norm, ab_author_norm)
        if author_score < AUTO_MATCH_AUTHOR_GATE:
            return None  # Authors too different — don't pair regardless of title
        threshold = AUTO_MATCH_TITLE_THRESHOLD
    else:
        # No author on one side, so nothing corroborates the title. Rather than
        # skip the gate silently, demand a much better title match (issue #253).
        author_score = 0
        threshold = AUTO_MATCH_TITLE_THRESHOLD_NO_AUTHOR

    # Compare titles using fuzzy matching (token sort handles word order)
    score = fuzz.token_sort_ratio(eb_title, ab_title)

    # Boost score if authors also match well
    if author_score >= AUTO_MATCH_AUTHOR_BOOST:
        score = min(100, score + AUTO_MATCH_AUTHOR_BOOST_POINTS)

    if score < threshold:
        return None
    return score


async def auto_match_books(db: AsyncSession) -> int:
    """
    Attempt to auto-match unmatched ebooks and audiobooks by title similarity.
    Uses fuzzy string matching on the extracted titles (`_score_candidate`).
    Returns the number of new pairs created.

    Assignment is greedy in scan order: the first ebook to claim an audiobook
    keeps it, and `matched_audiobook_ids` stops a second ebook taking it in the
    same run.
    """
    # Check if auto-transcribe is enabled
    result = await db.execute(select(SystemSetting).where(SystemSetting.key == "auto_transcribe_enabled"))
    setting = result.scalar_one_or_none()
    auto_transcribe = False
    if setting and setting.value:
        auto_transcribe = setting.value.lower() == "true"

    # Get all ebooks that aren't already paired
    paired_ebook_ids = select(BookPair.ebook_id)
    result = await db.execute(
        select(EBook).where(EBook.id.notin_(paired_ebook_ids))
    )
    unpaired_ebooks = result.scalars().all()

    # Get all audiobooks that aren't already paired
    paired_audiobook_ids = select(BookPair.audiobook_id)
    result = await db.execute(
        select(AudioBook).where(AudioBook.id.notin_(paired_audiobook_ids))
    )
    unpaired_audiobooks = result.scalars().all()

    matched = 0
    matched_audiobook_ids = set()
    new_pair_ids = []

    for ebook in unpaired_ebooks:
        best_match = None
        best_score = 0

        for audiobook in unpaired_audiobooks:
            if audiobook.id in matched_audiobook_ids:
                continue

            score = _score_candidate(ebook, audiobook)
            if score is None:
                continue

            if score > best_score:
                best_score = score
                best_match = audiobook

        if best_match:
            pair = BookPair(
                ebook_id=ebook.id,
                audiobook_id=best_match.id,
                status=PairStatus.AUTO_MATCHED,
                matched_at=utcnow(),
            )
            db.add(pair)
            matched_audiobook_ids.add(best_match.id)
            matched += 1
            await db.flush() # Flush to get the ID
            new_pair_ids.append(pair.id)
            # Issue #458. This path matters more than the manual one: a
            # truncated download is usually auto-matched, not hand-paired, and
            # the result reaches `synced` without anything having asked whether
            # the pairing made sense. Comparing two numbers already on the rows
            # costs nothing here, which is why the check is file-size based —
            # parsing each EPUB would add minutes to a whole-library scan.
            await record_pair_plausibility(db, pair, ebook, best_match)

    if auto_transcribe and new_pair_ids:
        from services.queue_manager import add_to_queue
        # Share this transaction: the pairs above are flushed, not committed,
        # so a second session would not see them (issue #199).
        await add_to_queue(new_pair_ids, db)

    return matched


