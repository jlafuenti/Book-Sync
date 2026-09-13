"""
Shared metadata utility functions used by both the library router
and the ABS metadata service.

This module also owns the *one* definition of the book-metadata field list
(issue #257). It used to be retyped as a string literal at six sites in
`routers/library.py` plus the PATCH schema, and the copies had already drifted:
the ebook ingest never copied `narrators` even though `EBook.narrators` exists.
The gates that keep every copy site and the ORM models in step live in
`tests/test_media_column_parity.py`.
"""

import re
from typing import Optional


# ============================================================
# The shared metadata field list
# ============================================================
#
# Four groups, because the copy sites genuinely need different subsets — but a
# subset must be *derived*, never retyped, or it drifts again. The groups
# concatenate to `MEDIA_METADATA_FIELDS` in exactly the order the old literals
# used, so the ABS enrichment payloads keep their key order.

#: Identity metadata. Every ingest/rescan path handles these specially (a
#: user-cleared series must not be refilled), so they are rarely copied in bulk.
MEDIA_CORE_FIELDS: tuple[str, ...] = ("title", "author", "series", "series_index")

#: Free-text/descriptive metadata. Copied wholesale by every scan and rescan.
MEDIA_DESCRIPTIVE_FIELDS: tuple[str, ...] = (
    "description", "publisher", "publish_year", "language", "genres", "tags",
)

#: External identifiers and credits — filled once, then left alone.
MEDIA_IDENTIFIER_FIELDS: tuple[str, ...] = ("isbn", "asin", "narrators")

#: Booleans no tagger writes; only a user edit or Audiobookshelf sets them.
MEDIA_FLAG_FIELDS: tuple[str, ...] = ("is_explicit", "is_abridged")

#: Every metadata column `EBook` and `AudioBook` share. This is the list the
#: PATCH handlers, the ABS enrichment payloads and `MetadataUpdate` all use.
MEDIA_METADATA_FIELDS: tuple[str, ...] = (
    MEDIA_CORE_FIELDS + MEDIA_DESCRIPTIVE_FIELDS + MEDIA_IDENTIFIER_FIELDS
    + MEDIA_FLAG_FIELDS
)

#: What a file's own embedded tags can supply — everything but the two flags.
#: This is the whitelist `extract_metadata` merges `file_meta` into `meta` with.
MEDIA_EXTRACTED_FIELDS: tuple[str, ...] = (
    MEDIA_CORE_FIELDS + MEDIA_DESCRIPTIVE_FIELDS + MEDIA_IDENTIFIER_FIELDS
)

#: What a scan fills in on an existing row when the column is still NULL. The
#: core fields are excluded because the ingest decides those separately; the
#: flags because "not explicit" and "unset" are the same value here.
#:
#: `narrators` is in this list for **both** media types on purpose. The ebook
#: ingest used to omit it — the drift issue #257 found — even though the column
#: exists and `extract_metadata` puts the value in `meta` for ebooks too.
MEDIA_FILL_IF_NULL_FIELDS: tuple[str, ...] = (
    MEDIA_DESCRIPTIVE_FIELDS + MEDIA_IDENTIFIER_FIELDS
)


#: Name suffixes that can follow a comma without meaning 'Last, First'
#: (issue #514) -- e.g. 'Ann Axis, Jr.'. Matched case-insensitively against
#: the whole part after the comma, dot included, so both punctuated and
#: unpunctuated spellings are covered.
_AUTHOR_SUFFIXES = frozenset({
    "jr", "jr.", "sr", "sr.", "ii", "iii", "iv",
    "phd", "ph.d.", "md", "esq", "esq.",
})


def normalize_author(author: str) -> Optional[str]:
    """
    Normalize author name to 'First Last' format.
    Handles:
    - 'Last, First' -> 'First Last'
    - 'Jim Butcher' -> 'Jim Butcher' (no change)
    - 'Butcher, Jim' -> 'Jim Butcher'
    - 'Modesitt, L. E.' -> 'L. E. Modesitt' (first name plus a middle name/initial)
    - Extra whitespace is stripped.

    A comma is only treated as a 'Last, First' separator when the value looks
    like a single name written that way (issue #514): exactly one comma, a
    single-token last name before it, and a one- or two-token first name (an
    optional middle name/initial) after it that is not a suffix. Anything
    else that contains a comma is returned unchanged, with only whitespace
    collapsed -- never swapped or split -- because guessing wrong fabricates
    an author who doesn't exist:
    - a co-author list ('Ann Axis, Bob Bartleby' -- both sides are
      themselves multi-word full names)
    - a suffix ('Ann Axis, Jr.')
    - an author with a narrator tacked on ('Ann Axis, Narrator Name')
    - two or more commas, or names joined with '&' / ' and '
    Splitting and re-normalizing multi-author strings is out of scope for
    this fix; see issue #514.
    """
    if not author: return None
    author = author.strip()
    if not author: return None

    if "," not in author:
        return author

    if author.count(",") == 1 and "&" not in author and " and " not in author.lower():
        last, first = (part.strip() for part in author.split(",", 1))
        if first and last:
            is_suffix = first.lower() in _AUTHOR_SUFFIXES
            if not is_suffix and len(last.split()) == 1 and 1 <= len(first.split()) <= 2:
                return f"{first} {last}"
        else:
            return last or first

    return re.sub(r'\s+', ' ', author).strip()


def normalize_series(series: str) -> Optional[str]:
    """
    Normalize series name.
    - 'Dresden Files, The' -> 'The Dresden Files'
    - 'The Dresden Files' -> 'The Dresden Files' (preserved)
    - 'Dresden Files' -> 'Dresden Files' (no change, don't guess)
    - Trailing articles (A, An, The) are moved to the front.
    """
    if not series: return None
    clean = series.strip()
    if not clean: return None

    trailing_articles = [', The', ', A', ', An']
    for article in trailing_articles:
        if clean.endswith(article) or clean.lower().endswith(article.lower()):
            art = clean[-(len(article) - 2):].strip()
            base = clean[:-(len(article))].strip()
            return f"{art} {base}"

    return clean


def extract_series_and_index(text: str) -> tuple[Optional[str], Optional[float]]:
    """
    Given a string like 'The Cinder Spires #2' or 'The Cinder Spires, Book 2',
    returns the series name and the index.
    """
    if not text:
        return None, None
    text = text.strip()

    # Matches "Series Name #2", "Series Name # 2.5"
    match = re.search(r'#\s*(\d+(?:\.\d+)?)', text)
    if match:
        idx = float(match.group(1))
        series_name = text[:match.start()].strip()
        series_name = re.sub(r'[,:\-]\s*$', '', series_name).strip()
        return normalize_series(series_name), idx

    # Matches "Series Name, Book 2"
    match = re.search(r'(?:,\s*|\s+|-?\s*)Book\s+(\d+(?:\.\d+)?)', text, re.IGNORECASE)
    if match:
        idx = float(match.group(1))
        series_name = text[:match.start()].strip()
        series_name = re.sub(r'[,:\-]\s*$', '', series_name).strip()
        return normalize_series(series_name), idx

    return normalize_series(text), None
