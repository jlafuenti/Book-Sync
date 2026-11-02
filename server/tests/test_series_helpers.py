"""
`services/series.py` — the one place series entries are normalised, formatted,
parsed from tags and written onto an ORM row (issue #905).
"""

from types import SimpleNamespace

import pytest
from sqlalchemy import select

from models.book import AudioBook, AudioBookSeries, EBook, EBookSeries
from services.series import (
    entries_of,
    format_series,
    merge_missing,
    normalize_entries,
    parse_abs_series_name,
    parse_series,
    primary,
    set_series,
    split_tag_pair,
)
from tests.factories import make_audiobook, make_ebook


def E(name, index=None):
    return {"name": name, "index": index}


# ---------- normalize_entries ----------

def test_normalize_accepts_dicts_and_objects():
    entries = normalize_entries([E("Axis Saga", 4), SimpleNamespace(name="Axis Chronological", index=2.5)])
    assert entries == [E("Axis Saga", 4.0), E("Axis Chronological", 2.5)]


def test_normalize_dedupes_by_casefolded_name_first_wins():
    entries = normalize_entries([E("Axis Saga", 1), E("axis saga", 9), E("Other")])
    assert entries == [E("Axis Saga", 1.0), E("Other", None)]


def test_normalize_drops_blank_names_and_cleans_bad_indexes():
    entries = normalize_entries([E("  "), E(None, 1), E("A", "x"), E("B", float("nan")), E("C", "2")])
    assert entries == [E("A"), E("B"), E("C", 2.0)]


def test_normalize_moves_trailing_article_like_normalize_series():
    assert normalize_entries([E("Dresden Files, The", 1)]) == [E("The Dresden Files", 1.0)]


def test_normalize_none_is_empty():
    assert normalize_entries(None) == []


# ---------- format_series / parse_series ----------

@pytest.mark.parametrize("entries,expected", [
    ([], ""),
    ([E("Axis Saga")], "Axis Saga"),
    ([E("Axis Saga", 4.0)], "Axis Saga #4"),
    ([E("Axis Saga", 4), E("Axis Chronological", 2.5)], "Axis Saga #4; Axis Chronological #2.5"),
    ([E("Axis; Saga", 1)], "Axis, Saga #1"),
])
def test_format_series(entries, expected):
    assert format_series(entries) == expected


@pytest.mark.parametrize("text", [
    "Axis Saga #4",
    "Axis Saga #4; Axis Chronological #2.5",
    "Axis Saga",
    "Axis Saga; Axis Chronological #2",
])
def test_format_parse_round_trip(text):
    assert format_series(parse_series(text)) == text


def test_parse_series_reads_book_n_suffix():
    assert parse_series("Axis Saga, Book 2") == [E("Axis Saga", 2.0)]


def test_parse_series_mixed_and_empty_pieces():
    assert parse_series("A #1;; B ; ") == [E("A", 1.0), E("B")]
    assert parse_series("") == []
    assert parse_series(None) == []


def test_parse_series_dedupes():
    assert parse_series("A #1; a #2") == [E("A", 1.0)]


# ---------- split_tag_pair (ABS series / series-part rule) ----------

def test_split_tag_pair_same_count():
    assert split_tag_pair("A; B", "1; 2.5") == [E("A", 1.0), E("B", 2.5)]


def test_split_tag_pair_single():
    assert split_tag_pair("A", "3") == [E("A", 3.0)]


def test_split_tag_pair_without_parts():
    assert split_tag_pair("A; B", None) == [E("A"), E("B")]


def test_split_tag_pair_mismatched_counts_drop_the_indexes():
    assert split_tag_pair("A; B", "1") == [E("A"), E("B")]
    assert split_tag_pair("A", "1; 2") == [E("A")]


def test_split_tag_pair_unparseable_part_is_none():
    assert split_tag_pair("A; B", "1; n/a") == [E("A", 1.0), E("B")]


# ---------- parse_abs_series_name ----------

def test_parse_abs_series_name_two_series():
    assert parse_abs_series_name("Axis Saga #1, Axis Chronological #2.5") == [
        E("Axis Saga", 1.0), E("Axis Chronological", 2.5),
    ]


def test_parse_abs_series_name_no_sequence_and_non_numeric_sequence():
    assert parse_abs_series_name("Axis Saga, Other #x") == [E("Axis Saga"), E("Other")]


