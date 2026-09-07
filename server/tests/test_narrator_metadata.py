"""
Embedded narrator tags reach the DB, and the ABS write-back stops erasing them
(issue #135).

`extract_metadata` read the narrator out of an audiobook's own tags correctly
and then threw it away: the merge that copies `file_meta` into the returned
`meta` runs through a fixed field whitelist, and `"narrators"` was not on it.
`AudioBook.narrators` could therefore only ever be filled by Audiobookshelf,
never by the file — the one source that is always present. On prod, 229 of 298
files carried a narrator tag while three rows sat at `narrators IS NULL`,
exactly the ones ABS could not name.

The destructive half followed from the same gap. When ABS enrichment changes
any field, the merged `meta` is written back into the file. `enrich_from_abs`
only fills a key that is *missing*, and `meta["narrators"]` was always missing —
so for an item ABS has no `narratorName` for, the key stayed unset and
`_write_tags` deleted both the `\xa9wrt` atom and the
`----:com.apple.iTunes:NARRATOR` freeform atom from the user's own file.

The rules pinned here:
  - every narrator branch extract_metadata reads (`NARRATOR` freeform, `\xa9wrt`,
    `\xa9com`, `TCOM`, `composer`) survives the merge into `meta`;
  - a scan writes it onto a new row, and backfills a row that has none;
  - `_write_tags` leaves a tag alone when the caller has no opinion (`None`, or
    an absent key) and clears it only when the caller explicitly asks (`""`).
"""

import contextlib

import pytest
from sqlalchemy import select

from config import settings
from models.book import AudioBook
import routers.library as library
from services import abs_metadata, metadata_extract


# --------------------------------------------------------------------------
# Fake mutagen. Same approach as test_audio_duration.py: patch the mutagen
# entry point rather than ship binary audio fixtures. The class *name* matters —
# extract_metadata routes on `type(audio).__name__`, so the name chosen here is
# what selects the MP4-atom branch vs. the ID3/Vorbis branch.
# --------------------------------------------------------------------------

class _Info:
    def __init__(self, length):
        self.length = length


class _FakeAudio(dict):
    """Dict-like tags plus `.tags`/`.info`, which is all extract_metadata reads.

    Subclassing dict also reproduces the truthiness of the real thing: mutagen's
    `FileType` defines `__len__` as its tag count and no `__bool__`, so an
    untagged file is falsy in both, and `if audio:` really means "if this file
    has any tags".
    """

    def __init__(self, tags=None, length=None):
        super().__init__(tags or {})
        self.tags = self
        self.info = _Info(length) if length is not None else None


class MP4(_FakeAudio):
    pass


class MP3(_FakeAudio):
    pass


class FLAC(_FakeAudio):
    pass


@pytest.fixture
def fake_mutagen(monkeypatch):
    """Make `mutagen.File(...)` return a fake of the given class with the given tags."""
    import mutagen

    def _install(cls=MP4, tags=None, length=3600.0):
        audio = cls(tags or {}, length=length)
        monkeypatch.setattr(mutagen, "File", lambda path, **kw: audio)
        return audio

    return _install


@pytest.fixture(autouse=True)
def no_ffprobe(monkeypatch):
    """Keep these tests off ffprobe entirely — they are about tags, not length,
    and shelling out to a real binary over a fake file just adds latency."""
    monkeypatch.setattr(metadata_extract, "probe_duration_seconds", lambda path: None)


@pytest.fixture
def audio_library(monkeypatch, tmp_path):
    """A tmp audiobook dir bound to settings, plus a file inside it."""
    audio_dir = tmp_path / "audiobooks"
    audio_dir.mkdir()
    (tmp_path / "ebooks").mkdir()
    monkeypatch.setattr(settings, "audiobook_dir", str(audio_dir))
    monkeypatch.setattr(settings, "ebook_dir", str(tmp_path / "ebooks"))

    def _make(name="Successors Promise.m4b"):
        path = audio_dir / name
        path.write_bytes(b"not really audio")
        return path

    return _make


