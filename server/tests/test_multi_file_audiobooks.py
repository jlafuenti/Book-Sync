"""
Multi-file audiobooks are detected, not imported, and surfaced in Troubleshoot
Library (issue #63).

`AudioBook` has one `file_path`; a folder of per-track MP3s for one book is not
representable. The scanner used to import each track as its own audiobook. Now:

- `services.multi_file_audiobooks.classify_folder` flags a folder's same-extension
  group of ≥2 audio files when the files share an album tag, or (untagged) all
  look like tracks. Distinct albums in a flat folder are distinct books.
- `scan_library_impl` skips flagged files (zero `AudioBook` rows), records the
  folder in `multi_file_audiobook_folders`, and clears the row once the folder
  no longer qualifies (merged .m4b + rescan).
- Troubleshoot lists the folders with a fixed remediation; Dismiss survives
  rescans until the folder's contents change; "Remove imported tracks" deletes
  the AudioBook rows already imported from that folder (files untouched).
"""

import os

import pytest
from sqlalchemy import select

from config import settings
from models.book import AudioBook, BookPair, EBook, PairStatus
from models.library_issue import MultiFileAudiobookFolder
from services.multi_file_audiobooks import (
    classify_folder, looks_like_track_name,
)


# ---------------------------------------------------------------------------
# The classifier (pure)
# ---------------------------------------------------------------------------

def _tags(mapping):
    """A `read_tags` that answers from a {basename: {tag: value}} dict."""
    def _read(path):
        return mapping.get(os.path.basename(path), {})
    return _read


def _touch(folder, names, size=3):
    folder.mkdir(parents=True, exist_ok=True)
    for n in names:
        (folder / n).write_bytes(b"x" * size)
    return [str(folder / n) for n in names]


def test_track_name_heuristic():
    for name in ["01.mp3", "01 - Intro.mp3", "Track 07.mp3", "Part 3.mp3", "CD1 - 05.mp3",
                 "Chapter 12.mp3", "Dune - 07.mp3", "Dune 07.mp3", "Dune-03.mp3", "ch03.mp3"]:
        assert looks_like_track_name(name), name
    for name in ["Dune.m4b", "Dune Messiah.m4b", "1984.mp3", "Catch22.mp3", "The 39 Steps.mp3"]:
        assert not looks_like_track_name(name), name


def test_shared_album_tag_flags_the_group(tmp_path):
    folder = tmp_path / "Herbert" / "Dune"
    names = ["Dune.mp3", "Dune Messiah.mp3", "Children.mp3"]   # no track pattern at all
    _touch(folder, names)

    groups = classify_folder(str(folder), names, _tags({
        n: {"album": "Dune (Unabridged)", "albumartist": "Frank Herbert"} for n in names
    }))

    assert len(groups) == 1
    g = groups[0]
    assert g.extension == ".mp3"
    assert g.file_count == 3
    assert g.total_size == 9
    assert g.guessed_title == "Dune (Unabridged)"
    assert g.guessed_author == "Frank Herbert"
    assert {f.name for f in g.files} == set(names)


def test_differing_album_tags_are_distinct_books_not_a_multi_file_folder(tmp_path):
    folder = tmp_path / "flat"
    names = ["Mistborn 1.m4b", "Mistborn 2.m4b", "Mistborn 3.m4b"]  # track-looking names!
    _touch(folder, names)

    groups = classify_folder(str(folder), names, _tags({
        "Mistborn 1.m4b": {"album": "The Final Empire"},
        "Mistborn 2.m4b": {"album": "The Well of Ascension"},
        "Mistborn 3.m4b": {"album": "The Hero of Ages"},
    }))

    assert groups == []


def test_untagged_tracks_are_flagged_by_name_pattern(tmp_path):
    folder = tmp_path / "Herbert" / "Dune"
    names = [f"{i:02d}.mp3" for i in range(1, 13)]
    _touch(folder, names)

    groups = classify_folder(str(folder), names, _tags({}))

    assert len(groups) == 1
    assert groups[0].file_count == 12
    # No tags: the folder name is the best title guess, its parent the author.
    assert groups[0].guessed_title == "Dune"
    assert groups[0].guessed_author == "Herbert"