def test_parse_abs_series_name_empty():
    assert parse_abs_series_name(None) == []
    assert parse_abs_series_name("") == []


# ---------- primary / merge_missing / entries_of ----------

def test_primary():
    assert primary([]) == (None, None)
    assert primary([E("A", 2), E("B")]) == ("A", 2.0)


def test_merge_missing_appends_only_new_names_and_keeps_existing():
    merged = merge_missing([E("A", 1), E("B")], [E("b", 7), E("C", 3)])
    assert merged == [E("A", 1.0), E("B"), E("C", 3.0)]


def test_merge_missing_into_empty():
    assert merge_missing([], [E("A", 1)]) == [E("A", 1.0)]


def test_entries_of_accepts_list_dict_and_row():
    assert entries_of([E("A", 1)]) == [E("A", 1.0)]
    assert entries_of({"series_list": [E("A", 1)]}) == [E("A", 1.0)]
    assert entries_of({"title": "x"}) == []
    assert entries_of(None) == []
    row = SimpleNamespace(series_list=[SimpleNamespace(name="A", index=1.0)])
    assert entries_of(row) == [E("A", 1.0)]


# ---------- set_series on real rows ----------

async def test_set_series_builds_the_right_entry_class(db):
    eb = await make_ebook(db)
    ab = await make_audiobook(db)
    assert set_series(eb, [E("A", 1), E("B")], user_edit=False) is True
    assert set_series(ab, [E("A", 1)], user_edit=False) is True
    await db.commit()
    assert [type(s) for s in eb.series_list] == [EBookSeries, EBookSeries]
    assert [type(s) for s in ab.series_list] == [AudioBookSeries]
    assert [(s.name, s.index, s.position) for s in eb.series_list] == [("A", 1.0, 0), ("B", None, 1)]


async def test_set_series_replaces_in_place_without_unique_collisions(db):
    eb = await make_ebook(db, series_list=[E("A", 1), E("B", 2), E("C", 3)])
    assert set_series(eb, [E("X", 9)], user_edit=True) is True
    await db.commit()
    rows = (await db.execute(select(EBookSeries).where(EBookSeries.ebook_id == eb.id))).scalars().all()
    assert [(r.name, r.index, r.position) for r in rows] == [("X", 9.0, 0)]
    # and growing again
    assert set_series(eb, [E("P"), E("Q", 2), E("R")], user_edit=True) is True
    await db.commit()
    rows = (await db.execute(
        select(EBookSeries).where(EBookSeries.ebook_id == eb.id).order_by(EBookSeries.position)
    )).scalars().all()
    assert [(r.name, r.position) for r in rows] == [("P", 0), ("Q", 1), ("R", 2)]


async def test_set_series_unchanged_returns_false(db):
    eb = await make_ebook(db, series_list=[E("A", 1)])
    assert set_series(eb, [E("A", 1.0)], user_edit=True) is False


async def test_user_clearing_existing_series_sets_the_flag(db):
    eb = await make_ebook(db, series_list=[E("A", 1)])
    assert set_series(eb, [], user_edit=True) is True
    assert eb.series_list == []
    assert eb.series_cleared is True


async def test_user_edit_of_seriesless_book_does_not_set_the_flag(db):
    eb = await make_ebook(db)
    assert set_series(eb, [], user_edit=True) is False
    assert eb.series_cleared is False


async def test_non_empty_result_clears_the_flag(db):
    eb = await make_ebook(db, series="")   # legacy sentinel -> series_cleared
    assert eb.series_cleared is True
    assert set_series(eb, [E("A")], user_edit=True) is True
    assert eb.series_cleared is False


async def test_scan_style_empty_set_never_sets_the_flag(db):
    eb = await make_ebook(db)
    set_series(eb, [], user_edit=False)
    assert eb.series_cleared is False


async def test_partial_removal_keeps_flag_false(db):
    eb = await make_ebook(db, series_list=[E("A"), E("B")])
    set_series(eb, [E("A")], user_edit=True)
    assert eb.series_cleared is False


async def test_factory_translates_legacy_scalar_kwargs(db):
    eb = await make_ebook(db, series="Axis Saga", series_index=3)
    assert entries_of(eb) == [E("Axis Saga", 3.0)]
    ab = await make_audiobook(db, series_list=[E("A", 1), E("B", 2)])
    assert [e["name"] for e in entries_of(ab)] == ["A", "B"]
