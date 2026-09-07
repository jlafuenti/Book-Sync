"""
Metadata extraction for library files: filename patterns, embedded tags, covers.

Split out of `routers/library.py` (issue #255). Everything the scan, the
uploads, the rescans and troubleshoot's `replace_file` learn about a file
comes through here:

* `parse_filename_metadata_with_settings` — the configured filename / path
  patterns (`services/filename_patterns.py`), falling back to a cleaned stem;
* `_read_embedded_metadata` — the tags inside the file (EPUB OPF via
  ebooklib, audio containers via mutagen, runtime via ffprobe). A plain
  **sync** function on purpose: it is seconds of blocking file work per book,
  and `extract_metadata` is the only place that should call it, through
  `asyncio.to_thread` (issue #203);
* `extract_metadata` — the merge of the two, with `_metadata_source` saying
  which won;
* `_extract_and_save_cover` — the cover image, written under
  `settings.covers_dir`. Blocking too; callers wrap it the same way.

`compute_file_hash` sits here rather than in `services/file_hash.py` because
the ingest path and troubleshoot import it from the library module by that
name; it is a thin alias of `hash_file` and must stay in lockstep with the
byte-level twin `replace_file` uses (issue #45).
"""

import asyncio
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import ebooklib
from ebooklib import epub
import mutagen
from markdownify import markdownify as md
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from models.settings import SystemSetting
from routers.settings import DEFAULT_SETTINGS
from services.audio_duration import probe_duration_seconds
from services.filename_patterns import compile_pattern
from services.metadata_utils import (
    MEDIA_EXTRACTED_FIELDS,
    extract_series_and_index,
    normalize_author,
    normalize_series,
)

logger = logging.getLogger(__name__)


# Regex patterns for filename parsing
# Pattern 1: Author - [Series Num] - Title
REGEX_AUTHOR_SERIES_TITLE = re.compile(r"^(.+?) - \[(.+?) (\d+(?:\.\d+)?)\] - (.+)$")
# Pattern 2: [Series Num] Title (often found in Author folders)
REGEX_SERIES_TITLE = re.compile(r"^\[(.+?) (\d+(?:\.\d+)?)\] (.+)$")


# The pattern compiler moved to services/filename_patterns.py (issue #354) so
# the settings PUT can validate a pattern without importing this router — which
# it cannot, because this module already imports DEFAULT_SETTINGS from it. The
# name stays exported here: it is what the rest of the router and its tests call.


def is_path_pattern(pattern: str) -> bool:
    """Check whether a pattern contains '/' indicating it matches directory structure."""
    return "/" in pattern


async def get_filename_patterns(db: AsyncSession, pattern_type: str = "ebook") -> List[str]:
    """
    Fetch filename patterns from settings. 
    pattern_type: 'ebook' or 'audiobook'
    """
    key = "ebook_filename_patterns" if pattern_type == "ebook" else "audiobook_filename_patterns"
    
    result = await db.execute(select(SystemSetting).where(SystemSetting.key == key))
    setting = result.scalar_one_or_none()
    
    if setting and setting.value:
        return setting.value.split("\n")
        
    # Fallback to old key if new ones missing (backward compat)
    if not setting:
         result = await db.execute(select(SystemSetting).where(SystemSetting.key == "filename_patterns"))
         setting = result.scalar_one_or_none()
         if setting and setting.value:
             return setting.value.split("\n")
             
    return DEFAULT_SETTINGS.get(key, DEFAULT_SETTINGS["ebook_filename_patterns"])


def compute_file_hash(filepath: str) -> str:
    """Composite content hash (size + head + tail) — see services/file_hash.py.

    Used by scan/upload ingest and Calibre registration; troubleshoot's
    replace_file uses the byte-level twin. Keep them in lockstep (issue #45).
    """
    from services.file_hash import hash_file
    return hash_file(filepath)

def extract_title_from_filename(filename: str) -> str:
    """
    Extract a clean title from a filename by removing extension and
    common audiobook/ebook suffixes.
    """
    name = Path(filename).stem
    # Remove common suffixes like " - Audiobook", " (Unabridged)", etc.
    for suffix in [" - Audiobook", " - Audio", " (Unabridged)", " (Abridged)", " [Audiobook]"]:
        name = name.replace(suffix, "")
    return name.strip()


