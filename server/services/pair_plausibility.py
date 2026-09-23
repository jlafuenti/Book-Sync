"""Is this pairing plausible at all? (issue #458)

A live library had a full-length novel EPUB matched to a 132-second audio file.
It scanned, matched, transcribed and reached `synced` without a warning, and the
sync map it produced was meaningless — 29 sentences of audio aligned against a
whole book. Nothing in the code was broken. Every stage did what it was told;
no stage asks whether the *pairing* makes sense, so a truncated or failed
download presents as a healthy synced pair. It surfaces to the reader as a book
that syncs to the wrong place, which is silent and hard to attribute.

**Why file size and not a word count.** Nothing stores how long a book's text
is. Getting one means unzipping and parsing the whole EPUB — blocking work, on a
path that today runs a few selects, and `auto_match` creates pairs in bulk, so a
parse per pair would add minutes to a large scan. `file_size` is already on the
row, so this is a pure comparison that can run on every pair however it was
made. The cost is precision: images and embedded fonts inflate an EPUB well
beyond its text.

That imprecision is why the band below is enormous. #458 asks for
order-of-magnitude mismatches — "two minutes of audio for a 200,000-word novel",
not "this narrator reads fast". A warning that fires on real books gets ignored,
and it takes the true findings with it.

For calibration: a normal novel lands near 50-150 KB of EPUB per hour of
narration. The reported pair is about 32 MB per hour, roughly 16x outside the
upper bound here. A heavily illustrated EPUB can still land outside it too —
issue #693 was exactly that: a picture-heavy book at ~24 MB/hour, 12x over the
upper bound, with a perfectly ordinary words-per-hour rate once its text was
actually counted. That is why `check_pair_plausibility` treats the two signals
as unequal rather than as peers: when a word count is available, its verdict
is final, and this byte check only ever gets the last word when no word count
could be taken at all.
"""

import asyncio
import logging
import os
from typing import Optional, Tuple

from sqlalchemy import select

from models.library_issue import LibraryCheckResult
from utils import utcnow

logger = logging.getLogger(__name__)

# The plausible band, in bytes of EPUB per hour of audio. Two orders of
# magnitude wide on purpose; `test_pair_plausibility.py` guards that width so a
# later "let's tighten this" cannot quietly turn it into a nuisance.
MIN_BYTES_PER_HOUR = 10_000
MAX_BYTES_PER_HOUR = 2_000_000

# The plausible band, in words of ebook text per hour of audio (issue #620).
# `bytes_per_hour` above can miss what this catches directly: two real
# production pairs (an abridgement at 87,398 words / 2.99 h = ~29.3k words/h,
# and a one-hour excerpt at 87,996 words / 1.09 h = ~80.8k words/h) had file
# sizes that happened to sit inside the byte band — the imprecision the
# module docstring above already names, images/fonts inflating an EPUB well
# beyond its text — while a direct word count places both far outside a real
# reading's pace. A real unabridged narration lands around 8,000-12,000
# words/hour (roughly 130-200 spoken words/minute); the band below keeps
# wide margin around that on both sides — 8,000/2 and 12,000*1.67 — the same
# "order-of-magnitude mismatches only" philosophy as the byte band, not a
# "this narrator reads fast" tripwire. `test_pair_plausibility.py` guards
# that it still comfortably contains a normal reading.
MIN_WORDS_PER_HOUR = 4_000
MAX_WORDS_PER_HOUR = 20_000

# The check domain, stored against `item_type="pair"` — the finding is the
# pairing, not either file on its own. Both other check types key on a single
# ebook or audiobook, so this is the first pair-scoped one.
CHECK_TYPE = "pair_plausibility"


def _human_duration(seconds: float) -> str:
    """`132` → `"2 minutes"`. The operator reads this in Troubleshoot Library."""
    if seconds < 90:
        return f"{int(round(seconds))} seconds"
    minutes = seconds / 60.0
    if minutes < 90:
        return f"{int(round(minutes))} minutes"
    return f"{minutes / 60.0:.1f} hours"


