"""
Android source contract: no personal hosts in the sources (issues #58, #188).

The shipped Android app twice hardcoded one developer's machine — the default
server URL and a dev LAN address in the cleartext-HTTP allowlist
(`network_security_config.xml`). Both are meaningless to anyone else building
from a clean clone, and the second one silently decides which hosts the app
will talk to over plain HTTP.

Both are now build properties (`tandem.defaultServerUrl`, `tandem.cleartextHosts`)
that default to empty. This test keeps them that way: it walks the committed
Android sources — main *and* test — and fails on a private-network literal or
the owner's private domain.

**Comments are no longer exempt** (issue #188). They were, on the reasoning that
prose describing history is not a baked-in host; that holds right up until the
repo goes public, at which point a comment naming the owner's LAN hostname
publishes it just as effectively as a constant would. The comments that needed
it now say "a LAN-only hostname" and read no worse for it. Nothing is stripped
before scanning any more, which also retires the whole class of bug behind #311
(a comment stripper that did not know what a string literal was, and blanked
every URL before the detectors ran).

Documentation-range addresses are deliberately still allowed: 192.0.2.x,
198.51.100.x and 203.0.113.x (RFC 5737) are not in the RFC1918 alternation
below, so tests and docs have synthetic addresses they can use freely.

Lives in the server suite because that's where this repo already puts
cross-language file contracts (see test_schema_contract.py, which parses Kotlin).
"""

import os
import re

import pytest

from tests.test_repo_hygiene import PERSONAL_DOMAIN

_SERVER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_REPO_ROOT = os.path.dirname(_SERVER_DIR)
_ANDROID_APP = os.path.join(_REPO_ROOT, "android", "app")

# Literals that are legitimately generic and must stay.
ALLOWED_LITERALS = {
    # Loopback, and the Android emulator's alias for the host machine. Neither
    # identifies a particular person's network.
    "127.0.0.1",
    "10.0.2.2",
    "0.0.0.0",
}

# Private-network ranges (RFC1918) plus link-local. Matching literal IPs is the
# point; a baked-in hostname is caught by the personal-domain rule below.
_PRIVATE_IP = re.compile(
    r"\b(?:"
    r"10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
    r"|192\.168\.\d{1,3}\.\d{1,3}"
    r"|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}"
    r"|169\.254\.\d{1,3}\.\d{1,3}"
    r")\b"
)
_PERSONAL_HOST = re.compile(
    r"\b[\w-]+(?:\.[\w-]+)*\.?" + re.escape(PERSONAL_DOMAIN) + r"\b"
)

_SCANNED_SUFFIXES = (".kt", ".xml", ".kts", ".pro")


def scan_text(text: str) -> list[tuple[int, str]]:
    """Detector hits in `text` as (line number, literal). Comments included.

    Split out from `_offenders` so the detectors can be exercised against a
    string. Walking the real tree only tells you the tree is clean today; it
    cannot tell you the scanner would notice if it were not, which is exactly
    how issue #311 stayed hidden.
    """
    found = []
    for lineno, line in enumerate(text.splitlines(), 1):
        for match in list(_PRIVATE_IP.finditer(line)) + list(_PERSONAL_HOST.finditer(line)):
            literal = match.group(0)
            if literal in ALLOWED_LITERALS:
                continue
            found.append((lineno, literal))
    return found


def _android_source_files():
    for root, dirs, files in os.walk(_ANDROID_APP):
        # Build output is generated, not committed — and the generated network
        # security config legitimately contains whatever the local build set.
        dirs[:] = [d for d in dirs if d not in {"build", ".gradle", ".kotlin"}]
        for name in files:
            if name.endswith(_SCANNED_SUFFIXES):
                yield os.path.join(root, name)


