"""
Series through the rescan, rescan-all, ABS enrich, normalize and convert paths
(issue #905).

Series is a relationship, not a column, so none of these paths can pick it up
from a loop over `MEDIA_METADATA_FIELDS`; each one has to handle it by hand, and
a path that forgot would be a silent no-op. This file is the gate that each one
carries series, with the rule they share: an explicit rescan or enrich **adds**
names the row lacks, keeps the row's order and edits, never removes, and leaves
a cleared series cleared.
"""

import pytest
from sqlalchemy import select

import routers.library as library
from models.book import AudioBook, EBook
from tests.factories import make_audiobook, make_ebook

AXIS = {"name": "Axis Saga", "index": 4.0}
CHRONO = {"name": "Axis Chronological", "index": 2.5}


@pytest.fixture
async def editor_client(make_client, make_user, auth_header):
    user = await make_user(username="editor", role="admin")
    async with make_client(library.router) as c:
        yield c, auth_header(user)


@pytest.fixture
def extracted(monkeypatch):
    """`extract_metadata` returns whatever the test puts in this dict."""
    meta = {}

    async def _fake(filepath, file_type, db, library_root=None):
        return {"title": "From File", "author": "Ann Axis", "series_list": None,
                "_metadata_source": "stub", "_metadata_pattern": None, **meta}

    monkeypatch.setattr(library, "extract_metadata", _fake)
    monkeypatch.setattr(library, "_extract_and_save_cover", lambda *a, **k: None)
    return meta


async def _file_row(db, tmp_path, media, **fields):
    path = tmp_path / ("b.epub" if media == "ebook" else "b.m4b")
    path.write_bytes(b"x")
    make = make_ebook if media == "ebook" else make_audiobook
    row = await make(db, **fields)
    row.file_path = str(path)
    await db.commit()
    return row


# ------------------------------------------------------------- /rescan


@pytest.mark.parametrize("media", ["ebook", "audiobook"])
async def test_rescan_adds_missing_series_after_the_existing_ones(
    db, editor_client, extracted, tmp_path, media
):
    c, headers = editor_client
    row = await _file_row(db, tmp_path, media, series_list=[{"name": "Edited", "index": 9.0}])
    extracted["series_list"] = [AXIS, {"name": "edited", "index": 1.0}, CHRONO]

    r = await c.post(f"/api/library/{media}s/{row.id}/rescan", headers=headers)

    assert r.status_code == 200, r.text
    assert r.json()["series_list"] == [{"name": "Edited", "index": 9.0}, AXIS, CHRONO]


@pytest.mark.parametrize("media", ["ebook", "audiobook"])
async def test_rescan_fills_a_row_that_has_no_series(db, editor_client, extracted, tmp_path, media):
    c, headers = editor_client
    row = await _file_row(db, tmp_path, media)
    extracted["series_list"] = [AXIS]

    r = await c.post(f"/api/library/{media}s/{row.id}/rescan", headers=headers)

    assert r.json()["series_list"] == [AXIS]


@pytest.mark.parametrize("media", ["ebook", "audiobook"])
async def test_rescan_never_removes_a_series_the_file_no_longer_names(
    db, editor_client, extracted, tmp_path, media
):
    c, headers = editor_client
    row = await _file_row(db, tmp_path, media, series_list=[AXIS, CHRONO])
    extracted["series_list"] = [AXIS]

    r = await c.post(f"/api/library/{media}s/{row.id}/rescan", headers=headers)

    assert r.json()["series_list"] == [AXIS, CHRONO]


@pytest.mark.parametrize("media", ["ebook", "audiobook"])
async def test_rescan_leaves_a_cleared_series_cleared(db, editor_client, extracted, tmp_path, media):
    c, headers = editor_client
    row = await _file_row(db, tmp_path, media, series_cleared=True)
    extracted["series_list"] = [AXIS]

    r = await c.post(f"/api/library/{media}s/{row.id}/rescan", headers=headers)

    assert r.json()["series_list"] == []
    await db.refresh(row)
    assert row.series_cleared is True


# --------------------------------------------------------- /rescan-all


