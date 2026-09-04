"""
Filename-pattern compiler (issue #354).

`<Author>/<Title>/<Title>` — a perfectly ordinary `Author/Title/Title.ext`
library layout — turned into a regex with the group name `title` defined twice,
so `re.compile` raised. The scan caught it, logged a warning, and moved on: once
per file, for every file, forever. The pattern never matched anything, so that
library got no metadata from its paths, and the only way to find out was to read
the log.

Two halves:

* a repeated placeholder compiles, as a back-reference — the repeats have to
  match the same text, which is exactly what `Author/Title/Title` means; and
* a bad pattern is caught where it is saved (422 from the settings PUT, once)
  rather than at scan time (a warning per file), and never escapes the scan
  loop even if one gets stored some other way.
"""

import logging

import pytest

from services.filename_patterns import (
    PatternError,
    compile_pattern,
    regex_from_pattern,
    validate_patterns,
)


@pytest.fixture(autouse=True)
def _clear_compile_cache():
    """`compile_pattern` memoizes so it warns once per pattern, not once per
    file — which means it also warns once per *process* unless cleared."""
    compile_pattern.cache_clear()
    yield
    compile_pattern.cache_clear()


# ---------------------------------------------------------------------------
# The repeated placeholder
# ---------------------------------------------------------------------------

def test_a_repeated_placeholder_compiles():
    """This is the pattern that raised on every file in production."""
    assert regex_from_pattern("<Author>/<Title>/<Title>") is not None


def test_a_repeated_placeholder_extracts_the_title():
    regex = regex_from_pattern("<Author>/<Title>/<Title>")
    match = regex.match("Some Author/Some Title/Some Title")
    assert match is not None
    assert match.group("title") == "Some Title"
    assert match.group("author") == "Some Author"


def test_the_repeats_must_match_the_same_text():
    """A back-reference, not "first wins" — documented in the module docstring.

    `Author/Title/Title` describes a folder whose name repeats. A path where
    the two differ is not that layout, and matching it anyway would hand the
    scan a title taken from a folder that means something else.
    """
    regex = regex_from_pattern("<Author>/<Title>/<Title>")
    assert regex.match("Some Author/Some Title/A Different Title") is None


def test_a_placeholder_repeated_three_times_still_compiles():
    regex = regex_from_pattern("<Title>/<Title>/<Title>")
    assert regex.match("Dune/Dune/Dune").group("title") == "Dune"


def test_two_spellings_of_the_same_group_do_not_collide():
    """`<Book Number>` and `<Series Index>` are aliases for one group, as are
    `<Title>` and `<Book Title>`. Two aliases in one pattern used to raise for
    the same reason a literal repeat did."""
    regex = regex_from_pattern("<Series> <Book Number>/<Series Index> - <Title>")
    match = regex.match("Discworld 3/3 - Equal Rites")
    assert match is not None
    assert match.group("series_index") == "3"
    assert match.group("title") == "Equal Rites"


def test_the_ordinary_patterns_still_work():
    """The defaults are the regression surface — none of them repeat anything."""
    regex = regex_from_pattern("<Author> - [<Series> <Book Number>] - <Title>")
    match = regex.match("Some Author - [Some Series 2] - Some Title")
    assert match.group("author") == "Some Author"
    assert match.group("series") == "Some Series"
    assert match.group("series_index") == "2"
    assert match.group("title") == "Some Title"


# ---------------------------------------------------------------------------
# Validation, once, where the pattern is saved
# ---------------------------------------------------------------------------

def test_validate_accepts_the_repeated_placeholder():
    validate_patterns(["<Author>/<Title>/<Title>"])  # no raise


def test_validate_rejects_an_unknown_placeholder():
    """A misspelt tag is escaped to a literal and silently never matches —
    the same class of failure as an unknown role floor (#359). Name it."""
    with pytest.raises(PatternError) as exc:
        validate_patterns(["<Author>/<Titel>"])
    message = str(exc.value)
    assert "<Titel>" in message
    assert "<Title>" in message  # the message lists what is available


