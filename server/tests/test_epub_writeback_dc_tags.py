"""OPF write-back must not flatten repeated Dublin Core elements (issue #889)."""

import zipfile
from types import SimpleNamespace
from xml.etree import ElementTree as StdET

from services.tag_writer import write_ebook_metadata

DC = "{http://purl.org/dc/elements/1.1/}"

OPF = """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="uid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:opf="http://www.idpf.org/2007/opf">
    <dc:title id="main">Dune</dc:title>
    <dc:title id="subtitle">A Novel</dc:title>
    <meta refines="#subtitle" property="title-type">subtitle</meta>
    <dc:creator id="author" opf:role="aut">Frank Herbert</dc:creator>
    <dc:creator id="ill" opf:role="ill">Someone Else</dc:creator>
    <dc:language>en</dc:language>
  </metadata>
</package>
"""

CONTAINER = """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""


def _make_epub(path):
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr("META-INF/container.xml", CONTAINER)
        archive.writestr("OEBPS/content.opf", OPF)


def test_write_back_keeps_subtitle_and_illustrator(tmp_path):
    epub = tmp_path / "book.epub"
    _make_epub(epub)
    book = SimpleNamespace(
        title="Dune Messiah",
        author="Frank Herbert Jr.",
        description=None,
        publisher=None,
        language="en",
        publish_year=None,
        series_name=None,
        series_index=None,
    )

    write_ebook_metadata(str(epub), book)

    with zipfile.ZipFile(epub) as archive:
        root = StdET.fromstring(archive.read("OEBPS/content.opf"))

    titles = [element.text for element in root.iter(DC + "title")]
    creators = [
        (element.attrib.get("id"), element.text)
        for element in root.iter(DC + "creator")
    ]

    assert titles == ["Dune Messiah", "A Novel"]
    assert creators == [("author", "Frank Herbert Jr."), ("ill", "Someone Else")]
