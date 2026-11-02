"""
Series entries: the helpers every series consumer goes through (issue #905).

A book can belong to several series, so a book's series is an *ordered list of
entries*, each ``{"name": str, "index": float | None}``. Entry 0 is the primary
series. The list lives in the child tables ``ebook_series`` / ``audiobook_series``
(``models.book``) and travels through the code as plain dicts, so extraction,
tag writing, Audiobookshelf enrichment and the matcher never touch an ORM row
until the very end (`set_series`).

Conventions, shared with the web and Android clients:

* The **canonical string** — used in tags, pair discrepancies and display — is
  ``Name #4; Other #2.5``: the index through ``:g`` (``4``, not ``4.0``), a bare
  name when there is no index, entries joined by ``"; "``. An empty list is
  ``""``. A ``;`` inside a name becomes ``,`` (Audiobookshelf has the same limit).
* Entries are deduplicated by case-folded name, first wins, and names go
  through `normalize_series`.
* ``series_cleared`` on the row records that a user emptied the list, so a
  rescan does not put it back. See `set_series`.

This module is deliberately free of database access: `set_series` mutates the
row's in-memory relationship and the caller's transaction flushes it.
"""

import math
import re
from typing import Any, Iterable, Mapping, Optional

from services.metadata_utils import (
    SERIES_LIST_KEY,
    extract_series_and_index,
    normalize_series,
)

_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")
_ABS_PIECE_RE = re.compile(r"^(?P<name>.+?)\s+#(?P<seq>\S+)$")


def _get(item: Any, key: str) -> Any:
    if isinstance(item, Mapping):
        return item.get(key)
    return getattr(item, key, None)


