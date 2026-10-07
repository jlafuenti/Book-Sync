"""
Compact storage for per-word transcript timing (issue #835).

The worker returns each sentence with its words (`text`, `start_ms`, `end_ms`,
`probability`). `AudioTranscript.sentences_json` keeps only the sentence-level
fields — its shape is read by many callers — so the words live in a sibling
column, `words_json`: a JSON array parallel to the sentence list, one entry per
sentence, each `[[text, start_ms, end_ms], ...]`. Probability is dropped; nothing
downstream uses it and it would double the size.

Both functions are pure and synchronous. A transcript is at most a few hundred
thousand words, so the JSON work is milliseconds; callers on the event loop that
handle very long books may still wrap it in `asyncio.to_thread`.
"""

import json
import logging
from typing import List, Optional

logger = logging.getLogger(__name__)


def encode_words(sentences) -> Optional[str]:
    """Serialize every sentence's words, or None when no sentence has any.

    The result has exactly one entry per sentence — an empty list for a sentence
    without words — so position in the array identifies the sentence.
    """
    if not any(s.words for s in sentences):
        return None
    return json.dumps([
        [[w["text"], w["start_ms"], w["end_ms"]] for w in s.words]
        for s in sentences
    ])


def attach_words(sentences, words_json: Optional[str]) -> None:
    """Set each sentence's `words` from a `words_json` value, in place.

    Words are attached as `{"text", "start_ms", "end_ms"}`. A missing value (a
    transcript made before word timing), invalid JSON, a wrong shape or a length
    that does not match the sentence list attaches nothing and logs one warning:
    word timing is an extra, and a damaged one must never fail an alignment.
    """
    if not words_json:
        return
    try:
        parsed = json.loads(words_json)
        if not isinstance(parsed, list) or len(parsed) != len(sentences):
            raise ValueError(
                f"{len(parsed) if isinstance(parsed, list) else type(parsed).__name__} "
                f"entries for {len(sentences)} sentences"
            )
        decoded: List[list] = []
        for entry in parsed:
            decoded.append([
                {"text": text, "start_ms": int(start), "end_ms": int(end)}
                for text, start, end in entry
            ])
    except (ValueError, TypeError) as exc:
        logger.warning("Ignoring stored word timing: %s", exc)
        return
    for sentence, words in zip(sentences, decoded):
        sentence.words = words