async def _extract(db, path):
    return await library.extract_metadata(
        str(path), "audiobook", db, library_root=settings.audiobook_dir
    )


# ---------- the narrator survives the merge into `meta` ----------

async def test_itunes_narrator_freeform_atom_reaches_meta(db, fake_mutagen, audio_library):
    """Prod row 1561, "Successor's Promise": the freeform atom is the
    tagger-written one, and the first branch extract_metadata tries."""
    path = audio_library()
    fake_mutagen(MP4, {
        "\xa9nam": ["Successor's Promise"],
        "----:com.apple.iTunes:NARRATOR": [b"Grant Cartwright, Hannah Norris"],
    })

    meta = await _extract(db, path)

    assert meta["narrators"] == "Grant Cartwright, Hannah Norris"


async def test_mp4_writer_atom_is_the_narrator_fallback(db, fake_mutagen, audio_library):
    """Prod row 1522, "High Voltage": `\xa9wrt` only, no freeform atom.

    Audiobook taggers park the narrator in the "writer"/composer atom because
    MP4 has no narrator field of its own.
    """
    path = audio_library()
    fake_mutagen(MP4, {
        "\xa9nam": ["High Voltage"],
        "\xa9wrt": ["Amanda Leigh Cobb, Jim Frangione"],
    })

    meta = await _extract(db, path)

    assert meta["narrators"] == "Amanda Leigh Cobb, Jim Frangione"


async def test_mp4_composer_atom_is_the_last_narrator_fallback(db, fake_mutagen, audio_library):
    path = audio_library()
    fake_mutagen(MP4, {"\xa9nam": ["Some Book"], "\xa9com": ["Simon Vance"]})

    meta = await _extract(db, path)

    assert meta["narrators"] == "Simon Vance"


async def test_the_freeform_atom_wins_over_the_writer_atom(db, fake_mutagen, audio_library):
    """Row 1561 carries both. The branches are an `elif` chain for a reason —
    pin the order so a refactor cannot quietly invert it."""
    path = audio_library()
    fake_mutagen(MP4, {
        "----:com.apple.iTunes:NARRATOR": [b"Grant Cartwright, Hannah Norris"],
        "\xa9wrt": ["Somebody Else"],
    })

    meta = await _extract(db, path)

    assert meta["narrators"] == "Grant Cartwright, Hannah Norris"


async def test_id3_tcom_reaches_meta(db, fake_mutagen, audio_library):
    """Prod row 1551, "Magia de Recluce": an MP3 whose narrator is in TCOM."""
    path = audio_library("Magia de Recluce.mp3")
    fake_mutagen(MP3, {"TIT2": "Magia de Recluce", "TCOM": "Kirby Heyborne"})

    meta = await _extract(db, path)

    assert meta["narrators"] == "Kirby Heyborne"


async def test_vorbis_composer_reaches_meta(db, fake_mutagen, audio_library):
    path = audio_library("Some Book.flac")
    fake_mutagen(FLAC, {"title": ["Some Book"], "composer": ["Kirby Heyborne"]})

    meta = await _extract(db, path)

    assert meta["narrators"] == "Kirby Heyborne"


async def test_no_narrator_tag_leaves_the_key_absent(db, fake_mutagen, audio_library):
    """The key has to be *absent*, not None: every consumer treats "the file
    told us nothing" as "leave the stored value alone", and `_write_tags` now
    reads an absent key as "do not touch the file's tag"."""
    path = audio_library()
    fake_mutagen(MP4, {"\xa9nam": ["Some Book"]})

    meta = await _extract(db, path)

    assert "narrators" not in meta


# ---------- writing it onto the row ----------

async def test_scanning_a_new_audiobook_stores_the_narrator(db, fake_mutagen, audio_library):
    path = audio_library()
    fake_mutagen(MP4, {
        "\xa9nam": ["Successor's Promise"],
        "----:com.apple.iTunes:NARRATOR": [b"Grant Cartwright, Hannah Norris"],
    })

    await library.scan_files_impl(db, [str(path)])
    await db.commit()

    row = (await db.execute(select(AudioBook))).scalar_one()
    assert row.narrators == "Grant Cartwright, Hannah Norris"