def _to_index(value: Any) -> Optional[float]:
    """A usable index from a number or a tag value, else None.

    Tag values come in as text (``"2"``, ``"2.5"``, sometimes ``"Book 2"``), so
    this takes the first number it can find rather than insisting on a clean
    float.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return None if math.isnan(number) or math.isinf(number) else number
    match = _NUMBER_RE.search(str(value))
    return float(match.group(0)) if match else None


def normalize_entries(entries: Optional[Iterable[Any]]) -> list[dict]:
    """Clean a list of entries (dicts, or objects with ``.name``/``.index``).

    Names go through `normalize_series`; blank names are dropped; the index is
    coerced to a float or None; entries repeating an earlier name (case-folded)
    are dropped, so the first one wins. Order is kept.
    """
    result: list[dict] = []
    seen: set[str] = set()
    for item in entries or []:
        raw = _get(item, "name")
        name = normalize_series(str(raw)) if raw is not None else None
        if not name:
            continue
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append({"name": name, "index": _to_index(_get(item, "index"))})
    return result


def format_series(entries: Optional[Iterable[Any]]) -> str:
    """The canonical string for `entries` (see the module docstring)."""
    pieces = []
    for entry in normalize_entries(entries):
        name = entry["name"].replace(";", ",")
        index = entry["index"]
        pieces.append(name if index is None else f"{name} #{index:g}")
    return "; ".join(pieces)


def parse_series(text: Optional[str]) -> list[dict]:
    """Entries from a free-text series value: ``"A #1; B, Book 2; C"``.

    Splits on ``;`` and reads each piece with `extract_series_and_index`, so
    ``Name #N`` and ``Name, Book N`` both give an index.
    """
    if not text:
        return []
    found = []
    for piece in str(text).split(";"):
        piece = piece.strip()
        if not piece:
            continue
        name, index = extract_series_and_index(piece)
        if name:
            found.append({"name": name, "index": index})
    return normalize_entries(found)


def split_tag_pair(names: Optional[str], parts: Optional[str]) -> list[dict]:
    """Entries from a ``series`` tag and its optional ``series-part`` tag.

    Audiobookshelf's rule: both are split on ``;``, and the indexes are paired
    with the names only when the two lists are the same length. Otherwise the
    names are kept and the indexes dropped, rather than guessing which index
    belongs to which series.
    """
    name_list = [n.strip() for n in str(names or "").split(";") if n.strip()]
    part_list = [p.strip() for p in str(parts or "").split(";") if p.strip()]
    paired = len(name_list) == len(part_list)
    found = []
    for position, piece in enumerate(name_list):
        # A name can carry its own ``#N`` / ``, Book N`` (older Tandem writes
        # and hand-tagged files do); that wins over the part tag.
        name, embedded = extract_series_and_index(piece)
        index = embedded
        if index is None and paired:
            index = _to_index(part_list[position])
        found.append({"name": name, "index": index})
    return normalize_entries(found)


def parse_abs_series_name(text: Optional[str]) -> list[dict]:
    """Entries from Audiobookshelf's minified ``seriesName``.

    The library listing carries one string: ABS's own ``", "``-join of
    ``Name #seq`` pieces. Split on ``", "`` and take a trailing `` #seq`` from
    each piece (a float when it parses). A comma *inside* a series name is
    indistinguishable from the separator — a limitation ABS shares, documented
    in ``docs/library-conventions.md`` and not chased.
    """
    if not text:
        return []
    found = []
    for piece in str(text).split(", "):
        piece = piece.strip()
        if not piece:
            continue
        match = _ABS_PIECE_RE.match(piece)
        if match:
            found.append({"name": match.group("name"), "index": _to_index(match.group("seq"))})
        else:
            found.append({"name": piece, "index": None})
    return normalize_entries(found)


def abs_series_entries(series: Optional[Iterable[Any]]) -> list[dict]:
    """Entries from an ABS ``series`` array: ``[{"name", "sequence"}]``."""
    return normalize_entries(
        {"name": _get(s, "name"), "index": _get(s, "sequence")} for s in series or []
    )


def primary(entries: Optional[Iterable[Any]]) -> tuple[Optional[str], Optional[float]]:
    """``(name, index)`` of the first entry, or ``(None, None)``."""
    normalized = normalize_entries(entries)
    if not normalized:
        return None, None
    return normalized[0]["name"], normalized[0]["index"]


def merge_missing(existing: Optional[Iterable[Any]], found: Optional[Iterable[Any]]) -> list[dict]:
    """`existing` plus the names in `found` it lacks (case-folded), appended.

    The rescan rule: add what the row does not have, keep the row's order and
    its edited indexes, never remove anything.
    """
    merged = normalize_entries(existing)
    have = {e["name"].casefold() for e in merged}
    for entry in normalize_entries(found):
        if entry["name"].casefold() not in have:
            merged.append(entry)
            have.add(entry["name"].casefold())
    return merged


def entries_of(obj: Any) -> list[dict]:
    """Normalised entries from an ORM row, a metadata dict, or a plain list."""
    if obj is None:
        return []
    if isinstance(obj, Mapping):
        return normalize_entries(obj.get(SERIES_LIST_KEY))
    if isinstance(obj, (list, tuple)):
        return normalize_entries(obj)
    return normalize_entries(getattr(obj, "series_list", None))


def set_series(row: Any, entries: Optional[Iterable[Any]], *, user_edit: bool) -> bool:
    """Make `row.series_list` equal `entries`; return whether anything changed.

    Rows are updated **in place by position** rather than deleted and
    re-inserted: the unit of work runs a flush's INSERTs before its DELETEs, so
    replacing position 0 with a fresh row would collide with the still-present
    old one on the unique ``(owner, position)`` index.

    ``series_cleared``: a non-empty result clears it. An empty result sets it
    only for a *user edit that actually removed something* — saving an unrelated
    edit on a book that never had a series must not mark it "cleared", or a
    later Audiobookshelf enrich would be blocked for no reason. A scan or enrich
    (``user_edit=False``) never sets it.
    """
    new = normalize_entries(entries)
    old = entries_of(row)
    changed = False

    if new != old:
        current = list(row.series_list)
        entry_cls = type(row).series_list.property.mapper.class_
        for position, entry in enumerate(new):
            if position < len(current):
                current[position].name = entry["name"]
                current[position].index = entry["index"]
                current[position].position = position
            else:
                row.series_list.append(
                    entry_cls(name=entry["name"], index=entry["index"], position=position)
                )
        for stale in current[len(new):]:
            row.series_list.remove(stale)
        changed = True

    cleared = bool(row.series_cleared)
    if new:
        cleared = False
    elif user_edit and old:
        cleared = True
    if cleared != bool(row.series_cleared):
        row.series_cleared = cleared
        changed = True
    return changed
