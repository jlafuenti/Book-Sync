"""
Write library metadata back into the file on disk.

Split out of `routers/library.py` (issue #255) so the two writers can be
exercised without an HTTP request. Both are **blocking** file work — the EPUB
path rewrites the whole archive, the audio path opens the container with
mutagen and saves it — so a caller inside an `async def` should reach them
through `asyncio.to_thread` (issue #203).

The EPUB writer edits the OPF in place with `zipfile` + `defusedxml` rather
than `ebooklib.write_epub`, which is notorious for destroying complex EPUB
structures. The XML parser is defusedxml on purpose (issue #265): the archive
is attacker-supplied input, and `tests/test_xml_entity_safety.py` pins the
choice.

The two writers differ in how they fail, and both callers depend on it: the
EPUB writer re-raises after logging (the PATCH handler catches it, the
discrepancy resolver commits *before* calling it so a failure cannot lose the
resolution — see docs/request-transactions.md), while the audio writer
swallows everything and logs.
"""

import logging
import os
import re
import tempfile
import zipfile

import mutagen
# defusedxml rather than the stdlib parser (issue #265). Python 3.12's expat
# already refuses external entities and caps amplification, so this changes no
# behaviour -- it makes the choice explicit for input that arrives as an
# attacker-supplied archive, and keeps bandit quiet on a public repo.
import defusedxml.ElementTree as ET

from services.file_mode import copy_mode_and_owner
# defusedxml re-exports only the *parsers* (fromstring, parse, tostring …), so
# parsing stays defused (issue #265) while the two tree builders the writer
# needs come from the stdlib. The parsed tree is a plain stdlib Element, so the
# two mix without ceremony. Issue #428.
from xml.etree.ElementTree import SubElement, register_namespace

from services.metadata_utils import normalize_author
from services.series import entries_of, tag_values

logger = logging.getLogger(__name__)

_OPF_NS = "{http://www.idpf.org/2007/opf}"


def _local_name(element) -> str:
    return element.tag.rsplit('}', 1)[-1]


def _refinements(metadata, prop) -> dict:
    """{element id: value} from EPUB 3 `<meta refines="#id" property=prop>`."""
    found = {}
    for meta in metadata:
        refines = meta.attrib.get('refines', '')
        if _local_name(meta) == 'meta' and meta.attrib.get('property') == prop and refines.startswith('#'):
            found.setdefault(refines[1:], (meta.text or '').strip().lower())
    return found


def _creator_roles(metadata, creators) -> list:
    """The role of each creator, from opf:role or a refines meta; None if unstated."""
    refined = _refinements(metadata, 'role')
    return [
        (c.attrib.get(f'{_OPF_NS}role') or refined.get(c.attrib.get('id')) or '').strip().lower() or None
        for c in creators
    ]


def _primary_dc_element(metadata, tag_name, matches):
    """The one element of a repeated Dublin Core kind that the library's value
    belongs to, or None when there are only others (issue #889): a subtitle or
    collection title, an illustrator or translator, a modification date."""
    if tag_name == 'title':
        kinds = _refinements(metadata, 'title-type')
        marked = [kinds.get(c.attrib.get('id')) for c in matches]
    elif tag_name == 'creator':
        marked = ['main' if role == 'aut' else role for role in _creator_roles(metadata, matches)]
    elif tag_name == 'date':
        events = [(c.attrib.get(f'{_OPF_NS}event') or '').strip().lower() or None for c in matches]
        marked = ['main' if event == 'publication' else event for event in events]
    else:
        return matches[0] if matches else None
    for wanted in ('main', None):
        for child, mark in zip(matches, marked):
            if mark == wanted:
                return child
    return None


def _remove_dc_element(metadata, element) -> None:
    """Remove an element and the metas refining it, which would otherwise
    dangle and fail EPUB validation."""
    element_id = element.attrib.get('id')
    metadata.remove(element)
    if element_id:
        for meta in list(metadata):
            if _local_name(meta) == 'meta' and meta.attrib.get('refines') == f'#{element_id}':
                metadata.remove(meta)


