"""
Shared test-data factories.

Async helpers that seed rows into the SQLite test DB. Import these instead of
re-implementing seeding per test file. (User creation lives in the `make_user`
fixture in conftest.py.)
"""

from sqlalchemy import text

from models.book import EBook, AudioBook, BookPair, PairStatus
from models.sync_map import SyncMap, SyncPoint


async def suspend_user_progress_uniqueness(db):
    """Drop the `user_progress` unique indexes for this test's schema.

    Lets a test reproduce the pre-#64 state — two rows for the same
    (user, media), left by the old GET-creates-a-row race — which the indexes
    now make unrepresentable. The schema is rebuilt per test, so this affects
    nothing else.
    """
    await db.execute(text("DROP INDEX ux_user_progress_user_ebook"))
    await db.execute(text("DROP INDEX ux_user_progress_user_audiobook"))


async def make_ebook(db, *, title="E", filename="e.epub"):
    """Create a standalone (unpaired) EBook and return it."""
    eb = EBook(title=title, filename=filename, file_path=f"/x/{filename}")
    db.add(eb)
    await db.commit()
    await db.refresh(eb)
    return eb


async def make_audiobook(db, *, title="A", filename="a.m4b"):
    """Create a standalone (unpaired) AudioBook and return it."""
    ab = AudioBook(title=title, filename=filename, file_path=f"/x/{filename}")
    db.add(ab)
    await db.commit()
    await db.refresh(ab)
    return ab


async def make_book_pair(db, status=PairStatus.SYNCED, *,
                         ebook_title="E", audiobook_title="A",
                         duration_seconds=None):
    """Create an EBook + AudioBook + BookPair and return the committed pair.

    `duration_seconds` lands on the AudioBook (None = unknown length, which
    is what a freshly scanned file has until ffprobe runs)."""
    eb = EBook(title=ebook_title, filename="e.epub", file_path="/x/e.epub")
    ab = AudioBook(title=audiobook_title, filename="a.m4b", file_path="/x/a.m4b",
                   duration_seconds=duration_seconds)
    db.add_all([eb, ab])
    await db.flush()
    pair = BookPair(ebook_id=eb.id, audiobook_id=ab.id, status=status)
    db.add(pair)
    await db.commit()
    await db.refresh(pair)
    return pair


# Default sync points (chapter, sentence_index, audio_start_ms, preview).
# ch=1/s=1 has a NULL preview to exercise the nearest-preview fallback.
DEFAULT_SYNC_POINTS = [
    (0, 0, 0, "chapter one opening line"),
    (0, 1, 5000, "second sentence here"),
    (1, 0, 10000, "chapter two begins now"),
    (1, 1, 15000, None),
    (1, 2, 20000, "later sentence in two"),
]


async def make_sync_map(db, book_pair_id=1, points=None):
    """Create a SyncMap + ordered SyncPoints for a pair and return the map."""
    points = DEFAULT_SYNC_POINTS if points is None else points
    chapters = {ch for ch, *_ in points}
    sm = SyncMap(book_pair_id=book_pair_id, version=1,
                 total_sentences=len(points), total_chapters=len(chapters))
    db.add(sm)
    await db.flush()
    for ch, si, ms, preview in points:
        db.add(SyncPoint(
            sync_map_id=sm.id, epub_chapter=ch, epub_sentence_index=si,
            epub_text_preview=preview, audio_start_ms=ms, audio_end_ms=ms + 3000,
            confidence=1.0,
        ))
    await db.commit()
    return sm
