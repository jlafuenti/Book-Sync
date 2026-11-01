"""Series tags on audiobook write-back (issue #886), against real files.

Readarr-style managers identify an audiobook by its album tag. Tandem used to
write the series there (and the series index into the track number) for MP3 and
FLAC/Ogg, so a folder watcher re-identified a freshly paired file as another
book in the same series. The series belongs in grouping and SERIES /
SERIES-PART tags; the album is the book's title, and clearing a series must
leave it alone.

The files are built by hand (a few bare MPEG frames, a bare FLAC STREAMINFO, a
bare MP4 `moov`) so that mutagen reads and writes real tags without ffmpeg.
"""

import struct
from types import SimpleNamespace

import mutagen
import pytest
from mutagen.id3 import TALB, TIT1, TIT2, TRCK

from services import tag_writer
from services.metadata_extract import _read_embedded_metadata


def _mp3_bytes():
    # MPEG-1 Layer III, 128 kbps, 44.1 kHz, unpadded: 417-byte frames.
    return (b"\xff\xfb\x90\x64" + b"\x00" * 413) * 20


def _flac_bytes():
    info = struct.pack(">HH", 4096, 4096) + b"\x00" * 6
    packed = (44100 << 44) | (1 << 41) | (15 << 36) | 44100
    info += packed.to_bytes(8, "big") + b"\x00" * 16
    return b"fLaC" + bytes([0x80]) + len(info).to_bytes(3, "big") + info


def _box(kind, payload):
    return struct.pack(">I", 8 + len(payload)) + kind + payload


def _m4b_bytes():
    mvhd = _box(b"mvhd", b"\x00" * 12 + struct.pack(">II", 1000, 5000) + b"\x00" * 80)
    return (_box(b"ftyp", b"M4B \x00\x00\x00\x00M4B mp42isom")
            + _box(b"moov", mvhd) + _box(b"mdat", b"\x00" * 16))


_BYTES = {"mp3": _mp3_bytes, "flac": _flac_bytes, "m4b": _m4b_bytes}


def _file(tmp_path, ext, **tags):
    """A real file of `ext`, pre-tagged with `album`, `title`, `track`,
    `grouping` where given."""
    path = tmp_path / f"book.{ext}"
    path.write_bytes(_BYTES[ext]())
    audio = mutagen.File(str(path))
    if ext == "mp3":
        audio.add_tags()
        frames = {"album": TALB, "title": TIT2, "track": TRCK, "grouping": TIT1}
        for key, value in tags.items():
            audio.tags.add(frames[key](encoding=3, text=value))
    elif ext == "flac":
        names = {"album": "album", "title": "title", "track": "tracknumber",
                 "grouping": "grouping"}
        for key, value in tags.items():
            audio[names[key]] = [value]
    else:
        names = {"album": "\xa9alb", "title": "\xa9nam", "grouping": "\xa9grp"}
        for key, value in tags.items():
            audio[names[key]] = [value]
    audio.save()
    return str(path)


def _book(**fields):
    base = dict(
        title=None, author=None, series=None, series_index=None, description=None,
        publisher=None, language=None, publish_year=None, genres=None,
    )
    base.update(fields)
    return SimpleNamespace(**base)


def _album(path):
    audio = mutagen.File(path)
    if type(audio).__name__ == "MP3":
        return str(audio.tags["TALB"]) if "TALB" in audio.tags else None
    key = "\xa9alb" if type(audio).__name__ == "MP4" else "album"
    return audio[key][0] if key in audio else None


def _track(path):
    audio = mutagen.File(path)
    if type(audio).__name__ == "MP3":
        return str(audio.tags["TRCK"]) if "TRCK" in audio.tags else None
    return audio["tracknumber"][0] if "tracknumber" in audio else None


def _read(path):
    meta = _read_embedded_metadata(path, "audiobook")
    return meta.get("series"), meta.get("series_index")


@pytest.mark.parametrize("ext", ["mp3", "flac"])
def test_series_goes_to_grouping_and_the_album_is_the_title(tmp_path, ext):
    path = _file(tmp_path, ext, album="Ripper Album")

    tag_writer.write_audiobook_metadata(path, _book(title="Book Two", series="Saga", series_index=2.0))

    assert _album(path) == "Book Two"
    assert _read(path) == ("Saga", 2.0)


