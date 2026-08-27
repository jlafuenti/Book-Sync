"""
Android source contract: no personal hosts baked into the build (issue #58).

The shipped Android app twice hardcoded one developer's machine — the default
server URL (`https://tandem.example.com`) and a dev LAN address in the
cleartext-HTTP allowlist (`network_security_config.xml`). Both are meaningless
to anyone else building from a clean clone, and the second one silently decides
which hosts the app will talk to over plain HTTP.

Both are now build properties (`tandem.defaultServerUrl`, `tandem.cleartextHosts`)
that default to empty. This test keeps them that way: it walks the committed
Android sources and fails on a private-network literal or a `example.com`
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
    "booksync.example.com",
}

# Private-network ranges (RFC1918) plus link-local. Matching literal IPs is the
# point; a baked-in hostname is caught by the example.com rule below.
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


def strip_comments(text: str, suffix: str) -> str:
    """Blank out comments, preserving line numbering so offenders stay locatable."""

    def _blank(match: re.Match) -> str:
        # Keep the newlines so line numbers don't shift.
        return re.sub(r"[^\n]", " ", match.group(0))

    if suffix == ".xml":
        return _XML_COMMENT.sub(_blank, text)

    text = _KOTLIN_BLOCK_COMMENT.sub(_blank, text)
    if suffix == ".pro":
        return re.sub(r"#[^\n]*", _blank, text)
    return re.sub(r"//[^\n]*", _blank, text)


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
            body = strip_comments(fh.read(), suffix)
        for lineno, line in enumerate(body.splitlines(), 1):
            for match in list(_PRIVATE_IP.finditer(line)) + list(_PERSONAL_HOST.finditer(line)):
                literal = match.group(0)
                if literal in ALLOWED_LITERALS:
                    continue
                found.append((os.path.relpath(path, _REPO_ROOT), lineno, literal))
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
        "https://tandem.example.com",
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