def _drop_absorbed_co_authors(metadata, primary, creators, author) -> None:
    """The library's author field can join several names. Any co-author it
    names is now in the primary creator; keeping the element too would credit
    them twice. Co-authors it doesn't name stay: the extractor reads only the
    first creator, so a row often holds just that one."""
    names = {name.strip().casefold() for name in re.split(r'[,;&]', author)}
    if len(names) < 2:
        return
    for child, role in zip(creators, _creator_roles(metadata, creators)):
        if child is not primary and role in (None, 'aut') and (child.text or '').strip().casefold() in names:
            _remove_dc_element(metadata, child)


def write_ebook_metadata(filepath: str, book) -> None:
    """
    Write metadata back to an EPUB file.
    Modifies Dublin Core fields and Calibre series metadata in-place.
    """
    if not filepath.lower().endswith(".epub"):
        logger.info(f"[write-back] Skipping non-EPUB file: {filepath}")
        return
    
    if not os.path.exists(filepath):
        logger.warning(f"[write-back] File not found: {filepath}")
        return
    
    logger.info(f"[write-back] Writing EPUB metadata to: {filepath}")
    
    try:
        # We use zipfile and ElementTree instead of ebooklib.write_epub because 
        # ebooklib is notorious for destroying complex epub structures and causing "Bad Zip File" errors.
        
        # 1. Find the OPF file
        opf_path = None
        with zipfile.ZipFile(filepath, 'r') as zin:
            # First look at container.xml
            try:
                container = zin.read("META-INF/container.xml")
                root = ET.fromstring(container)
                # usually urn:oasis:names:tc:opendocument:xmlns:container
                for rootfile in root.iter():
                    if 'rootfile' in rootfile.tag and 'full-path' in rootfile.attrib:
                        opf_path = rootfile.attrib['full-path']
                        break
            except Exception:
                pass
            
            # Fallback if container.xml parsing fails
            if not opf_path:
                for name in zin.namelist():
                    if name.lower().endswith('.opf'):
                        opf_path = name
                        break
                        
        if not opf_path:
            logger.error(f"[write-back] Could not locate OPF file in {filepath}")
            return
            
        # 2. Extract and modify the OPF
        with zipfile.ZipFile(filepath, 'r') as zin:
            opf_content = zin.read(opf_path)
            
        # Parse OPF XML
        # Register namespaces to preserve them on write
        namespaces = {
            'opf': 'http://www.idpf.org/2007/opf',
            'dc': 'http://purl.org/dc/elements/1.1/',
            'calibre': 'http://calibre.kovidgoyal.net/2009/metadata',
        }
        for prefix, uri in namespaces.items():
            register_namespace(prefix, uri)
            
        root = ET.fromstring(opf_content)
        metadata = None
        for child in root.iter():
            if child.tag.endswith('metadata'):
                metadata = child
                break
                
        if metadata is None:
            logger.error(f"[write-back] No metadata block found in OPF for {filepath}")
            return
            
        # Helper to set or add a DC tag
        def set_dc_tag(tag_name, value):
            if not value: return
            matches = [c for c in metadata if _local_name(c) == tag_name]
            primary = _primary_dc_element(metadata, tag_name, matches)
            if primary is None and tag_name == 'creator':
                # An anthology may credit only editors, and the extractor reads
                # the first one into the row: already credited, don't add it.
                wanted = normalize_author(str(value))
                primary = next((c for c in matches if normalize_author(c.text or '') == wanted), None)
                if primary is not None:
                    value = primary.text
            if primary is None:
                primary = SubElement(metadata, f"{{http://purl.org/dc/elements/1.1/}}{tag_name}")
            # The library holds only a year: keep a full date that agrees with it.
            if not (tag_name == 'date' and (primary.text or '').strip().startswith(str(value))):
                primary.text = str(value)
            # The extractor, like most readers, takes the first element of a
            # kind; anywhere else, the next scan would read a different value.
            if matches and primary is not matches[0]:
                metadata.remove(primary)
                metadata.insert(list(metadata).index(matches[0]), primary)
            if tag_name == 'creator':
                _drop_absorbed_co_authors(metadata, primary, matches, str(value))

        def clear_dc_tag(tag_name, value):
            """Set DC tag when value is present; remove all matching tags when
            empty/None, since the extractor reads back any one left behind
            (issue #526)."""
            if value:
                set_dc_tag(tag_name, value)
            else:
                for child in list(metadata):
                    if _local_name(child) == tag_name:
                        _remove_dc_element(metadata, child)

        set_dc_tag("title", book.title)
        set_dc_tag("creator", book.author)
        clear_dc_tag("description", getattr(book, 'description', None))
        clear_dc_tag("publisher", getattr(book, 'publisher', None))
        clear_dc_tag("language", getattr(book, 'language', None))
        clear_dc_tag("date", getattr(book, 'publish_year', None))
        
        # Series (issue #905). `None` means "not touched" (leave whatever is in
        # the OPF alone); an empty list means the user cleared the series and
        # every series meta must go, or the next scan's fill-empty-fields step
        # reads them straight back onto the row and undoes the clear (issue
        # #526); a non-empty list replaces them.
        series_entries = _series_to_write(book)
        if series_entries is not None:
            _drop_series_metas(metadata)
            # One `calibre:series` meta per series, each immediately followed by
            # its `calibre:series_index`: the pairing Calibre, Audiobookshelf and
            # Tandem's own reader all use. EPUB3 collection metas are not
            # emitted (they are invalid on an EPUB2 OPF).
            for entry in series_entries:
                series_meta = SubElement(metadata, "{http://www.idpf.org/2007/opf}meta")
                series_meta.attrib['name'] = 'calibre:series'
                series_meta.attrib['content'] = entry['name']

                if entry['index'] is not None:
                    index_meta = SubElement(metadata, "{http://www.idpf.org/2007/opf}meta")
                    index_meta.attrib['name'] = 'calibre:series_index'
                    index_meta.attrib['content'] = f"{entry['index']:g}"
                
        # Write modified OPF back to a new zip file, then replace original
        modified_opf = ET.tostring(root, encoding='utf-8', xml_declaration=True)
        
        # Next to the target, so the replace is an atomic rename rather than a
        # copy across filesystems. Hidden and not `.epub`, so a scan of the
        # folder doesn't take it for a book.
        directory = os.path.dirname(os.path.abspath(filepath))
        fd, temp_path = tempfile.mkstemp(dir=directory, prefix=".", suffix=".tandem-tmp")
        os.close(fd)
        
        try:
            with zipfile.ZipFile(filepath, 'r') as zin:
                with zipfile.ZipFile(temp_path, 'w', zipfile.ZIP_DEFLATED) as zout:
                    # Write all files except the OPF
                    for item in zin.infolist():
                        if item.filename != opf_path:
                            zout.writestr(item, zin.read(item.filename))
                    # Write the new OPF
                    zout.writestr(opf_path, modified_opf)
                    
            # Replace original
            copy_mode_and_owner(filepath, temp_path)
            os.replace(temp_path, filepath)
            logger.info(f"[write-back] EPUB metadata written safely to {filepath}")
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        
    except Exception as e:
        logger.error(f"[write-back] Failed to write EPUB metadata: {e}")
        raise


