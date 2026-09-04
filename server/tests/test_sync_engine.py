"""
Tests for services.sync_engine (issue #46, Phase 3).

epub_to_audio / audio_to_epub are pure functions over duck-typed sync points.
save_sync_map is exercised against the SQLite test DB.
"""

from dataclasses import dataclass

import pytest
from sqlalchemy import select

from config import settings
from models.book import AudioBook, BookPair, EBook
from models.sync_map import SyncMap, SyncPoint
from services.alignment import AlignedPoint
from services.sync_engine import audio_to_epub, epub_to_audio, save_sync_map
from tests.factories import make_book_pair


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
    # default_rewind_seconds is 5 -> 5_000 ms. Pinned so the server can't drift
    # away from the clients' RESUME_REWIND (issue #42) unnoticed — see
    # docs/position-sync-contract.md § Playback offsets.
    assert settings.default_rewind_seconds == 5
    assert epub_to_audio(_POINTS, 0, 1) == 25000 - 5000


def test_epub_to_audio_custom_rewind_zero():
    assert epub_to_audio(_POINTS, 1, 0, rewind_seconds=0) == 40000


def test_epub_to_audio_closest_preceding_when_no_exact():
    # (1, 5) has no exact point -> best preceding is (1, 0) @ 40000
    assert epub_to_audio(_POINTS, 1, 5, rewind_seconds=0) == 40000


def test_epub_to_audio_clamps_at_zero_on_rewind_underflow():
    pts = [_P(0, 0, 2000)]
    assert epub_to_audio(pts, 0, 0) == 0  # 2000 - 5000 -> clamped


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


def test_audio_to_epub_before_first_point_returns_the_first_point():
    """Issue #200. Not the book's origin — the first point the map actually
    has. Where the map starts at (0, 0) the two coincide, which is exactly how
    the old fallback stayed invisible."""
    assert audio_to_epub([_P(0, 0, 3000)], 1000) == (0, 0)
    assert audio_to_epub([_P(2, 4, 600_000), _P(2, 5, 605_000)], 1000) == (2, 4)


def test_audio_to_epub_with_no_points_has_nothing_to_name():
    assert audio_to_epub([], 1000) == (0, 0)


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
    pair = await make_book_pair(db)
    points = [_aligned(0, 0, 0), _aligned(0, 1, 3000), _aligned(1, 0, 6000)]
    sm = await save_sync_map(db, book_pair_id=pair.id, aligned_points=points)

    assert sm.version == 1
    assert sm.total_sentences == 3
    assert sm.total_chapters == 2  # chapters {0, 1}

    stored = (await db.execute(
        select(SyncPoint).where(SyncPoint.sync_map_id == sm.id)
    )).scalars().all()
    assert len(stored) == 3


async def test_save_sync_map_stamps_the_ebook_file_hash(db, tmp_path):
    """Provenance for the drift audit (issue #295).

    Every map is written through this one function, so stamping here is what
    makes "was this map built from the file now on disk?" answerable for maps
    produced by transcription, by re-alignment, and by the Convert flow alike.
    """
    from services.file_hash import hash_file
    from tests.factories import write_epub

    path = write_epub(
        tmp_path / "e.epub",
        [("c1.xhtml", "<html><body><p>The harbour lay still.</p></body></html>")],
    )
    eb = EBook(title="E", filename="e.epub", file_path=path)
    ab = AudioBook(title="A", filename="a.m4b", file_path="/x/a.m4b")
    db.add_all([eb, ab])
    await db.flush()
    pair = BookPair(ebook_id=eb.id, audiobook_id=ab.id)
    db.add(pair)
    await db.commit()

    sm = await save_sync_map(db, pair.id, [_aligned(0, 0, 0)])

    assert sm.epub_file_hash == hash_file(path)


async def test_save_sync_map_leaves_the_hash_null_when_the_file_is_gone(db):
    """A missing or unreadable ebook must not fail the save — the map is still
    the best thing we have; it just has no provenance to record."""
    eb = EBook(title="E", filename="e.epub", file_path="/nope/missing.epub")
    ab = AudioBook(title="A", filename="a.m4b", file_path="/x/a.m4b")
    db.add_all([eb, ab])
    await db.flush()
    pair = BookPair(ebook_id=eb.id, audiobook_id=ab.id)
    db.add(pair)
    await db.commit()

    sm = await save_sync_map(db, pair.id, [_aligned(0, 0, 0)])

    assert sm.epub_file_hash is None


async def test_save_sync_map_leaves_the_hash_null_when_the_file_wont_open(db, tmp_path):
    """A path that exists but can't be read — a directory, a permission-denied
    file — must not fail the save either."""
    directory = tmp_path / "not-a-file"
    directory.mkdir()
    eb = EBook(title="E", filename="e.epub", file_path=str(directory))
    ab = AudioBook(title="A", filename="a.m4b", file_path="/x/a.m4b")
    db.add_all([eb, ab])
    await db.flush()
    pair = BookPair(ebook_id=eb.id, audiobook_id=ab.id)
    db.add(pair)
    await db.commit()

    sm = await save_sync_map(db, pair.id, [_aligned(0, 0, 0)])

    assert sm.epub_file_hash is None


async def test_save_sync_map_replaces_and_bumps_version(db):
    pair = await make_book_pair(db)
    await save_sync_map(db, pair.id, [_aligned(0, 0, 0), _aligned(0, 1, 3000)])
    sm2 = await save_sync_map(db, pair.id, [_aligned(0, 0, 0)])

    assert sm2.version == 2
    assert sm2.total_sentences == 1

    maps = (await db.execute(
        select(SyncMap).where(SyncMap.book_pair_id == pair.id)
    )).scalars().all()
    assert len(maps) == 1  # old map replaced, not duplicated


# ---------------------------------------------------------------------------
# An empty point list is never a map (issue #194)
# ---------------------------------------------------------------------------

async def test_save_sync_map_refuses_an_empty_point_list(db):
    """Defence in depth behind the pipeline's own guard.

    `save_sync_map` deletes the outgoing map's points *before* inserting the
    new ones, so a caller handing it `[]` destroys a working map and puts
    nothing in its place. No caller should ever do that, and now none can.
    """
    pair = await make_book_pair(db)
    original = await save_sync_map(db, pair.id, [_aligned(0, 0, 0), _aligned(0, 1, 3000)])

    with pytest.raises(ValueError):
        await save_sync_map(db, pair.id, [])

    maps = (await db.execute(
        select(SyncMap).where(SyncMap.book_pair_id == pair.id)
    )).scalars().all()
    assert len(maps) == 1
    assert maps[0].id == original.id
    assert maps[0].version == 1
    points = (await db.execute(
        select(SyncPoint).where(SyncPoint.sync_map_id == original.id)
    )).scalars().all()
    assert len(points) == 2


async def test_save_sync_map_refuses_an_empty_list_on_a_first_ever_map(db):
    pair = await make_book_pair(db)
    with pytest.raises(ValueError):
        await save_sync_map(db, pair.id, [])
    assert (await db.execute(
        select(SyncMap).where(SyncMap.book_pair_id == pair.id)
    )).scalar_one_or_none() is None