def test_validate_reports_every_bad_pattern_not_just_the_first():
    with pytest.raises(PatternError) as exc:
        validate_patterns(["<Nope>", "<Title>", "<Alsonope>"])
    message = str(exc.value)
    assert "<Nope>" in message
    assert "<Alsonope>" in message


def test_validate_ignores_blank_lines():
    """The settings UI stores patterns as one textarea split on newlines, so a
    trailing newline arrives as an empty pattern. That is not an error."""
    validate_patterns(["<Title>", "", "   ", "<Author>/<Title>"])


# ---------------------------------------------------------------------------
# Nothing escapes the scan loop
# ---------------------------------------------------------------------------

def test_compile_pattern_returns_none_instead_of_raising(monkeypatch):
    """Belt and braces: whatever a stored pattern does to `re.compile`, the
    scan gets `None` back and skips it."""
    def _boom(*_args, **_kwargs):
        raise ValueError("synthetic compile failure")

    monkeypatch.setattr("services.filename_patterns.regex_from_pattern", _boom)
    assert compile_pattern("<Title>") is None


def test_compile_pattern_warns_once_per_pattern_not_once_per_file(monkeypatch, caplog):
    """The production symptom was one warning per file for every file in the
    library. The result is memoized, so the log line happens once."""
    def _boom(*_args, **_kwargs):
        raise ValueError("synthetic compile failure")

    monkeypatch.setattr("services.filename_patterns.regex_from_pattern", _boom)
    with caplog.at_level(logging.WARNING):
        for _ in range(50):
            assert compile_pattern("<Title>") is None

    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warnings) == 1
    assert "<Title>" in warnings[0].getMessage()


def test_compile_pattern_is_quiet_for_a_good_pattern(caplog):
    with caplog.at_level(logging.WARNING):
        assert compile_pattern("<Author>/<Title>/<Title>") is not None
    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []


# ---------------------------------------------------------------------------
# End to end through the scan's metadata parser
# ---------------------------------------------------------------------------

async def _set_patterns(db, patterns, key="ebook_filename_patterns"):
    from models.settings import SystemSetting

    db.add(SystemSetting(key=key, value="\n".join(patterns)))
    await db.commit()


async def test_scan_metadata_uses_a_repeated_placeholder_pattern(db, caplog):
    """The whole point: `Author/Title/Title.epub` now yields metadata."""
    from routers.library import parse_filename_metadata_with_settings

    await _set_patterns(db, ["<Author>/<Title>/<Title>"])

    with caplog.at_level(logging.WARNING):
        meta = await parse_filename_metadata_with_settings(
            "Some Title.epub",
            db,
            parent_dir_name="Some Title",
            file_type="ebook",
            relative_path="Some Author/Some Title/Some Title.epub",
        )

    assert meta["title"] == "Some Title"
    assert meta["author"] == "Some Author"
    assert meta["_metadata_source"] == "pattern"
    assert meta["_metadata_pattern"] == "<Author>/<Title>/<Title>"
    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []


async def test_a_stored_bad_pattern_does_not_break_the_scan(db, caplog):
    """Validation is on the save path, so a pattern stored before this change —
    or written straight into the table — still has to be survivable. It is
    skipped, and the next pattern in the list does the work."""
    from routers.library import parse_filename_metadata_with_settings

    await _set_patterns(db, ["<Titel>", "<Author>/<Title>"])

    with caplog.at_level(logging.WARNING):
        meta = await parse_filename_metadata_with_settings(
            "Some Title.epub",
            db,
            parent_dir_name="Some Author",
            file_type="ebook",
            relative_path="Some Author/Some Title.epub",
        )

    assert meta["title"] == "Some Title"
    assert meta["author"] == "Some Author"
