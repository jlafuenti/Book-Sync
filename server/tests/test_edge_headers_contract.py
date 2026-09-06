"""
Edge-proxy security-header contract (issue #178).

The web app keeps a 24 h access JWT and a 30 day refresh JWT in localStorage,
so the one control that limits what an injected script can do with them is a
Content-Security-Policy — and the proxy that would carry it (plus HSTS,
nosniff, frame denial) was a hand-edited file on the host, not in the repo.
`Caddyfile.example` is now that recipe, and these tests keep it honest:

  * every required header directive is present, with the values the docs
    promise (HSTS one year + includeSubDomains, no preload by default);
  * the CSP has the directives the SPA and the epub.js reader need, and none
    of the sources that would make it decorative (`'unsafe-eval'`, an inline
    allowance on `script-src`, wildcard hosts);
  * the CSP ships as Report-Only, because a wrong *enforcing* policy breaks
    the reader silently — the file says how to flip it;
  * the file names no real host and no private address, only placeholders;
  * the web nginx repeats the cheap headers and hides its version, so a
    deployment without Caddy still gets those.

File-reading style, like test_compose_contract.py and test_proxy_contract.py —
no Docker, no Caddy binary. The inline-script hash inside `script-src` is
checked from the web side (`web/src/security/cspInlineScript.test.js`), where
the script lives.
"""

import os
import re

import pytest

from tests.personal_identifiers import PERSONAL_DOMAIN
from tests.test_android_no_personal_hosts import _PRIVATE_IP

_SERVER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_REPO_ROOT = os.path.dirname(_SERVER_DIR)

CADDYFILE = os.path.join(_REPO_ROOT, "Caddyfile.example")
WEB_DOCKERFILE = os.path.join(_REPO_ROOT, "web", "Dockerfile")

PLACEHOLDER_HOST = "tandem.example.com"


def _read(path: str) -> str:
    assert os.path.isfile(path), f"{path} is missing"
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _strip_comments(text: str) -> str:
    """Drop `# ...` comment lines so a directive mentioned in prose does not
    count as present, and a hostname in a comment does not count as absent."""
    return "\n".join(
        line for line in text.splitlines() if not line.strip().startswith("#")
    )


def _header_block(text: str) -> str:
    """The body of the site's `header { ... }` block, comments removed."""
    stripped = _strip_comments(text)
    m = re.search(r"^\s*header\s*\{", stripped, re.MULTILINE)
    assert m, "Caddyfile.example has no `header { ... }` block"
    depth, start = 0, m.end()
    for i in range(m.end() - 1, len(stripped)):
        if stripped[i] == "{":
            depth += 1
        elif stripped[i] == "}":
            depth -= 1
            if depth == 0:
                return stripped[start:i]
    raise AssertionError("Caddyfile.example: unbalanced `header {` block")


def _csp_directives(text: str) -> dict[str, list[str]]:
    """The CSP (enforcing or Report-Only) as {directive: [sources]}."""
    block = _header_block(text)
    m = re.search(
        r'^\s*Content-Security-Policy(?:-Report-Only)?\s+"([^"]+)"',
        block,
        re.MULTILINE,
    )
    assert m, "Caddyfile.example's header block sets no Content-Security-Policy"
    directives: dict[str, list[str]] = {}
    for clause in m.group(1).split(";"):
        parts = clause.split()
        if not parts:
            continue
        directives[parts[0]] = parts[1:]
    return directives


# ---------------------------------------------------------------------------
# Placeholders only
# ---------------------------------------------------------------------------


def test_caddyfile_uses_the_placeholder_hostname():
    text = _read(CADDYFILE)
    assert re.search(rf"^{re.escape(PLACEHOLDER_HOST)}\s*\{{", text, re.MULTILINE), (
        f"Caddyfile.example's site block is not `{PLACEHOLDER_HOST} {{` — the "
        "docs and the curl smoke check are written against that placeholder."
    )


