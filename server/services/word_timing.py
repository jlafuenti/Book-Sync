"""
Per-token audio start times for one ebook sentence (issue #835).

The worker gives the words it heard, each with a start time. The ebook sentence
is the text we show. They differ — a misheard word, a dropped word, punctuation,
curly quotes — so the ebook's tokens are *anchored* to the heard words wherever
the two agree, and the tokens in between are spread over the time between their
neighbouring anchors, in proportion to their length.

A token is a maximal run of non-whitespace under Unicode White_Space, which is
exactly `str.split()`. Android must tokenise the same way (`(?U)\\s` in a Java
regex); `tests/fixtures/sync_parity/word_tokens_cases.json` pins the rule.

Pure and synchronous; a sentence is a few dozen tokens.
"""

import difflib
from typing import List, Optional

MIN_ANCHORS = 2
MIN_ANCHORED_SHARE = 0.5


def _normalise(tokens: List[str], placeholder: str) -> List[str]:
    """Casefold and keep only letters and digits.

    A token with none left gets a placeholder unique to its position, so the
    two lists stay index-aligned and punctuation-only tokens never match.
    """
    out = []
    for i, token in enumerate(tokens):
        kept = "".join(ch for ch in token.casefold() if ch.isalnum())
        out.append(kept if kept else f"{placeholder}{i}")
    return out


def _spread(weights: List[int], lo: int, hi: int, t_lo: float, t_hi: float) -> List[float]:
    """Start times for tokens lo..hi-1, laid over [t_lo, t_hi].

    The first token starts at `t_lo`; each later one starts after the weight of
    the tokens before it. `weights` holds one entry per token of the sentence.
    """
    total = sum(weights[lo:hi])
    if total <= 0:
        return [t_lo] * (hi - lo)
    out, consumed = [], 0
    for k in range(lo, hi):
        out.append(t_lo + (t_hi - t_lo) * consumed / total)
        consumed += weights[k]
    return out


def word_starts_for(
    epub_text: str, words: List[dict], start_ms: int, end_ms: int
) -> Optional[List[int]]:
    """Return one start time (ms) per whitespace token of `epub_text`, or None.

    None means the heard words do not line up well enough to trust: fewer than
    two anchors, or fewer than half the tokens anchored. Otherwise the result
    has exactly `len(epub_text.split())` ints, non-decreasing, all within
    `[start_ms, end_ms]`.
    """
    tokens = epub_text.split()
    if not tokens or not words:
        return None

    a = _normalise(tokens, "\x00")
    b = _normalise([w["text"] for w in words], "\x01")
    blocks = difflib.SequenceMatcher(None, a, b, autojunk=False).get_matching_blocks()

    anchors = []  # (token index, time)
    for i, j, size in blocks:
        for k in range(size):
            anchors.append((i + k, words[j + k]["start_ms"]))
    if len(anchors) < MIN_ANCHORS or len(anchors) < MIN_ANCHORED_SHARE * len(tokens):
        return None

    n = len(tokens)
    weights = [len(t) + 1 for t in tokens]
    times: List[Optional[float]] = [None] * n
    for i, t in anchors:
        times[i] = t

    first_i, first_t = anchors[0]
    for k, t in zip(range(first_i), _spread(weights, 0, first_i, start_ms, first_t)):
        times[k] = t

    for (ia, ta), (ib, tb) in zip(anchors, anchors[1:]):
        if ib - ia > 1:
            for k, t in zip(range(ia + 1, ib), _spread(weights, ia, ib, ta, tb)[1:]):
                times[k] = t

    last_i, last_t = anchors[-1]
    if last_i < n - 1:
        spread = _spread(weights, last_i, n, last_t, max(end_ms, last_t))
        for k, t in zip(range(last_i + 1, n), spread[1:]):
            times[k] = t

    lo, hi = start_ms, max(end_ms, start_ms)
    result, floor = [], lo
    for t in times:
        value = min(max(int(round(t)), floor), hi)
        result.append(value)
        floor = value
    return result
