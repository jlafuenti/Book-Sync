import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import HTTPException


def utcnow() -> datetime:
    """The current moment as a **naive UTC** datetime — this codebase's one
    timestamp convention.

    Replaces `datetime.utcnow()`, which is deprecated on Python 3.12+ (issue
    #52) and returns a naive value that reads like local time.

    Deliberately *not* `datetime.now(timezone.utc)`: every timestamp column in
    the schema is a bare `DateTime` (there is no `timezone=True` anywhere), so
    SQLAlchemy reads them back naive. Writing aware values while reading naive
    ones raises `TypeError: can't compare offset-naive and offset-aware
    datetimes` in the two places that compare timestamps —
    `services.position_service.is_stale` and
    `services.import_scheduler._due_sources`. `schemas._naive_utc` normalizes
    inbound API values to the same convention; this is the write-side half of
    that contract, and `tests/test_time_contract.py` pins both.

    `services/offhours.py` is the one module that works in aware datetimes: it
    reasons about wall-clock time in a user-configured zone and never writes a
    column.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


def safe_join(base_dir, filename: str) -> Path:
    """Reduce filename to a safe basename and join onto base_dir, guaranteeing
    the resolved path stays inside base_dir. Raises HTTPException(400) on
    empty/hidden/traversal-unsafe names or containment violations."""
    base = Path(base_dir).resolve()
    name = re.split(r"[\\/]+", filename or "")[-1]
    if not name or name in (".", "..") or name.startswith("."):
        raise HTTPException(status_code=400, detail="Invalid filename")
    candidate = (base / name).resolve()
    try:
        candidate.relative_to(base)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid filename")
    return candidate


def resolve_cover_url(cover_path: Optional[str], base_dir) -> Optional[Path]:
    """Resolve a stored cover_path URL (e.g. '/api/files/covers/x.jpg?...') to
    its real file under base_dir. Returns None if cover_path is empty or the
    resolved name is unsafe."""
    if not cover_path:
        return None
    name = os.path.basename(cover_path.split("?")[0])
    try:
        return safe_join(base_dir, name)
    except HTTPException:
        return None
