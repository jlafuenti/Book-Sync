"""The scan commits as it goes, and survives losing a race (issue #202).

Two halves of the same problem, both caused by the scan being one long
request-scoped transaction:

* every write in the walk was a `flush()` and the only `commit()` was `get_db`'s
  at the end of the request, so a crash, a container restart or one unreadable
  file part-way through the production library threw away the whole scan;
* ingestion is check-then-insert, which cannot be made race-free in the
  application. Issue #256's unique index on `file_path` makes the duplicate
  unrepresentable — which turns the losing side of a race from a silent duplicate
  into an `IntegrityError` that poisons the entire transaction unless it is
  caught inside a savepoint and re-read.

`test_library_scan.py` covers the constraint itself; this file covers what the
scan does around it.
"""

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from models.book import AudioBook, EBook
from tests.factories import write_epub

CHAPTER = "<html><body><p>A sentence long enough to survive filtering.</p></body></html>"


async def _count(session, model):
    return (await session.execute(select(func.count()).select_from(model))).scalar_one()


@pytest.fixture
def epub(tmp_path):
    path = tmp_path / "Some Author - Some Title.epub"
    write_epub(str(path), [("ch1.xhtml", CHAPTER)])
    return str(path)


@pytest.fixture
def library_dirs(monkeypatch, tmp_path):
    """Point the scan at empty throwaway directories, with ABS disabled."""
    from config import settings
    from routers import library

    ebook_dir = tmp_path / "ebooks"
    audio_dir = tmp_path / "audiobooks"
    ebook_dir.mkdir()
    audio_dir.mkdir()
    monkeypatch.setattr(settings, "ebook_dir", str(ebook_dir))
    monkeypatch.setattr(settings, "audiobook_dir", str(audio_dir))

    async def _no_abs(db):
        return {}

    monkeypatch.setattr(library, "_maybe_load_abs_index", _no_abs)
    return ebook_dir, audio_dir


# ------------------------------------------------------------ bounded batches


async def test_a_scan_that_dies_midway_keeps_the_batches_it_already_committed(
    db, library_dirs, monkeypatch
):
    from database import async_session
    from routers import library

    ebook_dir, _ = library_dirs
    for i in range(5):
        write_epub(str(ebook_dir / f"Author - Book {i}.epub"), [("ch1.xhtml", CHAPTER)])

    monkeypatch.setattr(library, "SCAN_COMMIT_BATCH", 2)

    real = library._ingest_one_ebook
    calls = {"n": 0}

    async def _fails_on_the_third(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 3:
            raise RuntimeError("the disk went away mid-scan")
        return await real(*args, **kwargs)

    monkeypatch.setattr(library, "_ingest_one_ebook", _fails_on_the_third)

    with pytest.raises(RuntimeError):
        await library.scan_library_impl(db)
    await db.rollback()

    # A *different* session, so nothing here can be reading uncommitted state.
    async with async_session() as fresh:
        assert await _count(fresh, EBook) == 2


async def test_a_completed_scan_leaves_its_last_short_batch_committed(db, library_dirs):
    """Batching must not strand the tail — three files against a batch of two."""
    from database import async_session
    from routers import library

    ebook_dir, _ = library_dirs
    for i in range(3):
        write_epub(str(ebook_dir / f"Author - Book {i}.epub"), [("ch1.xhtml", CHAPTER)])

    resp = await library.scan_library_impl(db)

    assert resp.new_ebooks == 3
    async with async_session() as fresh:
        assert await _count(fresh, EBook) == 3


async def test_the_scan_response_shape_is_unchanged(db, library_dirs):
    """Batching is invisible to the client: same fields, same counts."""
    from routers import library

    ebook_dir, _ = library_dirs
    write_epub(str(ebook_dir / "Author - Only Book.epub"), [("ch1.xhtml", CHAPTER)])

    resp = await library.scan_library_impl(db)

    assert set(resp.model_dump()) == {
        "new_ebooks", "new_audiobooks", "auto_matched_pairs",
        "multi_file_folders", "message",
    }
    assert resp.new_ebooks == 1
    assert resp.new_audiobooks == 0


# ----------------------------------------------------- the losing side of a race


def _miss_once(monkeypatch, library):
    """Make the next `_find_by_path` report "nothing there".

    A deterministic stand-in for the racing transaction: the real race is two
    sessions whose SELECTs both miss before either INSERT lands, which two
    sessions against one SQLite connection cannot reproduce. Forcing one miss
    after the row exists puts the write path in exactly that state. Mirrors
    `test_progress_uniqueness.py::_one_shot_none`.
    """
    real = library._find_by_path
    calls = {"n": 0}

    async def _misses_once(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return None
        return await real(*args, **kwargs)

    monkeypatch.setattr(library, "_find_by_path", _misses_once)


async def test_losing_a_race_to_insert_an_ebook_path_converges_on_one_row(
    db, epub, tmp_path, monkeypatch
):
    from routers import library

    assert await library._ingest_one_ebook(db, epub, str(tmp_path)) is True
    await db.commit()

    _miss_once(monkeypatch, library)

    # No exception, no second row, and the caller is told nothing was created.
    assert await library._ingest_one_ebook(db, epub, str(tmp_path)) is False
    await db.commit()
    assert await _count(db, EBook) == 1


async def test_losing_the_race_on_an_audiobook_converges_too(db, tmp_path, monkeypatch):
    """Both halves of the library ingest the same way, so both recover the same way."""
    from routers import library

    path = str(tmp_path / "Author - Title.m4b")
    with open(path, "wb") as fh:
        fh.write(b"not really an audio container")

    monkeypatch.setattr(library, "probe_duration_seconds", lambda *a, **k: None)

    assert await library._ingest_one_audiobook(db, path, str(tmp_path), {}) is True
    await db.commit()

    _miss_once(monkeypatch, library)

    assert await library._ingest_one_audiobook(db, path, str(tmp_path), {}) is False
    await db.commit()
    assert await _count(db, AudioBook) == 1


async def test_an_integrity_error_the_reread_cannot_explain_is_not_swallowed(
    db, epub, tmp_path, monkeypatch
):
    """The reread only recovers the conflict it is there for.

    If the insert failed and the re-read still finds nothing, the row is
    genuinely absent — hiding that would turn a schema bug into a book that
    silently never appears in the library. Here the lookup is stubbed to miss
    every time, so the recovery has nothing to hand back and must re-raise.
    """
    from routers import library

    db.add(EBook(title="Winner", filename="x.epub", file_path=epub))
    await db.commit()

    async def _always_missing(*args, **kwargs):
        return None

    monkeypatch.setattr(library, "_find_by_path", _always_missing)

    with pytest.raises(IntegrityError):
        await library._ingest_one_ebook(db, epub, str(tmp_path))

    await db.rollback()
