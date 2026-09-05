"""
Sync / position-conversion tests (issue #46).

The matcher is hand-duplicated between the Python server and the Android client, so
it's the highest-value thing to pin down. Two layers:

1. Pure-function golden vectors for `_normalize_for_search` and
   `_match_text_to_sync_points`, loaded from shared JSON fixtures in
   `tests/fixtures/sync_parity/` — the cross-platform parity contract (see the README
   there). Android JUnit tests should consume the same files.
2. A DB-backed round-trip proving the precise locator survives a write and a
   read through the canonical position endpoint.
"""

import json
import os
from dataclasses import dataclass

import pytest

from services import sync_matcher
from routers.sync import (
    _match_text_to_sync_points,
    _normalize_for_search,
)

_FIXTURE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "sync_parity")


def _load(name):
    with open(os.path.join(_FIXTURE_DIR, name), encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# Pure-function golden vectors (no DB) — Android parity contract
# ---------------------------------------------------------------------------

@dataclass
class _FakeSyncPoint:
    """Duck-typed stand-in — the matcher only reads these four attributes."""
    epub_chapter: int
    epub_sentence_index: int
    epub_text_preview: str | None
    # 0 means interpolated; the matcher nudges away from those. Fixture cases that
    # don't care omit the key and get a fully-confident point.
    confidence: float = 1.0


@pytest.mark.parametrize("case", _load("normalize_cases.json"), ids=lambda c: repr(c["input"]))
def test_normalize_for_search_golden(case):
    assert _normalize_for_search(case["input"]) == case["expected"]


@pytest.mark.parametrize("case", _load("match_cases.json"), ids=lambda c: c["name"])
def test_match_text_to_sync_points_golden(case):
    points = [
        _FakeSyncPoint(p["chapter"], p["sentence_index"], p["preview"], p.get("confidence", 1.0))
        for p in case["sync_points"]
    ]
    result = _match_text_to_sync_points(points, case["epub_text"], case["chapter_hint"])
    if case["expected_sentence_index"] is None:
        assert result is None
    else:
        assert result is not None
        assert result.epub_chapter == case["expected_chapter"]
        assert result.epub_sentence_index == case["expected_sentence_index"]


# ---------------------------------------------------------------------------
# audio -> epub point selection (issue #200) — Android parity contract
#
# The rule both platforms implement: the last point that has already started;
# and when the position precedes every point, the *first* point rather than a
# silent (0, 0). `services/sync_engine.audio_to_epub` is the Python half,
# `SyncMatcher.pointForAudioPosition` (via `BookSyncRepository.audioToEpubText`)
# the Kotlin one.
# ---------------------------------------------------------------------------

@dataclass
class _AudioPoint:
    """Duck-typed sync point — `audio_to_epub` reads only these three."""
    epub_chapter: int
    epub_sentence_index: int
    audio_start_ms: int


@pytest.mark.parametrize("case", _load("audio_to_epub_cases.json"), ids=lambda c: c["name"])
def test_audio_to_epub_point_selection_golden(case):
    from services.sync_engine import audio_to_epub

    raw = case["points"]
    points = sorted(
        (_AudioPoint(p["chapter"], p["sentence_index"], p["audio_start_ms"])
         for p in raw),
        key=lambda p: p.audio_start_ms,
    )
    result = audio_to_epub(points, case["audio_position_ms"])

    idx = case["expected_point_index"]
    if idx is None:
        # No point to name. Python's shape for that is the book's origin; what
        # matters for parity is that it is not claiming one of the points.
        assert result == (0, 0), case["why"]
    else:
        expected = raw[idx]
        assert result == (expected["chapter"], expected["sentence_index"]), case["why"]


# ---------------------------------------------------------------------------
# Fuzzy-pass internals (issue #41) — edge cases the golden vectors can't reach
# ---------------------------------------------------------------------------

def test_dice_similarity_identical_and_disjoint():
    a = sync_matcher.bigram_set("the quick brown fox")
    assert sync_matcher.dice_similarity(a, a) == 1.0
    assert sync_matcher.dice_similarity(a, sync_matcher.bigram_set("")) == 0.0


def test_fuzzy_find_rejects_short_needle():
    transcript = "the old clock in the hallway struck midnight and the house fell silent "
    assert sync_matcher.fuzzy_find_in_transcript(transcript, "too short") is None


def test_fuzzy_find_rejects_transcript_shorter_than_needle():
    needle = "the old clock in the hallway struck midnight and the house fell silent"
    assert sync_matcher.fuzzy_find_in_transcript("the old clock in the hall", needle) is None


def test_fuzzy_find_refines_to_the_exact_offset():
    needle = "the old clock in the hallway struck midnight and the house fell silent"
    # Pad by 7 chars — not a multiple of the coarse step, so only the step-1
    # refinement pass can land on the true offset with a perfect score.
    transcript = "abcdefg" + needle + " and then nobody stirred upstairs for a very long while "
    hit = sync_matcher.fuzzy_find_in_transcript(transcript, needle)
    assert hit is not None
    offset, score = hit
    assert offset == 7
    assert score == 1.0


def test_fuzzy_find_returns_none_below_threshold():
    needle = "the old clock in the hallway struck midnight and the house fell silent"
    transcript = "bananas and helicopters collided noisily above the purple accounting firm downtown "
    assert sync_matcher.fuzzy_find_in_transcript(transcript, needle) is None


def test_nudge_keeps_a_confident_match():
    points = [_FakeSyncPoint(2, 0, "a", 0.9), _FakeSyncPoint(2, 1, "b", 0.8)]
    assert sync_matcher.nudge_to_confident_point(points, 1) is points[1]


def test_nudge_ignores_confident_points_beyond_three_positions():
    points = [_FakeSyncPoint(2, i, "x", 0.0) for i in range(6)]
    points.append(_FakeSyncPoint(2, 6, "x", 0.9))
    # index 0 is interpolated; the only confident point is 6 positions away.
    assert sync_matcher.nudge_to_confident_point(points, 0) is points[0]


# ---------------------------------------------------------------------------
# Locator round-trip through the canonical endpoint
#
# The DB-backed `_convert_position` cases that used to live here went with the
# function (issue #102): the legacy bookmark/progress adapters were its only
# callers, and the canonical PUT does no server-side sync-map conversion —
# clients resolve their own sentence + audio anchor via `POST /match-text`,
# which the golden vectors above already pin.
# ---------------------------------------------------------------------------


async def test_position_roundtrip_persists_the_precise_locator_hint(
    client, make_user, auth_header, db
):
    """PUT then GET a position preserves the client-supplied precise locator
    (the field that was silently dropped before the Phase-2 fix, issue #40).
    It travels as a position hint now rather than the `epub_locator` column."""
    from tests.factories import make_book_pair

    pair = await make_book_pair(db)
    user = await make_user(username="reader")
    locator = "epubcfi(/6/4[chap01]!/4/2/2[para05]/1:12)"

    def _current_locator(payload):
        return next(
            (h["value"] for h in payload["hints"]
             if h["kind"] == "readium_locator" and h["current"]),
            None,
        )

    put = await client.put(
        f"/api/sync/position/pair/{pair.id}",
        headers=auth_header(user),
        json={"source": "ebook", "epub_chapter": 1, "epub_sentence_index": 3,
              "device_id": "pixel",
              "hint": {"kind": "readium_locator", "value": locator}},
    )
    assert put.status_code == 200, put.text
    assert _current_locator(put.json()) == locator

    got = await client.get(
        f"/api/sync/position/pair/{pair.id}", headers=auth_header(user))
    assert got.status_code == 200
    assert _current_locator(got.json()) == locator

    # A second update (existing record, update branch) changes the locator...
    new_locator = "epubcfi(/6/8[chap02]!/4/2/6[para20]/1:0)"
    upd = await client.put(
        f"/api/sync/position/pair/{pair.id}",
        headers=auth_header(user),
        json={"source": "ebook", "epub_chapter": 2, "epub_sentence_index": 20,
              "device_id": "pixel",
              "hint": {"kind": "readium_locator", "value": new_locator}},
    )
    assert upd.status_code == 200
    assert _current_locator(upd.json()) == new_locator

    # ...and an audiobook-source update that omits the hint must NOT wipe it.
    # Audio movement alone does not bump the anchor, so the hint stays current.
    aud = await client.put(
        f"/api/sync/position/pair/{pair.id}",
        headers=auth_header(user),
        json={"source": "audiobook", "audio_position_ms": 5000,
              "device_id": "pixel"},
    )
    assert aud.status_code == 200
    assert _current_locator(aud.json()) == new_locator