def _human_size(num_bytes: int) -> str:
    if num_bytes >= 1_000_000:
        return f"{num_bytes / 1_000_000:.1f} MB"
    return f"{int(round(num_bytes / 1000.0))} KB"


def check_pair_plausibility(
    ebook_file_size: Optional[int],
    duration_seconds: Optional[float],
    is_abridged: Optional[bool] = False,
    word_count: Optional[int] = None,
) -> Tuple[bool, Optional[str]]:
    """Return `(ok, detail)`; `detail` is None whenever `ok` is True.

    `ok=True` means "no reason to complain", which deliberately includes "cannot
    judge". Absent data is not a finding: `duration_seconds` is not always
    populated when a pair is created (#127), `word_count` is not always known
    (computing it means parsing the EPUB — see `estimate_word_count`) and
    reporting every such pair as implausible would bury the real ones on the
    first scan.

    Two signals, but not peers (issue #693, following #620). When `word_count`
    is available, the words-per-hour check runs first and its verdict is
    final either way: if it fails, that is the finding, full stop; if it
    passes, the pair is plausible, full stop — control never falls through to
    the bytes-per-hour check below. Bytes-per-hour is the imprecise signal
    (see the module docstring: images and embedded fonts inflate an EPUB well
    beyond its text), so it only ever gets to render a verdict on its own when
    no word count could be taken at all. Letting it override a real word-count
    measurement was the bug #693 fixed — a heavily illustrated book has a
    plausible words-per-hour rate and an implausible bytes-per-hour one at the
    same time, and the precise signal has to win.
    """
    # An abridgement genuinely has far less audio than the ebook has text, which
    # is exactly the shape this check looks for. #458 names it as the obvious
    # false-positive class and the flag already exists on the row.
    if is_abridged:
        return True, None

    if not duration_seconds:
        return True, None

    hours = duration_seconds / 3600.0
    length = _human_duration(duration_seconds)

    if word_count:
        words_per_hour = word_count / hours
        if not (MIN_WORDS_PER_HOUR <= words_per_hour <= MAX_WORDS_PER_HOUR):
            words = f"{word_count:,}"
            if words_per_hour > MAX_WORDS_PER_HOUR:
                detail = (
                    f"{words} words of ebook text paired with only {length} of "
                    f"audio (~{words_per_hour:,.0f} words/hour; a real unabridged "
                    f"reading lands around 8,000-12,000). The audio file looks "
                    f"truncated, abridged or excerpted, or the wrong file was "
                    f"matched — a sync map built from this would send the reader "
                    f"to the wrong place."
                )
            else:
                detail = (
                    f"{words} words of ebook text paired with {length} of audio "
                    f"(~{words_per_hour:,.0f} words/hour; a real unabridged "
                    f"reading lands around 8,000-12,000). That is far more audio "
                    f"than this ebook's text accounts for, which usually means "
                    f"the wrong audiobook was matched."
                )
            return False, detail
        # The precise signal passed. Its verdict is final — do not second-guess
        # a real word-count measurement with the imprecise byte heuristic
        # below (issue #693).
        return True, None

    if not ebook_file_size:
        return True, None

    bytes_per_hour = ebook_file_size / hours

    if MIN_BYTES_PER_HOUR <= bytes_per_hour <= MAX_BYTES_PER_HOUR:
        return True, None

    # Only reached when no word count was available at all, so the detail
    # says so — an operator reading Troubleshoot Library needs to know this
    # verdict came from the imprecise signal, not a confirmed word count.
    size = _human_size(ebook_file_size)
    if bytes_per_hour > MAX_BYTES_PER_HOUR:
        detail = (
            f"{size} ebook paired with only {length} of audio. No word count "
            f"could be taken from the ebook, so this is based on file size "
            f"alone, which is less precise. The audio file looks truncated, "
            f"or the wrong file was matched — a sync map built from this "
            f"would send the reader to the wrong place."
        )
    else:
        detail = (
            f"{size} ebook paired with {length} of audio. No word count "
            f"could be taken from the ebook, so this is based on file size "
            f"alone, which is less precise. That is far more audio than "
            f"this ebook's text accounts for, which usually means the wrong "
            f"audiobook was matched."
        )
    return False, detail