def test_caddyfile_names_no_real_host_or_private_address():
    text = _read(CADDYFILE)
    assert PERSONAL_DOMAIN not in text, (
        "Caddyfile.example names the maintainer's real domain. The example "
        f"must use `{PLACEHOLDER_HOST}`; the real name lives only on the host."
    )
    hit = _PRIVATE_IP.search(text)
    assert hit is None, (
        f"Caddyfile.example contains a private address ({hit.group(0)}). Point "
        "the upstreams at compose service names or localhost, not a LAN IP."
    )


# ---------------------------------------------------------------------------
# The non-CSP headers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "needle",
    [
        'X-Content-Type-Options "nosniff"',
        'X-Frame-Options "DENY"',
        "Referrer-Policy",
        "Permissions-Policy",
        "-Server",
        "-Via",
    ],
)
def test_caddyfile_header_block_sets_each_required_header(needle):
    block = _header_block(_read(CADDYFILE))
    assert needle in block, f"Caddyfile.example's header block lacks {needle!r}"


def test_caddyfile_hsts_is_one_year_with_subdomains_and_no_preload():
    block = _header_block(_read(CADDYFILE))
    m = re.search(r'Strict-Transport-Security\s+"([^"]+)"', block)
    assert m, "Caddyfile.example's header block sets no Strict-Transport-Security"
    value = m.group(1)
    assert "max-age=31536000" in value, f"HSTS max-age is not one year: {value!r}"
    assert "includeSubDomains" in value, f"HSTS lacks includeSubDomains: {value!r}"
    assert "preload" not in value, (
        "HSTS carries `preload` by default. Preload is a browser-list "
        "submission that is slow to undo; the example must leave it to the "
        "operator (a comment explains when to add it)."
    )


def test_caddyfile_header_block_is_deferred():
    """Without `defer`, Caddy applies the block before the upstream answers,
    so nginx's `Server:` header survives `-Server` and its Referrer-Policy
    overrides ours."""
    block = _header_block(_read(CADDYFILE))
    assert re.search(r"^\s*defer\s*$", block, re.MULTILINE), (
        "Caddyfile.example's header block has no `defer` sub-directive."
    )


# ---------------------------------------------------------------------------
# Content-Security-Policy
# ---------------------------------------------------------------------------


def test_csp_ships_report_only_with_instructions_to_enforce():
    text = _read(CADDYFILE)
    block = _header_block(text)
    assert "Content-Security-Policy-Report-Only" in block, (
        "The CSP is enforcing from the first deploy. A wrong enforcing policy "
        "breaks the ebook reader silently; ship Report-Only first."
    )
    assert not re.search(r'^\s*Content-Security-Policy\s+"', block, re.MULTILINE), (
        "Caddyfile.example sets both an enforcing and a Report-Only CSP."
    )
    comments = "\n".join(
        line for line in text.splitlines() if line.strip().startswith("#")
    )
    assert "enforc" in comments.lower(), (
        "Caddyfile.example never explains how to flip the CSP from "
        "Report-Only to enforcing."
    )
    assert "srcdoc" in comments, (
        "Caddyfile.example should say that epub.js's srcdoc iframes inherit "
        "this policy — that is why the reader's blob:/inline needs are here."
    )


@pytest.mark.parametrize(
    "directive, required_sources",
    [
        ("default-src", ["'self'"]),
        ("base-uri", ["'self'"]),
        ("object-src", ["'none'"]),
        ("frame-ancestors", ["'none'"]),
        ("form-action", ["'self'"]),
        ("script-src", ["'self'"]),
        ("worker-src", ["'self'"]),
        ("style-src", ["'self'", "'unsafe-inline'", "blob:", "https://fonts.googleapis.com"]),
        ("font-src", ["'self'", "blob:", "https://fonts.gstatic.com"]),
        ("img-src", ["'self'", "data:", "blob:"]),
        ("media-src", ["'self'", "blob:"]),
        ("frame-src", ["'self'", "blob:"]),
        ("connect-src", ["'self'"]),
        ("manifest-src", ["'self'"]),
    ],
)
def test_csp_has_each_required_directive(directive, required_sources):
    directives = _csp_directives(_read(CADDYFILE))
    assert directive in directives, f"CSP lacks a {directive} directive"
    for src in required_sources:
        assert src in directives[directive], (
            f"CSP {directive} lacks {src}: {' '.join(directives[directive])}"
        )