def _series_to_write(book):
    """The series entries to write into the file, or None to leave it alone.

    `book` is an ORM row, or any object/dict carrying ``series_list``:

    * ``None`` or no such key — the caller has no opinion (the filename parser
      emits ``None`` for a path that names no series); the file's series tags
      stay. Deleting on "unknown" is what once turned a read gap into data
      loss (#135).
    * a non-empty list — written, replacing whatever the file had.
    * an empty list — the series was cleared; the series tags are deleted. An
      ORM row with an empty list *and no* ``series_cleared`` flag has simply
      never had a series, which is "no opinion", not a clear.
    """
    raw = book.get('series_list') if isinstance(book, dict) else getattr(book, 'series_list', None)
    if raw is None:
        return None
    entries = entries_of(list(raw))
    if not entries:
        cleared = book.get('series_cleared', True) if isinstance(book, dict) \
            else getattr(book, 'series_cleared', True)
        if not cleared:
            return None
    return entries


def _drop_series_metas(metadata) -> None:
    """Remove every series meta from an OPF `<metadata>`: Calibre's
    ``calibre:series`` / ``calibre:series_index`` and the EPUB3
    ``belongs-to-collection`` metas with the metas refining them
    (``collection-type``, ``group-position``)."""
    collection_ids = {
        m.attrib.get('id') for m in metadata
        if _local_name(m) == 'meta' and m.attrib.get('property') == 'belongs-to-collection'
        and m.attrib.get('id')
    }
    for meta_tag in list(metadata):
        if _local_name(meta_tag) != 'meta':
            continue
        if meta_tag.attrib.get('name') in ('calibre:series', 'calibre:series_index'):
            metadata.remove(meta_tag)
        elif meta_tag.attrib.get('property') == 'belongs-to-collection':
            metadata.remove(meta_tag)
        elif meta_tag.attrib.get('refines', '').lstrip('#') in collection_ids \
                and meta_tag.attrib.get('refines', '').startswith('#'):
            metadata.remove(meta_tag)


