"""
Unit tests for services.library_writer.render_template — the pure-logic
piece that turns a metadata dict + a template string into a relative
filesystem path. The DB-touching `place_file` is exercised end-to-end in
manual verification (see plan), not here.

Run with: pytest server/tests/test_library_writer.py
"""

import pytest

from services.library_writer import render_template


def test_full_template_with_all_tokens():
    template = "<Author> - [<Series> <Book Number>] - <Title>"
    meta = {
        "author": "Jim Butcher",
        "series_list": [{"name": "Dresden Files", "index": 6}],
        "title": "Blood Rites",
    }
    assert render_template(template, meta) == "Jim Butcher - [Dresden Files 6] - Blood Rites"


def test_path_template_creates_subdirs():
    template = "<Author>/<Series>/<Book Number> - <Title>"
    meta = {
        "author": "Brandon Sanderson",
        "series_list": [{"name": "Stormlight Archive", "index": 4}],
        "title": "Rhythm of War",
    }
    assert render_template(template, meta) == "Brandon Sanderson/Stormlight Archive/4 - Rhythm of War"


def test_missing_series_collapses_bracket_group():
    template = "<Author> - [<Series> <Book Number>] - <Title>"
    meta = {
        "author": "Andy Weir",
        "title": "Project Hail Mary",
    }
    # Bracket group with both <Series> and <Book Number> empty should disappear
    assert render_template(template, meta) == "Andy Weir - Project Hail Mary"


def test_missing_author_drops_empty_segment():
    """
    Path templates like <Author>/<Title> with no author should NOT produce
    a phantom 'Unknown/' subfolder — empty segments are dropped, the title
    just lands at the top level. Keeps the library tidy when one of many
    purchases happens to lack author metadata.
    """
    template = "<Author>/<Title>"
    meta = {"title": "Anonymous Story"}
    assert render_template(template, meta) == "Anonymous Story"


def test_unsafe_filename_chars_are_sanitized():
    template = "<Title>"
    meta = {"title": 'Bad: Title / With "Bad" Chars?'}
    rendered = render_template(template, meta)
    # Forward slashes split into segments, each sanitized
    for ch in '<>:"\\|?*':
        assert ch not in rendered


def test_float_series_index_with_integer_value_drops_decimal():
    template = "<Book Number>: <Title>"
    meta = {"series_list": [{"name": "Axis Saga", "index": 3.0}], "title": "Foo"}
    # The literal ':' is sanitized to '_' because ':' is illegal in Windows paths.
    assert render_template(template, meta) == "3_ Foo"


def test_float_series_index_keeps_decimal_when_non_integer():
    template = "<Book Number>: <Title>"
    meta = {"series_list": [{"name": "Axis Saga", "index": 3.5}], "title": "Foo"}
    # ':' sanitized to '_'; the .5 decimal is preserved for non-integer indexes.
    assert render_template(template, meta) == "3.5_ Foo"


def test_only_title_falls_back_to_title():
    template = "<Title>"
    meta = {"title": "Just a Title"}
    assert render_template(template, meta) == "Just a Title"


def test_empty_meta_returns_unknown():
    template = "<Title>"
    meta = {}
    assert render_template(template, meta) == "Unknown"


def test_collapses_double_dashes_left_by_missing_series_brackets():
    # If author and title remain but series bracket vanishes,
    # we shouldn't get "Author -  - Title" with weird spacing.
    template = "<Author> - [<Series> <Book Number>] - <Title>"
    meta = {"author": "X", "title": "Y"}
    assert render_template(template, meta) == "X - Y"


def test_the_primary_series_names_the_file_when_there_are_several():
    """Only entry 0 can name a file: a path has room for one series folder."""
    template = "<Author>/<Series>/<Book Number> - <Title>"
    meta = {
        "author": "Ann Axis",
        "series_list": [{"name": "Axis Saga", "index": 4}, {"name": "Axis Chronological", "index": 2.5}],
        "title": "Two Series",
    }
    assert render_template(template, meta) == "Ann Axis/Axis Saga/4 - Two Series"


def test_an_empty_series_list_collapses_the_bracket_group():
    template = "<Author> - [<Series> <Book Number>] - <Title>"
    meta = {"author": "X", "series_list": [], "title": "Y"}
    assert render_template(template, meta) == "X - Y"
