"""
Pure golden-vector tests for services.metadata_utils (issue #46, Phase 3).
"""

import pytest

from services.metadata_utils import (
    extract_series_and_index,
    normalize_author,
    normalize_series,
)


@pytest.mark.parametrize("raw,expected", [
    ("Butcher, Jim", "Jim Butcher"),
    ("Jim Butcher", "Jim Butcher"),
    ("  Sanderson,  Brandon ", "Brandon Sanderson"),
    ("Madonna", "Madonna"),
    ("", None),
    ("   ", None),
])
def test_normalize_author(raw, expected):
    assert normalize_author(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    # 'Last, First' still swaps, including a first name with a trailing
    # middle name/initial (still a single first-name unit).
    ("Butcher, Jim", "Jim Butcher"),
    ("Modesitt, L. E.", "L. E. Modesitt"),
    ("Axis, Ann Marie", "Ann Marie Axis"),
    # A multi-word surname before the comma still swaps, as long as the part
    # after the comma is a single name -- either one token, or a lead token
    # plus trailing initials only.
    ("Le Guin, Ursula K.", "Ursula K. Le Guin"),
    ("van Gogh, Vincent", "Vincent van Gogh"),
    ("de la Cruz, Maria", "Maria de la Cruz"),
    # Two full names separated by a comma is a co-author list, not
    # 'Last, First' -- neither side is a single token nor a lead-plus-
    # initials first name, so it is left as-is rather than swapped
    # (issue #514).
    ("Ann Axis, Bob Bartleby", "Ann Axis, Bob Bartleby"),
    # A suffix after the comma is not a first name -- left as-is.
    ("Ann Axis, Jr.", "Ann Axis, Jr."),
    ("Ann Axis, Jr", "Ann Axis, Jr"),
    ("Ann Axis, Sr.", "Ann Axis, Sr."),
    ("Ann Axis, II", "Ann Axis, II"),
    ("Ann Axis, III", "Ann Axis, III"),
    ("Ann Axis, IV", "Ann Axis, IV"),
    ("Ann Axis, PhD", "Ann Axis, PhD"),
    ("Ann Axis, Ph.D.", "Ann Axis, Ph.D."),
    ("Ann Axis, MD", "Ann Axis, MD"),
    ("Ann Axis, Esq.", "Ann Axis, Esq."),
    # Author plus narrator tacked on with a comma -- left as-is.
    ("Ann Axis, Bob Bartleby (Narrator)", "Ann Axis, Bob Bartleby (Narrator)"),
    # Two or more commas -- left as-is, not split/re-normalized here.
    ("Ann Axis, Bob Bartleby, Carol Copyist", "Ann Axis, Bob Bartleby, Carol Copyist"),
    ("Axis, Ann, Jr.", "Axis, Ann, Jr."),
    # '&' or ' and ' joins names -- left as-is even with a single comma.
    ("Ann Axis & Bob Bartleby", "Ann Axis & Bob Bartleby"),
    ("Bartleby, Bob & Axis, Ann", "Bartleby, Bob & Axis, Ann"),
    ("Ann Axis and Bob Bartleby", "Ann Axis and Bob Bartleby"),
    # Whitespace is still collapsed on the as-is path.
    ("Ann Axis,   Jr.", "Ann Axis, Jr."),
    ("Ann Axis,  Bob Bartleby", "Ann Axis, Bob Bartleby"),
])
def test_normalize_author_comma_disambiguation(raw, expected):
    assert normalize_author(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    ("Dresden Files, The", "The Dresden Files"),
    ("The Dresden Files", "The Dresden Files"),
    ("Dresden Files", "Dresden Files"),
    ("Some Series, A", "A Some Series"),
    ("Adventures, An", "An Adventures"),
    ("", None),
])
def test_normalize_series(raw, expected):
    assert normalize_series(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    ("The Cinder Spires #2", ("The Cinder Spires", 2.0)),
    ("The Cinder Spires, Book 2", ("The Cinder Spires", 2.0)),
    ("Stormlight Archive #4.5", ("Stormlight Archive", 4.5)),
    ("Just A Title", ("Just A Title", None)),
    ("", (None, None)),
    # Composition: trailing-article normalization applies to the extracted name.
    ("Dresden Files, The #6", ("The Dresden Files", 6.0)),
])
def test_extract_series_and_index(raw, expected):
    assert extract_series_and_index(raw) == expected
