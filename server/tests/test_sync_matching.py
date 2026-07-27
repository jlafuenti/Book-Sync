"""
Sync / position-conversion tests (issue #46).

The matcher is hand-duplicated between the Python server and the Android client, so
it's the highest-value thing to pin down. Two layers:

1. Pure-function golden vectors for `_normalize_for_search` and
   `_match_text_to_sync_points`, loaded from shared JSON fixtures in
   `tests/fixtures/sync_parity/` — the cross-platform parity contract (see the README
   there). Android JUnit tests should consume the same files.
2. DB-backed `_convert_position` cases exercising the SyncMap lookup against real ORM
   rows in the SQLite test DB.
"""

import json
import os
from dataclasses import dataclass

import pytest

from models.bookmark import BookmarkSource
from services import sync_matcher
from models.sync_map import SyncMap
from tests.factories import make_sync_map
from routers.sync import (
    _convert_position,
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
# _convert_position — DB-backed
# ---------------------------------------------------------------------------



async def test_convert_ebook_exact_match(db):
    await make_sync_map(db)
    ch, si, ms, preview = await _convert_position(
        db, 1, BookmarkSource.EBOOK, epub_chapter=1, epub_sentence_index=0, audio_position_ms=None,
    )
    assert (ch, si, ms) == (1, 0, 10000)
    assert preview == "chapter two begins now"


async def test_convert_ebook_no_exact_uses_closest_preceding(db):
    await make_sync_map(db)
    # (1, 5) has no exact point — closest preceding is (1, 2) @ 20000ms
    ch, si, ms, preview = await _convert_position(
        db, 1, BookmarkSource.EBOOK, epub_chapter=1, epub_sentence_index=5, audio_position_ms=None,
    )
    assert ms == 20000
    assert preview == "later sentence in two"


async def test_convert_ebook_null_preview_falls_back_to_nearest(db):
    await make_sync_map(db)
    # Exact match (1,1) has audio 15000 but a NULL preview -> nearest preview in
    # chapter 1 (equidistant (1,0) and (1,2); min() picks the earlier (1,0)).
    ch, si, ms, preview = await _convert_position(
        db, 1, BookmarkSource.EBOOK, epub_chapter=1, epub_sentence_index=1, audio_position_ms=None,
    )
    assert ms == 15000
    assert preview == "chapter two begins now"


async def test_convert_audiobook_finds_covering_point(db):
    await make_sync_map(db)
    # 12000ms -> last point with audio_start_ms <= 12000 is (1,0) @ 10000
    ch, si, ms, preview = await _convert_position(
        db, 1, BookmarkSource.AUDIOBOOK, epub_chapter=None, epub_sentence_index=None, audio_position_ms=12000,
    )
    assert (ch, si) == (1, 0)
    assert ms == 12000  # audio position echoed back unchanged
    assert preview == "chapter two begins now"


async def test_convert_audiobook_before_second_point(db):
    await make_sync_map(db)
    ch, si, ms, preview = await _convert_position(
        db, 1, BookmarkSource.AUDIOBOOK, epub_chapter=None, epub_sentence_index=None, audio_position_ms=3000,
    )
    assert (ch, si) == (0, 0)
    assert preview == "chapter one opening line"


async def test_convert_no_sync_map_returns_input_unchanged(db):
    await make_sync_map(db, book_pair_id=1)
    # pair 999 has no map -> inputs returned as-is, no preview
    ch, si, ms, preview = await _convert_position(
        db, 999, BookmarkSource.EBOOK, epub_chapter=3, epub_sentence_index=7, audio_position_ms=None,
    )
    assert (ch, si, ms, preview) == (3, 7, None, None)


async def test_bookmark_roundtrip_persists_epub_locator(client, make_user, auth_header, db):
    """PUT then GET a bookmark preserves the client-supplied epub_locator (the
    field that was silently dropped before the Phase-2 fix)."""
    from tests.factories import make_book_pair

    pair = await make_book_pair(db)
    user = await make_user(username="reader")
    locator = "epubcfi(/6/4[chap01]!/4/2/2[para05]/1:12)"
    put = await client.put(
        f"/api/sync/bookmark/{pair.id}",
        headers=auth_header(user),
        json={"source": "ebook", "epub_chapter": 1, "epub_sentence_index": 3,
              "epub_locator": locator},
    )
    assert put.status_code == 200, put.text
    assert put.json()["epub_locator"] == locator

    got = await client.get(f"/api/sync/bookmark/{pair.id}", headers=auth_header(user))
    assert got.status_code == 200
    assert got.json()["epub_locator"] == locator

    # A second update (existing bookmark, update branch) changes the locator...
    new_locator = "epubcfi(/6/8[chap02]!/4/2/6[para20]/1:0)"
    upd = await client.put(
        f"/api/sync/bookmark/{pair.id}",
        headers=auth_header(user),
        json={"source": "ebook", "epub_chapter": 2, "epub_sentence_index": 20,
              "epub_locator": new_locator},
    )
    assert upd.status_code == 200
    assert upd.json()["epub_locator"] == new_locator

    # ...and an audiobook-source update that omits the locator must NOT wipe it.
    aud = await client.put(
        f"/api/sync/bookmark/{pair.id}",
        headers=auth_header(user),
        json={"source": "audiobook", "audio_position_ms": 5000},
    )
    assert aud.status_code == 200
    assert aud.json()["epub_locator"] == new_locator


async def test_convert_empty_sync_map_returns_input_unchanged(db):
    # A SyncMap with zero points is treated the same as no map.
    sm = SyncMap(book_pair_id=2, version=1, total_sentences=0, total_chapters=0)
    db.add(sm)
    await db.commit()
    ch, si, ms, preview = await _convert_position(
        db, 2, BookmarkSource.AUDIOBOOK, epub_chapter=None, epub_sentence_index=None, audio_position_ms=8000,
    )
    assert (ch, si, ms, preview) == (None, None, 8000, None)