async def parse_filename_metadata_with_settings(
    filename: str, db: AsyncSession, parent_dir_name: str = None,
    file_type: str = "ebook", relative_path: str = None
) -> Dict[str, Any]:
    """
    Attempt to extract metadata using configured patterns.
    Supports both filename-only and directory-path patterns.
    
    Args:
        filename: Just the filename (e.g. 'Battle Ground.m4b')
        db: Database session for loading settings
        parent_dir_name: The immediate parent directory name
        file_type: 'ebook' or 'audiobook'
        relative_path: Full path relative to the library root
                       (e.g. 'Jim Butcher/Battle Ground/Battle Ground.m4b')
    """
    clean_name = Path(filename).stem
    # Build a clean relative path without extension for path-based matching
    if relative_path:
        rel_path_stem = str(Path(relative_path).with_suffix(''))
        # Normalize to forward slashes
        rel_path_stem = rel_path_stem.replace('\\', '/')
    else:
        rel_path_stem = clean_name
    
    meta = {
        "title": None,
        "author": None,
        "series": None,
        "series_index": None,
        "_metadata_source": "filename",
        "_metadata_pattern": None,
    }
    
    patterns = await get_filename_patterns(db, file_type)
    
    logger.debug(f"[metadata] Parsing '{filename}' (type={file_type})")
    logger.debug(f"[metadata]   clean_name='{clean_name}', relative_path='{relative_path}', rel_path_stem='{rel_path_stem}'")
    
    for pattern_str in patterns:
        # `compile_pattern` returns None rather than raising, and warns at most
        # once per pattern for the whole process (issue #354). The old code
        # compiled inside the try below, so a pattern that could not compile —
        # `<Author>/<Title>/<Title>` was enough — logged a warning for every
        # single file in the library, every scan, and matched nothing.
        regex = compile_pattern(pattern_str)
        if regex is None:
            continue

        try:
            # Decide what to match against based on whether pattern uses directories
            if is_path_pattern(pattern_str):
                match_target = rel_path_stem
            else:
                match_target = clean_name
            
            match = regex.match(match_target)
            logger.debug(f"[metadata]   Pattern '{pattern_str}' vs '{match_target}' -> {'MATCH' if match else 'no match'}")
            
            if match:
                groups = match.groupdict()
                logger.debug(f"[metadata]   Matched pattern '{pattern_str}' -> groups={groups}")
                
                if "author" in groups: meta["author"] = normalize_author(groups["author"])
                if "series" in groups: meta["series"] = normalize_series(groups["series"])
                if "title" in groups: meta["title"] = groups["title"].strip()
                if "series_index" in groups:
                    try:
                        meta["series_index"] = float(groups["series_index"])
                    except ValueError:
                        pass
                
                # If we have at least a title, we consider it a match
                if meta["title"]:
                    meta["_metadata_source"] = "pattern"
                    meta["_metadata_pattern"] = pattern_str
                    
                    # Fallbacks from context if missing in pattern
                    if not meta["author"] and parent_dir_name:
                         meta["author"] = normalize_author(parent_dir_name)
                    
                    logger.debug(f"[metadata]   Final (pattern): {meta}")
                    return meta
        except Exception as e:
             # Applying a compiled pattern to one path, not compiling it: this
             # is now a per-file event about a specific file, which is what a
             # per-file log line should be about.
             logger.warning(f"[metadata]   Pattern '{pattern_str}' failed on '{filename}': {e}")
             continue

    # Fallback to simple filename cleaning
    meta["title"] = extract_title_from_filename(filename)
    meta["_metadata_source"] = "filename"
    meta["_metadata_pattern"] = None
    if parent_dir_name:
        meta["author"] = normalize_author(parent_dir_name)
    
    logger.debug(f"[metadata]   Final (fallback): {meta}")
    return meta


