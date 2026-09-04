"""
Compile the operator's filename patterns into regexes (issue #354).

A pattern is written the way a folder layout reads —
`<Author>/<Series>/<Book Number> - <Title>` — and each `<Placeholder>` becomes a
named capture group. That is fine until a placeholder appears twice.
`<Author>/<Title>/<Title>` is an ordinary `Author/Title/Title.ext` library, and
it produced a regex defining the group `title` twice, so `re.compile` raised.
The scan swallowed the exception and logged a warning — once per file, for every
file, on every scan — and that library never got metadata from its paths.

**A repeated placeholder is a back-reference.** The second and later occurrences
must match the same text the first one did. That is what the layout means: the
folder and the file are named the same thing. A path where they differ is a
different layout and does not match, rather than matching on whichever
occurrence happened to be tried first.

`<Book Number>`/`<Series Index>` and `<Title>`/`<Book Title>` are aliases for one
group each, so two aliases in one pattern collide in exactly the same way and
are handled the same way.

Two entry points, deliberately different:

* `validate_patterns` raises. Used by the settings PUT, so a bad pattern is
  rejected once, at the moment somebody saves it, with a message naming what is
  wrong.
* `compile_pattern` never raises and warns at most once per pattern. Used by the
  scan, where a pattern stored before this existed — or written straight into
  the table — must be survivable rather than fatal.
"""

import logging
import re
from functools import lru_cache
from typing import Iterable, List, Optional

logger = logging.getLogger(__name__)

# placeholder -> (capture group, fragment). Several placeholders share a group:
# they are alternative spellings, not separate fields.
PLACEHOLDERS = {
    "<Author>": ("author", r"[^/]+?"),
    "<Series>": ("series", r"[^/]+?"),
    "<Book Number>": ("series_index", r"\d+(?:\.\d+)?"),
    "<Series Index>": ("series_index", r"\d+(?:\.\d+)?"),
    "<Title>": ("title", r"[^/]+?"),
    "<Book Title>": ("title", r"[^/]+?"),
}

# Anything in angle brackets. Splitting on a capturing group keeps the literal
# text between tags, which is then escaped.
_TAG_RE = re.compile(r"(<[^>]+>)")


class PatternError(ValueError):
    """A filename pattern that cannot be used. Carries a message for the operator."""


def unknown_placeholders(pattern: str) -> List[str]:
    """Angle-bracket tags in `pattern` that are not real placeholders.

    A misspelt tag is not a compile error — it is escaped and matched as
    literal text, so the pattern quietly matches nothing at all. Same silent
    shape as an unknown role floor (#359), so name it rather than let it sit.
    """
    return [tag for tag in _TAG_RE.findall(pattern) if tag not in PLACEHOLDERS]


def regex_from_pattern(pattern: str) -> re.Pattern:
    """Compile one user-facing pattern. Raises `PatternError` if it cannot.

    Repeated placeholders become back-references — see the module docstring.
    """
    parts = _TAG_RE.split(pattern)
    seen_groups = set()
    regex_parts = ["^"]

    for part in parts:
        if part in PLACEHOLDERS:
            group, fragment = PLACEHOLDERS[part]
            if group in seen_groups:
                # Second sighting of this field: match whatever the first one
                # captured. Naming the group twice is what used to raise.
                regex_parts.append(f"(?P={group})")
            else:
                seen_groups.add(group)
                regex_parts.append(f"(?P<{group}>{fragment})")
        else:
            regex_parts.append(re.escape(part))

    regex_parts.append("$")
    source = "".join(regex_parts)
    try:
        return re.compile(source)
    except re.error as exc:
        raise PatternError(f"pattern {pattern!r} is not a valid pattern: {exc}") from exc


def validate_patterns(patterns: Iterable[str]) -> None:
    """Raise `PatternError` if any pattern is unusable. Blank entries are ignored.

    Blank entries are not an error: the settings UI keeps the patterns in one
    textarea and splits on newlines, so a trailing newline arrives as "".

    Every bad pattern is reported, not just the first — an operator editing a
    list should not have to fix one, save, and discover the next.
    """
    problems = []
    for pattern in patterns:
        if not pattern or not pattern.strip():
            continue
        unknown = unknown_placeholders(pattern)
        if unknown:
            problems.append(
                f"{pattern!r}: unknown placeholder(s) {', '.join(unknown)}. "
                f"Available: {', '.join(sorted(PLACEHOLDERS))}"
            )
            continue
        try:
            regex_from_pattern(pattern)
        except PatternError as exc:
            problems.append(str(exc))

    if problems:
        raise PatternError("; ".join(problems))


@lru_cache(maxsize=256)
def compile_pattern(pattern: str) -> Optional[re.Pattern]:
    """Compile for the scan: `None` instead of an exception, one log line at most.

    Memoized on the pattern string, which is what keeps a bad pattern from
    warning once per file — the production symptom that opened #354. Call
    `compile_pattern.cache_clear()` if the stored patterns change in-process.
    """
    if not pattern or not pattern.strip():
        return None

    unknown = unknown_placeholders(pattern)
    if unknown:
        logger.warning(
            "[metadata] Pattern %r uses unknown placeholder(s) %s and will never "
            "match. Available: %s",
            pattern,
            ", ".join(unknown),
            ", ".join(sorted(PLACEHOLDERS)),
        )

    try:
        return regex_from_pattern(pattern)
    except Exception as exc:  # noqa: BLE001 - a stored pattern must never break a scan
        logger.warning("[metadata] Pattern %r could not be compiled and is skipped: %s",
                       pattern, exc)
        return None
