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


def scan_plain_text(text: str) -> list[tuple[int, str]]:
    """The same detectors, with no comment stripping — for prose, not source.

    `strip_comments` exists to answer "is this `//` a comment or part of a URL?",
    a question only source code asks. Run it over Markdown and the `//` in every
    link blanks the rest of the line, hiding the host inside it. Documentation
    (docs/privacy.md, checked by test_docs_contract.py) is scanned with this
    instead: every line is content, so every line is examined.
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


# ---------------------------------------------------------------------------
# Issue #311: the guard could not see a URL.
#
# `strip_comments` blanked everything after `//` with no idea what a string
# literal is, so the scheme separator in any `http://` or `https://` was read as
# a line-comment opener and the rest of the line was erased before the detectors
# ran. A hardcoded `"https://tandem.example.com"` — issue #58's original defect,
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
    found = scan_text('private const val DEFAULT = "https://tandem.example.com"\n', ".kt")
    assert [lit for _, lit in found] == ["tandem.example.com"]


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
    assert scan_text("// see tandem.example.com for why\n", ".kt") == []
    assert scan_text("/* tandem.example.com */\n", ".kt") == []
    assert scan_text("<!-- tandem.example.com -->\n", ".xml") == []


def test_reported_line_numbers_survive_stripping():
    found = scan_text('// pad\n\nval a = "http://192.168.1.50"\n', ".kt")
    assert found == [(3, "192.168.1.50")]


def test_allowed_literals_still_pass_when_visible_in_a_url():
    """10.0.2.2 is the emulator's alias for the host machine, allowed on purpose."""
    assert scan_text('val emu = "http://10.0.2.2:8000"\n', ".kt") == []


# ---------------------------------------------------------------------------
# Issue #150: the app's declared third parties, pinned against the sources.
#
# The privacy policy (docs/privacy.md) and the Play Data safety form both rest on
# one factual claim: apart from the server the *user* configures, the app talks to
# exactly one outside host — `api.dictionaryapi.dev`, on demand, when the reader's
# "Define" action is used. Play enforces those answers retroactively, so the day
# someone adds a second destination is the day the published policy becomes false.
#
# Nothing else notices that. The tests above catch a *personal* host; a perfectly
# generic new SDK endpoint sails through them. This one fails instead, and its
# message says what else has to be updated.
# ---------------------------------------------------------------------------

# Every host the app may name in code, and why. Anything not here is a new
# destination and needs a policy/form revision before it can be allowlisted.
DECLARED_THIRD_PARTY_HOSTS = {
    # The dictionary lookup in the reader (AppModule.provideDictionaryRetrofit).
    # The single entry the privacy policy exists to declare.
    "api.dictionaryapi.dev",
}

_NON_DESTINATION_HOSTS = {
    # Retrofit's placeholder base URL while no server is configured
    # (ServerUrlPolicy.UNCONFIGURED_BASE_URL). Requests to it fail locally.
    "localhost",
    "127.0.0.1",
    # The emulator's alias for the developer's own machine.
    "10.0.2.2",
    "0.0.0.0",
    # The dead production hostname, kept so a stored value can be migrated off it
    # (ServerUrlPolicy.LEGACY_SERVER_URL). Nothing resolves; nothing is sent.
    "booksync.example.com",
    # Where the first-run screen sends someone who has no server yet
    # (ServerUrlPolicy.TANDEM_REPO_URL). Handed to the *browser* via an intent —
    # the app itself never requests it, and it carries no user data.
    "github.com",
    # RFC 2606 placeholders shown in hint text and error messages.
    "example.com",
    "tandem.example.com",
}

# `scheme://` followed by the authority. Stops at the first character that cannot
# be part of a host: path, quote, whitespace, or a Kotlin interpolation.
_URL_HOST = re.compile(r"https?://([^/\s\"'`)>\\$<{}]*)")

_ANDROID_JAVA = os.path.join(_ANDROID_APP, "src", "main", "java")


def hosts_in_kotlin(text: str) -> set[str]:
    """Hostnames appearing in non-comment Kotlin, port and userinfo stripped.

    Empty authorities (a bare `"https://"` used as a scheme test, of which
    ServerUrlPolicy has several) and interpolated ones (`https://$trimmed`,
    `http://$currentIp:$port`) are not hosts and are dropped — the regex stops at
    the `$`, so those come through as an empty string.
    """
    found = set()
    for authority in _URL_HOST.findall(strip_comments(text, ".kt")):
        host = authority.rsplit("@", 1)[-1].split(":", 1)[0].strip().lower()
        if host:
            found.add(host)
    return found