def _read_embedded_metadata(filepath: str, file_type: str) -> Dict[str, Any]:
    """Read the tags embedded in the file itself. Blocking; call it on a thread.

    Split out of :func:`extract_metadata` so the whole of it runs off the event
    loop (issue #203). Everything here is synchronous file work — unzipping and
    XML-parsing the EPUB, opening the audio container with mutagen, and shelling
    out to ffprobe for the runtime — and one uvicorn worker serves every request,
    so doing it inline stalled position sync, audio streaming, login and
    ``/api/health`` for the length of a library scan.

    Returns whatever it could read; a file with no readable tags returns ``{}``.
    Failures are logged and swallowed on purpose: an unreadable EPUB or a
    truncated container must fall back to the filename, not fail the scan.
    """
    file_meta: Dict[str, Any] = {}
    try:
        if file_type == "ebook" and filepath.lower().endswith(".epub"):
            book = epub.read_epub(filepath, options={'ignore_ncx': True})
            
            # Title
            t = book.get_metadata('DC', 'title')
            if t: file_meta["title"] = t[0][0]
            
            # Author
            c = book.get_metadata('DC', 'creator')
            if c: file_meta["author"] = normalize_author(c[0][0])
            
            # Extended EPUB metadata
            desc = book.get_metadata('DC', 'description')
            if desc: file_meta["description"] = md(desc[0][0]).strip()
            
            pub = book.get_metadata('DC', 'publisher')
            if pub: file_meta["publisher"] = pub[0][0]
            
            lang = book.get_metadata('DC', 'language')
            if lang: file_meta["language"] = lang[0][0]
            
            date = book.get_metadata('DC', 'date')
            if date:
                match = re.search(r'\d{4}', date[0][0])
                if match: file_meta["publish_year"] = int(match.group(0))
                
            subjects = book.get_metadata('DC', 'subject')
            if subjects: file_meta["genres"] = ",".join([s[0] for s in subjects if s[0]])
                
            identifiers = book.get_metadata('DC', 'identifier')
            if identifiers:
                for id_tuple in identifiers:
                    val = id_tuple[0].lower()
                    if 'isbn' in val or (len(id_tuple)>1 and isinstance(id_tuple[1], dict) and 'isbn' in str(id_tuple[1]).lower()):
                        file_meta["isbn"] = id_tuple[0].replace('urn:isbn:', '')
                    elif 'asin' in val or (len(id_tuple)>1 and isinstance(id_tuple[1], dict) and 'asin' in str(id_tuple[1]).lower()):
                        file_meta["asin"] = id_tuple[0].replace('urn:asin:', '')
            
            # Series (Calibre stores in <meta name="calibre:series" content="..."/>)
            all_meta = book.get_metadata('OPF', 'meta')
            if all_meta:
                for m in all_meta:
                    attrs = m[1] if len(m) > 1 else {}
                    if attrs.get('name') == 'calibre:series':
                        file_meta["series"] = normalize_series(attrs.get('content', ''))
                    elif attrs.get('name') == 'calibre:series_index':
                        try:
                            file_meta["series_index"] = float(attrs.get('content', '0'))
                        except ValueError:
                            pass
            
            # If no series found in Calibre meta, try extracting from title
            if not file_meta.get('series'):
                title_str = file_meta.get('title', '')
                s_name, s_idx = extract_series_and_index(title_str)
                if s_name and s_idx is not None:
                    file_meta['series'] = s_name
                    file_meta['series_index'] = s_idx

            logger.debug(f"[extract_metadata]   EPUB embedded: {file_meta}")

        elif file_type == "audiobook":
            # Runtime, off the container header (issue #127). ffprobe is the
            # authority and mutagen's info.length only the fallback for hosts
            # without ffmpeg — see services/audio_duration.py for the library-wide
            # measurement behind that ordering. Left *absent* when neither can
            # supply one; callers take "no key" as "leave whatever is stored
            # alone", and an unknown length cleanly disables the audio end zone
            # while a wrong one would silently finish the book.
            duration = probe_duration_seconds(filepath)

            try:
                audio = mutagen.File(filepath)
            except mutagen.mp4.MP4MetadataError:
                audio = None
                logger.warning(f"[extract_metadata] MP4 chapter parse failed for {filepath}, skipping embedded tags")

            # The mutagen fallback is guarded on `is not None`, not on the
            # `if audio:` below: mutagen's FileType defines __len__ as its tag
            # count and no __bool__, so a file with *no tags at all* is falsy.
            # Those are exactly the files where the length is the only metadata
            # worth recovering.
            if duration is None and audio is not None:
                duration = getattr(getattr(audio, 'info', None), 'length', None)
            if duration and duration > 0:
                file_meta["duration_seconds"] = int(duration)

            if audio:
                logger.debug(f"[extract_metadata]   Mutagen type: {type(audio).__name__}")
                logger.debug(f"[extract_metadata]   Available tags: {list(audio.keys())[:30]}")

                # MP4/M4B/M4A files (mutagen.mp4.MP4)
                if hasattr(audio, 'tags') and hasattr(audio, 'info'):
                    audio_type = type(audio).__name__
                    
                    if audio_type in ('MP4', 'M4A'):
                        # MP4/M4B uses iTunes-style atoms
                        if '\xa9nam' in audio: file_meta["title"] = str(audio['\xa9nam'][0])
                        if '\xa9ART' in audio: file_meta["author"] = normalize_author(str(audio['\xa9ART'][0]))
                        
                        series_str = None
                        custom_series = audio.get('----:com.apple.iTunes:SERIES')
                        if custom_series:
                            series_str = bytes(custom_series[0]).decode('utf-8', 'replace').strip()
                        elif '\xa9grp' in audio:
                            series_str = str(audio['\xa9grp'][0])

                        if series_str:
                            s_name, s_idx = extract_series_and_index(series_str)
                            if s_name: file_meta["series"] = s_name
                            if s_idx is not None: file_meta["series_index"] = s_idx

                        custom_series_part = audio.get('----:com.apple.iTunes:SERIES-PART')
                        if custom_series_part and not file_meta.get('series_index'):
                            try:
                                file_meta['series_index'] = float(bytes(custom_series_part[0]).decode('utf-8', 'replace').strip())
                            except ValueError:
                                pass
                        
                        if '\xa9des' in audio: file_meta["description"] = md(str(audio['\xa9des'][0])).strip()
                        elif 'desc' in audio: file_meta["description"] = md(str(audio['desc'][0])).strip()
                        elif '\xa9cmt' in audio: file_meta["description"] = md(str(audio['\xa9cmt'][0])).strip()
                        
                        if '\xa9day' in audio:
                            match = re.search(r'\d{4}', str(audio['\xa9day'][0]))
                            if match: file_meta["publish_year"] = int(match.group(0))
                            
                        if '\xa9gen' in audio: file_meta["genres"] = str(audio['\xa9gen'][0])
                        
                        custom_narrator = audio.get('----:com.apple.iTunes:NARRATOR')
                        if custom_narrator:
                            file_meta["narrators"] = bytes(custom_narrator[0]).decode('utf-8', 'replace').strip()
                        elif '\xa9wrt' in audio:
                            file_meta["narrators"] = str(audio['\xa9wrt'][0])
                        elif '\xa9com' in audio:
                            file_meta["narrators"] = str(audio['\xa9com'][0])

                        if '\xa9pub' in audio: file_meta["publisher"] = str(audio['\xa9pub'][0])
                        elif '----:com.apple.iTunes:publisher' in audio:
                            file_meta["publisher"] = str(audio['----:com.apple.iTunes:publisher'][0], 'utf-8')
                    elif audio_type in ('MP3', 'FLAC', 'OggVorbis', 'OggOpus'):
                        # ID3 tags (MP3)
                        if 'TIT2' in audio: file_meta["title"] = str(audio['TIT2'])
                        elif 'title' in audio: file_meta["title"] = str(audio['title'][0])
                        
                        if 'TPE1' in audio: file_meta["author"] = normalize_author(str(audio['TPE1']))
                        elif 'artist' in audio: file_meta["author"] = normalize_author(str(audio['artist'][0]))
                        
                        series_str = None
                        if 'TIT3' in audio: series_str = str(audio['TIT3'])
                        elif 'subtitle' in audio: series_str = str(audio['subtitle'][0])
                        elif 'TIT1' in audio: series_str = str(audio['TIT1'])
                        elif 'GRP1' in audio: series_str = str(audio['GRP1'])
                        elif 'grouping' in audio: series_str = str(audio['grouping'][0])
                        elif 'TALB' in audio: series_str = str(audio['TALB'])
                        elif 'album' in audio: series_str = str(audio['album'][0])
                        
                        if series_str:
                            s_name, s_idx = extract_series_and_index(series_str)
                            if s_name: file_meta["series"] = s_name
                            if s_idx is not None: file_meta["series_index"] = s_idx
                        
                        # Extended fields
                        for kv in audio.keys():
                            if kv.startswith('COMM'):
                                file_meta["description"] = md(str(audio[kv].text[0])).strip()
                                break
                        if 'description' not in file_meta:
                            for dk in ['description', 'summary']:
                                if dk in audio: 
                                    file_meta["description"] = md(str(audio[dk][0])).strip()
                                    break
                                    
                        if 'TCON' in audio: file_meta["genres"] = str(audio['TCON'])
                        elif 'genre' in audio: file_meta["genres"] = str(audio['genre'][0])
                        
                        year_val = None
                        if 'TDRC' in audio: year_val = str(audio['TDRC'])
                        elif 'TYER' in audio: year_val = str(audio['TYER'])
                        elif 'date' in audio: year_val = str(audio['date'][0])
                        elif 'year' in audio: year_val = str(audio['year'][0])
                        if year_val:
                            match = re.search(r'\d{4}', year_val)
                            if match: file_meta["publish_year"] = int(match.group(0))
                            
                        if 'TPUB' in audio: file_meta["publisher"] = str(audio['TPUB'])
                        elif 'organization' in audio: file_meta["publisher"] = str(audio['organization'][0])
                        elif 'publisher' in audio: file_meta["publisher"] = str(audio['publisher'][0])
                        
                        if 'TCOM' in audio: file_meta["narrators"] = str(audio['TCOM'])
                        elif 'composer' in audio: file_meta["narrators"] = str(audio['composer'][0])
                    else:
                        # Generic fallback — try common keys
                        for title_key in ['\xa9nam', 'TIT2', 'title', 'TITLE']:
                            if title_key in audio:
                                val = audio[title_key]
                                file_meta["title"] = str(val[0]) if isinstance(val, list) else str(val)
                                break
                        for author_key in ['\xa9ART', 'TPE1', 'artist', 'ARTIST']:
                            if author_key in audio:
                                val = audio[author_key]
                                file_meta["author"] = normalize_author(str(val[0]) if isinstance(val, list) else str(val))
                                break
                        for album_key in ['\xa9alb', 'TALB', 'album', 'ALBUM']:
                            if album_key in audio:
                                val = audio[album_key]
                                file_meta["series"] = normalize_series(str(val[0]) if isinstance(val, list) else str(val))
                                break
                
                logger.debug(f"[extract_metadata]   Audio embedded: {file_meta}")
                
    except Exception as e:
        logger.warning(f"[extract_metadata]   Embedded metadata read failed: {e}")

    return file_meta


