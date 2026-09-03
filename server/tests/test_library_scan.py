"""
The scan's identity key is `file_path`, and now the database agrees (issue #256).

`_ingest_one_ebook` is check-then-insert: it looks the path up with
`.scalar_one_or_none()` and enriches the row if it finds one. That is correct as
long as no second row for the same path exists — and nothing prevented one. A
duplicate arriving any other way (a manual insert, a restore from an older dump,
a second ingest worker) turns every later scan of that file into a
`MultipleResultsFound`, which 500s the whole library scan, not just that book.

Two halves, both needed:

* the scan really does update rather than insert on a re-scan (the behaviour the
  unique index assumes), and
* the index actually rejects the second row, on SQLite as well as Postgres, so
  the assumption is enforced rather than merely believed.
"""

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from models.book import AudioBook, BookPair, EBook, PairStatus
from tests.factories import write_epub

CHAPTER = "<html><body><p>A sentence long enough to survive filtering.</p></body></html>"


async def _count(db, model):
    return (await db.execute(select(func.count()).select_from(model))).scalar_one()


@pytest.fixture
def epub(tmp_path):
    path = tmp_path / "Some Author - Some Title.epub"
    write_epub(str(path), [("ch1.xhtml", CHAPTER)])
    return str(path)


async def test_ingesting_the_same_path_twice_updates_rather_than_inserts(db, epub, tmp_path):
    from routers.library import _ingest_one_ebook

    assert await _ingest_one_ebook(db, epub, str(tmp_path)) is True
    await db.commit()
    assert await _count(db, EBook) == 1

    # Second scan of an unchanged library: the same file must land on the same
    # row. If this ever inserted instead, the unique index would turn a silent
    # duplicate into a loud scan failure — which is the better bug, but the
    # behaviour under test is that neither happens.
    assert await _ingest_one_ebook(db, epub, str(tmp_path)) is False
    await db.commit()
    assert await _count(db, EBook) == 1


async def test_a_second_ebook_row_with_the_same_path_cannot_be_flushed(db):
    db.add(EBook(title="First", filename="dup.epub", file_path="/books/dup.epub"))
    await db.commit()

    db.add(EBook(title="Second", filename="dup.epub", file_path="/books/dup.epub"))
    with pytest.raises(IntegrityError):
        await db.flush()
    await db.rollback()

    assert await _count(db, EBook) == 1


async def test_a_second_audiobook_row_with_the_same_path_cannot_be_flushed(db):
    """Both halves of the library are looked up the same way, so both are
    constrained — an index on only one of them is a half-fixed bug."""
    db.add(AudioBook(title="First", filename="dup.m4b", file_path="/audio/dup.m4b"))
    await db.commit()

    db.add(AudioBook(title="Second", filename="dup.m4b", file_path="/audio/dup.m4b"))
    with pytest.raises(IntegrityError):
        await db.flush()
    await db.rollback()

    assert await _count(db, AudioBook) == 1


async def test_the_same_pairing_cannot_be_recorded_twice(db):
    """`create_pair` returns 409 for this; `uq_book_pairs_pair` means the 409 is
    no longer the only thing standing between it and a duplicate."""
    ebook = EBook(title="E", filename="e.epub", file_path="/books/e.epub")
    audio = AudioBook(title="A", filename="a.m4b", file_path="/audio/a.m4b")
    db.add_all([ebook, audio])
    await db.flush()
    db.add(BookPair(ebook_id=ebook.id, audiobook_id=audio.id,
                    status=PairStatus.SYNCED))
    await db.commit()

    db.add(BookPair(ebook_id=ebook.id, audiobook_id=audio.id,
                    status=PairStatus.SYNCED))
    with pytest.raises(IntegrityError):
        await db.flush()
    await db.rollback()

    assert await _count(db, BookPair) == 1


async def test_two_different_paths_still_coexist(db):
    """The constraint is on the path, not the file's content or its name."""
    db.add_all([
        EBook(title="A", filename="same.epub", file_path="/books/one/same.epub",
              file_hash="deadbeef"),
        EBook(title="B", filename="same.epub", file_path="/books/two/same.epub",
              file_hash="deadbeef"),
    ])
    await db.commit()

    assert await _count(db, EBook) == 2
