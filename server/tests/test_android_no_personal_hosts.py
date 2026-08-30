"""
Android source contract: no personal hosts baked into the build (issue #58).

The shipped Android app twice hardcoded one developer's machine — the default
server URL (`https://tandem.lafuenti.com`) and a dev LAN address in the
cleartext-HTTP allowlist (`network_security_config.xml`). Both are meaningless
to anyone else building from a clean clone, and the second one silently decides
which hosts the app will talk to over plain HTTP.

Both are now build properties (`tandem.defaultServerUrl`, `tandem.cleartextHosts`)
that default to empty. This test keeps them that way: it walks the committed
Android sources and fails on a private-network literal or a `lafuenti.com`
hostname that would affect runtime behavior.

**Comments are exempt on purpose.** Several comments legitimately name the
production host to explain why the LAN cast server exists (see
`LocalCastHttpServer.kt`) — prose describing history isn't a baked-in host, and
forbidding it would just push people to write vaguer comments.

Lives in the server suite because that's where this repo already puts
cross-language file contracts (see test_schema_contract.py, which parses Kotlin).
"""

import os
import re

import pytest

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
    # The dead production hostname, kept only so a stored value can be migrated
    # away from it. Pinned by ServerUrlManagerTest.
    "booksync.lafuenti.com",
}

# Private-network ranges (RFC1918) plus link-local. Matching literal IPs is the
# point; a baked-in hostname is caught by the lafuenti.com rule below.
_PRIVATE_IP = re.compile(
    r"\b(?:"
    r"10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
    r"|192\.168\.\d{1,3}\.\d{1,3}"
    r"|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}"
    r"|169\.254\.\d{1,3}\.\d{1,3}"
    r")\b"
)
_PERSONAL_HOST = re.compile(r"\b[\w-]+(?:\.[\w-]+)*\.?lafuenti\.com\b")

_SCANNED_SUFFIXES = (".kt", ".xml", ".kts", ".pro")

_KOTLIN_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_XML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)


def _strip_kotlin_comments(text: str) -> str:
    """Blank Kotlin comments, leaving string literals alone (issue #311).

    This used to be two regexes. Neither knew what a string was, so the `//` in
    any `http://` blanked the rest of the line and a `/*` inside a literal opened
    a comment that ran to the next `*/` anywhere in the file. The guard could
    therefore not see a URL in Kotlin code at all -- including the hardcoded
    default server URL it was written to catch.

    One pass, tracking whether we are inside `"`, `\'` or a `\"\"\"` block.
    Comments become spaces; newlines are preserved so reported line numbers stay
    accurate. Not a Kotlin parser, and does not need to be: the only question is
    whether a `//` or `/*` is inside a literal.
    """
    out = list(text)
    i, n = 0, len(text)
    while i < n:
        if text.startswith('"""', i):
            end = text.find('"""', i + 3)
            i = n if end == -1 else end + 3
        elif text[i] == '"' or text[i] == "'":
            quote = text[i]
            i += 1
            while i < n and text[i] != quote:
                # A backslash escapes whatever follows, including the quote. An
                # unterminated literal stops at the newline rather than running
                # away with the rest of the file.
                if text[i] == "\\":
                    i += 1
                elif text[i] == "\n":
                    break
                i += 1
            i += 1
        elif text.startswith("//", i):
            while i < n and text[i] != "\n":
                out[i] = " "
                i += 1
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            stop = n if end == -1 else end + 2
            while i < stop:
                if text[i] != "\n":
                    out[i] = " "
                i += 1
        else:
            i += 1
    return "".join(out)


def strip_comments(text: str, suffix: str) -> str:
    """Blank out comments, preserving line numbering so offenders stay locatable."""

    def _blank(match: re.Match) -> str:
        # Keep the newlines so line numbers don't shift.
        return re.sub(r"[^\n]", " ", match.group(0))

    if suffix == ".xml":
        return _XML_COMMENT.sub(_blank, text)
    if suffix == ".pro":
        # ProGuard rules: `#` comments, and no string syntax to protect.
        return re.sub(r"#[^\n]*", _blank, text)
    return _strip_kotlin_comments(text)


