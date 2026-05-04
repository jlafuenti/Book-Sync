"""
library_writer — places a file into the configured ebook/audiobook directory
using the first user-configured filename template, then leaves the rest to
the existing library scan logic.

Tokens supported in templates (already used elsewhere in the codebase):
  <Author>, <Series>, <Book Number>, <Series Index>, <Title>

If a token is unknown for a given book, that token (and any surrounding
bracketed group like '[<Series> <Book Number>]') is collapsed.
"""

import os
import re
import shutil
import logging
from pathlib import Path
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from models.settings import SystemSetting
from routers.settings import DEFAULT_SETTINGS

logger = logging.getLogger(__name__)


# Characters that are not safe in file paths on Windows/Linux.
_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _sanitize_segment(s: str) -> str:
    """Make a single path segment filesystem-safe."""
    s = _UNSAFE.sub("_", s).strip()
    s = re.sub(r"\s+", " ", s)
    s = s.rstrip(". ")
    return s or "Unknown"


async def _get_template(db: AsyncSession, book_type: str) -> str:
    """
    Return the first configured filename pattern for the given book_type
    ('ebook' or 'audiobook'). Falls back to DEFAULT_SETTINGS.
    """
    key = "ebook_filename_patterns" if book_type == "ebook" else "audiobook_filename_patterns"
    result = await db.execute(select(SystemSetting).where(SystemSetting.key == key))
    setting = result.scalar_one_or_none()
    if setting and setting.value:
        patterns = [p for p in setting.value.split("\n") if p.strip()]
        if patterns:
            return patterns[0]
    return DEFAULT_SETTINGS[key][0]


def render_template(template: str, meta: dict) -> str:
    """
    Substitute <Token> values in `template` with values from `meta`.

    If a token has no value, the token is removed; if its surrounding
    bracketed group `[...]` becomes empty as a result, the brackets are
    removed too. Repeated whitespace is collapsed.
    """
    series_idx = meta.get("series_index")
    if isinstance(series_idx, float) and series_idx.is_integer():
        series_idx = str(int(series_idx))
    elif series_idx is not None:
        series_idx = str(series_idx)

    token_values = {
        "<Author>": meta.get("author"),
        "<Series>": meta.get("series"),
        "<Book Number>": series_idx,
        "<Series Index>": series_idx,
        "<Title>": meta.get("title"),
        "<Book Title>": meta.get("title"),
    }

    out = template
    for tok, val in token_values.items():
        out = out.replace(tok, val if val else "")

    # Collapse empty brackets like "[]" or "[ ]" or "[ - ]"
    out = re.sub(r"\[\s*[-,\s]*\s*\]", "", out)
    # Collapse stray separators left behind ("  -  ", " - - ", trailing " - ")
    out = re.sub(r"\s+-\s+-\s+", " - ", out)
    out = re.sub(r"\s{2,}", " ", out)
    out = out.strip(" -_")

    # Sanitize each path segment
    parts = [p for p in out.split("/") if p.strip()]
    parts = [_sanitize_segment(p) for p in parts]
    return "/".join(parts) if parts else _sanitize_segment(meta.get("title") or "Unknown")


def _resolve_collision(target: Path) -> Path:
    """If target exists, append ' (1)', ' (2)' etc until we find a free name."""
    if not target.exists():
        return target
    stem = target.stem
    suffix = target.suffix
    parent = target.parent
    n = 1
    while True:
        candidate = parent / f"{stem} ({n}){suffix}"
        if not candidate.exists():
            return candidate
        n += 1


async def place_file(
    db: AsyncSession,
    book_type: str,
    source_path: str,
    extension: str,
    meta: dict,
    move: bool = True,
) -> str:
    """
    Place `source_path` into the configured library directory under a path
    derived from the first filename template + the supplied metadata.

    Args:
        book_type: 'ebook' or 'audiobook'
        source_path: absolute path to the source file (e.g. a temp file from a sync)
        extension: target extension including the dot (e.g. '.epub', '.m4b')
        meta: dict with keys: title, author, series, series_index
        move: if True, move; if False, copy.

    Returns the absolute destination path.
    """
    if book_type not in ("ebook", "audiobook"):
        raise ValueError(f"Unknown book_type: {book_type}")

    template = await _get_template(db, book_type)
    rendered = render_template(template, meta)
    base_dir = settings.ebook_dir if book_type == "ebook" else settings.audiobook_dir

    target = Path(base_dir) / f"{rendered}{extension}"
    target.parent.mkdir(parents=True, exist_ok=True)
    target = _resolve_collision(target)

    if move:
        shutil.move(source_path, target)
    else:
        shutil.copy2(source_path, target)

    logger.info(f"[library_writer] Placed {book_type} at {target}")
    return str(target)
