"""
Every shared metadata field round-trips through PATCH and the detail endpoint,
on **both** media types (issue #257).

The two PATCH handlers used to be two hand-written blocks of
``if meta.x is not None: book.x = meta.x``, one per media type, 15 lines each.
Nothing checked that the two blocks agreed, or that either agreed with the model
— a field added to `MetadataUpdate` and forgotten in one block would simply be
dropped, silently, for that media type only. Both now loop over
`MEDIA_METADATA_FIELDS`, and this is the test that would have caught the old
shape: it drives every name in that list through the real API on both types.

`cover_path` is deliberately absent from the list (it is written by the cover
upload endpoint, not by a metadata PATCH) and so is not exercised here.
"""

import pytest

from config import settings
from models.book import AudioBook, EBook
import routers.library as library
from services.metadata_utils import MEDIA_DESCRIPTIVE_FIELDS, MEDIA_METADATA_FIELDS

# One representative value per field, typed the way the column is.
SAMPLE_VALUES = {
    "title": "Round Trip",
    "author": "A. Author",
    "series": "The Series",
    "series_index": 3.5,
    "description": "A description.",
    "publisher": "A Publisher",
    "publish_year": 1998,
    "language": "en",
    "genres": "Fantasy,Adventure",
    "tags": "owned,favourite",
    "isbn": "9780000000001",
    "asin": "B00TESTASIN",
    "narrators": "N. Narrator",
    "is_explicit": True,
    "is_abridged": True,
}


@pytest.fixture
def no_file_writeback(monkeypatch):
    """The PATCH handlers write the new metadata back into the file on disk.

    These rows point at paths that do not exist, and the handlers already
    swallow the resulting error — but stubbing the writers keeps the test about
    the DB round-trip rather than about mutagen's behaviour on a missing file.
    """
    import routers.library as library

    monkeypatch.setattr(library, "_write_ebook_metadata", lambda path, book: None)
    monkeypatch.setattr(library, "_write_audiobook_metadata", lambda path, book: None)


@pytest.fixture
async def editor_client(make_client, make_user, auth_header):
    from routers import library

    user = await make_user(username="editor", role="admin")
    async with make_client(library.router) as c:
        yield c, auth_header(user)


async def _seed(db, media):
    if media == "ebook":
        row = EBook(title="Before", filename="b.epub", file_path="/x/rt/b.epub")
    else:
        row = AudioBook(title="Before", filename="b.m4b", file_path="/x/rt/b.m4b")
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


def test_every_field_has_a_sample_value():
    """Keeps this file honest: adding a field to `MEDIA_METADATA_FIELDS` without
    a sample here would otherwise silently shrink the parametrisation."""
    assert set(SAMPLE_VALUES) == set(MEDIA_METADATA_FIELDS)


