"""
EPUB Parser Service

Extracts text from EPUB files and splits it into sentences,
organized by chapter.
"""

import logging
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import List, Tuple

from ebooklib import epub
from bs4 import BeautifulSoup
import nltk

from services.nltk_data import ensure_punkt

logger = logging.getLogger(__name__)



@dataclass
class EpubSentence:
    """A sentence extracted from an EPUB with chapter/position info."""
    chapter: int           # Chapter index (0-based)
    sentence_index: int    # Sentence index within the chapter (0-based)
    text: str              # The sentence text
    chapter_title: str = ""  # Optional chapter title


def _extract_text_from_html(html_content: str) -> str:
    """
    Extract readable text from HTML content, preserving paragraph breaks.
    """
    soup = BeautifulSoup(html_content, "html.parser")

    # Remove script and style elements
    for element in soup(["script", "style", "head"]):
        element.decompose()

    # Get text, using newlines to separate block elements
    text = soup.get_text(separator="\n")

    # Clean up whitespace
    lines = []
    for line in text.splitlines():
        line = line.strip()
        if line:
            lines.append(line)

    return "\n".join(lines)


def _split_into_sentences(text: str) -> List[str]:
    """Split text into sentences using NLTK."""
    # Lazily, not at import: this used to run at module scope and could
    # block the whole app lifespan on a slow CDN (issue #322).
    ensure_punkt()
    sentences = nltk.sent_tokenize(text)

    # Filter out very short fragments (likely headers or artifacts)
    filtered = []
    for sent in sentences:
        sent = sent.strip()
        # Keep sentences that have at least 3 words
        if len(sent.split()) >= 3:
            filtered.append(sent)

    return filtered


def _build_sentences_from_documents(documents: List[str]) -> List[EpubSentence]:
    """
    Build the ordered EpubSentence list from the spine's document strings.

    [documents] must be aligned 1:1 with the EPUB spine — one entry per
    itemref, in spine order, with "" for anything unreadable. The list index
    IS the chapter number.

    That alignment is the whole point: both readers position themselves by
    spine index (epub.js `book.spine.items`, Readium `publication.readingOrder`),
    so a chapter number only means something to them if it is one. An earlier
    version incremented its own counter and skipped documents that produced no
    sentences, which silently shifted every chapter after the first blank page.
    """
    sentences: List[EpubSentence] = []

    for spine_index, content in enumerate(documents):
        text = _extract_text_from_html(content)

        if not text.strip():
            continue

        # Try to extract chapter title from the first line
        lines = text.strip().split("\n")
        chapter_title = ""
        if lines and len(lines[0].split()) <= 10:
            # First line is short enough to be a title
            chapter_title = lines[0].strip()

        # Split into sentences
        chapter_sentences = _split_into_sentences(text)

        if not chapter_sentences:
            continue

        for sent_index, sent_text in enumerate(chapter_sentences):
            sentences.append(EpubSentence(
                chapter=spine_index,
                sentence_index=sent_index,
                text=sent_text,
                chapter_title=chapter_title,
            ))

    return sentences


def old_chapter_to_spine_index(documents: List[str]) -> List[int]:
    """
    Remap table from the pre-spine-index chapter numbering to spine indices.

    Old chapter N was "the Nth document that produced sentences", so entry N of
    the returned list is that document's true spine index. Used by the
    migration to re-base stored sync points without re-running alignment.
    """
    mapping: List[int] = []
    for spine_index, content in enumerate(documents):
        text = _extract_text_from_html(content)
        if not text.strip():
            continue
        if not _split_into_sentences(text):
            continue
        mapping.append(spine_index)
    return mapping


def _extract_epub_documents_via_zip(epub_path: str) -> List[str]:
    """
    Read an EPUB's content documents in spine order WITHOUT ebooklib.

    Returns one entry per spine itemref, in spine order, with "" for any item
    that can't be read or isn't a content document. **The list index is the
    spine index** — entries are never dropped, because dropping one would shift
    every chapter after it away from what the readers use.

    This is the primary path: it walks container.xml -> OPF -> manifest/spine
    directly, which is both spine-accurate and immune to the ebooklib NCX bug
    (0.18.x crashes on some valid EPUB2 files whose `toc.ncx` won't parse).
    Namespace-agnostic via local-name().
    """
    import zipfile
    from lxml import etree

    # Explicit rather than inherited (issue #265). lxml >= 5 defaults to
    # resolve_entities='internal', so this is safe today by library default --
    # but an EPUB is attacker-supplied input and the setting that protects it
    # should be visible at the call site, not a version-dependent default.
    safe = etree.XMLParser(
        resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False
    )

    with zipfile.ZipFile(epub_path) as z:
        container = etree.fromstring(z.read("META-INF/container.xml"), parser=safe)
        opf_paths = container.xpath('//*[local-name()="rootfile"]/@full-path')
        if not opf_paths:
            raise RuntimeError("no rootfile in META-INF/container.xml")
        opf_path = opf_paths[0]
        opf = etree.fromstring(z.read(opf_path), parser=safe)
        opf_dir = os.path.dirname(opf_path)

        manifest = {
            item.get("id"): item.get("href")
            for item in opf.xpath('//*[local-name()="item"]')
            if item.get("id") and item.get("href")
        }
        spine_ids = [
            ref.get("idref")
            for ref in opf.xpath('//*[local-name()="itemref"]')
            if ref.get("idref")
        ]

        documents: List[str] = []
        for sid in spine_ids:
            href = manifest.get(sid)
            if not href or not href.lower().split("#")[0].endswith((".xhtml", ".html", ".htm")):
                # Still a spine slot as far as the readers are concerned.
                documents.append("")
                continue
            # Resolve href relative to the OPF directory (zip uses forward slashes).
            full = href if not opf_dir else f"{opf_dir}/{href}"
            full = os.path.normpath(full).replace(os.sep, "/").lstrip("/")
            try:
                raw = z.read(full)
            except KeyError:
                documents.append("")
                continue
            documents.append(raw.decode("utf-8", errors="ignore"))

    return documents


