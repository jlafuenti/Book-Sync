"""
`PATCH /api/library/{ebooks,audiobooks}/{id}` and series (issue #905).

`series_list` replaces the whole list (`[]` clears it); `series_add` appends the
names the row lacks and, for a name it already has, keeps the entry's place and
takes the new number when one is given (issue #913). `series_cleared` records a user emptying a list, and only
that: saving an unrelated edit on a book that never had a series must not set
it. Any non-empty result clears it again.
"""

import pytest
from sqlalchemy import select

from models.book import AudioBook, AudioBookSeries, EBook, EBookSeries
from tests.factories import make_audiobook, make_ebook

AXIS = {"name": "Axis Saga", "index": 4.0}
CHRONO = {"name": "Axis Chronological", "index": 2.5}


@pytest.fixture(autouse=True)
def no_file_writeback(monkeypatch):
    import routers.library as library

    monkeypatch.setattr(library, "_write_ebook_metadata", lambda path, book: None)
    monkeypatch.setattr(library, "_write_audiobook_metadata", lambda path, book: None)


@pytest.fixture
async def editor_client(make_client, make_user, auth_header):
    from routers import library

    user = await make_user(username="editor", role="admin")
    async with make_client(library.router) as c:
        yield c, auth_header(user)


_MAKE = {"ebook": make_ebook, "audiobook": make_audiobook}
_MODEL = {"ebook": EBook, "audiobook": AudioBook}


