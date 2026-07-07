"""
Tests for services.sync_engine (issue #46, Phase 3).

epub_to_audio / audio_to_epub are pure functions over duck-typed sync points.
save_sync_map is exercised against the SQLite test DB.
"""

from dataclasses import dataclass

import pytest
from sqlalchemy import select

from config import settings
from models.sync_map import SyncMap, SyncPoint
from services.alignment import AlignedPoint
from services.sync_engine import audio_to_epub, epub_to_audio, save_sync_map


@dataclass
class _P:
    """Duck-typed sync point — the converters only read these fields."""
    epub_chapter: int
    epub_sentence_index: int
    audio_start_ms: int


_POINTS = [
    _P(0, 0, 20000),
    _P(0, 1, 25000),
    _P(1, 0, 40000),
]


# ---------------------------------------------------------------------------
# epub_to_audio
# ---------------------------------------------------------------------------

def test_epub_to_audio_exact_match_applies_default_rewind():
    # default_rewind_seconds is 10 -> 10_000 ms
    assert settings.default_rewind_seconds == 10
    assert epub_to_audio(_POINTS, 0, 1) == 25000 - 10000


def test_epub_to_audio_custom_rewind_zero():
    assert epub_to_audio(_POINTS, 1, 0, rewind_seconds=0) == 40000


def test_epub_to_audio_closest_preceding_when_no_exact():
    # (1, 5) has no exact point -> best preceding is (1, 0) @ 40000
    assert epub_to_audio(_POINTS, 1, 5, rewind_seconds=0) == 40000


def test_epub_to_audio_clamps_at_zero_on_rewind_underflow():
    pts = [_P(0, 0, 5000)]
    assert epub_to_audio(pts, 0, 0) == 0  # 5000 - 10000 -> clamped


def test_epub_to_audio_returns_zero_when_nothing_precedes():
    pts = [_P(5, 0, 100000)]
    assert epub_to_audio(pts, 0, 0, rewind_seconds=0) == 0


# ---------------------------------------------------------------------------
# audio_to_epub
# ---------------------------------------------------------------------------

def test_audio_to_epub_finds_covering_point():
    pts = [_P(0, 0, 0), _P(0, 1, 5000), _P(1, 0, 10000)]
    assert audio_to_epub(pts, 7000) == (0, 1)
    assert audio_to_epub(pts, 12000) == (1, 0)


def test_audio_to_epub_before_first_point_returns_origin():
    pts = [_P(0, 0, 3000)]
    assert audio_to_epub(pts, 1000) == (0, 0)


# ---------------------------------------------------------------------------
# save_sync_map (DB)
# ---------------------------------------------------------------------------

def _aligned(ch, si, ms):
    return AlignedPoint(
        epub_chapter=ch, epub_sentence_index=si,
        epub_text_preview=f"preview {ch}.{si}",
        audio_start_ms=ms, audio_end_ms=ms + 3000, confidence=1.0,
    )


async def test_save_sync_map_creates_map_and_points(db):
    points = [_aligned(0, 0, 0), _aligned(0, 1, 3000), _aligned(1, 0, 6000)]
    sm = await save_sync_map(db, book_pair_id=1, aligned_points=points)

    assert sm.version == 1
    assert sm.total_sentences == 3
    assert sm.total_chapters == 2  # chapters {0, 1}

    stored = (await db.execute(
        select(SyncPoint).where(SyncPoint.sync_map_id == sm.id)
    )).scalars().all()
    assert len(stored) == 3


async def test_save_sync_map_replaces_and_bumps_version(db):
    await save_sync_map(db, 1, [_aligned(0, 0, 0), _aligned(0, 1, 3000)])
    sm2 = await save_sync_map(db, 1, [_aligned(0, 0, 0)])

    assert sm2.version == 2
    assert sm2.total_sentences == 1

    maps = (await db.execute(
        select(SyncMap).where(SyncMap.book_pair_id == 1)
    )).scalars().all()
    assert len(maps) == 1  # old map replaced, not duplicated