def _extract_epub_documents_via_ebooklib(epub_path: str) -> List[str]:
    """
    Fallback extractor, also spine-aligned.

    Walks `book.spine` (itemref order) rather than
    `get_items_of_type(ITEM_DOCUMENT)`, which yields *manifest* order and so
    can't produce spine indices at all.
    """
    book = epub.read_epub(epub_path)
    documents: List[str] = []
    for idref, *_ in book.spine:
        item = book.get_item_with_id(idref)
        if item is None:
            documents.append("")
            continue
        try:
            documents.append(item.get_content().decode("utf-8", errors="ignore"))
        except Exception:
            documents.append("")
    return documents


def _load_epub_documents(epub_path: str) -> List[str]:
    """The EPUB's content documents in spine order, one entry per itemref.

    The zip+OPF spine walk is primary: it is spine-accurate by construction and
    immune to ebooklib's NCX crash. ebooklib is the fallback for archives the
    direct walk can't make sense of.
    """
    try:
        return _extract_epub_documents_via_zip(epub_path)
    except Exception as e:
        logger.warning(f"zip+OPF walk failed on '{epub_path}' ({e}); trying ebooklib")
        try:
            return _extract_epub_documents_via_ebooklib(epub_path)
        except Exception as fallback_err:
            raise RuntimeError(
                f"Failed to open EPUB '{epub_path}': {e} "
                f"(ebooklib fallback also failed: {fallback_err})"
            ) from e


def extract_epub_sentences(epub_path: str) -> List[EpubSentence]:
    """
    Extract all sentences from an EPUB file, organized by chapter.

    Args:
        epub_path: Path to the EPUB file

    Returns:
        List of EpubSentence objects, ordered by chapter and position.
    """
    logger.info(f"Parsing EPUB: {epub_path}")

    documents = _load_epub_documents(epub_path)

    sentences = _build_sentences_from_documents(documents)

    logger.info(f"Extracted {len(sentences)} sentences from EPUB")

    return sentences


def get_epub_metadata(epub_path: str) -> dict:
    """Extract metadata (title, author, etc.) from an EPUB file."""
    book = epub.read_epub(epub_path)

    title = book.get_metadata("DC", "title")
    author = book.get_metadata("DC", "creator")
    language = book.get_metadata("DC", "language")

    return {
        "title": title[0][0] if title else None,
        "author": author[0][0] if author else None,
        "language": language[0][0] if language else None,
    }


def extract_mobi_sentences(mobi_path: str) -> List[EpubSentence]:
    """
    Extract all sentences from a MOBI file, organized by chapter.

    Uses the mobi library to unpack the file, then processes the extracted
    HTML with the same pipeline used for EPUBs.

    Args:
        mobi_path: Path to the MOBI file

    Returns:
        List of EpubSentence objects, ordered by chapter and position.
    """
    import mobi as mobi_lib

    logger.info(f"Parsing MOBI: {mobi_path}")

    tempdir = None
    try:
        tempdir, extracted_path = mobi_lib.extract(mobi_path)

        # The extracted file may be an HTML file or an EPUB — handle both
        extracted = Path(extracted_path)
        if extracted.suffix.lower() in (".epub",):
            return extract_epub_sentences(extracted_path)

        # Otherwise treat as raw HTML
        with open(extracted_path, "r", encoding="utf-8", errors="ignore") as f:
            html_content = f.read()

        text = _extract_text_from_html(html_content)
        sentences_text = _split_into_sentences(text)

        sentences = []
        for sent_index, sent_text in enumerate(sentences_text):
            sentences.append(EpubSentence(
                chapter=0,
                sentence_index=sent_index,
                text=sent_text,
            ))

        logger.info(f"Extracted {len(sentences)} sentences from MOBI (single chapter)")
        return sentences

    except Exception as e:
        raise RuntimeError(f"Failed to open MOBI '{mobi_path}': {e}") from e
    finally:
        if tempdir and os.path.exists(tempdir):
            shutil.rmtree(tempdir, ignore_errors=True)


def extract_book_sentences(path: str) -> List[EpubSentence]:
    """
    Extract sentences from an ebook file, dispatching based on file extension.

    Supports .epub and .mobi formats.

    Args:
        path: Path to the ebook file

    Returns:
        List of EpubSentence objects, ordered by chapter and position.
    """
    ext = Path(path).suffix.lower()
    if ext == ".mobi":
        return extract_mobi_sentences(path)
    return extract_epub_sentences(path)


def extract_book_text(path: str) -> str:
    """The whole book's readable text, spine order, as one string.

    Deliberately stops short of sentence tokenization: the drift audit only asks
    "does this stored sentence occur anywhere in the book?", and NLTK over a
    whole novel is the expensive half of parsing. Same extraction as
    `extract_book_sentences` otherwise, so the two agree on what the text *is*.
    """
    if Path(path).suffix.lower() == ".mobi":
        return "\n".join(s.text for s in extract_mobi_sentences(path))
    documents = _load_epub_documents(path)
    return "\n".join(_extract_text_from_html(doc) for doc in documents)