async def test_scanning_backfills_a_row_with_no_narrator(db, fake_mutagen, audio_library):
    """The backfill for the rows ABS could not name: they are already in the DB,
    so they take the *existing row* branch of the ingest and one ordinary scan
    fills them."""
    path = audio_library()
    fake_mutagen(MP4, {
        "\xa9nam": ["Successor's Promise"],
        "\xa9wrt": ["Grant Cartwright, Hannah Norris"],
    })
    db.add(AudioBook(title="Successor's Promise", filename=path.name,
                     file_path=str(path), format="m4b", narrators=None))
    await db.commit()

    await library.scan_files_impl(db, [str(path)])
    await db.commit()

    row = (await db.execute(select(AudioBook))).scalar_one()
    assert row.narrators == "Grant Cartwright, Hannah Norris"


# ---------- the write-back must not erase what it cannot see ----------

class _FakeMP4Write(dict):
    """Stand-in for the file `_write_tags` opens for writing."""

    def __init__(self, tags=None):
        super().__init__(tags or {})
        self.saved = False

    def save(self):
        self.saved = True


@pytest.fixture
def fake_mp4_file(monkeypatch, tmp_path):
    """Bind `mutagen.mp4.MP4(path)` to a fake seeded with the given tags, and
    hand the fake back so a test can assert on what survived the write."""
    import mutagen.mp4

    def _install(tags=None, name="book.m4b"):
        path = tmp_path / name
        path.write_bytes(b"")
        audio = _FakeMP4Write(tags)
        monkeypatch.setattr(mutagen.mp4, "MP4", lambda p: audio)
        return path, audio

    return _install


def test_write_tags_leaves_a_tag_alone_when_the_caller_has_no_opinion(fake_mp4_file):
    """The erasure regression, at its narrowest.

    Deleting a tag because the caller happened not to mention it is what turned
    a read gap into data loss: the whitelist dropped the narrator on the way in,
    so the write-back saw no narrator and destroyed the atoms in the user's file.
    An absent key means "no opinion", and no opinion must not be destructive.
    """
    path, audio = fake_mp4_file({
        "\xa9wrt": ["Grant Cartwright, Hannah Norris"],
        "----:com.apple.iTunes:NARRATOR": [b"Grant Cartwright, Hannah Norris"],
    })

    ok, error = abs_metadata.write_metadata_to_file(
        str(path), {"title": "Successor's Promise"}  # no "narrators" key at all
    )

    assert (ok, error) == (True, None)
    assert audio["\xa9wrt"] == ["Grant Cartwright, Hannah Norris"]
    assert audio["----:com.apple.iTunes:NARRATOR"] == [b"Grant Cartwright, Hannah Norris"]


def test_write_tags_clears_a_tag_the_caller_explicitly_empties(fake_mp4_file):
    """The other half of the same rule: a key that is present but empty is an
    explicit "make this blank", and still clears the tag."""
    path, audio = fake_mp4_file({
        "\xa9wrt": ["Grant Cartwright"],
        "----:com.apple.iTunes:NARRATOR": [b"Grant Cartwright"],
    })

    abs_metadata.write_metadata_to_file(
        str(path), {"title": "Successor's Promise", "narrators": ""}
    )

    assert "\xa9wrt" not in audio
    assert "----:com.apple.iTunes:NARRATOR" not in audio


def test_write_tags_still_writes_the_value_it_is_given(fake_mp4_file):
    path, audio = fake_mp4_file({})

    abs_metadata.write_metadata_to_file(
        str(path), {"title": "Successor's Promise", "narrators": "Simon Vance"}
    )

    assert audio["\xa9wrt"] == ["Simon Vance"]
    assert audio["----:com.apple.iTunes:NARRATOR"] == [b"Simon Vance"]