async def test_rescan_all_adds_missing_series_on_both_media_types(db, editor_client, extracted, tmp_path):
    c, headers = editor_client
    eb = await _file_row(db, tmp_path, "ebook", series_list=[AXIS])
    ab = await _file_row(db, tmp_path, "audiobook")
    cleared = await make_audiobook(db, filename="c.m4b", series_cleared=True)
    cleared.file_path = str(tmp_path / "c.m4b")
    (tmp_path / "c.m4b").write_bytes(b"x")
    await db.commit()
    extracted["series_list"] = [AXIS, CHRONO]

    r = await c.post("/api/library/rescan-all", headers=headers)

    assert r.status_code == 200, r.text
    for row in (eb, ab, cleared):
        await db.refresh(row)
    assert [e.name for e in eb.series_list] == ["Axis Saga", "Axis Chronological"]
    assert [e.name for e in ab.series_list] == ["Axis Saga", "Axis Chronological"]
    assert cleared.series_list == []


# ------------------------------------------------------------ enrich-abs


def _stub_abs(monkeypatch, abs_series):
    """A fake ABS: the real `enrich_from_abs` runs against a canned index entry."""
    async def _settings(db):
        return True, "http://abs.example.test", "token", ""

    monkeypatch.setattr(library, "_load_abs_settings", _settings)
    monkeypatch.setattr(library, "fetch_abs_index", lambda url, token, prefix: {"x": {}})
    real = library.enrich_from_abs

    def _enrich(file_meta, file_path, abs_index, prefix, force=False):
        from services import abs_metadata
        monkeypatch.setattr(abs_metadata, "_normalize_path", lambda *a, **k: "x")
        index = {"x": {"media": {"metadata": {"seriesName": abs_series}}}}
        return real(file_meta, file_path, index, prefix, force=force)

    monkeypatch.setattr(library, "enrich_from_abs", _enrich)
    written = []
    monkeypatch.setattr(library, "write_metadata_to_file",
                        lambda fp, meta: written.append(dict(meta)) or (True, None))
    return written


async def test_single_enrich_adds_abs_series_and_keeps_existing_order(db, editor_client, monkeypatch):
    c, headers = editor_client
    ab = await make_audiobook(db, series_list=[{"name": "Edited", "index": 9.0}])
    written = _stub_abs(monkeypatch, "Axis Saga #4, Axis Chronological #2.5")

    r = await c.post(f"/api/library/audiobooks/{ab.id}/enrich-abs", headers=headers)

    assert r.status_code == 200, r.text
    assert r.json()["book"]["series_list"] == [
        {"name": "Edited", "index": 9.0}, AXIS, CHRONO]
    # The tag write carries the merged list, byte for byte what the row holds.
    assert written[0]["series_list"] == [
        {"name": "Edited", "index": 9.0}, AXIS, CHRONO]


async def test_single_enrich_force_never_replaces_the_series_list(db, editor_client, monkeypatch):
    c, headers = editor_client
    ab = await make_audiobook(db, series_list=[AXIS, CHRONO])
    _stub_abs(monkeypatch, "Something Else #1")

    r = await c.post(f"/api/library/audiobooks/{ab.id}/enrich-abs", headers=headers)

    assert [e["name"] for e in r.json()["book"]["series_list"]] == [
        "Axis Saga", "Axis Chronological", "Something Else"]


async def test_single_enrich_skips_a_cleared_series_entirely(db, editor_client, monkeypatch):
    c, headers = editor_client
    ab = await make_audiobook(db, series_cleared=True)
    written = _stub_abs(monkeypatch, "Axis Saga #4")

    r = await c.post(f"/api/library/audiobooks/{ab.id}/enrich-abs", headers=headers)

    assert r.json()["book"]["series_list"] == []
    assert all("series_list" not in w for w in written)


async def test_enrich_with_no_abs_series_never_clears_the_files_tags(db, editor_client, monkeypatch):
    """ABS has nothing to say about series: the write-back must say nothing
    either (key absent), not an empty list, which would wipe the tags."""
    c, headers = editor_client
    ab = await make_audiobook(db)
    written = _stub_abs(monkeypatch, "")

    async def _desc(file_meta, *a, **k):  # pragma: no cover - placeholder
        return file_meta

    monkeypatch.setattr(library, "enrich_from_abs",
                        lambda fm, fp, idx, prefix, force=False: ({**fm, "description": "D"}, True, True))

    r = await c.post(f"/api/library/audiobooks/{ab.id}/enrich-abs", headers=headers)

    assert r.status_code == 200
    assert written == [] or all("series_list" not in w for w in written)


