"""
Obfuscated fonts are not DRM (issue #560).

Every `META-INF/encryption.xml` speaks XML-ENC (`<enc:EncryptedData>`), including the ones that
only obfuscate embedded fonts with the IDPF or Adobe algorithm. The font bytes are mangled so the
font cannot be lifted out of the book; the text is plain and every reader opens it. Calibre writes
such files when it converts an AZW3 with embedded fonts, so Tandem's own Convert flow produced
books the integrity check then refused as "DRM-encrypted (Adobe ADEPT)".

An EPUB is DRM-encrypted only when the manifest encrypts something a reader needs the key for:
an entry with any other algorithm, or an entry that is not a font.
"""

import importlib.util
import sys
import zipfile
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from services.ebook_integrity import epub_is_drm_encrypted, check_ebook_integrity
from tests.factories import write_epub

IDPF_FONT = "http://www.idpf.org/2008/embedding"
ADOBE_FONT = "http://ns.adobe.com/pdf/enc#RC"
AES = "http://www.w3.org/2001/04/xmlenc#aes128-cbc"

DRM_DETAIL = "EPUB is DRM-encrypted (Adobe ADEPT) — re-import a DRM-free copy"


def _prose(chapter: int, sentences: int = 30) -> str:
    body = " ".join(
        f"Sentence {i} of chapter {chapter} is plain readable prose about the axis." for i in range(sentences)
    )
    return f"<html><body><h1>Chapter {chapter}</h1><p>{body}</p></body></html>"


def _entry(uri: str, algorithm: str, *, adept_key: bool = False) -> str:
    key_info = (
        '<KeyInfo xmlns="http://www.w3.org/2000/09/xmldsig#">'
        '<resource xmlns="http://ns.adobe.com/adept">urn:uuid:axis</resource></KeyInfo>'
        if adept_key
        else ""
    )
    return (
        '<enc:EncryptedData xmlns:enc="http://www.w3.org/2001/04/xmlenc#">'
        f'<enc:EncryptionMethod Algorithm="{algorithm}"/>'
        f"{key_info}"
        f'<enc:CipherData><enc:CipherReference URI="{uri}"/></enc:CipherData>'
        "</enc:EncryptedData>"
    )


def _manifest(*entries: str) -> str:
    return (
        '<?xml version="1.0"?>'
        '<encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
        f"{''.join(entries)}</encryption>"
    )


def _book(tmp_path, name, encryption_xml=None, *, rights_xml=None, extra=()):
    path = write_epub(tmp_path / name, [(f"Text/ch{i}.xhtml", _prose(i)) for i in range(3)])
    with zipfile.ZipFile(path, "a") as z:
        if encryption_xml is not None:
            z.writestr("META-INF/encryption.xml", encryption_xml)
        if rights_xml is not None:
            z.writestr("META-INF/rights.xml", rights_xml)
        for member, data in extra:
            z.writestr(member, data)
    return path


ADEPT_RIGHTS = (
    '<?xml version="1.0"?>'
    '<adept:rights xmlns:adept="http://ns.adobe.com/adept">'
    "<adept:licenseToken><adept:encryptedKey>AAAA</adept:encryptedKey></adept:licenseToken>"
    "</adept:rights>"
)


# ---------------------------------------------------------------------------
# Font obfuscation alone is not DRM.
# ---------------------------------------------------------------------------


def test_adobe_obfuscated_font_is_not_drm_and_the_book_passes(tmp_path):
    epub = _book(
        tmp_path, "adobe-font.epub", _manifest(_entry("OEBPS/fonts/a.ttf", ADOBE_FONT)),
        extra=[("OEBPS/fonts/a.ttf", b"\x00obfuscated")],
    )

    assert not epub_is_drm_encrypted(epub)
    ok, detail = check_ebook_integrity(epub)
    assert ok, detail


def test_idpf_obfuscated_font_is_not_drm(tmp_path):
    epub = _book(tmp_path, "idpf-font.epub", _manifest(_entry("OEBPS/fonts/a.otf", IDPF_FONT)))

    assert not epub_is_drm_encrypted(epub)
    assert check_ebook_integrity(epub)[0]


def test_several_obfuscated_fonts_of_both_kinds_are_not_drm(tmp_path):
    epub = _book(
        tmp_path, "fonts.epub",
        _manifest(
            _entry("OEBPS/fonts/00001.ttf", ADOBE_FONT),
            _entry("OEBPS/Fonts/Axis%20Test.OTF", IDPF_FONT),
            _entry("OEBPS/fonts/c.woff2", IDPF_FONT),
        ),
    )

    assert not epub_is_drm_encrypted(epub)