def test_untagged_files_without_track_names_are_not_flagged(tmp_path):
    folder = tmp_path / "flat"
    names = ["Dune.m4b", "Dune Messiah.m4b"]
    _touch(folder, names)
    assert classify_folder(str(folder), names, _tags({})) == []


def test_a_single_audio_file_is_never_flagged(tmp_path):
    folder = tmp_path / "Herbert" / "Dune"
    names = ["01.mp3"]
    _touch(folder, names)
    assert classify_folder(str(folder), names, _tags({"01.mp3": {"album": "Dune"}})) == []


def test_only_the_qualifying_extension_group_is_flagged(tmp_path):
    """A merged .m4b beside leftover MP3 tracks: the .m4b imports normally, the
    MP3 group stays flagged."""
    folder = tmp_path / "Herbert" / "Dune"
    names = ["Dune.m4b", "01.mp3", "02.mp3", "03.mp3"]
    _touch(folder, names)

    groups = classify_folder(str(folder), names, _tags({}))

    assert [g.extension for g in groups] == [".mp3"]
    assert {f.name for f in groups[0].files} == {"01.mp3", "02.mp3", "03.mp3"}


def test_the_parent_is_not_offered_as_author_when_it_is_the_library_root(tmp_path):
    root = tmp_path / "audiobooks"
    folder = root / "Dune"
    names = ["01.mp3", "02.mp3"]
    _touch(folder, names)

    g = classify_folder(str(folder), names, _tags({}), library_root=str(root))[0]

    assert g.guessed_title == "Dune"
    assert g.guessed_author is None


def test_fingerprint_changes_with_the_folder_contents(tmp_path):
    folder = tmp_path / "Dune"
    names = ["01.mp3", "02.mp3"]
    _touch(folder, names)
    before = classify_folder(str(folder), names, _tags({}))[0].fingerprint

    _touch(folder, ["03.mp3"])
    after = classify_folder(str(folder), names + ["03.mp3"], _tags({}))[0].fingerprint

    assert before != after


# ---------------------------------------------------------------------------
# The scanner
# ---------------------------------------------------------------------------

@pytest.fixture
def library_dirs(monkeypatch, tmp_path):
    from services import library_scan
    from services import multi_file_audiobooks as mfa

    ebook_dir = tmp_path / "ebooks"
    audio_dir = tmp_path / "audiobooks"
    ebook_dir.mkdir()
    audio_dir.mkdir()
    monkeypatch.setattr(settings, "ebook_dir", str(ebook_dir))
    monkeypatch.setattr(settings, "audiobook_dir", str(audio_dir))

    async def _fake_extract(filepath, file_type, db, library_root=None):
        return {"title": os.path.splitext(os.path.basename(filepath))[0],
                "author": None, "series": None, "series_index": None}

    async def _no_abs(db):
        return {}

    monkeypatch.setattr(library_scan, "extract_metadata", _fake_extract)
    monkeypatch.setattr(library_scan, "_maybe_load_abs_index", _no_abs)
    monkeypatch.setattr(library_scan, "_extract_and_save_cover", lambda *a, **k: None)
    # Fixture files carry no tags; the scanner's tag reader must not choke on them.
    monkeypatch.setattr(mfa, "read_audio_tags", lambda path: {})
    return ebook_dir, audio_dir


async def _scan(db):
    from routers.library import scan_library_impl
    resp = await scan_library_impl(db)
    await db.commit()
    return resp


async def _folder_rows(db):
    return (await db.execute(select(MultiFileAudiobookFolder))).scalars().all()


async def test_scan_flags_a_track_folder_and_imports_no_rows_for_it(db, library_dirs):
    _, audio_dir = library_dirs
    _touch(audio_dir / "Herbert" / "Dune", [f"{i:02d}.mp3" for i in range(1, 13)])
    _touch(audio_dir / "Herbert" / "Dune Messiah", ["Dune Messiah.m4b"])

    resp = await _scan(db)

    audiobooks = (await db.execute(select(AudioBook))).scalars().all()
    assert [a.title for a in audiobooks] == ["Dune Messiah"]     # zero rows for the tracks
    rows = await _folder_rows(db)
    assert len(rows) == 1
    assert rows[0].folder_path == str(audio_dir / "Herbert" / "Dune")
    assert rows[0].file_count == 12 and rows[0].extension == ".mp3"
    assert rows[0].dismissed is False
    assert resp.multi_file_folders == 1
    assert resp.new_audiobooks == 1