async def test_bulk_enrich_merges_series_for_every_book(db, editor_client, monkeypatch):
    c, headers = editor_client
    one = await make_audiobook(db, filename="one.m4b", series_list=[AXIS])
    two = await make_audiobook(db, filename="two.m4b")
    cleared = await make_audiobook(db, filename="three.m4b", series_cleared=True)
    _stub_abs(monkeypatch, "Axis Saga #4, Axis Chronological #2.5")

    r = await c.post("/api/library/enrich-abs", headers=headers)

    assert r.status_code == 200, r.text
    for row in (one, two, cleared):
        await db.refresh(row)
    assert [e.name for e in one.series_list] == ["Axis Saga", "Axis Chronological"]
    assert [e.name for e in two.series_list] == ["Axis Saga", "Axis Chronological"]
    assert cleared.series_list == []


# ------------------------------------------------------------- /normalize


async def test_normalize_fixes_every_entry_name(db, editor_client):
    c, headers = editor_client
    from tests.factories import series_rows

    eb = EBook(title="T", filename="t.epub", file_path="/x/n/t.epub", series_list=series_rows(
        EBook, [{"name": "Axis Saga, The", "index": 1.0}, {"name": "Chronological, A", "index": None}]))
    db.add(eb)
    await db.commit()

    r = await c.post("/api/library/normalize", headers=headers)

    assert r.status_code == 200, r.text
    assert r.json()["updated_ebooks"] == 1
    await db.refresh(eb)
    assert [(e.name, e.index) for e in eb.series_list] == [
        ("The Axis Saga", 1.0), ("A Chronological", None)]


# ---------------------------------------------------- convert (register EPUB)


async def test_a_converted_epub_inherits_every_series(db):
    source = await make_ebook(db, series_list=[AXIS, CHRONO])
    source_cleared = await make_ebook(db, series_cleared=True)
    from services import library_scan

    import os
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        for name, src in (("a.epub", source), ("b.epub", source_cleared)):
            path = os.path.join(d, name)
            with open(path, "wb") as f:
                f.write(b"x")
            created = await library._register_epub_in_db(path, src, db)
            await db.commit()
            await db.refresh(created)
            if src is source:
                assert [(e.name, e.index) for e in created.series_list] == [
                    ("Axis Saga", 4.0), ("Axis Chronological", 2.5)]
            else:
                assert created.series_list == [] and created.series_cleared is True


# ------------------------------------------------------- /debug-metadata


async def test_debug_metadata_reports_the_series_list_and_every_series_meta(db, editor_client, tmp_path):
    from tests.test_embedded_metadata import _epub
    c, headers = editor_client
    path = _epub(tmp_path / "d.epub", (
        '<dc:identifier id="bookid">urn:uuid:x</dc:identifier><dc:title>Two</dc:title>'
        '<meta name="calibre:series" content="Axis Saga"/>'
        '<meta name="calibre:series_index" content="4"/>'
        '<meta name="calibre:series" content="Axis Chronological"/>'
        '<meta property="belongs-to-collection" id="c1">Collected</meta>'
        '<meta refines="#c1" property="group-position">3</meta>'
    ))
    row = await make_ebook(db, series_list=[AXIS, CHRONO])
    row.file_path = path
    await db.commit()

    r = await c.get(f"/api/library/debug-metadata/ebook/{row.id}", headers=headers)

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["current_db_metadata"]["series_list"] == [AXIS, CHRONO]
    names = [m.get("name") or m.get("property") for m in body["embedded_tags"]["series_metas"]]
    assert names == ["calibre:series", "calibre:series_index", "calibre:series",
                     "belongs-to-collection", "group-position"]
    assert body["merged_result"]["series_list"][0] == {"name": "Axis Saga", "index": 4.0}
