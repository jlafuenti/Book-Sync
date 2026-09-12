"""
The restore ladder: how a stored position becomes an opening location.

This is the *decision* layer — which anchors are worth trying, in what order,
and with what inputs. Executing a step needs the EPUB itself (text search,
spine lookup, CFI/locator parsing), so that stays in the readers. The decision
is shared because that is where the two clients drifted apart: Android restored
only from a Readium locator, the web reader only from an epub.js CFI, and
neither had a path back to the portable anchors both of them write.

**The invariant this exists to enforce: a position holding any anchor always
produces at least one step.** Android used to treat "no locator" as "no
position", which opened the book at page one; its autosave then persisted
chapter 0 over a real position from another device. A precise hint being absent
or stale is normal — it is not the same as not knowing where the reader was.

Steps are returned best-first. A client tries each in order and takes the first
that yields a location; if every step fails it must report *unresolved* and
block saving, which is again distinct from "start of book".
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

# Below this, a Readium locator captured at a different audio position is
# assumed to still describe the same page. Mirrors the reader's own reuse
# threshold.
LOCATOR_REUSE_THRESHOLD_MS = 30_000

# Shortest preview worth searching for. Below this the text matches too much.
MIN_SEARCHABLE_PREVIEW = 10

# Whether a hint of this kind is only usable by the device that captured it.
# Readium locators encode href + progression as that device rendered the page;
# epub.js CFIs are derived from the EPUB DOM and so are portable.
HINT_DEVICE_SCOPED = {"readium_locator": True, "epubjs_cfi": False}


@dataclass
class RestoreStep:
    """One rung of the ladder, carrying everything the client needs."""
    kind: str                       # hint | text | chapter | percent | audio
    value: Optional[str] = None     # hint: the locator/CFI payload
    text: Optional[str] = None      # text: the preview to search for
    seed_chapter: Optional[int] = None   # text: where to start searching
    chapter: Optional[int] = None   # chapter: spine index
    percent: Optional[float] = None      # percent: 0-100
    audio_position_ms: Optional[int] = None


def _usable_hint(position: Dict[str, Any], device_id: str, hint_kind: str) -> Optional[dict]:
    """The caller's own current hint, or None.

    A hint qualifies only if it was captured at the live anchor revision — that
    is the whole staleness rule, and it is why hints are never deleted when the
    anchor moves. A stale hint just stops qualifying, and starts again when its
    device re-captures.
    """
    revision = position.get("anchor_revision")
    for hint in position.get("hints") or []:
        if hint.get("kind") != hint_kind:
            continue
        if hint.get("anchor_revision") != revision:
            continue
        if HINT_DEVICE_SCOPED.get(hint_kind, True) and hint.get("device_id") != device_id:
            continue
        if not hint.get("value"):
            continue
        # On an audiobook-source position the page only still matches if the
        # audio hasn't moved on since the hint was captured.
        hint_audio = hint.get("audio_position_ms")
        if position.get("source") == "audiobook" and hint_audio is not None:
            now_audio = position.get("audio_position_ms")
            if now_audio is not None and abs(now_audio - hint_audio) >= LOCATOR_REUSE_THRESHOLD_MS:
                continue
        return hint
    return None


def plan_restore(
    position: Optional[Dict[str, Any]],
    *,
    spine_count: int,
    device_id: str,
    hint_kind: str,
) -> List[RestoreStep]:
    """Ordered restore steps for [position], best first.

    An empty list means "genuinely no position, open at the start" and is only
    correct when the record holds no anchor at all.
    """
    if not position:
        return []

    steps: List[RestoreStep] = []

    hint = _usable_hint(position, device_id, hint_kind)
    if hint:
        steps.append(RestoreStep(kind="hint", value=hint["value"]))

    audio_ms = position.get("audio_position_ms")
    audio_step = (
        RestoreStep(kind="audio", audio_position_ms=audio_ms)
        if audio_ms is not None and audio_ms > 0
        else None
    )

    # When the audiobook is the live format, its position is the only coordinate
    # known to be current (issue #479). Only a reader save refreshes
    # epub_chapter / epub_text_preview / epub_progress_percent, so after a few
    # hours of listening they describe wherever the book was last *read* — and
    # they still resolve, so trying them first opens the book there and the save
    # that follows can write it back over the real position.
    #
    # `_usable_hint` already applies exactly this reasoning, dropping a locator
    # captured more than LOCATOR_REUSE_THRESHOLD_MS away from where the audio
    # now is. The ebook rungs are stale for the same reason; they simply carry
    # no capture-time stamp to measure it with, so the ordering carries the rule
    # instead.
    #
    # The hint still leads when it qualifies: it is freshness-checked and exact,
    # whereas the audio rung re-derives the page through the sync map and is
    # lossier. So this only changes which fallback is reached when the precise
    # answer is unavailable — the case where the page is least trustworthy.
    listening = position.get("source") == "audiobook" and audio_step is not None
    if listening:
        steps.append(audio_step)

    preview = (position.get("epub_text_preview") or "").strip()
    chapter = position.get("epub_chapter")
    chapter_navigable = chapter is not None and 0 <= chapter < spine_count

    if len(preview) >= MIN_SEARCHABLE_PREVIEW:
        steps.append(RestoreStep(
            kind="text",
            text=preview,
            seed_chapter=chapter if chapter_navigable else None,
        ))

    if chapter_navigable:
        steps.append(RestoreStep(kind="chapter", chapter=chapter))

    percent = position.get("epub_progress_percent")
    # 0% is indistinguishable from an unread book, so it is not an anchor.
    if percent is not None and percent > 0:
        steps.append(RestoreStep(kind="percent", percent=percent))

    if audio_step is not None and not listening:
        steps.append(audio_step)

    return steps


def has_anchor(position: Optional[Dict[str, Any]]) -> bool:
    """Whether [position] records a place in the book at all.

    Deliberately independent of `plan_restore`: a client uses this to tell
    "we don't know where you were" (block saving, offer retry) apart from
    "you hadn't started" (open at the beginning, saving is fine).
    """
    return bool(plan_restore(
        position or {}, spine_count=10**9, device_id="", hint_kind="",
    ))