def test_csp_script_src_carries_exactly_one_hash_and_nothing_unsafe():
    directives = _csp_directives(_read(CADDYFILE))
    script = directives["script-src"]
    hashes = [s for s in script if s.startswith("'sha256-")]
    assert len(hashes) == 1, (
        f"script-src should carry exactly one sha256 hash (the theme bootstrap "
        f"in web/index.html), found {hashes}"
    )
    assert "'unsafe-inline'" not in script, (
        "script-src allows 'unsafe-inline' — that turns the CSP into decoration "
        "for the one attack it exists to limit."
    )


def test_csp_has_no_unsafe_eval_or_wildcards():
    text = _read(CADDYFILE)
    directives = _csp_directives(text)
    flat = [src for sources in directives.values() for src in sources]
    assert "'unsafe-eval'" not in flat, "CSP allows 'unsafe-eval'"
    for src in flat:
        assert src != "*" and not src.startswith("*."), f"CSP has a wildcard source {src!r}"
        assert src not in ("https:", "http:"), (
            f"CSP has a bare scheme source {src!r} — name the hosts instead."
        )
    assert "frame-src" in directives and "data:" not in directives["frame-src"], (
        "frame-src allows data: — epub.js renders through srcdoc (inherits the "
        "policy) or blob:, neither needs data: frames."
    )


# ---------------------------------------------------------------------------
# The nginx fallback in web/Dockerfile
# ---------------------------------------------------------------------------


def test_web_nginx_turns_server_tokens_off():
    text = _read(WEB_DOCKERFILE)
    assert re.search(r"server_tokens\s+off;", text), (
        "web/Dockerfile's nginx config does not set `server_tokens off;` — the "
        "nginx version is advertised on every response."
    )


@pytest.mark.parametrize(
    "header, value",
    [
        ("X-Content-Type-Options", "nosniff"),
        ("X-Frame-Options", "DENY"),
        ("Referrer-Policy", "same-origin"),
    ],
)
def test_web_nginx_sets_the_cheap_headers_with_always(header, value):
    text = _read(WEB_DOCKERFILE)
    assert re.search(rf'add_header\s+{header}\s+"{value}"\s+always;', text), (
        f"web/Dockerfile's nginx config does not `add_header {header} "
        f'"{value}" always;`'
    )


def test_web_nginx_every_location_with_add_header_includes_the_security_headers():
    """nginx add_header does not merge: a location with its own add_header
    discards every inherited one. So every such location must pull the shared
    security-header file back in, or the document/asset it serves goes out
    without nosniff and frame denial."""
    text = _read(WEB_DOCKERFILE)
    m = re.search(r"RUN echo '(server \{.*?)' > /etc/nginx/conf.d/default\.conf", text, re.DOTALL)
    assert m, "web/Dockerfile: could not find the generated nginx server block"
    server_block = m.group(1)
    include_re = re.compile(r"include\s+/etc/nginx/security-headers\.inc;")
    assert include_re.search(server_block), (
        "web/Dockerfile's server block never includes /etc/nginx/security-headers.inc"
    )
    for loc in re.finditer(r"(location[^{]*\{)(.*?)\n\s*\}", server_block, re.DOTALL):
        head, body = loc.group(1), loc.group(2)
        if "add_header" in body:
            assert include_re.search(body), (
                f"web/Dockerfile: `{head.strip()}` sets add_header without "
                "including /etc/nginx/security-headers.inc, so it drops the "
                "server-level security headers."
            )
