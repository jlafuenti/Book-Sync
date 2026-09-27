"""Which Google Books API key to use (issue #739).

An admin can save one on the System page; it is kept in the encrypted
credential store under `CREDENTIAL`, like the Hardcover token, and never read
back. The `GOOGLE_BOOKS_API_KEY` environment variable (or its `_FILE` form)
still works and is the fallback: a saved key wins over it. The Match search
(`routers/match.py`) and the print page lookup (`services/print_pages.py`) both
ask here, so they always use the same key.
"""
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database import async_session
from services import credentials as credential_store

CREDENTIAL = "google_books"


async def saved_key(db: Optional[AsyncSession] = None) -> Optional[str]:
    """The key saved on the System page, or None."""
    if db is not None:
        return await credential_store.get_credential(db, CREDENTIAL)
    async with async_session() as own:
        return await credential_store.get_credential(own, CREDENTIAL)


async def api_key(db: Optional[AsyncSession] = None) -> Optional[str]:
    """The saved key, else the environment's, else None."""
    return await saved_key(db) or settings.google_books_api_key or None
