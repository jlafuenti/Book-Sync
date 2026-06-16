"""
Ebook integrity validation.

Counterpart to services.audio_integrity for the ebook side of a pair. Ebooks can
arrive DRM-encrypted (an import that never actually de-DRM'd the file) or
otherwise unreadable; such files can never be aligned to audio. Without an early
check they fail only at the post-transcription extraction step — after a
multi-hour transcription has already run — and with a cryptic parser error
(e.g. ebooklib's `'NoneType' object has no attribute 'find'`).

`check_ebook_integrity` validates the ebook up front:

  1. DRM detection (EPUB): presence of `META-INF/encryption.xml` referencing
     Adobe ADEPT / XML-ENC means the content is encrypted and unusable.
  2. Extraction sanity (any format): actually parse the book and confirm it
     yields a non-trivial amount of text.

Designed to run on the server before transcription so a bad ebook fails in
seconds with a clear, actionable message.
"""

import logging
import zipfile
from typing import Tuple

logger = logging.getLogger(__name__)

# An EPUB whose extraction yields fewer than this many sentences is almost
# certainly encrypted or corrupt (a real book yields thousands).
_MIN_SENTENCES = 50


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
    Validate that an ebook is readable and yields usable text.

    Returns (ok, detail). When ok is False, `detail` is a short, user-facing
    reason (e.g. for a queue error message). Never raises for ebook problems.
    """
    lower = path.lower()

    # Stage 1: DRM detection (EPUB only; deterministic).
    if lower.endswith(".epub"):
        try:
            with zipfile.ZipFile(path):
                pass
        except zipfile.BadZipFile:
            return False, "EPUB is not a valid zip (corrupt download) — re-import required"
        except OSError as e:
            return False, f"could not open ebook file: {e}"

        if _epub_is_drm_encrypted(path):
            return False, "EPUB is DRM-encrypted (Adobe ADEPT) — re-import a DRM-free copy"

    # Stage 2: extraction sanity (all formats).
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