async def extract_metadata(
    filepath: str, file_type: str, db: AsyncSession,
    library_root: str = None
) -> Dict[str, Any]:
    """
    Extract metadata with priority:
    1. Embedded Metadata (EPUB/ID3/M4B tags)
    2. Regex on Filename (including directory-path patterns)
    3. Fallback to simple filename parsing
    
    Args:
        filepath: Absolute path to the file
        file_type: 'ebook' or 'audiobook'
        db: Database session
        library_root: Root directory of the library for computing relative paths
    """
    filename = os.path.basename(filepath)
    parent_dir = os.path.basename(os.path.dirname(filepath))
    
    # Compute relative path from the library root for directory-based patterns
    relative_path = None
    if library_root:
        try:
            relative_path = os.path.relpath(filepath, library_root)
            relative_path = relative_path.replace('\\', '/')
        except ValueError:
            pass  # Different drives on Windows, etc.
    
    logger.debug(f"[extract_metadata] Processing '{filepath}' (type={file_type})")
    logger.debug(f"[extract_metadata]   library_root='{library_root}', relative_path='{relative_path}'")
    
    # 1. Filename/path metadata (Regex/Settings) - Default priority as requested:
    filename_meta = await parse_filename_metadata_with_settings(
        filename, db, parent_dir, file_type, relative_path=relative_path
    )
    
    # 2. Embedded metadata, off the event loop (issue #203). This unzips and
    #    XML-parses the EPUB, or opens the audio container and probes its
    #    runtime — seconds of blocking file work per book on a single worker.
    file_meta = await asyncio.to_thread(_read_embedded_metadata, filepath, file_type)
        
    # Merge: Prefer embedded if exists, but keep filename/path data as fallback
    meta = filename_meta.copy()

    has_embedded = False
    for field in MEDIA_EXTRACTED_FIELDS:
        if file_meta.get(field) is not None:
            meta[field] = file_meta[field]
            has_embedded = True

    # Duration is a physical property of the file rather than something a
    # tagger wrote, so it merges outside the loop above: a readable length must
    # not by itself flip `_metadata_source` to "embedded".
    if file_meta.get("duration_seconds") is not None:
        meta["duration_seconds"] = file_meta["duration_seconds"]

    # Track the source: if embedded data overrode anything, note it
    if has_embedded:
        if meta.get("_metadata_source") == "pattern":
            meta["_metadata_source"] = "embedded+pattern"
        else:
            meta["_metadata_source"] = "embedded"
    
    logger.debug(f"[extract_metadata]   Merged result: {meta}")
    return meta