def scan_text(text: str, suffix: str) -> list[tuple[int, str]]:
    """Detector hits in `text` as (line number, literal), comments excluded.

    Split out from `_offenders` so the detectors can be exercised against a
    string. Walking the real tree only tells you the tree is clean today; it
    cannot tell you the scanner would notice if it were not, which is exactly
    how issue #311 stayed hidden.
    """
    found = []
    for lineno, line in enumerate(strip_comments(text, suffix).splitlines(), 1):
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
        suffix = os.path.splitext(path)[1]
        with open(path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        rel = os.path.relpath(path, _REPO_ROOT)
        found.extend((rel, lineno, literal) for lineno, literal in scan_text(text, suffix))
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
        "https://tandem.lafuenti.com",
    ],
)
def test_detectors_match_the_things_they_are_meant_to_catch(sample):
    assert _PRIVATE_IP.search(sample) or _PERSONAL_HOST.search(sample)


def test_comment_stripping_blanks_comments_without_eating_code():
    kt = strip_comments(
        'val a = "keep.me"  // drop.me\n/* also\ndropped */\nval b = "keep.two"\n', ".kt"
    )
    assert "keep.me" in kt and "keep.two" in kt
    assert "drop.me" not in kt and "dropped" not in kt
    # Line numbering must survive so reported offenders point at the right line.
    assert len(kt.splitlines()) == 4

    xml = strip_comments("<!-- drop.me -->\n<domain>keep.me</domain>\n", ".xml")
    assert "drop.me" not in xml and "keep.me" in xml


# ---------------------------------------------------------------------------
# Issue #311: the guard could not see a URL.
#
# `strip_comments` blanked everything after `//` with no idea what a string
# literal is, so the scheme separator in any `http://` or `https://` was read as
# a line-comment opener and the rest of the line was erased before the detectors
# ran. A hardcoded `"https://tandem.lafuenti.com"` — issue #58's original defect,
# the exact thing this file was written to prevent — sailed straight through.
#
# Found by accident: #149 added four `192.168.1.5` literals to
# ServerUrlPolicyTest.kt and only one was reported, the one written
# `https:/192.168.1.5` with a single slash.
# ---------------------------------------------------------------------------


def test_a_url_in_a_string_literal_survives_comment_stripping():
    kt = strip_comments('val a = "https://host.example.com"  // drop.me', ".kt")
    assert "host.example.com" in kt, (
        "the // in https:// was treated as a comment opener, which is why this "
        "guard could not see any URL in Kotlin code"
    )
    assert "drop.me" not in kt, "real line comments must still be stripped"


def test_the_original_hardcoded_default_server_url_is_reported():
    """The #58 regression this file exists to prevent. Fails before #311."""
    found = scan_text('private const val DEFAULT = "https://tandem.lafuenti.com"\n', ".kt")
    assert [lit for _, lit in found] == ["tandem.lafuenti.com"]


def test_a_private_ip_inside_a_url_literal_is_reported():
    found = scan_text('val lan = "http://192.168.1.50:8000/api"\n', ".kt")
    assert [lit for _, lit in found] == ["192.168.1.50"]


def test_raw_string_blocks_are_scanned():
    """build.gradle.kts builds the network security config in a raw string."""
    kts = (
        'val cfg = ' + '"""' + '\n'
        '    <domain includeSubdomains="false">192.168.1.50</domain>\n'
        + '"""' + '.trimIndent()\n'
    )
    found = scan_text(kts, ".kts")
    assert [lit for _, lit in found] == ["192.168.1.50"]


def test_a_comment_marker_inside_a_string_does_not_open_a_comment():
    # The closing `*/` is load-bearing: without one the old regex found no match
    # and this passed against the very bug it is meant to catch.
    kt = strip_comments(
        'val open = "/*"\nval b = "keep.me"\nval close = "*/"\n', ".kt"
    )
    assert "keep.me" in kt, (
        "a `/*` inside a string literal opened a block comment that swallowed "
        "everything up to the next `*/` in the file"
    )


def test_comments_naming_the_production_host_stay_exempt():
    """The docstring's deliberate exemption — prose is not a baked-in host."""
    assert scan_text("// see tandem.lafuenti.com for why\n", ".kt") == []
    assert scan_text("/* tandem.lafuenti.com */\n", ".kt") == []
    assert scan_text("<!-- tandem.lafuenti.com -->\n", ".xml") == []


def test_reported_line_numbers_survive_stripping():
    found = scan_text('// pad\n\nval a = "http://192.168.1.50"\n', ".kt")
    assert found == [(3, "192.168.1.50")]


def test_allowed_literals_still_pass_when_visible_in_a_url():
    """10.0.2.2 is the emulator's alias for the host machine, allowed on purpose."""
    assert scan_text('val emu = "http://10.0.2.2:8000"\n', ".kt") == []