def _offenders():
    found = []
    for path in _android_source_files():
        with open(path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        rel = os.path.relpath(path, _REPO_ROOT)
        found.extend((rel, lineno, literal) for lineno, literal in scan_text(text))
    return found


def test_no_personal_hosts_in_android_sources():
    offenders = _offenders()
    assert not offenders, (
        "Personal/private hosts hardcoded in the Android sources - move them to a "
        "build property (see tandem.defaultServerUrl / tandem.cleartextHosts in "
        "android/app/build.gradle.kts):\n"
        + "\n".join(f"  {p}:{ln} -> {lit}" for p, ln, lit in offenders)
    )


def test_scanner_actually_reads_the_android_tree():
    """A silently-empty walk would make the test above pass for the wrong reason."""
    files = list(_android_source_files())
    assert len(files) > 50, f"only found {len(files)} android source files"
    assert any(f.endswith("AndroidManifest.xml") for f in files)
    # Test sources count: a fixture URL is published just as widely as a
    # constant is, and #188 found the legacy hostname in a test's docstring.
    assert any(os.path.join("src", "test") in f for f in files), (
        "the walk is missing android/app/src/test — test sources are in scope"
    )


# These are inputs for the detectors, not addresses anyone uses: the assertion is
# that the regexes above still match what they are supposed to match, so that a
# broken alternation can't leave `test_no_personal_hosts_in_android_sources`
# passing against a detector that matches nothing. Keep every address here
# synthetic — a real one from someone's LAN is the exact thing this file exists to
# keep out of the repo. (The 192.168 sample was lost to a history rewrite that
# redacted it in place, which is what left that branch both uncovered and failing;
# 169.254 never had one.)
@pytest.mark.parametrize(
    "sample",
    [
        "192.168.1.50",
        "169.254.10.20",
        "172.16.0.9",
        "10.1.2.3",
        f"https://tandem.{PERSONAL_DOMAIN}",
    ],
)
def test_detectors_match_the_things_they_are_meant_to_catch(sample):
    assert _PRIVATE_IP.search(sample) or _PERSONAL_HOST.search(sample)


# The documentation ranges (RFC 5737) are the escape hatch this guard leaves
# open: tests and docs that need an address to parse should reach for one of
# these rather than inventing a realistic-looking LAN address.
@pytest.mark.parametrize("sample", ["192.0.2.10", "198.51.100.7", "203.0.113.5"])
def test_documentation_range_addresses_are_not_reported(sample):
    assert scan_text(f'val host = "http://{sample}:8000"\n') == []


def test_the_original_hardcoded_default_server_url_is_reported():
    """The #58 regression this file exists to prevent. Fails before #311."""
    found = scan_text(f'private const val DEFAULT = "https://tandem.{PERSONAL_DOMAIN}"\n')
    assert [lit for _, lit in found] == [f"tandem.{PERSONAL_DOMAIN}"]


def test_a_private_ip_inside_a_url_literal_is_reported():
    found = scan_text('val lan = "http://192.168.1.50:8000/api"\n')
    assert [lit for _, lit in found] == ["192.168.1.50"]


def test_raw_string_blocks_are_scanned():
    """build.gradle.kts builds the network security config in a raw string."""
    kts = (
        'val cfg = ' + '"""' + '\n'
        '    <domain includeSubdomains="false">192.168.1.50</domain>\n'
        + '"""' + '.trimIndent()\n'
    )
    found = scan_text(kts)
    assert [lit for _, lit in found] == ["192.168.1.50"]


# ---------------------------------------------------------------------------
# Issue #188: comments are scanned too.
#
# They used to be stripped before the detectors ran, on the reasoning that prose
# is not a baked-in host. That reasoning ends at publication: a comment naming
# the owner's LAN hostname publishes it exactly as well as a constant does. The
# stripping is gone entirely, which is also the permanent fix for #311 — a
# stripper that did not know what a string literal was blanked every `http://`
# URL before the detectors could see it, so the hardcoded default server URL
# this file exists to catch sailed straight through.
# ---------------------------------------------------------------------------


def test_comments_naming_the_production_host_are_reported():
    for text in (
        f"// see tandem.{PERSONAL_DOMAIN} for why\n",
        f"/* tandem.{PERSONAL_DOMAIN} */\n",
        f"<!-- tandem.{PERSONAL_DOMAIN} -->\n",
        "// the dev box at 192.168.1.50\n",
    ):
        assert scan_text(text), f"comment not scanned: {text!r}"


def test_a_url_in_a_string_literal_is_still_reported_next_to_a_comment():
    """#311: the `//` in `https://` must not hide the rest of the line."""
    found = scan_text('val a = "http://192.168.1.50"  // the dev box\n')
    assert [lit for _, lit in found] == ["192.168.1.50"]


def test_reported_line_numbers_are_accurate():
    found = scan_text('// pad\n\nval a = "http://192.168.1.50"\n')
    assert found == [(3, "192.168.1.50")]


def test_allowed_literals_still_pass_when_visible_in_a_url():
    """10.0.2.2 is the emulator's alias for the host machine, allowed on purpose."""
    assert scan_text('val emu = "http://10.0.2.2:8000"\n') == []
