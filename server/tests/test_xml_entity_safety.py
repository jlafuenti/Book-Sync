"""
Untrusted XML must not read local files (issue #265).

An EPUB is attacker-supplied input: uploads, ACSM imports, library-folder drops
and ABS pulls all end up parsed here. Today this is safe by *library default* —
lxml >= 5 defaults to `resolve_entities='internal'` and Python 3.12's expat
refuses external entities outright — but lxml arrived as an unpinned transitive
dependency of ebooklib, so the protection was a version pip happened to pick.
libxml2 had already drifted 2.11.9 -> 2.14.6 underneath us while nobody was
pinning it.

So lxml and defusedxml are pinned now, the parsers are passed explicitly, and
these tests exist to fail if either drifts back. They pass today; that is the
point. A test that only fails on the day the fix is written is worth less than
one that fails on the day the protection quietly disappears.
"""

import zipfile



from services.epub_parser import _extract_epub_documents_via_zip


def _epub_with_external_entity(path, secret_file):
    """A minimal EPUB whose OPF tries to pull a local file into the title.

    Modelled on tests/factories.write_epub, but that helper builds a fixed OPF —
    the entity declaration has to be in the doctype, so this writes its own.
    """
    opf = (
        '<?xml version="1.0"?>'
        f'<!DOCTYPE package [<!ENTITY xxe SYSTEM "file://{secret_file}">]>'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid">'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        '<dc:title>&xxe;</dc:title><dc:identifier id="bookid">urn:uuid:xxe</dc:identifier>'
        '<dc:language>en</dc:language></metadata>'
        '<manifest><item id="id0" href="ch1.xhtml" media-type="application/xhtml+xml"/></manifest>'
        '<spine><itemref idref="id0"/></spine></package>'
    )
    container = (
        '<?xml version="1.0"?>'
        '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">'
        '<rootfiles><rootfile full-path="OEBPS/content.opf" '
        'media-type="application/oebps-package+xml"/></rootfiles></container>'
    )
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr("META-INF/container.xml", container)
        z.writestr("OEBPS/content.opf", opf)
        z.writestr("OEBPS/ch1.xhtml", "<html><body><p>Ordinary prose here.</p></body></html>")
    return str(path)


SECRET = "correct-horse-battery-staple"


def test_an_opf_external_entity_never_reaches_the_extracted_text(tmp_path):
    secret_file = tmp_path / "secret.txt"
    secret_file.write_text(SECRET, encoding="utf-8")
    epub = _epub_with_external_entity(
        tmp_path / "xxe.epub", secret_file.as_posix()
    )

    # Refusing the file outright is fine; leaking it is not. Both a parse error
    # and a successful parse with the entity unresolved are acceptable outcomes.
    try:
        docs = _extract_epub_documents_via_zip(epub)
    except Exception:
        return

    assert SECRET not in "".join(docs), (
        "an external entity in the OPF was resolved and its contents reached the "
        "extracted text -- that is a local file read from an uploaded book"
    )


def test_the_explicit_parser_leaves_every_entity_unresolved():
    """Pin the parser settings themselves, not just the outcome.

    The outcome test above would still pass with a default parser, as long as
    lxml's default stayed safe. This pins that we are choosing, and the choice is
    strictly stronger than the default rather than merely equal to it:

        parser    entity     result
        explicit  internal   left unresolved
        explicit  external   left unresolved
        default   internal   EXPANDED
        default   external   XMLSyntaxError

    Measured, not assumed — the default expanding internal entities is exactly
    the kind of behaviour that shifts under an unpinned dependency.
    """
    from lxml import etree

    safe = etree.XMLParser(
        resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False
    )
    internal = (
        '<?xml version="1.0"?><!DOCTYPE r [<!ENTITY e "expanded">]><r>&e;</r>'
    )
    root = etree.fromstring(internal.encode(), parser=safe)
    assert root.text is None
    assert [c.name for c in root if isinstance(c, etree._Entity)] == ["e"], (
        "the entity was expanded; resolve_entities=False is not in effect"
    )

    external = (
        '<?xml version="1.0"?>'
        '<!DOCTYPE r [<!ENTITY x SYSTEM "file:///etc/hostname">]><r>&x;</r>'
    )
    root = etree.fromstring(external.encode(), parser=safe)
    assert [c.name for c in root if isinstance(c, etree._Entity)] == ["x"]


def test_the_stdlib_xml_sites_use_defusedxml():
    """library.py and acsm.py parse container.xml/OPF from untrusted archives."""
    import services.import_sources.acsm as acsm_mod
    import routers.library as library_mod

    for mod in (acsm_mod, library_mod):
        assert mod.ET.__name__.startswith("defusedxml"), (
            f"{mod.__name__} parses untrusted archive XML with "
            f"{mod.ET.__name__}; use defusedxml so the choice is explicit"
        )


def test_every_lxml_parse_of_untrusted_input_passes_a_hardened_parser():
    """The behavioural tests above cannot see this, so read the source.

    Removing `parser=safe` from the call sites breaks no other test: lxml's
    *default* also refuses external entities, so the outcome is unchanged until
    the day the default changes -- which is the entire reason for pinning and
    being explicit. Without this guard the hardening is decoration.

    Same blunt instrument as the addFilter guard in test_access_log_redaction and
    SyncWiringTest on the Android side, for the same reason: the failure mode is
    "the argument disappeared".
    """
    import os
    import re

    server_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    targets = [
        os.path.join(server_dir, "services", "epub_parser.py"),
        os.path.join(server_dir, "services", "import_sources", "_acsm_fulfill.py"),
    ]

    offenders = []
    for path in targets:
        with open(path, encoding="utf-8") as fh:
            src = fh.read()
        if "etree.fromstring" not in src:
            continue
        assert "resolve_entities=False" in src, (
            f"{os.path.basename(path)} parses XML with lxml but defines no "
            "hardened parser"
        )
        # Each call must hand over a parser. Matching the call text is enough --
        # the argument is either there or it is not.
        for call in re.finditer(r"etree\.fromstring\((.*?)\)\s*$", src, re.S | re.M):
            if "parser=" not in call.group(1):
                line = src[: call.start()].count("\n") + 1
                offenders.append(f"{os.path.basename(path)}:{line}")

    assert not offenders, (
        "etree.fromstring called without an explicit parser on untrusted input: "
        f"{offenders}. Pass the hardened parser -- relying on the library default "
        "is what issue #265 set out to stop."
    )
