"""`services.mp4_open` -- read and write tags around a `chpl` chapter atom whose
titles are not valid UTF-8 (issue #935).

mutagen parses the legacy Nero chapter atom while opening the file, so one bad
title used to abort the open: the tag writer wrote nothing, the scan dropped
the embedded tags and cover, and only the Audiobookshelf write-back recovered,
by editing the file. These tests run against a real container built by
`tests.factories.write_m4a_with_bad_chapter_title`.
"""

from types import SimpleNamespace

import mutagen
import mutagen.mp4
import pytest

from config import settings
from services import abs_metadata, chapter_repair, mp4_open, tag_writer
from services.metadata_extract import _extract_and_save_cover, _read_embedded_metadata
from tests.factories import (
    BAD_CHPL_PAYLOAD,
    write_m4a_with_bad_chapter_title,
    write_minimal_mp3,
)


@pytest.fixture
def bad_m4b(tmp_path):
    return write_m4a_with_bad_chapter_title(tmp_path / "book.m4b")


@pytest.fixture
def covers_dir(monkeypatch, tmp_path):
    path = tmp_path / "covers"
    monkeypatch.setattr(settings, "covers_dir", str(path))
    return path


def _seed(path, **tags):
    audio = mp4_open.MP4(path)
    for key, value in tags.items():
        audio[key] = value
    audio.save()


def _book(**kw):
    fields = dict(
        title="T", author="A", series_list=None, description=None,
        publisher=None, language=None, publish_year=None, genres=None,
    )
    fields.update(kw)
    return SimpleNamespace(**fields)


def test_the_fixture_reproduces_the_production_error(bad_m4b):
    with pytest.raises(mutagen.mp4.MP4MetadataError) as excinfo:
        mutagen.mp4.MP4(bad_m4b)

    assert chapter_repair.CHAPTER_TITLE_ERROR_RE.search(str(excinfo.value))


def test_open_audio_reads_the_file_without_its_chapters(bad_m4b):
    audio = mp4_open.open_audio(bad_m4b)

    assert type(audio).__name__ == "MP4"
    assert not audio.chapters
    assert audio.tags is not None


def test_troubleshoot_still_sees_the_file_as_faulty(bad_m4b):
    ok, detail = chapter_repair.check_chapter_encoding(bad_m4b)

    assert ok is False
    assert "chapter 0 title" in detail


def test_a_file_without_the_fault_opens_as_mutagen_would(tmp_path):
    path = write_minimal_mp3(tmp_path / "book.mp3")

    audio = mp4_open.open_audio(path)

    assert type(audio).__name__ == "MP3"


def test_an_unreadable_file_behaves_as_mutagen_does(tmp_path):
    path = tmp_path / "garbage.m4b"
    path.write_bytes(bytes(range(256)) * 4)

    try:
        expected = mutagen.File(str(path))
    except Exception as e:
        with pytest.raises(type(e)):
            mp4_open.open_audio(str(path))
    else:
        assert mp4_open.open_audio(str(path)) == expected


def test_the_writer_tags_the_file_and_leaves_the_chapter_atom_alone(bad_m4b):
    _seed(bad_m4b, **{"\xa9nam": ["Old"]})
    before = open(bad_m4b, "rb").read()
    assert before.count(BAD_CHPL_PAYLOAD) == 1

    tag_writer.write_audiobook_metadata(
        bad_m4b, _book(series_list=[{"name": "Saga", "index": 2.0}]),
    )

    after = open(bad_m4b, "rb").read()
    assert after.count(b"chpl") == 1
    assert after.count(BAD_CHPL_PAYLOAD) == 1
    audio = mp4_open.open_audio(bad_m4b)
    assert audio["\xa9nam"] == ["T"]
    assert audio["\xa9grp"] == ["Saga #2"]
    assert "----:com.apple.iTunes:SERIES" in audio
    assert "----:com.apple.iTunes:SERIES-PART" in audio


def test_the_scan_reads_the_embedded_tags(bad_m4b):
    _seed(bad_m4b, **{"\xa9nam": ["Bartleby"], "\xa9ART": ["Herman Melville"]})
    tag_writer.write_audiobook_metadata(
        bad_m4b, _book(title="Bartleby", author="Herman Melville",
                       series_list=[{"name": "Saga", "index": 2.0}]),
    )

    meta = _read_embedded_metadata(bad_m4b, "audiobook")

    assert meta["title"] == "Bartleby"
    assert meta["author"] == "Herman Melville"
    assert meta["series_list"] == [{"name": "Saga", "index": 2.0}]


def test_the_scan_extracts_the_cover(bad_m4b, covers_dir):
    _seed(bad_m4b, covr=[mutagen.mp4.MP4Cover(b"JPEG-bytes", mutagen.mp4.MP4Cover.FORMAT_JPEG)])

    url = _extract_and_save_cover(bad_m4b, "audiobook", 1, "T")

    assert url == "/api/files/covers/T_1.jpg"
    assert (covers_dir / "T_1.jpg").read_bytes() == b"JPEG-bytes"


def test_the_abs_write_back_writes_without_repairing_the_file(bad_m4b, monkeypatch):
    def _no_repair(path):
        raise AssertionError("must not repair")

    monkeypatch.setattr(chapter_repair, "repair_chapter_encoding", _no_repair)
    before = open(bad_m4b, "rb").read()
    assert before.count(BAD_CHPL_PAYLOAD) == 1

    assert abs_metadata.write_metadata_to_file(bad_m4b, {"title": "T"}) == (True, None)

    after = open(bad_m4b, "rb").read()
    assert after.count(b"chpl") == 1
    assert after.count(BAD_CHPL_PAYLOAD) == 1
    assert mp4_open.open_audio(bad_m4b)["\xa9nam"] == ["T"]