@pytest.mark.parametrize("media", ["ebook", "audiobook"])
@pytest.mark.parametrize("field", MEDIA_METADATA_FIELDS)
async def test_metadata_field_round_trips(db, editor_client, no_file_writeback, media, field):
    c, headers = editor_client
    row = await _seed(db, media)
    value = SAMPLE_VALUES[field]

    patch = await c.patch(
        f"/api/library/{media}s/{row.id}", json={field: value}, headers=headers
    )
    assert patch.status_code == 200, patch.text
    assert patch.json()[field] == value

    detail = await c.get(f"/api/library/{media}s/{row.id}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()[field] == value


@pytest.mark.parametrize("media", ["ebook", "audiobook"])
async def test_all_fields_round_trip_together(db, editor_client, no_file_writeback, media):
    """The whole list in one request — the shape the metadata editor actually
    sends, and the one where a per-field `if` block dropping a name shows up as
    a field that just refuses to save."""
    c, headers = editor_client
    row = await _seed(db, media)

    patch = await c.patch(
        f"/api/library/{media}s/{row.id}", json=dict(SAMPLE_VALUES), headers=headers
    )
    assert patch.status_code == 200, patch.text

    detail = (await c.get(f"/api/library/{media}s/{row.id}", headers=headers)).json()
    for field, value in SAMPLE_VALUES.items():
        assert detail[field] == value, f"{field} did not round-trip on {media}"


@pytest.mark.parametrize("media", ["ebook", "audiobook"])
async def test_omitted_fields_are_left_alone(db, editor_client, no_file_writeback, media):
    """A PATCH is a partial update: `None` means "no opinion", not "clear it".
    The loop over the shared list must keep that, or every edit of one field
    would wipe the other fourteen."""
    c, headers = editor_client
    row = await _seed(db, media)

    await c.patch(
        f"/api/library/{media}s/{row.id}",
        json={"publisher": "Kept Publisher", "tags": "kept"},
        headers=headers,
    )
    await c.patch(
        f"/api/library/{media}s/{row.id}", json={"tags": "changed"}, headers=headers
    )

    detail = (await c.get(f"/api/library/{media}s/{row.id}", headers=headers)).json()
    assert detail["publisher"] == "Kept Publisher"
    assert detail["tags"] == "changed"


# ---------------------------------------------------------------------------
# The rescan paths share one loop over MEDIA_DESCRIPTIVE_FIELDS
# ---------------------------------------------------------------------------
#
# `rescan_all_files` had the same six `book.x = meta.get("x") or book.x` lines
# written out twice (ebook branch, audiobook branch), and `rescan_book_file` a
# third time. They are one loop now, and the `or` is load-bearing: an empty read
# must keep what is stored rather than null it.


@pytest.fixture
def rescan_library(monkeypatch, tmp_path):
    """Real files under `settings.{ebook,audiobook}_dir`, with a stubbed extract.

    `rescan_all_files` skips any row whose `file_path` is not on disk, so the
    files have to exist; their contents never matter because `extract_metadata`
    is stubbed.
    """
    ebook_dir = tmp_path / "ebooks"
    audio_dir = tmp_path / "audiobooks"
    ebook_dir.mkdir()
    audio_dir.mkdir()
    monkeypatch.setattr(settings, "ebook_dir", str(ebook_dir))
    monkeypatch.setattr(settings, "audiobook_dir", str(audio_dir))
    monkeypatch.setattr(library, "_extract_and_save_cover", lambda *a, **kw: None)

    epub = ebook_dir / "b.epub"
    epub.write_bytes(b"not really an epub")
    m4b = audio_dir / "b.m4b"
    m4b.write_bytes(b"not really audio")

    def _stub_extract(meta):
        async def _fake(path, book_type, db, library_root=None):
            return dict(meta)

        monkeypatch.setattr(library, "extract_metadata", _fake)

    return epub, m4b, _stub_extract


def _descriptive_values(prefix):
    return {
        f: 1999 if f == "publish_year" else f"{prefix}-{f}"
        for f in MEDIA_DESCRIPTIVE_FIELDS
    }


@pytest.mark.parametrize("media", ["ebook", "audiobook"])
async def test_rescan_fills_every_descriptive_field(db, editor_client, rescan_library, media):
    epub, m4b, stub_extract = rescan_library
    c, headers = editor_client
    stub_extract(_descriptive_values("from-file"))

    if media == "ebook":
        row = EBook(title="Before", filename=epub.name, file_path=str(epub))
    else:
        row = AudioBook(title="Before", filename=m4b.name, file_path=str(m4b))
    db.add(row)
    await db.commit()

    resp = await c.post("/api/library/rescan-all", headers=headers)
    assert resp.status_code == 200, resp.text

    await db.refresh(row)
    for f, value in _descriptive_values("from-file").items():
        assert getattr(row, f) == value, f


@pytest.mark.parametrize("media", ["ebook", "audiobook"])
async def test_rescan_keeps_a_stored_value_the_file_cannot_supply(
    db, editor_client, rescan_library, media
):
    """The `or` fallback: a rescan of a file with no embedded metadata must not
    wipe what the user (or Audiobookshelf) put there."""
    epub, m4b, stub_extract = rescan_library
    c, headers = editor_client
    stub_extract({})

    stored = _descriptive_values("stored")
    if media == "ebook":
        row = EBook(title="Before", filename=epub.name, file_path=str(epub), **stored)
    else:
        row = AudioBook(title="Before", filename=m4b.name, file_path=str(m4b), **stored)
    db.add(row)
    await db.commit()

    resp = await c.post("/api/library/rescan-all", headers=headers)
    assert resp.status_code == 200, resp.text

    await db.refresh(row)
    for f, value in stored.items():
        assert getattr(row, f) == value, f
