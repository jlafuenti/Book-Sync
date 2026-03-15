"""
Shared metadata utility functions used by both the library router
and the ABS metadata service.
"""

import re
from typing import Optional


def normalize_author(author: str) -> Optional[str]:
    """
    Normalize author name to 'First Last' format.
    Handles:
    - 'Last, First' -> 'First Last'
    - 'Jim Butcher' -> 'Jim Butcher' (no change)
    - 'Butcher, Jim' -> 'Jim Butcher'
    - Extra whitespace is stripped.
    """
    if not author: return None
    author = author.strip()
    if not author: return None

    if "," in author:
        parts = author.split(",", 1)
        first = parts[1].strip()
        last = parts[0].strip()
        if first and last:
            return f"{first} {last}"
        return last or first
    return author


def normalize_series(series: str) -> Optional[str]:
    """
    Normalize series name.
    - 'Dresden Files, The' -> 'The Dresden Files'
    - 'The Dresden Files' -> 'The Dresden Files' (preserved)
    - 'Dresden Files' -> 'Dresden Files' (no change, don't guess)
    - Trailing articles (A, An, The) are moved to the front.
    """
    if not series: return None
    clean = series.strip()
    if not clean: return None

    trailing_articles = [', The', ', A', ', An']
    for article in trailing_articles:
        if clean.endswith(article) or clean.lower().endswith(article.lower()):
            art = clean[-(len(article) - 2):].strip()
            base = clean[:-(len(article))].strip()
            return f"{art} {base}"

    return clean


def extract_series_and_index(text: str) -> tuple[Optional[str], Optional[float]]:
    """
    Given a string like 'The Cinder Spires #2' or 'The Cinder Spires, Book 2',
    returns the series name and the index.
    """
    if not text:
        return None, None
    text = text.strip()

    # Matches "Series Name #2", "Series Name # 2.5"
    match = re.search(r'#\s*(\d+(?:\.\d+)?)', text)
    if match:
        idx = float(match.group(1))
        series_name = text[:match.start()].strip()
        series_name = re.sub(r'[,:\-]\s*$', '', series_name).strip()
        return normalize_series(series_name), idx

    # Matches "Series Name, Book 2"
    match = re.search(r'(?:,\s*|\s+|-?\s*)Book\s+(\d+(?:\.\d+)?)', text, re.IGNORECASE)
    if match:
        idx = float(match.group(1))
        series_name = text[:match.start()].strip()
        series_name = re.sub(r'[,:\-]\s*$', '', series_name).strip()
        return normalize_series(series_name), idx

    return normalize_series(text), None
