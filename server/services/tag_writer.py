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
import shutil
import tempfile
import zipfile

import mutagen
# defusedxml rather than the stdlib parser (issue #265). Python 3.12's expat
# already refuses external entities and caps amplification, so this changes no
# behaviour -- it makes the choice explicit for input that arrives as an
# attacker-supplied archive, and keeps bandit quiet on a public repo.
import defusedxml.ElementTree as ET
# defusedxml re-exports only the *parsers* (fromstring, parse, tostring …), so
# parsing stays defused (issue #265) while the two tree builders the writer
# needs come from the stdlib. The parsed tree is a plain stdlib Element, so the
# two mix without ceremony. Issue #428.
from xml.etree.ElementTree import SubElement, register_namespace

logger = logging.getLogger(__name__)


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
            found = False
            for child in list(metadata):
                if child.tag.endswith(tag_name):
                    child.text = str(value)
                    found = True
            if not found:
                el = SubElement(metadata, f"{{http://purl.org/dc/elements/1.1/}}{tag_name}")
                el.text = str(value)

        def clear_dc_tag(tag_name, value):
            """Set DC tag when value is present; remove all matching tags when empty/None."""
            if value:
                set_dc_tag(tag_name, value)
            else:
                for child in list(metadata):
                    if child.tag.endswith(tag_name):
                        metadata.remove(child)

        set_dc_tag("title", book.title)
        set_dc_tag("creator", book.author)
        clear_dc_tag("description", getattr(book, 'description', None))
        clear_dc_tag("publisher", getattr(book, 'publisher', None))
        clear_dc_tag("language", getattr(book, 'language', None))
        clear_dc_tag("date", getattr(book, 'publish_year', None))
        
        # Calibre series meta tags
        if getattr(book, 'series', None):
            # Remove existing series tags
            for meta_tag in list(metadata):
                if meta_tag.tag.endswith('meta'):
                    name_attr = meta_tag.attrib.get('name')
                    if name_attr in ('calibre:series', 'calibre:series_index'):
                        metadata.remove(meta_tag)
                        
            # Add new ones
            series_meta = SubElement(metadata, "{http://www.idpf.org/2007/opf}meta")
            series_meta.attrib['name'] = 'calibre:series'
            series_meta.attrib['content'] = str(book.series)
            
            if getattr(book, 'series_index', None) is not None:
                index_meta = SubElement(metadata, "{http://www.idpf.org/2007/opf}meta")
                index_meta.attrib['name'] = 'calibre:series_index'
                index_meta.attrib['content'] = str(book.series_index)
                
        # Write modified OPF back to a new zip file, then replace original
        modified_opf = ET.tostring(root, encoding='utf-8', xml_declaration=True)
        
        fd, temp_path = tempfile.mkstemp(suffix=".epub")
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
            shutil.move(temp_path, filepath)
            logger.info(f"[write-back] EPUB metadata written safely to {filepath}")
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        
    except Exception as e:
        logger.error(f"[write-back] Failed to write EPUB metadata: {e}")
        raise


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
            # Write series to the tags that extract_metadata reads (©grp, SERIES atom)
            # so that clearing series actually takes effect on subsequent scans.
            if book.series is not None:
                if book.series:
                    grp = (f"{book.series} #{int(book.series_index)}"
                           if book.series_index is not None else book.series)
                    audio['\xa9grp'] = [grp]
                    audio['----:com.apple.iTunes:SERIES'] = [book.series.encode('utf-8')]
                    if book.series_index is not None:
                        audio['----:com.apple.iTunes:SERIES-PART'] = [
                            str(int(book.series_index)).encode('utf-8')
                        ]
                else:
                    # Empty string = user cleared series; delete all series tags from file
                    for tag in ('\xa9grp', '\xa9alb', '----:com.apple.iTunes:SERIES',
                                '----:com.apple.iTunes:SERIES-PART'):
                        if tag in audio:
                            del audio[tag]
            if book.series_index is not None:
                audio['trkn'] = [(int(book.series_index), 0)]
            if book.description:
                audio['desc'] = [book.description]
            elif 'desc' in audio:
                del audio['desc']
            if getattr(book, 'genres', None) is not None: audio['\xa9gen'] = [book.genres]
            if getattr(book, 'publish_year', None) is not None: audio['\xa9day'] = [str(book.publish_year)]
                
        elif audio_type == 'MP3':
            from mutagen.id3 import TIT2, TPE1, TALB, TRCK, COMM, TCON, TYER, TPUB
            
            if audio.tags is None:
                audio.add_tags()
            
            if book.title is not None: audio.tags['TIT2'] = TIT2(encoding=3, text=book.title)
            if book.author is not None: audio.tags['TPE1'] = TPE1(encoding=3, text=book.author)
            if book.series is not None:
                if book.series:
                    audio.tags['TALB'] = TALB(encoding=3, text=book.series)
                elif 'TALB' in audio.tags:
                    del audio.tags['TALB']
            if book.series_index is not None:
                audio.tags['TRCK'] = TRCK(encoding=3, text=str(int(book.series_index)))
            if book.description:
                audio.tags['COMM'] = COMM(encoding=3, lang='eng', desc='', text=book.description)
            elif audio.tags and 'COMM' in audio.tags:
                del audio.tags['COMM']
            if getattr(book, 'genres', None) is not None: audio.tags['TCON'] = TCON(encoding=3, text=book.genres)
            if getattr(book, 'publish_year', None) is not None: audio.tags['TYER'] = TYER(encoding=3, text=str(book.publish_year))
            if getattr(book, 'publisher', None) is not None: audio.tags['TPUB'] = TPUB(encoding=3, text=book.publisher)
                
        elif audio_type in ('FLAC', 'OggVorbis', 'OggOpus'):
            # Vorbis comments
            if book.title is not None: audio['title'] = [book.title]
            if book.author is not None: audio['artist'] = [book.author]
            if book.series is not None:
                if book.series:
                    audio['album'] = [book.series]
                elif 'album' in audio:
                    del audio['album']
            if book.series_index is not None:
                audio['tracknumber'] = [str(int(book.series_index))]
            if book.description:
                audio['description'] = [book.description]
            elif 'description' in audio:
                del audio['description']
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

