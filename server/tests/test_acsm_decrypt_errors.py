"""A failed DeDRM decryption must say so (issue #566).

DeDRM 10.0.3 raises inside `ineptepub.decryptBook` for an ADEPT EPUB that also
carries obfuscated fonts; its blanket `except` turns that into rc 2, the fulfill
helper exits 6, and every hint in `_ADEPT_ERROR_HINTS` misses. The operator was
told only "ACSM conversion failed (exit 6); see server logs.", which points at
the loan or the Adobe authorization — neither of which is at fault, and neither
of which a retry can fix.
"""

from services.import_sources.acsm import _friendly_acsm_error

# Trimmed from a real reproduction against the plugin in the image (issue #566).
DEDRM_DIAG = """\
Loading DeDRM plugin...
book.epub is a secure Adobe Adept ePub.
Could not decrypt book.epub because of an exception:
Traceback (most recent call last):
  File "calibre_plugins.dedrm.ineptepub", line 342, in decryptBook
    data = decryptor.get_xml()
  File "calibre_plugins.dedrm.ineptepub", line 180, in get_xml
    return "<?xml version=\\"1.0\\" encoding=\\"UTF-8\\"?>\\n" + etree.tostring(self._encryption, ...)
AttributeError: 'Decryptor' object has no attribute '_encryption'. Did you mean: '_encrypted'?
Decryption failed (ineptepub rc=2)
"""


def test_a_dedrm_decryption_failure_names_the_plugin_not_the_loan():
    message = _friendly_acsm_error(DEDRM_DIAG, 6)

    assert "exit 6" not in message, "the generic fallback tells the operator nothing"
    lowered = message.lower()
    assert "decrypt" in lowered
    # The operator must be able to tell this from an Adobe authorization problem.
    assert "dedrm" in lowered or "plugin" in lowered
    # Carry the exception line: it is what distinguishes this from a corrupt book.
    assert "AttributeError" in message
    assert len(message) <= 400


def test_the_rc_line_alone_is_enough_to_recognise_it():
    """`_acsm_fulfill.py` prints the traceback to stdout; a diagnostic that kept
    only its own line must still be recognised."""
    message = _friendly_acsm_error("Decryption failed (ineptepub rc=2)\n", 6)

    assert "exit 6" not in message
    assert "decrypt" in message.lower()


def test_an_adobe_error_still_wins_over_the_decryption_hint():
    """Ordering guard: a fulfillment refusal is the operator's problem to fix and
    must not be masked by a decryption message further down the log."""
    diag = "E_ADEPT_REQUEST_EXPIRED\n" + DEDRM_DIAG

    assert "expired" in _friendly_acsm_error(diag, 6).lower()


def test_an_unrelated_failure_still_falls_back_to_the_generic_message():
    assert _friendly_acsm_error("something else went wrong\n", 6) == (
        "ACSM conversion failed (exit 6); see server logs."
    )
