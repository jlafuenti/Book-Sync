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
upper bound here, and a heavily illustrated EPUB still sits comfortably inside.
"""

from typing import Optional, Tuple

from sqlalchemy import select

from models.library_issue import LibraryCheckResult

# The plausible band, in bytes of EPUB per hour of audio. Two orders of
# magnitude wide on purpose; `test_pair_plausibility.py` guards that width so a
# later "let's tighten this" cannot quietly turn it into a nuisance.
MIN_BYTES_PER_HOUR = 10_000
MAX_BYTES_PER_HOUR = 2_000_000

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
) -> Tuple[bool, Optional[str]]:
    """Return `(ok, detail)`; `detail` is None whenever `ok` is True.

    `ok=True` means "no reason to complain", which deliberately includes "cannot
    judge". Absent data is not a finding: `duration_seconds` is not always
    populated when a pair is created (#127), and reporting every such pair as
    implausible would bury the real ones on the first scan.
    """
    # An abridgement genuinely has far less audio than the ebook has text, which
    # is exactly the shape this check looks for. #458 names it as the obvious
    # false-positive class and the flag already exists on the row.
    if is_abridged:
        return True, None

    if not ebook_file_size or not duration_seconds:
        return True, None

    hours = duration_seconds / 3600.0
    bytes_per_hour = ebook_file_size / hours

    if MIN_BYTES_PER_HOUR <= bytes_per_hour <= MAX_BYTES_PER_HOUR:
        return True, None

    size = _human_size(ebook_file_size)
    length = _human_duration(duration_seconds)
    if bytes_per_hour > MAX_BYTES_PER_HOUR:
        detail = (
            f"{size} ebook paired with only {length} of audio. The audio file "
            f"looks truncated, or the wrong file was matched — a sync map built "
            f"from this would send the reader to the wrong place."
        )
    else:
        detail = (
            f"{size} ebook paired with {length} of audio. That is far more audio "
            f"than this ebook's text accounts for, which usually means the wrong "
            f"audiobook was matched."
        )
    return False, detail


async def record_pair_plausibility(db, pair, ebook, audiobook) -> bool:
    """Run the check for `pair` and store the verdict. Returns `ok`.

    Takes the already-loaded `ebook` and `audiobook` rather than re-querying:
    both call sites have them in hand, and `auto_match` runs this inside a loop
    over a whole library, where a re-query per pair would reintroduce exactly the
    bulk cost that choosing file size over a text parse was meant to avoid.

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
    await db.flush()
    return ok
