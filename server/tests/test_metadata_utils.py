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
