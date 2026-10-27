"""
A scan's Audiobookshelf write-back must converge (issue #878).

`enrich_from_abs` fills any field the file "lacks", and the scan wrote the result
back into the file whenever anything was filled. But four of those fields
(language, isbn, asin, tags) and both flags are never stored by `_write_tags`,
and the MP4 reader never reads them. So the file lacked them again on the next
scan, and every scan rewrote every such M4B: in production about 240 files and
158 GB per scan, each one re-hashed afterwards and handed a new mtime that other
library tools treated as a changed file.

The scan now writes back only when enrichment filled a field that survives the
round trip through the file (`abs_metadata.WRITTEN_BACK_FIELDS`). Everything
else still reaches the database. The contract tests below pin that list to what
the real writer and reader do with a real M4B, so the two cannot drift apart.
"""

import os

import pytest
from sqlalchemy import select

from config import settings
from models.book import AudioBook
from services import abs_metadata, library_scan, metadata_extract
from services.metadata_utils import MEDIA_METADATA_FIELDS
from tests.factories import write_minimal_m4a


@pytest.fixture(autouse=True)
def no_ffprobe(monkeypatch):
    # The hand-built M4B has no audio track; these tests are about tags.
    monkeypatch.setattr(metadata_extract, "probe_duration_seconds", lambda path: None)


@pytest.fixture
def audio_dir(monkeypatch, tmp_path):
    ebooks = tmp_path / "ebooks"
    audio = tmp_path / "audiobooks"
    ebooks.mkdir()
    audio.mkdir()
    monkeypatch.setattr(settings, "ebook_dir", str(ebooks))
    monkeypatch.setattr(settings, "audiobook_dir", str(audio))
    monkeypatch.setattr(settings, "covers_dir", str(tmp_path / "covers"))
    return audio


def _abs_index(book_dir, audio_dir, **metadata):
    key = abs_metadata._normalize_path(str(book_dir), str(audio_dir))
    tags = metadata.pop("tags", None)
    item = {"media": {"metadata": metadata}}
    if tags is not None:
        item["media"]["tags"] = tags
    return {key: item}


async def _extract(db, path, audio_dir):
    return await metadata_extract.extract_metadata(
        str(path), "audiobook", db, library_root=str(audio_dir)
    )


# ---------------------------------------------------------------------------
# The scan, twice
# ---------------------------------------------------------------------------

async def test_a_second_scan_does_not_rewrite_the_file(db, audio_dir, monkeypatch):
    book_dir = audio_dir / "Ann Axis" / "Axis Test"
    book_dir.mkdir(parents=True)
    path = write_minimal_m4a(book_dir / "Axis Test.m4b")
    db.add(AudioBook(title="Axis Test", filename="Axis Test.m4b", file_path=path,
                     metadata_source="manual"))
    await db.commit()

    index = _abs_index(
        book_dir, audio_dir,
        title="Axis Test", authorName="Ann Axis", description="A synthetic book.",
        language="English", isbn="9780000000002", asin="B000000000",
        tags=["Synthetic", "Test"],
    )
    writes = []
    real_write = library_scan.write_metadata_to_file

    def _spy(filepath, meta):
        writes.append(filepath)
        return real_write(filepath, meta)

    monkeypatch.setattr(library_scan, "write_metadata_to_file", _spy)

    # First scan: the file lacks author and description, which the writer
    # stores, so one write-back is right.
    await library_scan._ingest_one_audiobook(db, path, str(audio_dir), index)
    await db.commit()
    assert writes == [path]
    mtime = os.stat(path).st_mtime_ns

    # Second scan: ABS still "fills" language, isbn, asin and tags, which the
    # file can never hold. That must not touch the file again.
    await library_scan._ingest_one_audiobook(db, path, str(audio_dir), index)
    await db.commit()
    assert writes == [path], "the second scan rewrote the file"
    assert os.stat(path).st_mtime_ns == mtime

    # Those four still reach the database, as before.
    row = (await db.execute(select(AudioBook))).scalar_one()
    assert (row.language, row.isbn, row.asin, row.tags) == (
        "English", "9780000000002", "B000000000", "Synthetic,Test",
    )


async def test_a_fill_the_file_cannot_hold_never_writes(db, audio_dir, monkeypatch):
    """Only unwritable fields missing: no write-back at all, on any scan."""
    book_dir = audio_dir / "Ann Axis" / "Axis Test"
    book_dir.mkdir(parents=True)
    path = write_minimal_m4a(book_dir / "Axis Test.m4b")
    abs_metadata._write_tags(path, {"title": "Axis Test", "author": "Ann Axis"})
    db.add(AudioBook(title="Axis Test", filename="Axis Test.m4b", file_path=path,
                     metadata_source="manual"))
    await db.commit()
    index = _abs_index(
        book_dir, audio_dir, title="Axis Test", authorName="Ann Axis",
        asin="B000000000", explicit=True, abridged=True,
    )
    writes = []
    monkeypatch.setattr(library_scan, "write_metadata_to_file",
                        lambda fp, meta: writes.append(fp) or (True, None))

    await library_scan._ingest_one_audiobook(db, path, str(audio_dir), index)
    await db.commit()

    assert writes == []


