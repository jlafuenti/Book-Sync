"""
Series through the import sources and the replace-file path (issue #905).

Series is a relationship, so these paths cannot inherit it from a loop over the
shared metadata field list; each one reads or writes the list by hand.
"""

import zipfile

import pytest

from services.import_sources import acsm
from tests.factories import make_ebook
from tests.test_embedded_metadata import CONTAINER, OPF


def _epub(path, meta):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr("META-INF/container.xml", CONTAINER)
        z.writestr("OEBPS/content.opf", OPF.format(meta=meta))
    return path


def test_acsm_reads_every_calibre_series_with_the_next_index(tmp_path):
    path = _epub(tmp_path / "a.epub", (
        '<dc:title>Two Series</dc:title>'
        '<meta name="calibre:series" content="Axis Saga"/>'
        '<meta name="calibre:series_index" content="4"/>'
        '<meta name="calibre:series" content="Axis Chronological"/>'
    ))

    meta = acsm._read_epub_meta(path)

    assert meta["series_list"] == [
        {"name": "Axis Saga", "index": 4.0},
        {"name": "Axis Chronological", "index": None},
    ]
    assert "series" not in meta


def test_acsm_with_no_series_has_no_series_key(tmp_path):
    path = _epub(tmp_path / "a.epub", "<dc:title>Plain</dc:title>")

    assert "series_list" not in acsm._read_epub_meta(path)


def test_audible_keeps_every_series_in_order():
    audible = pytest.importorskip("audible")  # noqa: F841 - the module imports it
    from services.import_sources.audible import _normalize_item

    meta = _normalize_item({
        "asin": "B000000001", "title": "Two Series",
        "series": [
            {"title": "Axis Saga", "sequence": "4"},
            {"title": "Axis Chronological", "sequence": "2.5"},
            {"title": "Axis Loose", "sequence": ""},
        ],
    })

    assert meta["series_list"] == [
        {"name": "Axis Saga", "index": 4.0},
        {"name": "Axis Chronological", "index": 2.5},
        {"name": "Axis Loose", "index": None},
    ]


def test_audible_book_without_a_series_has_an_empty_list():
    pytest.importorskip("audible")
    from services.import_sources.audible import _normalize_item

    assert _normalize_item({"asin": "B000000002", "title": "Alone"})["series_list"] == []


def test_a_placed_file_is_named_after_the_primary_series():
    from services.library_writer import render_template

    meta = {
        "author": "Ann Axis", "title": "Two Series",
        "series_list": [{"name": "Axis Saga", "index": 4.0}, {"name": "Axis Chronological", "index": 2.5}],
    }
    assert render_template("<Author>/<Series>/<Book Number> - <Title>", meta) == (
        "Ann Axis/Axis Saga/4 - Two Series")


# ----------------------------------------------------------- replace file


class TestReplaceFile:
    @pytest.fixture
    def replaced(self, monkeypatch, tmp_path):
        """A replace whose new file yields `extracted`; returns the setter."""
        import routers.library as library
        from config import settings

        ebook_dir = tmp_path / "ebooks"
        ebook_dir.mkdir()
        monkeypatch.setattr(settings, "ebook_dir", str(ebook_dir))
        extracted = {}

        async def _fake(filepath, file_type, db, library_root=None):
            return dict(extracted)

        monkeypatch.setattr(library, "extract_metadata", _fake)
        return ebook_dir, extracted

    async def _replace(self, db, make_user, auth_header, ebook_dir, row):
        from tests.test_replace_invalidates_positions import _client, _replace

        path = ebook_dir / f"book-{row.id}.epub"
        path.write_bytes(b"old")
        row.file_path = str(path)
        row.filename = path.name
        row.format = "epub"
        await db.commit()
        editor = await make_user(username="editor", role="editor")
        async with _client() as c:
            await _replace(c, editor, auth_header, "ebook", row.id, "new.epub", b"new bytes")
        await db.refresh(row)

    async def test_the_new_files_series_replace_the_rows(self, db, make_user, auth_header, replaced):
        ebook_dir, extracted = replaced
        row = await make_ebook(db, series_list=[{"name": "Old", "index": 1.0}])
        extracted["series_list"] = [
            {"name": "Axis Saga", "index": 4.0}, {"name": "Axis Chronological", "index": 2.5}]

        await self._replace(db, make_user, auth_header, ebook_dir, row)

        assert [(e.name, e.index) for e in row.series_list] == [
            ("Axis Saga", 4.0), ("Axis Chronological", 2.5)]

    async def test_a_file_that_names_no_series_keeps_the_rows(self, db, make_user, auth_header, replaced):
        ebook_dir, extracted = replaced
        row = await make_ebook(db, series_list=[{"name": "Kept", "index": 1.0}])

        await self._replace(db, make_user, auth_header, ebook_dir, row)

        assert [e.name for e in row.series_list] == ["Kept"]