def test_an_abs_item_with_no_narrator_does_not_strip_the_files_narrator(fake_mp4_file):
    """End-to-end over the exact production shape.

    ABS matched the book and filled a description, so `changed` is True and the
    write-back runs — but ABS has no `narratorName` for it. With the narrator
    now surviving the merge, `_fill` leaves the file's own value in place and
    the write-back puts back what it found.
    """
    path, audio = fake_mp4_file({
        "\xa9wrt": ["Grant Cartwright, Hannah Norris"],
        "----:com.apple.iTunes:NARRATOR": [b"Grant Cartwright, Hannah Norris"],
    })
    abs_index = {
        "Successors Promise": {
            "media": {"metadata": {
                "title": "Successor's Promise",
                "description": "Book two of Millennium's Rule.",
                # no narratorName — this is the case that erased the tag
            }}
        }
    }
    meta = {
        "title": "Successor's Promise",
        "narrators": "Grant Cartwright, Hannah Norris",
    }

    enriched, changed, matched = abs_metadata.enrich_from_abs(
        meta, str(path), abs_index, str(path.parent)
    )
    assert (changed, matched) == (True, True)

    abs_metadata.write_metadata_to_file(str(path), enriched)

    assert audio["\xa9wrt"] == ["Grant Cartwright, Hannah Norris"]
    assert audio["----:com.apple.iTunes:NARRATOR"] == [b"Grant Cartwright, Hannah Norris"]
def test_write_tags_leaves_a_tag_alone_for_an_explicit_none(fake_mp4_file):
    """The manual enrich-abs shape (`library.py` POST /enrich-abs).

    Those handlers build the dict straight off the DB row, so an unknown
    narrator arrives as `narrators: None` rather than as a missing key. That is
    still "we do not know", not "blank it" — the DB is not the authority on a
    tag it never managed to read.
    """
    path, audio = fake_mp4_file({
        "©wrt": ["Grant Cartwright, Hannah Norris"],
        "----:com.apple.iTunes:NARRATOR": [b"Grant Cartwright, Hannah Norris"],
    })

    abs_metadata.write_metadata_to_file(
        str(path), {"title": "Successor's Promise", "narrators": None}
    )

    assert audio["©wrt"] == ["Grant Cartwright, Hannah Norris"]
    assert audio["----:com.apple.iTunes:NARRATOR"] == [b"Grant Cartwright, Hannah Norris"]


def test_write_tags_leaves_the_series_alone_when_the_scan_found_none(fake_mp4_file):
    """Same bug, different field — and this one fires on an ordinary scan.

    The filename parser always emits `series` as a key, `None` when no pattern
    matched, and that dict is what the write-back receives. So every scan of a
    book whose filename carries no series used to delete the series atoms the
    file itself had.
    """
    path, audio = fake_mp4_file({
        "©grp": ["Millennium's Rule #2"],
        "----:com.apple.iTunes:SERIES": [b"Millennium's Rule"],
    })

    abs_metadata.write_metadata_to_file(
        str(path), {"title": "Successor's Promise", "series": None, "series_index": None}
    )

    assert audio["©grp"] == ["Millennium's Rule #2"]
    assert audio["----:com.apple.iTunes:SERIES"] == [b"Millennium's Rule"]


def test_write_tags_still_clears_a_series_the_user_cleared(fake_mp4_file):
    """The ingest marks a user-cleared series with `""`, and that must still
    reach the file — otherwise "no opinion" would swallow a real instruction."""
    path, audio = fake_mp4_file({
        "©grp": ["Millennium's Rule #2"],
        "----:com.apple.iTunes:SERIES": [b"Millennium's Rule"],
        "----:com.apple.iTunes:SERIES-PART": [b"2"],
    })

    abs_metadata.write_metadata_to_file(
        str(path), {"title": "Successor's Promise", "series": ""}
    )

    assert "©grp" not in audio
    assert "----:com.apple.iTunes:SERIES" not in audio
    assert "----:com.apple.iTunes:SERIES-PART" not in audio