@pytest.mark.parametrize("ext", ["mp3", "flac"])
def test_series_index_is_not_written_to_the_track_number(tmp_path, ext):
    path = _file(tmp_path, ext, track="1/12")

    tag_writer.write_audiobook_metadata(path, _book(title="Book Two", series="Saga", series_index=2.0))

    assert _track(path) == "1/12"


def test_mp3_writes_grouping_and_txxx_series_frames(tmp_path):
    path = _file(tmp_path, "mp3", title="Old")

    tag_writer.write_audiobook_metadata(path, _book(title="Book Two", series="Saga", series_index=2.0))

    tags = mutagen.File(path).tags
    assert str(tags["TIT1"]) == "Saga #2"
    assert str(tags["TXXX:SERIES"]) == "Saga"
    assert str(tags["TXXX:SERIES-PART"]) == "2"


def test_flac_writes_grouping_and_series_comments(tmp_path):
    path = _file(tmp_path, "flac", title="Old")

    tag_writer.write_audiobook_metadata(path, _book(title="Book Two", series="Saga", series_index=2.0))

    audio = mutagen.File(path)
    assert audio["grouping"] == ["Saga #2"]
    assert audio["series"] == ["Saga"]
    assert audio["series-part"] == ["2"]


@pytest.mark.parametrize("ext", ["mp3", "flac", "m4b"])
def test_clearing_the_series_survives_a_rescan_and_keeps_the_album(tmp_path, ext):
    path = _file(tmp_path, ext, album="Book Two")
    tag_writer.write_audiobook_metadata(path, _book(title="Book Two", series="Saga", series_index=2.0))

    tag_writer.write_audiobook_metadata(path, _book(title="Book Two", series=""))

    assert _album(path) == "Book Two"
    assert _read(path)[0] is None


def test_m4b_clearing_the_series_leaves_an_unrelated_album_alone(tmp_path):
    path = _file(tmp_path, "m4b", album="Collected Edition")

    tag_writer.write_audiobook_metadata(path, _book(title="Book Two", series=""))

    assert _album(path) == "Collected Edition"


@pytest.mark.parametrize("ext", ["mp3", "flac"])
def test_an_album_that_is_the_title_is_not_read_as_a_series(tmp_path, ext):
    path = _file(tmp_path, ext, title="Book Two", album="Book Two")

    assert _read(path) == (None, None)


@pytest.mark.parametrize("ext", ["mp3", "flac"])
def test_a_file_written_before_the_fix_keeps_its_series(tmp_path, ext):
    """Older write-backs left the series in the album and nothing in
    grouping; a rescan must still find it."""
    path = _file(tmp_path, ext, title="Book Two", album="Saga")

    assert _read(path)[0] == "Saga"


@pytest.mark.parametrize("ext", ["mp3", "flac"])
def test_a_file_already_hand_fixed_reads_its_series_from_grouping(tmp_path, ext):
    path = _file(tmp_path, ext, title="Book Two", album="Book Two", grouping="Saga #2")

    assert _read(path) == ("Saga", 2.0)


@pytest.mark.parametrize("ext", ["mp3", "flac"])
def test_rewriting_a_file_from_before_the_fix_moves_the_series_off_the_album(tmp_path, ext):
    path = _file(tmp_path, ext, title="Book Two", album="Saga")

    tag_writer.write_audiobook_metadata(path, _book(title="Book Two", series="Saga", series_index=2.0))

    assert _album(path) == "Book Two"
    assert _read(path) == ("Saga", 2.0)


@pytest.mark.parametrize("ext", ["mp3", "flac", "m4b"])
def test_a_fractional_series_index_survives_the_round_trip(tmp_path, ext):
    """A novella numbered 3.5 must not come back as book 3: the rescan reads
    the index from these tags and would overwrite the stored one."""
    path = _file(tmp_path, ext, title="Old")

    tag_writer.write_audiobook_metadata(path, _book(title="Interlude", series="Saga", series_index=3.5))

    assert _read(path) == ("Saga", 3.5)
