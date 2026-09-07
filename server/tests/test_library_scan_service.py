"""`services.library_scan` — the ingest path, driven without HTTP (issue #255).

`test_library_scan.py` covers the unique-path constraint and
`test_library_scan_batching.py` the commit cadence and the insert race; both
already drive the service directly. This file covers what neither did: the
walk and classify helpers, `scan_files_impl`'s routing of paths by extension,
the enrich-an-existing-row branches of both ingest helpers, and the ABS hook
inside the audiobook ingest. Metadata extraction is stubbed on the service
module -- that is where the ingest looks it up.
"""

import os

import pytest
from sqlalchemy import select

from config import settings
from models.book import AudioBook, EBook
from services import abs_metadata, library_scan
from tests.factories import write_epub

CHAPTER = "<html><body><p>A sentence long enough to survive filtering.</p></body></html>"


def _touch(path, data=b"not really an audio container"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return str(path)


@pytest.fixture
def library_dirs(monkeypatch, tmp_path):
    ebook_dir = tmp_path / "ebooks"
    audio_dir = tmp_path / "audiobooks"
    ebook_dir.mkdir()
    audio_dir.mkdir()
    monkeypatch.setattr(settings, "ebook_dir", str(ebook_dir))
    monkeypatch.setattr(settings, "audiobook_dir", str(audio_dir))
    monkeypatch.setattr(settings, "covers_dir", str(tmp_path / "covers"))
    return ebook_dir, audio_dir


@pytest.fixture
def stub_extract(monkeypatch):
    """Metadata comes from a dict the test controls, keyed by basename."""
    metas = {}

    async def _fake(filepath, file_type, db, library_root=None):
        base = {"title": os.path.splitext(os.path.basename(filepath))[0],
                "author": None, "series": None, "series_index": None,
                "_metadata_source": "stub", "_metadata_pattern": None}
        base.update(metas.get(os.path.basename(filepath), {}))
        return base

    monkeypatch.setattr(library_scan, "extract_metadata", _fake)
    monkeypatch.setattr(library_scan, "_extract_and_save_cover", lambda *a, **k: None)
    return metas


@pytest.fixture
def no_abs(monkeypatch):
    calls = []

    async def _none(db):
        calls.append(1)
        return {}

    monkeypatch.setattr(library_scan, "_maybe_load_abs_index", _none)
    return calls


# ------------------------------------------------------------ sync helpers


def test_walk_tree_materialises_every_directory_and_is_empty_for_a_missing_root(tmp_path):
    (tmp_path / "a" / "b").mkdir(parents=True)
    (tmp_path / "a" / "one.epub").write_bytes(b"x")
    (tmp_path / "a" / "b" / "two.epub").write_bytes(b"y")

    tree = library_scan._walk_tree(str(tmp_path / "a"))

    assert {(os.path.basename(d), tuple(f)) for d, f in tree} == {("a", ("one.epub",)), ("b", ("two.epub",))}
    assert library_scan._walk_tree(str(tmp_path / "absent")) == []


def test_multi_file_groups_reads_the_folder_and_tolerates_a_missing_one(tmp_path, monkeypatch):
    folder = tmp_path / "Dune"
    for i in range(1, 4):
        _touch(folder / f"{i:02d}.mp3")
    (folder / "sub").mkdir()  # directories are not candidates
    monkeypatch.setattr(library_scan.multi_file_audiobooks, "read_audio_tags", lambda p: {})

    groups = library_scan._multi_file_groups(str(folder), str(tmp_path))

    assert len(groups) == 1
    assert sorted(os.path.basename(p) for p in groups[0].paths) == ["01.mp3", "02.mp3", "03.mp3"]
    assert library_scan._multi_file_groups(str(tmp_path / "absent"), str(tmp_path)) == []


def test_classify_tree_flags_track_folders_across_a_walked_tree(tmp_path, monkeypatch):
    for i in range(1, 4):
        _touch(tmp_path / "Dune" / f"{i:02d}.mp3")
    _touch(tmp_path / "Single" / "book.m4b")
    monkeypatch.setattr(library_scan.multi_file_audiobooks, "read_audio_tags", lambda p: {})

    groups = library_scan._classify_tree(library_scan._walk_tree(str(tmp_path)), str(tmp_path))

    assert [os.path.basename(g.folder_path) for g in groups] == ["Dune"]


def test_hash_and_size_read_the_same_file(tmp_path):
    path = tmp_path / "f.bin"
    path.write_bytes(b"z" * 1234)

    file_hash, size = library_scan._hash_and_size(str(path))

    assert size == 1234
    assert file_hash == library_scan.compute_file_hash(str(path))


async def test_batch_commits_every_n_ticks_and_defaults_to_the_module_constant(monkeypatch):
    commits = []

    class _Db:
        async def commit(self):
            commits.append(1)

    batch = library_scan._Batch(_Db(), size=2)
    for _ in range(5):
        await batch.tick()
    assert len(commits) == 2

    monkeypatch.setattr(library_scan, "SCAN_COMMIT_BATCH", 3)
    assert library_scan._Batch(_Db())._size == 3


# --------------------------------------------------------- scan_files_impl


async def test_targeted_scan_routes_paths_by_extension_and_skips_what_is_not_there(
    db, library_dirs, stub_extract, no_abs
):
    ebook_dir, audio_dir = library_dirs
    epub = write_epub(str(ebook_dir / "Book.epub"), [("ch1.xhtml", CHAPTER)])
    txt = _touch(ebook_dir / "notes.txt")
    missing = str(ebook_dir / "gone.epub")

    resp = await library_scan.scan_files_impl(db, [epub, txt, missing])

    assert (resp.new_ebooks, resp.new_audiobooks, resp.multi_file_folders) == (1, 0, 0)
    assert "Scanned 3 file(s)" in resp.message
    assert no_abs == [], "no audiobook in the list, so the ABS index must not be fetched"
    assert (await db.execute(select(EBook.filename))).scalars().all() == ["Book.epub"]


async def test_targeted_scan_fetches_the_abs_index_once_when_an_audiobook_is_present(
    db, library_dirs, stub_extract, no_abs, monkeypatch
):
    _, audio_dir = library_dirs
    monkeypatch.setattr(library_scan.multi_file_audiobooks, "read_audio_tags", lambda p: {})
    a = _touch(audio_dir / "One" / "one.m4b")
    b = _touch(audio_dir / "Two" / "two.m4b")

    resp = await library_scan.scan_files_impl(db, [a, b])

    assert resp.new_audiobooks == 2
    assert no_abs == [1]


# ------------------------------------------------------ _ingest_one_ebook


async def test_an_existing_ebook_without_a_source_is_backfilled_but_a_curated_one_is_not(
    db, library_dirs, stub_extract
):
    ebook_dir, _ = library_dirs
    path = write_epub(str(ebook_dir / "Book.epub"), [("ch1.xhtml", CHAPTER)])
    stub_extract["Book.epub"] = {"title": "From File", "author": "Ann", "series": "S",
                                 "series_index": 2.0, "publisher": "P"}
    row = EBook(title="Typed By Hand", filename="Book.epub", file_path=path,
                metadata_source=None, publisher=None)
    db.add(row)
    await db.commit()

    assert await library_scan._ingest_one_ebook(db, path, str(ebook_dir)) is False
    await db.commit()
    await db.refresh(row)
    assert (row.title, row.author, row.metadata_source) == ("From File", "Ann", "stub")
    assert (row.series, row.series_index, row.publisher) == ("S", 2.0, "P")

    # A row that already has a source keeps its title; only gaps are filled.
    row.title = "Curated"
    row.metadata_source = "manual"
    row.publisher = None
    await db.commit()
    stub_extract["Book.epub"]["publisher"] = "Filled"

    await library_scan._ingest_one_ebook(db, path, str(ebook_dir))
    await db.commit()
    await db.refresh(row)
    assert row.title == "Curated"
    assert row.publisher == "Filled"


async def test_a_file_that_vanishes_before_hashing_is_skipped(db, library_dirs, stub_extract, monkeypatch):
    ebook_dir, _ = library_dirs
    path = write_epub(str(ebook_dir / "Book.epub"), [("ch1.xhtml", CHAPTER)])

    def _gone(_path):
        raise OSError("vanished")

    monkeypatch.setattr(library_scan, "_hash_and_size", _gone)

    assert await library_scan._ingest_one_ebook(db, path, str(ebook_dir)) is False
    assert (await db.execute(select(EBook))).scalars().all() == []


async def test_a_cover_extraction_failure_never_fails_the_ingest(db, library_dirs, stub_extract, monkeypatch, caplog):
    ebook_dir, _ = library_dirs
    path = write_epub(str(ebook_dir / "Book.epub"), [("ch1.xhtml", CHAPTER)])

    def _boom(*a, **k):
        raise RuntimeError("cover exploded")

    monkeypatch.setattr(library_scan, "_extract_and_save_cover", _boom)

    assert await library_scan._ingest_one_ebook(db, path, str(ebook_dir)) is True
    assert "Error extracting cover for new ebook" in caplog.text


# -------------------------------------------------- _ingest_one_audiobook


async def test_an_existing_audiobook_takes_the_files_duration_and_fills_gaps(db, library_dirs, stub_extract):
    _, audio_dir = library_dirs
    path = _touch(audio_dir / "book.m4b")
    stub_extract["book.m4b"] = {"title": "T", "duration_seconds": 7200, "narrators": "N",
                                "series": "S", "series_index": 1.0}
    row = AudioBook(title="T", filename="book.m4b", file_path=path, metadata_source="manual",
                    duration_seconds=3600, series=None)
    db.add(row)
    await db.commit()

    assert await library_scan._ingest_one_audiobook(db, path, str(audio_dir), {}) is False
    await db.commit()
    await db.refresh(row)
    assert row.duration_seconds == 7200
    assert row.narrators == "N"
    assert (row.series, row.series_index) == ("S", 1.0)


async def test_abs_enrichment_respects_a_user_cleared_series_and_writes_tags_when_changed(
    db, library_dirs, stub_extract, monkeypatch
):
    _, audio_dir = library_dirs
    path = _touch(audio_dir / "book.m4b")
    row = AudioBook(title="T", filename="book.m4b", file_path=path, metadata_source="manual",
                    series="")  # "" means the user cleared it
    db.add(row)
    await db.commit()

    seen = {}

    def _enrich(meta, filepath, abs_index, prefix, force=False):
        return {**meta, "series": "From ABS", "series_index": 3.0, "publisher": "ABS Pub"}, True, True

    def _write(filepath, meta):
        seen["written"] = dict(meta)
        return True, None

    monkeypatch.setattr(library_scan, "enrich_from_abs", _enrich)
    monkeypatch.setattr(library_scan, "write_metadata_to_file", _write)

    await library_scan._ingest_one_audiobook(db, path, str(audio_dir), {"x": {}})
    await db.commit()
    await db.refresh(row)

    assert "series" not in seen["written"] and "series_index" not in seen["written"]
    assert row.series == ""            # still cleared
    assert row.publisher == "ABS Pub"  # the rest of the enrichment landed


async def test_a_new_audiobook_is_enriched_from_abs_before_insert(db, library_dirs, stub_extract, monkeypatch):
    _, audio_dir = library_dirs
    path = _touch(audio_dir / "book.m4b")
    monkeypatch.setattr(
        library_scan, "enrich_from_abs",
        lambda meta, fp, idx, prefix, force=False: ({**meta, "narrators": "ABS Narrator"}, True, True),
    )
    monkeypatch.setattr(library_scan, "write_metadata_to_file", lambda fp, meta: (True, None))

    assert await library_scan._ingest_one_audiobook(db, path, str(audio_dir), {"x": {}}) is True
    await db.commit()
    row = (await db.execute(select(AudioBook))).scalar_one()
    assert row.narrators == "ABS Narrator"


# ------------------------------------------------------- ABS settings load


async def test_the_abs_index_is_only_fetched_when_abs_is_enabled_and_configured(db, monkeypatch):
    calls = []
    monkeypatch.setattr(library_scan, "fetch_abs_index", lambda url, token, prefix: calls.append((url, token, prefix)) or {"k": {}})

    async def _off(db_):
        return False, "http://abs.example", "tok", "/p"

    monkeypatch.setattr(library_scan, "_load_abs_settings", _off)
    assert await library_scan._maybe_load_abs_index(db) == {}
    assert calls == []

    async def _on(db_):
        return True, "http://abs.example", "tok", "/p"

    monkeypatch.setattr(library_scan, "_load_abs_settings", _on)
    assert await library_scan._maybe_load_abs_index(db) == {"k": {}}
    assert calls == [("http://abs.example", "tok", "/p")]


async def test_load_abs_settings_reads_the_flag_url_prefix_and_token(db, monkeypatch):
    from models.settings import SystemSetting

    db.add_all([
        SystemSetting(key="abs_enabled", value="TRUE"),
        SystemSetting(key="abs_url", value="http://abs.example"),
        SystemSetting(key="abs_audiobooks_prefix", value="/audiobooks"),
    ])
    await db.commit()

    async def _token(db_):
        return "secret"

    monkeypatch.setattr(abs_metadata, "get_abs_token", _token)

    assert await abs_metadata.load_abs_settings(db) == (True, "http://abs.example", "secret", "/audiobooks")


async def test_load_abs_settings_defaults_to_disabled_and_empty(db, monkeypatch):
    async def _no_token(db_):
        return None

    monkeypatch.setattr(abs_metadata, "get_abs_token", _no_token)

    assert await abs_metadata.load_abs_settings(db) == (False, "", "", "")