def test_the_manifest_is_matched_by_local_name_not_prefix(tmp_path):
    """Calibre writes `enc:` prefixes, other tools a default namespace; both mean the same."""
    manifest = (
        '<?xml version="1.0"?>'
        '<encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
        '<EncryptedData xmlns="http://www.w3.org/2001/04/xmlenc#">'
        f'<EncryptionMethod Algorithm="{IDPF_FONT}"/>'
        '<CipherData><CipherReference URI="fonts/a.ttf"/></CipherData>'
        "</EncryptedData></encryption>"
    )
    epub = _book(tmp_path, "default-ns.epub", manifest)

    assert not epub_is_drm_encrypted(epub)


# ---------------------------------------------------------------------------
# Real encryption is still DRM.
# ---------------------------------------------------------------------------


def test_an_encrypted_content_document_is_drm(tmp_path):
    epub = _book(tmp_path, "aes.epub", _manifest(_entry("OEBPS/Text/ch1.xhtml", AES)))

    assert epub_is_drm_encrypted(epub)
    assert check_ebook_integrity(epub) == (False, DRM_DETAIL)


def test_a_content_document_under_the_font_algorithm_is_still_drm(tmp_path):
    """The obfuscation algorithms are defined for fonts only; anything else is not ours to trust."""
    epub = _book(tmp_path, "odd.epub", _manifest(_entry("OEBPS/Text/ch1.xhtml", IDPF_FONT)))

    assert epub_is_drm_encrypted(epub)


def test_a_font_under_a_real_cipher_is_drm(tmp_path):
    epub = _book(tmp_path, "aes-font.epub", _manifest(_entry("OEBPS/fonts/a.ttf", AES)))

    assert epub_is_drm_encrypted(epub)


def test_adept_book_is_drm(tmp_path):
    epub = _book(
        tmp_path, "adept.epub",
        _manifest(
            _entry("OEBPS/Text/ch0.xhtml", AES, adept_key=True),
            _entry("OEBPS/Text/ch1.xhtml", AES, adept_key=True),
        ),
        rights_xml=ADEPT_RIGHTS,
    )

    assert epub_is_drm_encrypted(epub)
    assert check_ebook_integrity(epub) == (False, DRM_DETAIL)


def test_adept_key_info_on_a_font_entry_is_drm(tmp_path):
    epub = _book(tmp_path, "adept-font.epub", _manifest(_entry("OEBPS/fonts/a.ttf", ADOBE_FONT, adept_key=True)))

    assert epub_is_drm_encrypted(epub)


def test_adept_rights_with_an_encryption_manifest_is_drm(tmp_path):
    epub = _book(
        tmp_path, "adept-rights.epub", _manifest(_entry("OEBPS/fonts/a.ttf", ADOBE_FONT)),
        rights_xml=ADEPT_RIGHTS,
    )

    assert epub_is_drm_encrypted(epub)


def test_obfuscated_fonts_plus_one_encrypted_chapter_is_drm(tmp_path):
    epub = _book(
        tmp_path, "mixed.epub",
        _manifest(
            _entry("OEBPS/fonts/a.ttf", ADOBE_FONT),
            _entry("OEBPS/fonts/b.otf", IDPF_FONT),
            _entry("OEBPS/Text/ch2.xhtml", AES),
        ),
    )

    assert epub_is_drm_encrypted(epub)


@pytest.mark.parametrize(
    "manifest",
    [
        "<encryption><EncryptedData>",  # truncated
        "not xml at all ns.adobe.com/pdf/enc#RC",
        _manifest('<enc:EncryptedData xmlns:enc="http://www.w3.org/2001/04/xmlenc#"/>'),  # no method, no target
    ],
    ids=["truncated", "garbage", "entry-without-method-or-target"],
)
def test_a_manifest_that_cannot_be_read_is_treated_as_drm(tmp_path, manifest):
    """Conservative on purpose: an unreadable manifest might be hiding encrypted content."""
    epub = _book(tmp_path, "broken.epub", manifest)

    assert epub_is_drm_encrypted(epub)


def test_no_manifest_is_not_drm(tmp_path):
    assert not epub_is_drm_encrypted(_book(tmp_path, "plain.epub"))


def test_an_empty_manifest_is_not_drm(tmp_path):
    assert not epub_is_drm_encrypted(_book(tmp_path, "empty.epub", _manifest()))


