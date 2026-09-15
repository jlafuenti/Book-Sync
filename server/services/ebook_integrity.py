"""
Ebook integrity validation.

Counterpart to services.audio_integrity for the ebook side of a pair. Ebooks can
arrive DRM-encrypted (an import that never actually de-DRM'd the file) or
otherwise unreadable; such files can never be aligned to audio. Without an early
check they fail only at the post-transcription extraction step — after a
multi-hour transcription has already run — and with a cryptic parser error
(e.g. ebooklib's `'NoneType' object has no attribute 'find'`).

`check_ebook_integrity` validates the ebook up front:

  1. Format (issue #101): only EPUB can be aligned, because EPUB is the only
     thing the readers render. Parsing anything else produces a sync map on an
     axis no reader shares.
  2. DRM detection (EPUB): a `META-INF/encryption.xml` that encrypts content
     (Adobe ADEPT or any other cipher) means the book is unusable. One that only
     obfuscates embedded fonts is not DRM (issue #560).
  3. Extraction sanity: actually parse the book and confirm it yields a
     non-trivial amount of text.

Designed to run on the server before transcription so a bad ebook fails in
seconds with a clear, actionable message.
"""

import logging
import zipfile
from pathlib import Path
from typing import Tuple

logger = logging.getLogger(__name__)

# An EPUB whose extraction yields fewer than this many sentences is almost
# certainly encrypted or corrupt (a real book yields thousands).
_MIN_SENTENCES = 50

#: The only format that can be aligned. Readium (Android) and epub.js (web) both
#: render EPUB and nothing else, so a sync map built from any other artifact
#: names chapters the reader cannot resolve — `extract_mobi_sentences` in
#: particular flattens a whole book into one chapter (issue #101).
ALIGNABLE_SUFFIX = ".epub"

#: Formats the Convert flow (System → Unsupported) can turn into an EPUB.
_CONVERTIBLE = (".mobi", ".azw3", ".azw")


def format_is_alignable(path: str) -> Tuple[bool, str]:
    """Check a path's extension against the one format alignment can use.

    Split out from `check_ebook_integrity` so callers that align without going
    through the integrity check (the realign endpoint) apply the same rule and
    quote the same message.
    """
    suffix = Path(path).suffix.lower()
    if suffix == ALIGNABLE_SUFFIX:
        return True, "ok"
    if suffix in _CONVERTIBLE:
        return False, (
            f"{suffix} cannot be aligned — the readers only render EPUB. "
            f"Convert it under System → Unsupported, then transcribe the "
            f"converted EPUB."
        )
    return False, (
        f"{suffix or 'this file'} cannot be aligned — only EPUB can be, because "
        f"it is the only format the readers render."
    )


#: Font obfuscation (issue #560). These mangle an embedded font with a key derived from the book's
#: own identifier so the font cannot be lifted out; the text stays plain and every reader opens the
#: book. Both are defined for fonts only.
_FONT_OBFUSCATION_ALGORITHMS = frozenset({
    "http://www.idpf.org/2008/embedding",  # IDPF (EPUB 3 font obfuscation)
    "http://ns.adobe.com/pdf/enc#RC",  # Adobe (what Calibre writes)
})
_FONT_SUFFIXES = (".ttf", ".otf", ".ttc", ".woff", ".woff2", ".eot", ".pfb", ".pfm", ".afm")
_ADEPT_NS = "ns.adobe.com/adept"


def _local(tag) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _is_font_uri(uri: str) -> bool:
    from urllib.parse import unquote

    target = unquote(uri.split("#", 1)[0].split("?", 1)[0]).strip().lower()
    return target.endswith(_FONT_SUFFIXES)


def _encryption_manifest_is_drm(xml: bytes) -> bool:
    """True if `META-INF/encryption.xml` encrypts anything beyond obfuscated fonts.

    Every entry must name a font-obfuscation algorithm, point at a font file and carry no ADEPT
    key; any other entry is content a reader needs a key for. A manifest that cannot be parsed,
    or an entry missing its algorithm or target, counts as DRM: it might be hiding exactly that.
    """
    from lxml import etree

    # Untrusted archive XML (issue #265): never resolve entities or touch the network.
    safe = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False)
    try:
        root = etree.fromstring(xml, parser=safe)
    except Exception:  # any parse failure: conservative, and this must never raise
        return True
    if root is None:
        return True

    for data in (el for el in root.iter() if _local(el.tag) == "EncryptedData"):
        algorithms = [el.get("Algorithm") for el in data.iter() if _local(el.tag) == "EncryptionMethod"]
        uris = [el.get("URI") for el in data.iter() if _local(el.tag) == "CipherReference"]
        if not algorithms or not uris or None in algorithms or None in uris:
            return True
        if any(a.strip() not in _FONT_OBFUSCATION_ALGORITHMS for a in algorithms):
            return True
        if not all(_is_font_uri(u) for u in uris):
            return True
        if any(_ADEPT_NS in (el.tag if isinstance(el.tag, str) else "") for el in data.iter()):
            return True
    return False


def epub_is_drm_encrypted(path: str) -> bool:
    """True if the EPUB's encryption manifest encrypts content, not just fonts.

    No `META-INF/encryption.xml` means nothing is encrypted. One that only obfuscates embedded
    fonts is not DRM (issue #560). An Adobe ADEPT `META-INF/rights.xml` next to any manifest counts
    as DRM; on its own, with nothing encrypted, it locks nothing.

    Runs standalone under `calibre-debug -e` too (the ACSM import's post-decryption check), so it
    imports nothing from `services` at module level.
    """
    try:
        with zipfile.ZipFile(path) as z:
            names = set(z.namelist())
            if "META-INF/encryption.xml" not in names:
                return False
            enc = z.read("META-INF/encryption.xml")
            rights = z.read("META-INF/rights.xml") if "META-INF/rights.xml" in names else b""
    except (zipfile.BadZipFile, KeyError, OSError):
        return False
    if _ADEPT_NS.encode() in rights:
        return True
    return _encryption_manifest_is_drm(enc)


def check_ebook_integrity(path: str) -> Tuple[bool, str]:
    """
    Validate that an ebook can be aligned: right format, readable, yields text.

    Returns (ok, detail). When ok is False, `detail` is a short, user-facing
    reason (e.g. for a queue error message). Never raises for ebook problems.
    """
    # Stage 0: format. Cheapest check and the most fundamental one — a .mobi
    # parses fine and still can't be aligned, because the reader renders a
    # different document (issue #101). Everything below therefore knows it is
    # looking at an EPUB.
    ok_format, detail_format = format_is_alignable(path)
    if not ok_format:
        return False, detail_format

    # Stage 1: DRM detection (deterministic).
    try:
        with zipfile.ZipFile(path):
            pass
    except zipfile.BadZipFile:
        return False, "EPUB is not a valid zip (corrupt download) — re-import required"
    except OSError as e:
        return False, f"could not open ebook file: {e}"

    if epub_is_drm_encrypted(path):
        return False, "EPUB is DRM-encrypted (Adobe ADEPT) — re-import a DRM-free copy"

    # Stage 2: extraction sanity.
    try:
        from services.epub_parser import extract_book_sentences
        sentences = extract_book_sentences(path)
    except Exception as e:
        return False, f"ebook parse failed: {e}"

    if len(sentences) < _MIN_SENTENCES:
        return False, (
            f"ebook produced almost no text ({len(sentences)} sentences) — "
            f"likely encrypted or corrupt"
        )

    return True, f"ok ({len(sentences)} sentences)"