async def test_flagged_tracks_never_reach_auto_pairing(db, library_dirs):
    ebook_dir, audio_dir = library_dirs
    _touch(audio_dir / "Dune", [f"Dune - {i:02d}.mp3" for i in range(1, 4)])
    db.add(EBook(title="Dune", author="Frank Herbert", filename="dune.epub", file_path="/x/dune.epub"))
    await db.commit()

    await _scan(db)

    assert (await db.execute(select(BookPair))).scalars().all() == []


async def test_replacing_the_folder_with_a_merged_file_clears_the_flag_on_rescan(db, library_dirs):
    _, audio_dir = library_dirs
    folder = audio_dir / "Dune"
    _touch(folder, [f"{i:02d}.mp3" for i in range(1, 4)])
    await _scan(db)
    assert len(await _folder_rows(db)) == 1

    for f in folder.iterdir():
        f.unlink()
    _touch(folder, ["Dune.m4b"])

    await _scan(db)

    assert await _folder_rows(db) == []
    audiobooks = (await db.execute(select(AudioBook))).scalars().all()
    assert [a.title for a in audiobooks] == ["Dune"]


async def test_dismissal_survives_a_rescan_until_the_folder_changes(db, library_dirs):
    _, audio_dir = library_dirs
    folder = audio_dir / "Dune"
    _touch(folder, [f"{i:02d}.mp3" for i in range(1, 4)])
    await _scan(db)
    row = (await _folder_rows(db))[0]
    row.dismissed = True
    await db.commit()

    await _scan(db)
    row = (await _folder_rows(db))[0]
    assert row.dismissed is True

    _touch(folder, ["04.mp3"])       # contents changed → worth a second look
    await _scan(db)
    row = (await _folder_rows(db))[0]
    assert row.dismissed is False
    assert row.file_count == 4


async def test_targeted_scan_skips_a_file_inside_a_flagged_folder(db, library_dirs):
    from routers.library import scan_files_impl

    _, audio_dir = library_dirs
    paths = _touch(audio_dir / "Dune", [f"{i:02d}.mp3" for i in range(1, 4)])

    resp = await scan_files_impl(db, [paths[0]])
    await db.commit()

    assert (await db.execute(select(AudioBook))).scalars().all() == []
    assert len(await _folder_rows(db)) == 1
    assert resp.new_audiobooks == 0


# ---------------------------------------------------------------------------
# Troubleshoot: listing, dismiss, remove imported tracks
# ---------------------------------------------------------------------------

@pytest.fixture
async def ts_client(make_client, make_user, auth_header):
    from routers import troubleshoot
    user = await make_user(role="admin")
    async with make_client(troubleshoot.router) as c:
        yield c, auth_header(user)


async def _seed_folder(db, folder, *, dismissed=False, imported=0):
    row = MultiFileAudiobookFolder(
        folder_path=folder, extension=".mp3", file_count=12, total_size=1234,
        guessed_title="Dune", guessed_author="Frank Herbert",
        fingerprint="abc", dismissed=dismissed,
    )
    db.add(row)
    for i in range(imported):
        db.add(AudioBook(title=f"Track {i}", filename=f"{i:02d}.mp3",
                         file_path=os.path.join(folder, f"{i:02d}.mp3")))
    await db.commit()
    await db.refresh(row)
    return row


async def test_issues_lists_multi_file_folders_with_the_imported_track_count(db, ts_client):
    c, headers = ts_client
    row = await _seed_folder(db, "/data/audiobooks/Herbert/Dune", imported=2)
    # A track in another folder must not be counted, nor a same-prefix sibling folder.
    db.add(AudioBook(title="Other", filename="o.mp3", file_path="/data/audiobooks/Herbert/Dune Messiah/01.mp3"))
    await db.commit()

    body = (await c.get("/api/troubleshoot/issues", headers=headers)).json()

    cat = body["categories"]["multi_file_audiobook"]
    assert body["counts"]["multi_file_audiobook"] == 1
    assert cat[0]["item_type"] == "folder"
    assert cat[0]["item_id"] == row.id
    assert cat[0]["title"] == "Dune"
    assert cat[0]["author"] == "Frank Herbert"
    assert cat[0]["file_path"] == "/data/audiobooks/Herbert/Dune"
    assert cat[0]["file_size"] == 1234
    assert cat[0]["file_count"] == 12
    assert cat[0]["extension"] == ".mp3"
    assert cat[0]["imported_track_count"] == 2
    assert "12 .mp3 files" in cat[0]["detail"]


