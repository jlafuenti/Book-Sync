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

import ebooklib
from ebooklib import epub
from bs4 import BeautifulSoup
import nltk

logger = logging.getLogger(__name__)

# Ensure NLTK sentence tokenizer data is available
try:
    nltk.data.find("tokenizers/punkt_tab")
except LookupError:
    nltk.download("punkt_tab", quiet=True)


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
    Build the ordered EpubSentence list from a list of HTML/XHTML document
    strings already in reading order. Shared by the ebooklib path and the
    zip+OPF fallback path so both produce identical output.
    """
    sentences: List[EpubSentence] = []
    chapter_index = 0

    for content in documents:
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
                chapter=chapter_index,
                sentence_index=sent_index,
                text=sent_text,
                chapter_title=chapter_title,
            ))

        chapter_index += 1

    return sentences


def _extract_epub_documents_via_zip(epub_path: str) -> List[str]:
    """
    Read an EPUB's content documents in spine order WITHOUT ebooklib.

    ebooklib (0.18.x) crashes on some otherwise-valid EPUBs — notably Google
    Books EPUB2 files whose `toc.ncx` won't parse (`_parse_ncx` raises
    'NoneType has no attribute find', and `ignore_ncx` is forced off when there
    is no EPUB3 nav document). This walks container.xml -> OPF -> manifest/spine
    directly, which is immune to the NCX bug. Namespace-agnostic via local-name().
    """
    import zipfile
    from lxml import etree

    with zipfile.ZipFile(epub_path) as z:
        container = etree.fromstring(z.read("META-INF/container.xml"))
        opf_paths = container.xpath('//*[local-name()="rootfile"]/@full-path')
        if not opf_paths:
            raise RuntimeError("no rootfile in META-INF/container.xml")
        opf_path = opf_paths[0]
        opf = etree.fromstring(z.read(opf_path))
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
                continue
            # Resolve href relative to the OPF directory (zip uses forward slashes).
            full = href if not opf_dir else f"{opf_dir}/{href}"
            full = os.path.normpath(full).replace(os.sep, "/").lstrip("/")
            try:
                raw = z.read(full)
            except KeyError:
                continue
            documents.append(raw.decode("utf-8", errors="ignore"))

    return documents


def extract_epub_sentences(epub_path: str) -> List[EpubSentence]:
    """
    Extract all sentences from an EPUB file, organized by chapter.

    Args:
        epub_path: Path to the EPUB file

    Returns:
        List of EpubSentence objects, ordered by chapter and position.
    """
    logger.info(f"Parsing EPUB: {epub_path}")

    try:
        book = epub.read_epub(epub_path)
        documents = [
            item.get_content().decode("utf-8", errors="ignore")
            for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT)
        ]
    except Exception as e:
        # ebooklib is brittle (e.g. crashes on Google Books EPUB2 NCX). Fall back
        # to a direct zip+OPF spine walk before giving up.
        logger.warning(f"ebooklib failed on '{epub_path}' ({e}); trying zip+OPF fallback")
        try:
            documents = _extract_epub_documents_via_zip(epub_path)
        except Exception as fallback_err:
            raise RuntimeError(
                f"Failed to open EPUB '{epub_path}': {e} "
                f"(zip fallback also failed: {fallback_err})"
            ) from e

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