def test_the_manifest_parser_never_resolves_entities(tmp_path):
    """encryption.xml is untrusted archive XML like the OPF (issue #265)."""
    secret = tmp_path / "secret.txt"
    secret.write_text("x", encoding="utf-8")
    manifest = (
        '<?xml version="1.0"?>'
        f'<!DOCTYPE encryption [<!ENTITY uri SYSTEM "file://{secret.as_posix()}">]>'
        '<encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
        '<enc:EncryptedData xmlns:enc="http://www.w3.org/2001/04/xmlenc#">'
        f'<enc:EncryptionMethod Algorithm="{IDPF_FONT}"/>'
        '<enc:CipherData><enc:CipherReference URI="fonts/a.ttf"/></enc:CipherData>'
        "&uri;</enc:EncryptedData></encryption>"
    )
    epub = _book(tmp_path, "xxe.epub", manifest)

    # Either outcome is acceptable; the call must simply not raise or read the file.
    assert epub_is_drm_encrypted(epub) in (True, False)


# ---------------------------------------------------------------------------
# ACSM import: the post-decryption check shares the same rule.
# ---------------------------------------------------------------------------

_SERVICES = Path(__file__).resolve().parent.parent / "services"


def test_acsm_fulfill_verifies_with_the_shared_rule_not_file_presence():
    """DeDRM keeps encryption.xml for the obfuscated fonts it leaves alone.

    `_acsm_fulfill.py` runs in Calibre's interpreter, so it cannot be driven here; pin the source.
    A bare "encryption.xml is present" check rejects a correctly decrypted book with embedded fonts.
    """
    src = (_SERVICES / "import_sources" / "_acsm_fulfill.py").read_text(encoding="utf-8")

    assert "from ebook_integrity import epub_is_drm_encrypted" in src
    assert "if epub_is_drm_encrypted(out_path):" in src


def test_ebook_integrity_imports_standalone_as_calibre_sees_it(tmp_path):
    """Under `calibre-debug -e` there is no `services` package: only the directory is on sys.path."""
    spec = importlib.util.spec_from_file_location("ebook_integrity_standalone", _SERVICES / "ebook_integrity.py")
    module = importlib.util.module_from_spec(spec)
    saved = {k: v for k, v in sys.modules.items() if k == "services" or k.startswith("services.")}
    try:
        for k in saved:
            del sys.modules[k]
        spec.loader.exec_module(module)
    finally:
        sys.modules.update(saved)

    epub = _book(tmp_path, "adobe-font.epub", _manifest(_entry("fonts/a.ttf", ADOBE_FONT)))
    assert module.epub_is_drm_encrypted(epub) is False


# ---------------------------------------------------------------------------
# The false failures already cached on existing servers.
# ---------------------------------------------------------------------------

_MIGRATION = Path(__file__).resolve().parent.parent / "alembic" / "versions" / "0021_epub_font_obfuscation.py"


def _load_migration():
    # By path: `server/alembic/` shares its name with the installed alembic package.
    spec = importlib.util.spec_from_file_location("migration_0021", _MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_migration_follows_0020():
    module = _load_migration()

    assert module.revision == "0021_epub_font_obfuscation"
    assert len(module.revision) <= 32
    assert module.down_revision == "0020_epub_xml_content_docs"


def test_the_migration_clears_only_the_cached_drm_failures(tmp_path):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from models.library_issue import LibraryCheckResult

    engine = create_engine(f"sqlite:///{tmp_path / 'm.db'}")
    LibraryCheckResult.__table__.create(engine)
    rows = [
        # (item_type, item_id, check_type, ok, detail)
        ("ebook", 1, "ebook_integrity", False, DRM_DETAIL),
        ("ebook", 2, "ebook_integrity", False,
         "ebook produced almost no text (0 sentences) — likely encrypted or corrupt"),
        ("ebook", 3, "ebook_integrity", False,
         "EPUB is not a valid zip (corrupt download) — re-import required"),
        ("ebook", 4, "ebook_integrity", True, "ok (4000 sentences)"),
        ("audiobook", 5, "audio_integrity", False, DRM_DETAIL),
    ]
    with engine.begin() as conn:
        for item_type, item_id, check_type, ok, detail in rows:
            conn.execute(
                text(
                    "INSERT INTO library_check_results (item_type, item_id, check_type, ok, detail, checked_at) "
                    "VALUES (:t, :i, :c, :ok, :d, CURRENT_TIMESTAMP)"
                ),
                {"t": item_type, "i": item_id, "c": check_type, "ok": ok, "d": detail},
            )

    module = _load_migration()
    with engine.begin() as conn:
        with Operations.context(MigrationContext.configure(conn)):
            module.upgrade()

    with engine.connect() as conn:
        remaining = sorted(r[0] for r in conn.execute(text("SELECT item_id FROM library_check_results")))
    assert remaining == [2, 3, 4, 5]