async def test_a_user_cleared_series_from_abs_never_writes(db, audio_dir, monkeypatch):
    """Found on production during verification: a row whose series the user
    cleared (`""`) is never given ABS's series in the file — the scan drops it
    before writing — so ABS "filled" it again on every scan. Judging the
    write-back after that drop, not before, is what makes it converge."""
    book_dir = audio_dir / "Ann Axis" / "Axis Test"
    book_dir.mkdir(parents=True)
    path = write_minimal_m4a(book_dir / "Axis Test.m4b")
    abs_metadata._write_tags(path, {"title": "Axis Test", "author": "Ann Axis"})
    db.add(AudioBook(title="Axis Test", filename="Axis Test.m4b", file_path=path,
                     metadata_source="manual", series=""))
    await db.commit()
    index = _abs_index(
        book_dir, audio_dir, title="Axis Test", authorName="Ann Axis",
        seriesName="Axis #1",
    )
    writes = []
    monkeypatch.setattr(library_scan, "write_metadata_to_file",
                        lambda fp, meta: writes.append(fp) or (True, None))

    await library_scan._ingest_one_audiobook(db, path, str(audio_dir), index)
    await db.commit()

    assert writes == []
    assert (await db.execute(select(AudioBook))).scalar_one().series == ""


async def test_a_user_cleared_series_the_file_still_carries_never_writes(db, audio_dir, monkeypatch):
    """The other half of the cleared-series case, also found on production: the
    file (or its path) still yields a series, the scan drops it for the write,
    and the writer leaves a field it is not given alone. So "the file had a
    series, the write-back has none" is not a change, and must not count."""
    book_dir = audio_dir / "Ann Axis" / "Axis Test"
    book_dir.mkdir(parents=True)
    path = write_minimal_m4a(book_dir / "Axis Test.m4b")
    abs_metadata._write_tags(path, {"title": "Axis Test", "author": "Ann Axis",
                                    "series": "Axis", "series_index": 1.0})
    db.add(AudioBook(title="Axis Test", filename="Axis Test.m4b", file_path=path,
                     metadata_source="manual", series=""))
    await db.commit()
    index = _abs_index(
        book_dir, audio_dir, title="Axis Test", authorName="Ann Axis",
        seriesName="Axis #1", asin="B000000000",
    )
    writes = []
    monkeypatch.setattr(library_scan, "write_metadata_to_file",
                        lambda fp, meta: writes.append(fp) or (True, None))

    await library_scan._ingest_one_audiobook(db, path, str(audio_dir), index)
    await db.commit()

    assert writes == []


def test_a_field_the_write_back_does_not_carry_is_not_a_change():
    before = {"title": "Axis Test", "series": "Axis", "series_index": 1.0}
    assert not abs_metadata.fills_written_back_field(before, {"title": "Axis Test"})
    assert not abs_metadata.fills_written_back_field(
        before, {"title": "Axis Test", "series": None})
    assert abs_metadata.fills_written_back_field(
        before, {"title": "Axis Test", "series": "Axis", "series_index": 1.0,
                 "author": "Ann Axis"})
    # An explicit "" still reaches the file (the writer clears the tag).
    assert abs_metadata.fills_written_back_field(before, {"title": "Axis Test", "series": ""})


async def test_a_new_audiobook_with_only_unwritable_fills_is_not_written(db, audio_dir, monkeypatch):
    book_dir = audio_dir / "Ann Axis" / "Axis Test"
    book_dir.mkdir(parents=True)
    path = write_minimal_m4a(book_dir / "Axis Test.m4b")
    abs_metadata._write_tags(path, {"title": "Axis Test", "author": "Ann Axis"})
    index = _abs_index(book_dir, audio_dir, title="Axis Test", language="English")
    writes = []
    monkeypatch.setattr(library_scan, "write_metadata_to_file",
                        lambda fp, meta: writes.append(fp) or (True, None))

    assert await library_scan._ingest_one_audiobook(db, path, str(audio_dir), index) is True
    await db.commit()

    assert writes == []
    assert (await db.execute(select(AudioBook))).scalar_one().language == "English"


# ---------------------------------------------------------------------------
# The contract: WRITTEN_BACK_FIELDS is exactly what round-trips through a file
# ---------------------------------------------------------------------------

_SAMPLE = {
    "title": "Axis Test",
    "author": "Ann Axis",
    "description": "A synthetic book.",
    "publish_year": 1999,
    "genres": "Fiction",
    "publisher": "Example Press",
    "narrators": "Bea Reader",
    "series": "Axis",
    "series_index": 2.0,
    "language": "English",
    "isbn": "9780000000002",
    "asin": "B000000000",
    "tags": "Synthetic",
    "is_explicit": True,
    "is_abridged": True,
}


def test_the_sample_covers_every_metadata_field():
    assert set(MEDIA_METADATA_FIELDS) <= set(_SAMPLE) | {"series_index"}


@pytest.mark.parametrize("field", sorted(abs_metadata.WRITTEN_BACK_FIELDS))
async def test_every_written_back_field_survives_the_file(db, audio_dir, field):
    path = write_minimal_m4a(audio_dir / "Axis Test.m4b")
    abs_metadata._write_tags(path, dict(_SAMPLE))

    meta = await _extract(db, path, audio_dir)

    assert meta.get(field) == _SAMPLE[field], (
        f"{field} is in WRITTEN_BACK_FIELDS but does not come back from the file"
    )


@pytest.mark.parametrize(
    "field", sorted(set(_SAMPLE) - set(abs_metadata.WRITTEN_BACK_FIELDS))
)
async def test_every_other_field_does_not_survive_the_file(db, audio_dir, field):
    """If one of these starts round-tripping, add it to WRITTEN_BACK_FIELDS so
    a scan that fills it writes it back."""
    path = write_minimal_m4a(audio_dir / "Axis Test.m4b")
    abs_metadata._write_tags(path, dict(_SAMPLE))

    meta = await _extract(db, path, audio_dir)

    assert not meta.get(field)
