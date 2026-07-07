"""
Unit tests for the source-detection helper in services.import_sources.acsm.

The full process_file() flow shells out to Calibre and is exercised by manual
verification (see plan). These tests pin down the lightweight metadata-vs-
filename heuristics so future refactors don't silently regress them.
"""

from services.import_sources.acsm import _detect_source_from_meta


def test_google_play_identifier():
    meta = {"identifiers": ["urn:gpb:id:abcd1234"], "publisher": "Penguin Random House"}
    assert _detect_source_from_meta(meta, "Some Book.acsm") == "google_play"


def test_bare_bn_filename_prefix_is_not_yet_a_nook_signal():
    """
    Current behavior: detection matches 'barnes'/'nook'/'bn.com' in the combined
    identifiers+publisher+filename blob, but a bare 'BN_' filename prefix is NOT
    recognized, so it falls back to 'acsm'. (Adding a 'BN_' prefix heuristic is
    tracked as a Phase-2 enhancement on issue #46.)
    """
    meta = {"identifiers": [], "publisher": ""}
    assert _detect_source_from_meta(meta, "BN_BlahBlah.acsm") == "acsm"


def test_falls_back_to_acsm_when_unknown():
    meta = {"identifiers": ["urn:isbn:9781234567890"], "publisher": "Random House"}
    assert _detect_source_from_meta(meta, "book.acsm") == "acsm"


def test_publisher_hint_for_nook():
    meta = {"identifiers": [], "publisher": "Barnes & Noble Press"}
    assert _detect_source_from_meta(meta, "x.acsm") == "nook"


def test_publisher_hint_for_google():
    meta = {"identifiers": [], "publisher": "Google Play Books"}
    assert _detect_source_from_meta(meta, "x.acsm") == "google_play"
