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
from typing import List, Optional, Tuple
from urllib.parse import unquote, urldefrag

from ebooklib import epub
from bs4 import BeautifulSoup
from bs4.element import CData, NavigableString, Tag
import nltk

from services.nltk_data import ensure_punkt

logger = logging.getLogger(__name__)

# Bump when sentence splitting changes in a way that shifts `sentence_index`, or
# when what a sync map stores per sentence changes; sync maps stamped with a
# lower value are outdated and the bulk rebuild realigns them (issue #774).
# 3: sync points store the whole sentence, not its first 200 chars (issue #763).
# 4: inline element boundaries no longer insert a space (issue #799).
SENTENCE_SPLITTER_VERSION = 4


@dataclass
class EpubSentence:
    """A sentence extracted from an EPUB with chapter/position info."""
    chapter: int           # Chapter index (0-based)
    sentence_index: int    # Sentence index within the chapter (0-based)
    text: str              # The sentence text
    chapter_title: str = ""  # Optional chapter title


def _legacy_extract_text_from_html(html_content: str) -> str:
    """
    The pre-#799 text extraction: a line break at EVERY tag boundary, so
    `<i>W</i>ord` reads `W` and `ord` on two lines.

    **Do not change this, and do not route it through `_extract_blocks_from_html`.**
    Only `old_chapter_to_spine_index` calls it, for Alembic migration 0004's
    chapter re-basing. That migration already ran in production and a fresh
    install replays it, so what it decides counts as "a document that produced
    sentences" must stay exactly what it was. Everything else uses
    `_extract_text_from_html` (issue #810).
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


def _extract_text_from_html(html_content: str) -> str:
    """
    A document's readable text, one line per block, read the way the stored
    sentences are (issue #810).

    This is `_extract_blocks_from_html` joined with newlines, so inline element
    boundaries add nothing (`<i>W</i>ord` is `Word`) while block elements and
    `<br>` separate. The sync-map drift audit searches stored sentences in this
    text and the plausibility word count counts it, so both must see the same
    words the sentences do.
    """
    return "\n".join(_extract_blocks_from_html(html_content))


_BLOCK_TAGS = frozenset({
    "p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li", "ul", "ol", "dl",
    "dt", "dd", "blockquote", "pre", "section", "article", "aside", "header",
    "footer", "nav", "main", "figure", "figcaption", "table", "tr", "td", "th",
    "caption", "hr", "address", "body", "html",
})


def _extract_blocks_from_html(html_content: str) -> List[str]:
    """
    Split an HTML document into its text blocks, in document order (issue #774).

    A block-level element flushes the running text both when it opens and when
    it closes, so loose text between two paragraphs is a block of its own and
    nested blocks never merge. Inline elements do not flush, and they add no
    whitespace of their own: a block's text nodes are concatenated exactly as
    they are and runs of whitespace are then collapsed, so `<b>Name</b>: x` is
    `Name: x` and `<i>W</i>ord` is `Word`, as on the rendered page (issue #799).
    The web client's `extractSearchableText` mirrors this. A single `<br>` is
    only whitespace; two or more in a row (nothing but whitespace between them)
    end the block.
    """
    soup = BeautifulSoup(html_content, "html.parser")

    for element in soup(["script", "style", "head"]):
        element.decompose()

    blocks: List[str] = []
    buffer: List[str] = []

    def flush() -> None:
        text = " ".join("".join(buffer).split())
        buffer.clear()
        if text:
            blocks.append(text)

    consecutive_br = 0
    # Iterative walk (deeply nested markup must not hit the recursion limit);
    # each entry is (children iterator, tag to flush on leaving or None).
    stack = [(iter(soup.contents), None)]
    while stack:
        children, closing = stack[-1]
        node = next(children, None)
        if node is None:
            stack.pop()
            if closing in _BLOCK_TAGS:
                flush()
                consecutive_br = 0
            continue
        if isinstance(node, Tag):
            if node.name == "br":
                buffer.append(" ")
                consecutive_br += 1
                if consecutive_br >= 2:
                    flush()
                continue
            consecutive_br = 0
            if node.name in _BLOCK_TAGS:
                flush()
            stack.append((iter(node.contents), node.name))
        elif type(node) in (NavigableString, CData):
            text = str(node)
            if text.strip():
                consecutive_br = 0
            # Whitespace-only nodes are kept: between two inline elements they
            # are the one real space the page shows.
            buffer.append(text)
    flush()
    return blocks


def _split_into_sentences(text: str) -> List[str]:
    """Split text into sentences using NLTK.

    Kept for Alembic migration 0004 (via `old_chapter_to_spine_index`, on the
    legacy extraction); sentence building uses `_extract_blocks_from_html` and
    `_split_block_into_sentences`.
    """
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


def _split_block_into_sentences(block: str) -> List[str]:
    """
    Split one block's text into sentences; none can span the block's edges.

    A sentence of fewer than three words is not dropped: it joins the previous
    sentence of the block, or, when it opens the block, the next one. A block
    with fewer than three words in total yields nothing.
    """
    ensure_punkt()
    pieces = [s.strip() for s in nltk.sent_tokenize(block)]

    sentences: List[str] = []
    pending = ""  # leading fragments waiting for the next full sentence
    for piece in pieces:
        if not piece:
            continue
        if len(piece.split()) >= 3:
            sentences.append(f"{pending} {piece}" if pending else piece)
            pending = ""
        elif sentences:
            sentences[-1] = f"{sentences[-1]} {piece}"
        else:
            pending = f"{pending} {piece}" if pending else piece

    if pending:  # the block held nothing but short fragments
        return [pending] if len(pending.split()) >= 3 else []
    return sentences


def _build_sentences_from_documents(documents: List[str]) -> List[EpubSentence]:
    """
    Build the ordered EpubSentence list from the spine's document strings.

    [documents] must be aligned 1:1 with the EPUB spine — one entry per
    itemref, in spine order, with "" for anything unreadable. The list index
    IS the chapter number.

    That alignment is the whole point: the web reader positions itself by
    spine index (epub.js `book.spine.items`), and Android translates to and from
    it because Readium's `readingOrder` omits `linear="no"` items (issue #804),
    so a chapter number only means something to them if it is one. An earlier
    version incremented its own counter and skipped documents that produced no
    sentences, which silently shifted every chapter after the first blank page.
    """
    sentences: List[EpubSentence] = []

    for spine_index, content in enumerate(documents):
        blocks = _extract_blocks_from_html(content)

        if not blocks:
            continue

        # Try to extract chapter title from the first block
        chapter_title = ""
        if len(blocks[0].split()) <= 10:
            # First block is short enough to be a title
            chapter_title = blocks[0]

        # Split each block separately so no sentence spans two of them
        chapter_sentences: List[str] = []
        for block in blocks:
            chapter_sentences.extend(_split_block_into_sentences(block))

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

    Deliberately on `_legacy_extract_text_from_html`: this reproduces the old
    numbering exactly as it was when the migration's data was written.
    """
    mapping: List[int] = []
    for spine_index, content in enumerate(documents):
        text = _legacy_extract_text_from_html(content)
        if not text.strip():
            continue
        if not _split_into_sentences(text):
            continue
        mapping.append(spine_index)
    return mapping


_CONTENT_MEDIA_TYPES = frozenset({"application/xhtml+xml", "text/html"})
_CONTENT_EXTENSIONS = (".xhtml", ".html", ".htm")


def _is_content_document(path: str, media_type: Optional[str]) -> bool:
    """Whether a spine item is a content document the parser should read.

    EPUB says so through the manifest `media-type`, not the file name (issue
    #561): publisher tooling names XHTML files `chapter01.xml`, and the old
    extension-only rule read every such book as empty. `text/html` covers old
    EPUB 2 files. A missing media-type falls back to the extension.

    An HTML-named item still counts whatever its media-type says. The extension
    rule used to admit those, and dropping one now would take sentences out of a
    chapter that may already have stored positions and sync points.
    """
    if media_type and media_type.split(";", 1)[0].strip().lower() in _CONTENT_MEDIA_TYPES:
        return True
    return path.lower().endswith(_CONTENT_EXTENSIONS)


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
            item.get("id"): (item.get("href"), item.get("media-type"))
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
            href, media_type = manifest.get(sid, (None, None))
            # A manifest href is a URL, not a file name (issue #554): the zip entry
            # `Text/Axis Test_1.html` is referenced as `Text/Axis%20Test_1.html`.
            # Reading the encoded name missed the entry and silently emptied the
            # slot, so a book named that way throughout extracted no text at all.
            # epub.js and Readium both decode, which is why such books read fine.
            path = unquote(urldefrag(href).url) if href else ""
            if not path or not _is_content_document(path, media_type):
                # Still a spine slot as far as the readers are concerned.
                documents.append("")
                continue
            # Resolve href relative to the OPF directory (zip uses forward slashes).
            full = path if not opf_dir else f"{opf_dir}/{path}"
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

        sentences_text: List[str] = []
        for block in _extract_blocks_from_html(html_content):
            sentences_text.extend(_split_block_into_sentences(block))

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


def extract_spine_chapter_texts(path: str) -> List[str]:
    """Readable text per spine item, one entry per itemref, in spine order —
    the same extraction `extract_book_text` joins into a single string,
    kept separate here for the sync-map audit's spine-order check (issue
    #595): for a sample of a map's stored points, which chapter that text
    *actually* lives in today, regardless of what chapter numbering the map
    itself stored (a map from before spine-order parsing used a different,
    now-wrong numbering — see the module docstring on `old_chapter_to_spine_index`).

    Deliberately stops short of sentence tokenization, same reasoning as
    `extract_book_text`: this only needs "which chapter is this text in",
    not sentence-level positions, and NLTK over a whole novel is the
    expensive half of parsing.

    MOBI has no independently-addressable spine here (`extract_mobi_sentences`
    treats the whole converted document as one unit), so it comes back as a
    single-entry list — a spine-order mismatch can't be detected for MOBI by
    this function, only EPUB.
    """
    if Path(path).suffix.lower() == ".mobi":
        return ["\n".join(s.text for s in extract_mobi_sentences(path))]
    documents = _load_epub_documents(path)
    return [_extract_text_from_html(doc) for doc in documents]


def extract_book_text(path: str) -> str:
    """The whole book's readable text, spine order, as one string.

    Deliberately stops short of sentence tokenization: the drift audit only asks
    "does this stored sentence occur anywhere in the book?", and NLTK over a
    whole novel is the expensive half of parsing. Same extraction as
    `extract_book_sentences` otherwise, so the two agree on what the text *is*.
    """
    return "\n".join(extract_spine_chapter_texts(path))