async def test_dismissed_folders_are_not_listed(db, ts_client):
    c, headers = ts_client
    await _seed_folder(db, "/data/audiobooks/Dune", dismissed=True)

    body = (await c.get("/api/troubleshoot/issues", headers=headers)).json()

    assert body["categories"]["multi_file_audiobook"] == []


async def test_dismiss_endpoint_marks_the_row(db, ts_client):
    c, headers = ts_client
    row = await _seed_folder(db, "/data/audiobooks/Dune")

    resp = await c.post(f"/api/troubleshoot/multi-file/{row.id}/dismiss", headers=headers)

    assert resp.status_code == 200
    await db.refresh(row)
    assert row.dismissed is True
    assert (await c.post("/api/troubleshoot/multi-file/9999/dismiss", headers=headers)).status_code == 404


async def test_remove_tracks_deletes_only_that_folders_rows_and_their_pairs(db, ts_client, tmp_path):
    c, headers = ts_client
    folder = str(tmp_path / "Dune")
    row = await _seed_folder(db, folder, imported=3)
    tracks = (await db.execute(select(AudioBook))).scalars().all()
    other = AudioBook(title="Other", filename="o.mp3", file_path=str(tmp_path / "Dune Messiah" / "o.mp3"))
    ebook = EBook(title="Dune", filename="d.epub", file_path="/x/d.epub")
    db.add_all([other, ebook])
    await db.flush()
    db.add(BookPair(ebook_id=ebook.id, audiobook_id=tracks[0].id, status=PairStatus.AUTO_MATCHED))
    await db.commit()
    # A real file on disk for one track: it must survive (DB rows only).
    os.makedirs(folder, exist_ok=True)
    kept_file = os.path.join(folder, "00.mp3")
    open(kept_file, "wb").write(b"x")

    resp = await c.post(f"/api/troubleshoot/multi-file/{row.id}/remove-tracks", headers=headers)

    assert resp.status_code == 200
    assert resp.json()["deleted"] == 3
    remaining = (await db.execute(select(AudioBook))).scalars().all()
    assert [a.title for a in remaining] == ["Other"]
    assert (await db.execute(select(BookPair))).scalars().all() == []
    assert os.path.exists(kept_file)
    # The folder row itself stays: the tracks are still on disk, still unmerged.
    assert len(await _folder_rows(db)) == 1


async def test_a_merged_file_beside_the_tracks_is_neither_counted_nor_removed(db, ts_client, tmp_path):
    """The remediation leaves `Book.m4b` next to leftover MP3 tracks (until the
    user deletes them). Only rows of the flagged group's extension are tracks —
    the merged file's row must survive Remove imported tracks."""
    c, headers = ts_client
    folder = str(tmp_path / "Dune")
    row = await _seed_folder(db, folder, imported=2)          # 2 .mp3 track rows
    db.add(AudioBook(title="Dune (merged)", filename="Dune.m4b",
                     file_path=os.path.join(folder, "Dune.m4b"), format="m4b"))
    await db.commit()

    body = (await c.get("/api/troubleshoot/issues", headers=headers)).json()
    assert body["categories"]["multi_file_audiobook"][0]["imported_track_count"] == 2

    resp = await c.post(f"/api/troubleshoot/multi-file/{row.id}/remove-tracks", headers=headers)

    assert resp.json()["deleted"] == 2
    remaining = (await db.execute(select(AudioBook))).scalars().all()
    assert [a.title for a in remaining] == ["Dune (merged)"]


async def test_troubleshoot_fixes_are_editor_gated(db, make_client, make_user, auth_header):
    from routers import troubleshoot
    row = await _seed_folder(db, "/data/audiobooks/Dune")
    viewer = await make_user(username="viewer", role="user")
    async with make_client(troubleshoot.router) as c:
        assert (await c.post(f"/api/troubleshoot/multi-file/{row.id}/dismiss",
                             headers=auth_header(viewer))).status_code == 403
