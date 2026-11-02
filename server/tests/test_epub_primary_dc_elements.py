"""EPUB write-back touches only the primary Dublin Core element (issue #889).

Repeated `dc:title` and `dc:creator` elements are normal: subtitles and
collection titles, co-authors, illustrators and translators, with their kind
given by `opf:role` / `opf:event` attributes or EPUB 3 `<meta refines>`
elements. The writer used to set every match to the one value.

The extractor reads the first element of each kind, so these tests also read
the file back through it: the value that was written has to be the value a
rescan finds, or the next scan disagrees with the library row.
"""

import re
import zipfile
from types import SimpleNamespace

import defusedxml.ElementTree as ET

from services import tag_writer
from services.metadata_extract import _read_embedded_metadata
from tests.factories import write_epub

DC = "{http://purl.org/dc/elements/1.1/}"
SUBTITLE = '<dc:title id="s">The Subtitle</dc:title><meta refines="#s" property="title-type">subtitle</meta>'
ILLUSTRATOR = '<dc:creator id="i" opf:role="ill">Ivy Illustrator</dc:creator>'


def _epub(tmp_path, metadata):
    """A real EPUB whose OPF metadata block holds `metadata`."""
    source = write_epub(str(tmp_path / "source.epub"), [("ch1.xhtml", "<html><body><p>One.</p></body></html>")])
    path = tmp_path / "book.epub"
    block = ('<metadata xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:opf="http://www.idpf.org/2007/opf" '
             f'xmlns:x="urn:example">{metadata}<dc:identifier id="bookid">urn:uuid:one</dc:identifier>'
             '<dc:identifier>urn:isbn:9780000000000</dc:identifier><dc:language>en</dc:language></metadata>')
    with zipfile.ZipFile(source) as zin, zipfile.ZipFile(path, "w") as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename == "OEBPS/content.opf":
                data = re.sub(r"<metadata.*</metadata>", block, data.decode(), flags=re.S).encode()
            zout.writestr(item, data)
    return str(path)


def _write(path, **fields):
    base = dict(title=None, author=None, series_list=None, description=None,
                publisher=None, language="en", publish_year=None, genres=None)
    base.update(fields)
    tag_writer.write_ebook_metadata(path, SimpleNamespace(**base))


def _metadata(path):
    with zipfile.ZipFile(path) as z:
        root = ET.fromstring(z.read("OEBPS/content.opf"))
    return next(c for c in root.iter() if c.tag.endswith("metadata"))


def _dc(path, tag):
    return [c.text for c in _metadata(path) if c.tag == DC + tag]


def _refines(path):
    return [m.attrib["refines"] for m in _metadata(path) if "refines" in m.attrib]


def test_the_subtitle_and_illustrator_are_left_alone(tmp_path):
    path = _epub(tmp_path, f'<dc:title id="m">Old</dc:title>{SUBTITLE}'
                           f'<dc:creator id="a" opf:role="aut">Old Author</dc:creator>{ILLUSTRATOR}')

    _write(path, title="New Title", author="New Author")

    assert _dc(path, "title") == ["New Title", "The Subtitle"]
    assert _dc(path, "creator") == ["New Author", "Ivy Illustrator"]
    assert _refines(path) == ["#s"]
    assert _dc(path, "identifier") == ["urn:uuid:one", "urn:isbn:9780000000000"]


def test_writing_twice_changes_nothing_more(tmp_path):
    path = _epub(tmp_path, f'<dc:title id="m">Old</dc:title>{SUBTITLE}{ILLUSTRATOR}')

    _write(path, title="New Title", author="New Author")
    first = (_dc(path, "title"), _dc(path, "creator"))
    _write(path, title="New Title", author="New Author")

    assert (_dc(path, "title"), _dc(path, "creator")) == first


def test_the_title_marked_main_is_the_one_written_even_after_a_collection_title(tmp_path):
    """Every title here carries a title-type, so "has a title-type" can't be
    what marks a title as secondary."""
    path = _epub(tmp_path, '<dc:title id="c">The Collection</dc:title>'
                           '<meta refines="#c" property="title-type">collection</meta>'
                           '<dc:title id="m">Old</dc:title>'
                           '<meta refines="#m" property="title-type">main</meta>')

    _write(path, title="New Title")

    assert sorted(_dc(path, "title")) == ["New Title", "The Collection"]
    assert _read_embedded_metadata(path, "ebook")["title"] == "New Title"


def test_a_subtitle_listed_first_is_kept_and_a_rescan_reads_the_new_title(tmp_path):
    path = _epub(tmp_path, f'{SUBTITLE}<dc:title id="m">Old</dc:title>')

    _write(path, title="New Title")

    assert sorted(_dc(path, "title")) == ["New Title", "The Subtitle"]
    assert _read_embedded_metadata(path, "ebook")["title"] == "New Title"


def test_a_file_with_only_a_subtitle_gains_a_main_title(tmp_path):
    path = _epub(tmp_path, SUBTITLE)

    _write(path, title="New Title")

    assert sorted(_dc(path, "title")) == ["New Title", "The Subtitle"]
    assert _read_embedded_metadata(path, "ebook")["title"] == "New Title"