async def _patch(client, media, row, body):
    c, headers = client
    r = await c.patch(f"/api/library/{media}s/{row.id}", json=body, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.parametrize("media", ["ebook", "audiobook"])
async def test_series_list_replaces_the_whole_list_in_order(db, editor_client, media):
    row = await _MAKE[media](db, series_list=[{"name": "Old", "index": 1.0}])

    body = await _patch(editor_client, media, row, {"series_list": [AXIS, CHRONO]})

    assert body["series_list"] == [AXIS, CHRONO]
    await db.refresh(row)
    assert [(s.name, s.position) for s in row.series_list] == [
        ("Axis Saga", 0), ("Axis Chronological", 1)]
    assert row.series_cleared is False


@pytest.mark.parametrize("media", ["ebook", "audiobook"])
async def test_reordering_changes_the_primary_series(db, editor_client, media):
    row = await _MAKE[media](db, series_list=[AXIS, CHRONO])

    body = await _patch(editor_client, media, row, {"series_list": [CHRONO, AXIS]})

    assert [e["name"] for e in body["series_list"]] == ["Axis Chronological", "Axis Saga"]


@pytest.mark.parametrize("media", ["ebook", "audiobook"])
async def test_an_empty_list_clears_and_flags_the_row(db, editor_client, media):
    row = await _MAKE[media](db, series_list=[AXIS])

    body = await _patch(editor_client, media, row, {"series_list": []})

    assert body["series_list"] == []
    await db.refresh(row)
    assert row.series_cleared is True


@pytest.mark.parametrize("media", ["ebook", "audiobook"])
async def test_an_empty_list_on_a_seriesless_book_does_not_flag_it(db, editor_client, media):
    """The metadata editor always sends `series_list`; an unrelated edit of a
    book that never had a series must not mark it "cleared"."""
    row = await _MAKE[media](db)

    await _patch(editor_client, media, row, {"title": "Retitled", "series_list": []})

    await db.refresh(row)
    assert row.series_cleared is False


@pytest.mark.parametrize("media", ["ebook", "audiobook"])
async def test_a_non_empty_list_unflags_a_cleared_row(db, editor_client, media):
    row = await _MAKE[media](db, series_list=[AXIS])
    await _patch(editor_client, media, row, {"series_list": []})

    await _patch(editor_client, media, row, {"series_list": [CHRONO]})

    await db.refresh(row)
    assert row.series_cleared is False
    assert [s.name for s in row.series_list] == ["Axis Chronological"]


@pytest.mark.parametrize("media", ["ebook", "audiobook"])
async def test_removing_one_of_two_series_is_not_a_clear(db, editor_client, media):
    row = await _MAKE[media](db, series_list=[AXIS, CHRONO])

    await _patch(editor_client, media, row, {"series_list": [AXIS]})

    await db.refresh(row)
    assert row.series_cleared is False
    assert [s.name for s in row.series_list] == ["Axis Saga"]


@pytest.mark.parametrize("media", ["ebook", "audiobook"])
async def test_an_omitted_series_list_leaves_series_alone(db, editor_client, media):
    row = await _MAKE[media](db, series_list=[AXIS, CHRONO])

    body = await _patch(editor_client, media, row, {"publisher": "Pub"})

    assert body["series_list"] == [AXIS, CHRONO]


@pytest.mark.parametrize("media", ["ebook", "audiobook"])
async def test_series_add_appends_missing_names(db, editor_client, media):
    row = await _MAKE[media](db, series_list=[AXIS])

    body = await _patch(editor_client, media, row, {"series_add": [CHRONO]})

    assert body["series_list"] == [AXIS, CHRONO]


@pytest.mark.parametrize("media", ["ebook", "audiobook"])
async def test_series_add_of_an_existing_name_changes_its_number_in_place(db, editor_client, media):
    row = await _MAKE[media](db, series_list=[CHRONO, AXIS])

    body = await _patch(editor_client, media, row, {"series_add": [{"name": "axis saga", "index": 9}]})

    # Same place, same spelling, new number; the other entry is untouched.
    assert body["series_list"] == [CHRONO, {"name": "Axis Saga", "index": 9.0}]


@pytest.mark.parametrize("media", ["ebook", "audiobook"])
async def test_series_add_of_an_existing_name_without_a_number_leaves_it(db, editor_client, media):
    row = await _MAKE[media](db, series_list=[AXIS])

    body = await _patch(editor_client, media, row, {"series_add": [{"name": "Axis Saga"}]})

    assert body["series_list"] == [AXIS]


@pytest.mark.parametrize("media", ["ebook", "audiobook"])
async def test_series_add_updates_and_appends_in_one_request(db, editor_client, media):
    row = await _MAKE[media](db, series_list=[AXIS])

    body = await _patch(
        editor_client, media, row,
        {"series_add": [CHRONO, {"name": "Axis Saga", "index": 5}]},
    )

    assert body["series_list"] == [{"name": "Axis Saga", "index": 5.0}, CHRONO]


@pytest.mark.parametrize("media", ["ebook", "audiobook"])
async def test_series_add_to_a_cleared_row_unflags_it(db, editor_client, media):
    row = await _MAKE[media](db, series_list=[AXIS])
    await _patch(editor_client, media, row, {"series_list": []})

    body = await _patch(editor_client, media, row, {"series_add": [CHRONO]})

    assert body["series_list"] == [CHRONO]
    await db.refresh(row)
    assert row.series_cleared is False


async def test_a_blank_name_is_rejected(db, editor_client):
    c, headers = editor_client
    row = await make_ebook(db)

    r = await c.patch(f"/api/library/ebooks/{row.id}", json={"series_list": [{"name": ""}]}, headers=headers)

    assert r.status_code == 422


async def test_the_legacy_scalar_fields_are_gone(db, editor_client):
    c, headers = editor_client
    row = await make_ebook(db)

    body = await _patch(editor_client, "ebook", row, {"series": "Ignored", "series_index": 3})

    assert body["series_list"] == []
    assert "series" not in body and "series_index" not in body


async def test_entry_names_are_normalised_and_deduplicated(db, editor_client):
    row = await make_ebook(db)

    body = await _patch(editor_client, "ebook", row, {"series_list": [
        {"name": "Dresden Files, The", "index": 1}, {"name": "the dresden files"}]})

    assert body["series_list"] == [{"name": "The Dresden Files", "index": 1.0}]


async def test_the_write_back_receives_the_new_series(db, editor_client, monkeypatch):
    import routers.library as library
    seen = {}

    def _spy(path, book):
        seen["series"] = [(s.name, s.index) for s in book.series_list]
        seen["cleared"] = book.series_cleared

    monkeypatch.setattr(library, "_write_audiobook_metadata", _spy)
    row = await make_audiobook(db)

    await _patch(editor_client, "audiobook", row, {"series_list": [AXIS, CHRONO]})

    assert seen["series"] == [("Axis Saga", 4.0), ("Axis Chronological", 2.5)]


async def test_clearing_reaches_the_writer_as_a_cleared_row(db, editor_client, monkeypatch):
    import routers.library as library
    seen = {}

    def _spy(path, book):
        seen["entries"] = list(book.series_list)
        seen["cleared"] = book.series_cleared

    monkeypatch.setattr(library, "_write_ebook_metadata", _spy)
    row = await make_ebook(db, series_list=[AXIS])

    await _patch(editor_client, "ebook", row, {"series_list": []})

    assert seen == {"entries": [], "cleared": True}


async def test_detail_paired_with_carries_the_other_halfs_series(db, editor_client):
    """The web applies the pair rule (union, ebook first) from the detail
    response alone, so `paired_with` must carry the other half's list."""
    from models.book import BookPair, PairStatus

    c, headers = editor_client
    eb = await make_ebook(db, series_list=[AXIS])
    ab = await make_audiobook(db, series_list=[CHRONO, {"name": "Axis Audio", "index": None}])
    db.add(BookPair(ebook_id=eb.id, audiobook_id=ab.id, status=PairStatus.SYNCED))
    await db.commit()

    ebook_detail = (await c.get(f"/api/library/ebooks/{eb.id}", headers=headers)).json()
    audio_detail = (await c.get(f"/api/library/audiobooks/{ab.id}", headers=headers)).json()

    assert ebook_detail["series_list"] == [AXIS]
    assert ebook_detail["paired_with"]["series_list"] == [
        CHRONO, {"name": "Axis Audio", "index": None}]
    assert audio_detail["paired_with"]["series_list"] == [AXIS]


async def test_deleting_a_book_removes_its_series_rows(db, editor_client):
    row = await make_ebook(db, series_list=[AXIS, CHRONO])
    await db.delete(row)
    await db.commit()

    assert (await db.execute(select(EBookSeries))).scalars().all() == []
    ab = await make_audiobook(db, series_list=[AXIS])
    await db.delete(ab)
    await db.commit()
    assert (await db.execute(select(AudioBookSeries))).scalars().all() == []