def _hardcoded_hosts():
    """(relative path, host) for every host literal under android/app/src/main/java."""
    found = []
    for root, dirs, files in os.walk(_ANDROID_JAVA):
        dirs[:] = [d for d in dirs if d not in {"build", ".gradle", ".kotlin"}]
        for name in files:
            if not name.endswith(".kt"):
                continue
            path = os.path.join(root, name)
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
            rel = os.path.relpath(path, _REPO_ROOT)
            found.extend((rel, host) for host in sorted(hosts_in_kotlin(text)))
    return found


def test_only_declared_third_party_hosts_are_hardcoded():
    allowed = DECLARED_THIRD_PARTY_HOSTS | _NON_DESTINATION_HOSTS
    offenders = [(rel, host) for rel, host in _hardcoded_hosts() if host not in allowed]
    assert not offenders, (
        "Undeclared hosts hardcoded in the Android sources:\n"
        + "\n".join(f"  {rel} -> {host}" for rel, host in offenders)
        + "\n\nThe privacy policy (docs/privacy.md) and the Play Data safety form "
        "in docs/play-listing.md both state that the only third party the app "
        "contacts is api.dictionaryapi.dev. Update both, then add the host to "
        "DECLARED_THIRD_PARTY_HOSTS with a note saying what it receives — or, if "
        "it is not a data destination, to _NON_DESTINATION_HOSTS."
    )


def test_the_declared_dictionary_host_is_actually_still_there():
    """The reverse: a policy declaring a call the app no longer makes is also wrong.

    Without this, deleting the Define feature would leave the test above passing
    against an empty tree and the published policy over-declaring.
    """
    hosts = {host for _, host in _hardcoded_hosts()}
    assert DECLARED_THIRD_PARTY_HOSTS <= hosts, (
        f"docs/privacy.md declares {sorted(DECLARED_THIRD_PARTY_HOSTS)} but the "
        f"sources no longer contain it. Found: {sorted(hosts)}. If the dictionary "
        "lookup was removed, remove the declaration from the policy and the Data "
        "safety form too."
    )


@pytest.mark.parametrize(
    "source, expected",
    [
        # The real shape of the one declared call.
        ('.baseUrl("https://api.dictionaryapi.dev/")', {"api.dictionaryapi.dev"}),
        # A new SDK endpoint: generic, no personal host, invisible to every other
        # test in this file. This is the case the guard exists for.
        ('val ingest = "https://telemetry.vendor.example/v1/events"',
         {"telemetry.vendor.example"}),
        # Ports and credentials are not part of the host.
        ('val u = "http://user:pw@api.dictionaryapi.dev:8443/x"',
         {"api.dictionaryapi.dev"}),
        # Interpolation is not a host — the LAN cast server builds its URL this way.
        ('val cast = "http://$currentIp:$localCastPort/"', set()),
        # A bare scheme used as a prefix test is not a host either.
        ('if (lower.startsWith("https://")) return trimmed', set()),
        # Comments are exempt here for the same reason as the rest of the file.
        ('// see https://telemetry.vendor.example for why\n', set()),
    ],
)
def test_host_extraction_sees_what_it_must_see(source, expected):
    assert hosts_in_kotlin(source + "\n") == expected


def test_plain_text_scan_sees_a_host_inside_a_markdown_link():
    """The reason docs are not scanned with `strip_comments` (issue #150).

    Source mode reads the `//` in `https://` as a line comment unless it is
    inside a string literal. Markdown has no string literals, so the whole link
    would be blanked and a personal host in a published policy would pass.
    """
    line = "Point the app at [my server](https://tandem.example.com/api).\n"
    assert scan_plain_text(line) == [(1, "tandem.example.com")]
    assert scan_text(line, ".kt") == [], (
        "source mode is expected to miss this — that is what scan_plain_text is for"
    )


def test_plain_text_scan_still_honours_the_allowlist():
    assert scan_plain_text("Try http://127.0.0.1:8000 on the same machine.\n") == []