def test_a_role_given_by_a_refines_meta_counts(tmp_path):
    """EPUB 3 states the role in a separate meta rather than opf:role."""
    path = _epub(tmp_path, '<dc:title>T</dc:title><dc:creator id="i">Ivy Illustrator</dc:creator>'
                           '<meta refines="#i" property="role" scheme="marc:relators">ill</meta>'
                           '<dc:creator id="a">Old Author</dc:creator>'
                           '<meta refines="#a" property="role" scheme="marc:relators">aut</meta>')

    _write(path, title="T", author="New Author")

    assert sorted(_dc(path, "creator")) == ["Ivy Illustrator", "New Author"]
    assert _read_embedded_metadata(path, "ebook")["author"] == "New Author"
    assert _refines(path) == ["#i", "#a"]


def test_an_illustrator_listed_first_is_kept_and_a_rescan_reads_the_new_author(tmp_path):
    path = _epub(tmp_path, f'<dc:title>T</dc:title>{ILLUSTRATOR}<dc:creator opf:role="aut">Old Author</dc:creator>')

    _write(path, title="T", author="New Author")

    assert sorted(_dc(path, "creator")) == ["Ivy Illustrator", "New Author"]
    assert _read_embedded_metadata(path, "ebook")["author"] == "New Author"


def test_a_file_crediting_only_an_illustrator_gains_an_author(tmp_path):
    path = _epub(tmp_path, f'<dc:title>T</dc:title>{ILLUSTRATOR}')

    _write(path, title="T", author="New Author")

    assert sorted(_dc(path, "creator")) == ["Ivy Illustrator", "New Author"]
    assert _read_embedded_metadata(path, "ebook")["author"] == "New Author"


def test_a_library_author_who_is_credited_in_another_role_is_not_added_again(tmp_path):
    """An anthology credits only its editors, and the extractor reads the
    first one into the row; writing that row back must not add a second copy."""
    path = _epub(tmp_path, '<dc:title>T</dc:title><dc:creator opf:role="edt">Eve Editor</dc:creator>'
                           '<dc:creator opf:role="edt">Second Editor</dc:creator>')

    _write(path, title="T", author="Eve Editor")

    assert _dc(path, "creator") == ["Eve Editor", "Second Editor"]


def test_a_co_author_the_library_does_not_name_is_kept(tmp_path):
    """The extractor reads only the first creator, so a row commonly names
    just the first of several authors."""
    path = _epub(tmp_path, '<dc:title>T</dc:title><dc:creator>First Author</dc:creator>'
                           '<dc:creator>Second Author</dc:creator>')

    _write(path, title="T", author="First Author")

    assert _dc(path, "creator") == ["First Author", "Second Author"]


def test_a_joined_author_field_does_not_duplicate_a_co_author(tmp_path):
    path = _epub(tmp_path, '<dc:title>T</dc:title><dc:creator id="a">First Author</dc:creator>'
                           '<meta refines="#a" property="role">aut</meta>'
                           '<dc:creator id="b">Second Author</dc:creator>'
                           '<meta refines="#b" property="role">aut</meta>'
                           f'{ILLUSTRATOR}')

    _write(path, title="T", author="First Author, Second Author")

    assert _dc(path, "creator") == ["First Author, Second Author", "Ivy Illustrator"]
    assert _refines(path) == ["#a"]
    assert _read_embedded_metadata(path, "ebook")["author"] == "First Author, Second Author"


def test_the_publish_year_goes_to_the_publication_date_not_the_modification_date(tmp_path):
    path = _epub(tmp_path, '<dc:title>T</dc:title><dc:date opf:event="modification">2020-05-05</dc:date>'
                           '<dc:date opf:event="publication">1990</dc:date>')

    _write(path, title="T", publish_year=1999)

    assert sorted(_dc(path, "date")) == ["1999", "2020-05-05"]
    assert _read_embedded_metadata(path, "ebook")["publish_year"] == 1999


def test_a_full_date_in_the_same_year_is_not_cut_down_to_the_year(tmp_path):
    """The library holds only a year, so writing it over `2020-01-28` lost the
    month and day on every write-back."""
    path = _epub(tmp_path, '<dc:title>T</dc:title><dc:date>2020-01-28</dc:date>')

    _write(path, title="T", publish_year=2020)

    assert _dc(path, "date") == ["2020-01-28"]


def test_a_new_year_replaces_a_full_date(tmp_path):
    path = _epub(tmp_path, '<dc:title>T</dc:title><dc:date>2020-01-28</dc:date>')

    _write(path, title="T", publish_year=1999)

    assert _dc(path, "date") == ["1999"]


def test_an_element_whose_name_only_ends_the_same_is_not_a_match(tmp_path):
    path = _epub(tmp_path, '<dc:title>Old</dc:title><x:subtitle>Custom</x:subtitle>')

    _write(path, title="New Title")

    meta = _metadata(path)
    assert [c.text for c in meta if c.tag == "{urn:example}subtitle"] == ["Custom"]
    assert _dc(path, "title") == ["New Title"]


def test_clearing_a_field_leaves_no_dangling_refinement(tmp_path):
    """A meta that refines a removed element fails EPUB validation."""
    path = _epub(tmp_path, '<dc:title>T</dc:title><dc:publisher id="p">Old House</dc:publisher>'
                           '<meta refines="#p" property="file-as">House, Old</meta>')

    _write(path, title="T", publisher=None)

    assert _dc(path, "publisher") == []
    assert _refines(path) == []