async def estimate_word_count(path: Optional[str]) -> Optional[int]:
    """Best-effort ebook word count for the words-per-hour check (issue #620).

    Returns `None` — never `0`, which would read as "this book has no words"
    and could trip the word-rate floor — on anything that stops the parse: no
    path, a missing file, an unsupported format, a corrupt archive. Matches
    this module's "absent data is not a finding" rule: cannot judge is not
    the same as implausible.

    Deliberately reuses `epub_parser.extract_book_text`, which itself stops
    short of sentence tokenization for the same reason: this only needs a
    word count, not sentence positions, and NLTK over a whole novel is the
    expensive half of parsing. Still real file I/O and CPU work, so this
    always runs via `asyncio.to_thread` — never awaited directly on the event
    loop (every other blocking pass in this codebase follows the same rule).
    """
    if not path or not os.path.exists(path):
        return None
    # Imported at call time: `epub_parser` pulls in ebooklib/nltk, and this
    # module is otherwise light enough to import unconditionally (mirrors
    # `sync_map_audit._audit_one`'s same lazy import for the same reason).
    from services import epub_parser
    try:
        text = await asyncio.to_thread(epub_parser.extract_book_text, path)
    except Exception as e:
        logger.warning("Could not compute word count for '%s': %s", path, e)
        return None
    return len(text.split())


async def record_pair_plausibility(db, pair, ebook, audiobook, word_count=None) -> bool:
    """Run the check for `pair` and store the verdict. Returns `ok`.

    Takes the already-loaded `ebook` and `audiobook` rather than re-querying:
    both call sites have them in hand, and `auto_match` runs this inside a loop
    over a whole library, where a re-query per pair would reintroduce exactly the
    bulk cost that choosing file size over a text parse was meant to avoid.

    `word_count` is the caller's job to compute (`estimate_word_count`, issue
    #620) and pass in, not this function's — both call sites already decide
    whether a parse is worth it for their situation (`auto_match_books` only
    for the winning candidate, and only once `duration_seconds` makes the
    check worth running at all).

    **Never commits.** `create_pair` and `auto_match` both run inside `get_db`'s
    one request-long transaction, and `auto_match` shares it deliberately because
    its pairs are flushed rather than committed (issue #199); committing here
    would leave half a bulk match permanently written if a later pair raised.

    Passing pairs are recorded too, not just failures. The row is unique on
    (item_type, item_id, check_type), so re-running after a file is replaced
    flips the existing row — which is what makes a fixed pair's warning actually
    disappear from Troubleshoot Library, rather than lingering as a stale
    `ok=False` row describing a problem that no longer exists.
    """
    ok, detail = check_pair_plausibility(
        ebook_file_size=getattr(ebook, "file_size", None),
        duration_seconds=getattr(audiobook, "duration_seconds", None),
        is_abridged=getattr(audiobook, "is_abridged", False),
        word_count=word_count,
    )

    row = (await db.execute(select(LibraryCheckResult).where(
        LibraryCheckResult.item_type == "pair",
        LibraryCheckResult.item_id == pair.id,
        LibraryCheckResult.check_type == CHECK_TYPE,
    ))).scalar_one_or_none()
    if row is None:
        row = LibraryCheckResult(
            item_type="pair", item_id=pair.id, check_type=CHECK_TYPE,
        )
        db.add(row)

    # file_path/file_size/file_mtime stay unset: they exist on this table to let
    # a rescan skip unchanged *files*, and this check is over a pair of them.
    row.ok = ok
    row.detail = detail
    # The column default only fires on insert. Library verify rewrites existing
    # rows (issue #693), so without this a re-checked row kept its creation time
    # and read as though the re-check never ran (issue #699).
    row.checked_at = utcnow()
    await db.flush()
    return ok