def sanitize_filename(name: str) -> str:
    if not name: return ""
    name = re.sub(r'[\\/*?:"<>|]', "", name)
    name = name.replace(" ", "_")
    return name.strip()

def _extract_and_save_cover(filepath: str, book_type: str, book_id: int, book_title: str = None) -> Optional[str]:
    covers_path = Path(settings.covers_dir)
    covers_path.mkdir(parents=True, exist_ok=True)
    
    cover_bytes = None
    ext = ".jpg"
    
    try:
        if book_type == "ebook" and filepath.lower().endswith(".epub"):
            book = epub.read_epub(filepath, options={'ignore_ncx': True})
            for item in book.get_items():
                if item.get_type() == ebooklib.ITEM_COVER:
                    cover_bytes = item.get_content()
                    ext = Path(item.file_name).suffix if item.file_name else ".jpg"
                    break
                elif item.get_type() == ebooklib.ITEM_IMAGE:
                    if "cover" in (item.file_name or "").lower():
                        cover_bytes = item.get_content()
                        ext = Path(item.file_name).suffix if item.file_name else ".jpg"
        
        elif book_type == "audiobook":
            audio = mutagen.File(filepath)
            if audio:
                if type(audio).__name__ in ('MP4', 'M4A') and 'covr' in audio:
                    covers = audio['covr']
                    if covers:
                        cover_bytes = bytes(covers[0])
                        ext = ".png" if getattr(covers[0], 'imageformat', None) == mutagen.mp4.MP4Cover.FORMAT_PNG else ".jpg"
                elif type(audio).__name__ == 'MP3':
                    from mutagen.id3 import APIC
                    if hasattr(audio, 'tags') and audio.tags:
                        for tag in audio.tags.values():
                            if isinstance(tag, APIC):
                                cover_bytes = tag.data
                                ext = ".png" if "png" in tag.mime.lower() else ".jpg"
                                break
                elif type(audio).__name__ in ('FLAC', 'OggVorbis', 'OggOpus'):
                    if getattr(audio, 'pictures', None) and audio.pictures:
                        cover_bytes = audio.pictures[0].data
                        ext = ".png" if "png" in audio.pictures[0].mime.lower() else ".jpg"
                        
        if cover_bytes:
            safe_title = sanitize_filename(book_title)
            base_name = safe_title if safe_title else book_type
            filename = f"{base_name}_{book_id}{ext}"
            dest_path = covers_path / filename
            with open(dest_path, "wb") as f:
                f.write(cover_bytes)
            return f"/api/files/covers/{filename}"
            
    except Exception as e:
        logger.warning(f"Failed to extract cover for {filepath}: {e}")
        
    return None


