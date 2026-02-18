"""
EPUB Parser Service

Extracts text from EPUB files and splits it into sentences,
organized by chapter.
"""

import logging
from dataclasses import dataclass
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


def extract_epub_sentences(epub_path: str) -> List[EpubSentence]:
    """
    Extract all sentences from an EPUB file, organized by chapter.

    Args:
        epub_path: Path to the EPUB file

    Returns:
        List of EpubSentence objects, ordered by chapter and position.
    """
    logger.info(f"Parsing EPUB: {epub_path}")

    book = epub.read_epub(epub_path)
    sentences = []
    chapter_index = 0

    # Get items in reading order
    items = list(book.get_items_of_type(ebooklib.ITEM_DOCUMENT))

    for item in items:
        content = item.get_content().decode("utf-8", errors="ignore")
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

    logger.info(
        f"Extracted {len(sentences)} sentences from {chapter_index} chapters"
    )

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