MP4_SERIES_ATOMS = (
    '\xa9grp',
    '----:com.apple.iTunes:SERIES',
    '----:com.apple.iTunes:SERIES-PART',
)


def apply_mp4_series(audio, entries) -> None:
    """Write `entries` into an MP4's series atoms: ``©grp`` as the canonical
    ``A #1; B #2``, ``SERIES`` as the names, ``SERIES-PART`` as the indexes
    (deleted unless every entry has one). Empty entries delete all three.

    Never ``©alb``: that is the book's title to Readarr-style tools (#886), and
    the reader takes no series from it. Shared with the ABS write-back.
    """
    grouping, names, parts = tag_values(entries)
    if not grouping:
        for tag in MP4_SERIES_ATOMS:
            if tag in audio:
                del audio[tag]
        return
    audio['\xa9grp'] = [grouping]
    audio['----:com.apple.iTunes:SERIES'] = [names.encode('utf-8')]
    if parts is not None:
        audio['----:com.apple.iTunes:SERIES-PART'] = [parts.encode('utf-8')]
    elif '----:com.apple.iTunes:SERIES-PART' in audio:
        del audio['----:com.apple.iTunes:SERIES-PART']


def write_audiobook_metadata(filepath: str, book) -> None:
    """
    Write metadata back to an audiobook file.
    Supports M4B/M4A (iTunes atoms), MP3 (ID3), FLAC, and Ogg.
    """
    if not os.path.exists(filepath):
        logger.warning(f"[write-back] File not found: {filepath}")
        return
    
    logger.info(f"[write-back] Writing audio metadata to: {filepath}")
    
    try:
        audio = mutagen.File(filepath)
        if not audio:
            logger.warning(f"[write-back] Mutagen could not open: {filepath}")
            return
        
        audio_type = type(audio).__name__
        logger.info(f"[write-back] Audio type: {audio_type}")
        
        if audio_type in ('MP4', 'M4A'):
            # iTunes-style atoms for M4B/M4A
            if book.title is not None: audio['\xa9nam'] = [book.title]
            if book.author is not None: audio['\xa9ART'] = [book.author]
            # Write series to the tags that extract_metadata reads (©grp, SERIES
            # and SERIES-PART atoms) so that clearing series actually takes
            # effect on subsequent scans.
            series_entries = _series_to_write(book)
            if series_entries is not None:
                apply_mp4_series(audio, series_entries)
                # The track number keeps today's behaviour, from the primary
                # series' index (issue #895 tracks changing that).
                if series_entries and series_entries[0]['index'] is not None:
                    audio['trkn'] = [(int(series_entries[0]['index']), 0)]
            # extract_metadata reads an MP4 description from `©des` first, then
            # `desc`, then `©cmt` (issue #538). Setting writes both of the first
            # two so a stale `©des` can't outrank the new text; clearing removes
            # all three, or the next library scan fills the field right back in
            # from whichever a ripper left behind.
            if book.description:
                audio['\xa9des'] = [book.description]
                audio['desc'] = [book.description]
            else:
                for tag in ('\xa9des', 'desc', '\xa9cmt'):
                    if tag in audio:
                        del audio[tag]
            if getattr(book, 'genres', None) is not None: audio['\xa9gen'] = [book.genres]
            if getattr(book, 'publish_year', None) is not None: audio['\xa9day'] = [str(book.publish_year)]
                
        elif audio_type == 'MP3':
            from mutagen.id3 import TIT1, TIT2, TPE1, TALB, TXXX, COMM, TCON, TYER, TPUB

            if audio.tags is None:
                audio.add_tags()

            if book.title is not None:
                audio.tags['TIT2'] = TIT2(encoding=3, text=book.title)
                # The album is the book's title (#886): Readarr-style managers
                # identify an audiobook by it, and a series here made their
                # folder watcher re-link the file to another book in the
                # series. Older write-backs put the series here, so this also
                # moves it off.
                audio.tags['TALB'] = TALB(encoding=3, text=book.title)
            if book.author is not None: audio.tags['TPE1'] = TPE1(encoding=3, text=book.author)
            # Series in grouping plus explicit SERIES / SERIES-PART frames, as the
            # MP4 branch does. The track number is left alone: it is not the
            # series index.
            series_entries = _series_to_write(book)
            if series_entries is not None:
                for key in ('TIT1', 'TXXX:SERIES', 'TXXX:SERIES-PART'):
                    if key in audio.tags:
                        del audio.tags[key]
                grouping, names, parts = tag_values(series_entries)
                if grouping:
                    audio.tags['TIT1'] = TIT1(encoding=3, text=grouping)
                    audio.tags.add(TXXX(encoding=3, desc='SERIES', text=names))
                    if parts is not None:
                        audio.tags.add(TXXX(encoding=3, desc='SERIES-PART', text=parts))
            if book.description:
                # extract_metadata takes the first COMM* frame it finds, so a
                # ripper's `COMM::eng` left beside the new frame could still be
                # what the next scan reads (issue #538). Replace, don't add.
                for key in list(audio.tags.keys()):
                    if key == 'COMM' or key.startswith('COMM:'):
                        del audio.tags[key]
                audio.tags['COMM'] = COMM(encoding=3, lang='eng', desc='', text=book.description)
            elif audio.tags:
                # extract_metadata reads a description back from *any* COMM
                # frame, matched by key prefix rather than the exact
                # desc/lang this function writes under -- ID3 keys a COMM
                # frame by "COMM:<desc>:<lang>" (e.g. "COMM::eng"), not the
                # literal "COMM" this function assigns to, so the on-disk key
                # never matches that literal delete. TXXX:comment is the
                # other slot rippers commonly leave a description-shaped
                # string in. Leaving either behind means clearing the
                # description doesn't survive the next library scan (#538).
                for key in list(audio.tags.keys()):
                    if key == 'COMM' or key.startswith('COMM:'):
                        del audio.tags[key]
                    elif key.startswith('TXXX:') and key[len('TXXX:'):].lower() == 'comment':
                        del audio.tags[key]
            if getattr(book, 'genres', None) is not None: audio.tags['TCON'] = TCON(encoding=3, text=book.genres)
            if getattr(book, 'publish_year', None) is not None: audio.tags['TYER'] = TYER(encoding=3, text=str(book.publish_year))
            if getattr(book, 'publisher', None) is not None: audio.tags['TPUB'] = TPUB(encoding=3, text=book.publisher)
                
        elif audio_type in ('FLAC', 'OggVorbis', 'OggOpus'):
            # Vorbis comments
            if book.title is not None:
                audio['title'] = [book.title]
                audio['album'] = [book.title]   # the book's title, not the series (#886)
            if book.author is not None: audio['artist'] = [book.author]
            series_entries = _series_to_write(book)
            if series_entries is not None:
                for tag in ('grouping', 'series', 'series-part'):
                    if tag in audio:
                        del audio[tag]
                grouping, names, parts = tag_values(series_entries)
                if grouping:
                    audio['grouping'] = [grouping]
                    audio['series'] = [names]
                    if parts is not None:
                        audio['series-part'] = [parts]
            if book.description:
                audio['description'] = [book.description]
            else:
                # extract_metadata falls back to `summary` when `description`
                # is absent, so clearing only one would hand the next scan the
                # other (issue #538).
                for tag in ('description', 'summary'):
                    if tag in audio:
                        del audio[tag]
            if getattr(book, 'genres', None) is not None: audio['genre'] = [book.genres]
            if getattr(book, 'publish_year', None) is not None: audio['date'] = [str(book.publish_year)]
            if getattr(book, 'publisher', None) is not None: audio['organization'] = [book.publisher]
        else:
            logger.warning(f"[write-back] Unsupported audio type for write-back: {audio_type}")
            return
        
        audio.save()
        logger.info(f"[write-back] Audio metadata written successfully")
        
    except Exception as e:
        logger.error(f"[write-back] Failed to write audio metadata: {e}")

