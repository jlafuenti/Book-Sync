import re
from pathlib import Path

from fastapi import HTTPException


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
