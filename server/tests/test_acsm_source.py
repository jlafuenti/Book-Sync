"""
Unit tests for the source-detection helper in services.import_sources.acsm.

The full process_file() flow shells out to Calibre and is exercised by manual
verification (see plan). These tests pin down the lightweight metadata-vs-
filename heuristics so future refactors don't silently regress them.
"""

import pytest

# services.import_sources.__init__ eagerly imports the Audible source, whose
# `audible` package is a heavy prod dep not present in every local env (CI
# installs it). Skip this file instead of breaking collection of the suite.
pytest.importorskip("audible")

import os  # noqa: E402
from pathlib import Path  # noqa: E402

from services.import_sources import acsm  # noqa: E402
from services.import_sources.acsm import _detect_source_from_meta  # noqa: E402


def test_google_play_identifier():
    meta = {"identifiers": ["urn:gpb:id:abcd1234"], "publisher": "Penguin Random House"}
    assert _detect_source_from_meta(meta, "Some Book.acsm") == "google_play"


def test_bn_filename_prefix_detected_as_nook():
    """Barnes & Noble names its ACSM files 'BN_<id>.acsm' / 'BN-<id>.acsm'."""
    meta = {"identifiers": [], "publisher": ""}
    assert _detect_source_from_meta(meta, "BN_BlahBlah.acsm") == "nook"
    assert _detect_source_from_meta(meta, "BN-1234.acsm") == "nook"


def test_bn_prefix_match_is_strict_not_substring():
    """A stray 'bn' substring (not a prefix) must NOT be detected as Nook."""
    meta = {"identifiers": [], "publisher": ""}
    assert _detect_source_from_meta(meta, "hobnob.acsm") == "acsm"


def test_falls_back_to_acsm_when_unknown():
    meta = {"identifiers": ["urn:isbn:9781234567890"], "publisher": "Random House"}
    assert _detect_source_from_meta(meta, "book.acsm") == "acsm"


def test_publisher_hint_for_nook():
    meta = {"identifiers": [], "publisher": "Barnes & Noble Press"}
    assert _detect_source_from_meta(meta, "x.acsm") == "nook"


def test_publisher_hint_for_google():
    meta = {"identifiers": [], "publisher": "Google Play Books"}
    assert _detect_source_from_meta(meta, "x.acsm") == "google_play"


# ---------------------------------------------------------------------------
# Issue #180: the Adobe account directory was hardcoded to
# /root/.config/calibre/plugins/DeACSM/account. The server container no longer
# runs as root, so that path is neither writable nor where `calibre-customize`
# put the plugin at build time — the startup restore of the stored Adobe
# authorization would fail with EACCES, and `is_adobe_id_authorized()` would
# report "not authorized" forever.
#
# It has to resolve the way Calibre itself resolves it: CALIBRE_CONFIG_DIRECTORY
# if set, otherwise ~/.config/calibre — with the old root path kept as a last
# resort so an image built before this change keeps finding its existing files.
# ---------------------------------------------------------------------------


def test_adobe_account_dir_follows_calibre_config_directory(monkeypatch, tmp_path):
    monkeypatch.setenv("CALIBRE_CONFIG_DIRECTORY", str(tmp_path / "cal"))
    assert acsm._adobe_id_path() == tmp_path / "cal" / "plugins" / "DeACSM" / "account"


def test_adobe_account_dir_follows_home_when_no_override(monkeypatch, tmp_path):
    monkeypatch.delenv("CALIBRE_CONFIG_DIRECTORY", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))  # expanduser on Windows
    expected = tmp_path / ".config" / "calibre" / "plugins" / "DeACSM" / "account"
    assert acsm._adobe_id_path() == expected


def test_adobe_account_dir_falls_back_to_the_legacy_root_path(monkeypatch, tmp_path):
    """An image built before #180 has its files under /root and no HOME copy."""
    monkeypatch.delenv("CALIBRE_CONFIG_DIRECTORY", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "nowhere"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "nowhere"))

    legacy = tmp_path / "legacy" / "calibre"
    (legacy / "plugins").mkdir(parents=True)
    monkeypatch.setattr(acsm, "_LEGACY_CALIBRE_CONFIG_DIR", legacy)

    assert acsm._adobe_id_path() == legacy / "plugins" / "DeACSM" / "account"


def test_an_unreadable_legacy_root_path_counts_as_absent(monkeypatch, tmp_path):
    """On a CI runner (and on any host where the app is not root) `/root` is
    mode 0700, so even asking whether the legacy directory exists raises
    PermissionError. That must read as "not there", not as a crash — the
    HOME candidate is the answer."""
    monkeypatch.delenv("CALIBRE_CONFIG_DIRECTORY", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))

    class Unreadable(type(Path("."))):
        def exists(self, *a, **k):
            raise PermissionError(13, "Permission denied", str(self))

    monkeypatch.setattr(acsm, "_LEGACY_CALIBRE_CONFIG_DIR", Unreadable("/root/.config/calibre"))
    expected = tmp_path / ".config" / "calibre" / "plugins" / "DeACSM" / "account"
    assert acsm._adobe_id_path() == expected


def test_no_module_hardcodes_the_root_calibre_config_as_its_only_candidate():
    """The three `calibre-debug -e` helper scripts locate DeACSM.zip themselves.

    They run in Calibre's interpreter, not the app's, so they cannot import the
    resolver above — but each must still offer a CALIBRE_CONFIG_DIRECTORY-aware
    candidate ahead of the `/root` one, or fulfillment breaks as a non-root uid.
    """
    here = Path(acsm.__file__).parent
    for name in ("_acsm_authorize.py", "_acsm_decrypt.py", "_acsm_fulfill.py"):
        text = (here / name).read_text(encoding="utf-8")
        assert "/root/.config/calibre/plugins/DeACSM.zip" in text, (
            f"{name} dropped the legacy candidate — pre-#180 images regress"
        )
        assert "CALIBRE_CONFIG_DIRECTORY" in text, (
            f"{name} does not consult CALIBRE_CONFIG_DIRECTORY, so it cannot "
            "find the plugin in a non-root image (issue #180)."
        )
        root_at = text.index("/root/.config/calibre/plugins/DeACSM.zip")
        env_at = text.index("CALIBRE_CONFIG_DIRECTORY")
        assert env_at < root_at, (
            f"{name} checks /root before the configured directory."
        )


def test_home_is_not_root_for_the_adobe_account_dir(monkeypatch, tmp_path):
    """Sanity: with a real HOME set, nothing resolves under /root."""
    monkeypatch.delenv("CALIBRE_CONFIG_DIRECTORY", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    assert "root" not in acsm._adobe_id_path().parts[:2]
    assert os.fspath(acsm._adobe_id_path()).startswith(os.fspath(tmp_path))
