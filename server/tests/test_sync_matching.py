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
from models.sync_map import SyncMap, SyncPoint
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
    """Duck-typed stand-in — the matcher only reads these three attributes."""
    epub_chapter: int
    epub_sentence_index: int
    epub_text_preview: str | None


@pytest.mark.parametrize("case", _load("normalize_cases.json"), ids=lambda c: repr(c["input"]))
def test_normalize_for_search_golden(case):
    assert _normalize_for_search(case["input"]) == case["expected"]


@pytest.mark.parametrize("case", _load("match_cases.json"), ids=lambda c: c["name"])
def test_match_text_to_sync_points_golden(case):
    points = [
        _FakeSyncPoint(p["chapter"], p["sentence_index"], p["preview"])
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
# _convert_position — DB-backed
# ---------------------------------------------------------------------------

async def _seed_sync_map(db, book_pair_id=1):
    """
    Build a SyncMap with ordered sync points. Note ch=1,si=1 has a NULL preview
    to exercise the nearest-preview fallback.
    """
    sm = SyncMap(book_pair_id=book_pair_id, version=1, total_sentences=5, total_chapters=2)
    db.add(sm)
    await db.flush()
    pts = [
        (0, 0, 0, "chapter one opening line"),
        (0, 1, 5000, "second sentence here"),
        (1, 0, 10000, "chapter two begins now"),
        (1, 1, 15000, None),
        (1, 2, 20000, "later sentence in two"),
    ]
    for ch, si, ms, preview in pts:
        db.add(SyncPoint(
            sync_map_id=sm.id, epub_chapter=ch, epub_sentence_index=si,
            epub_text_preview=preview, audio_start_ms=ms, audio_end_ms=ms + 3000,
            confidence=1.0,
        ))
    await db.commit()
    return sm


async def test_convert_ebook_exact_match(db):
    await _seed_sync_map(db)
    ch, si, ms, preview = await _convert_position(
        db, 1, BookmarkSource.EBOOK, epub_chapter=1, epub_sentence_index=0, audio_position_ms=None,
    )
    assert (ch, si, ms) == (1, 0, 10000)
    assert preview == "chapter two begins now"


async def test_convert_ebook_no_exact_uses_closest_preceding(db):
    await _seed_sync_map(db)
    # (1, 5) has no exact point — closest preceding is (1, 2) @ 20000ms
    ch, si, ms, preview = await _convert_position(
        db, 1, BookmarkSource.EBOOK, epub_chapter=1, epub_sentence_index=5, audio_position_ms=None,
    )
    assert ms == 20000
    assert preview == "later sentence in two"


async def test_convert_ebook_null_preview_falls_back_to_nearest(db):
    await _seed_sync_map(db)
    # Exact match (1,1) has audio 15000 but a NULL preview -> nearest preview in
    # chapter 1 (equidistant (1,0) and (1,2); min() picks the earlier (1,0)).
    ch, si, ms, preview = await _convert_position(
        db, 1, BookmarkSource.EBOOK, epub_chapter=1, epub_sentence_index=1, audio_position_ms=None,
    )
    assert ms == 15000
    assert preview == "chapter two begins now"


async def test_convert_audiobook_finds_covering_point(db):
    await _seed_sync_map(db)
    # 12000ms -> last point with audio_start_ms <= 12000 is (1,0) @ 10000
    ch, si, ms, preview = await _convert_position(
        db, 1, BookmarkSource.AUDIOBOOK, epub_chapter=None, epub_sentence_index=None, audio_position_ms=12000,
    )
    assert (ch, si) == (1, 0)
    assert ms == 12000  # audio position echoed back unchanged
    assert preview == "chapter two begins now"


async def test_convert_audiobook_before_second_point(db):
    await _seed_sync_map(db)
    ch, si, ms, preview = await _convert_position(
        db, 1, BookmarkSource.AUDIOBOOK, epub_chapter=None, epub_sentence_index=None, audio_position_ms=3000,
    )
    assert (ch, si) == (0, 0)
    assert preview == "chapter one opening line"


async def test_convert_no_sync_map_returns_input_unchanged(db):
    await _seed_sync_map(db, book_pair_id=1)
    # pair 999 has no map -> inputs returned as-is, no preview
    ch, si, ms, preview = await _convert_position(
        db, 999, BookmarkSource.EBOOK, epub_chapter=3, epub_sentence_index=7, audio_position_ms=None,
    )
    assert (ch, si, ms, preview) == (3, 7, None, None)


async def test_bookmark_roundtrip_persists_epub_locator(client, make_user, auth_header, db):
    """PUT then GET a bookmark preserves the client-supplied epub_locator (the
    field that was silently dropped before the Phase-2 fix)."""
    from models.book import EBook, AudioBook, BookPair

    eb = EBook(title="E", filename="e.epub", file_path="/x/e.epub")
    ab = AudioBook(title="A", filename="a.m4b", file_path="/x/a.m4b")
    db.add_all([eb, ab])
    await db.flush()
    pair = BookPair(ebook_id=eb.id, audiobook_id=ab.id)
    db.add(pair)
    await db.commit()
    await db.refresh(pair)

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


async def test_convert_empty_sync_map_returns_input_unchanged(db):
    # A SyncMap with zero points is treated the same as no map.
    sm = SyncMap(book_pair_id=2, version=1, total_sentences=0, total_chapters=0)
    db.add(sm)
    await db.commit()
    ch, si, ms, preview = await _convert_position(
        db, 2, BookmarkSource.AUDIOBOOK, epub_chapter=None, epub_sentence_index=None, audio_position_ms=8000,
    )
    assert (ch, si, ms, preview) == (None, None, 8000, None)
