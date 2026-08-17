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
  2. DRM detection (EPUB): presence of `META-INF/encryption.xml` referencing
     Adobe ADEPT / XML-ENC means the content is encrypted and unusable.
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


def _epub_is_drm_encrypted(path: str) -> bool:
    """True if the EPUB carries an Adobe ADEPT / XML-ENC encryption manifest."""
    try:
        with zipfile.ZipFile(path) as z:
            if "META-INF/encryption.xml" not in z.namelist():
                return False
            enc = z.read("META-INF/encryption.xml").decode("utf-8", errors="ignore").lower()
    except (zipfile.BadZipFile, KeyError, OSError):
        return False
    return "ns.adobe.com/adept" in enc or "xmlenc#" in enc or "encrypteddata" in enc


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

    if _epub_is_drm_encrypted(path):
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
