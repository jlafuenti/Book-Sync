"""
Base class and shared types for import-source adapters.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession


@dataclass
class SyncResult:
    """Outcome of a single sync run."""
    items_added: int = 0
    items_skipped: int = 0
    detail: str = ""  # human-readable summary, often the list of titles added
    error: Optional[str] = None  # set if the sync failed mid-run

    @property
    def succeeded(self) -> bool:
        return self.error is None


class SourceAdapter(ABC):
    """
    One adapter per source. Adapters are stateless singletons; per-source
    state (credentials, last_sync_at, etc.) lives in the database.
    """

    SOURCE_KEY: str = ""           # e.g. "audible"
    DISPLAY_NAME: str = ""         # e.g. "Audible"
    BOOK_TYPE: str = ""            # "ebook" or "audiobook"
    SUPPORTS_AUTO_SYNC: bool = False  # True if the source can be polled on a schedule

    @abstractmethod
    async def is_connected(self, db: AsyncSession) -> bool:
        """Whether the adapter has the credentials/config it needs to sync."""
        ...

    @abstractmethod
    async def sync(self, db: AsyncSession) -> SyncResult:
        """
        Pull any new items into the library. Implementations should:
        - dedupe against existing rows (by external_id / asin / file path),
        - call services.library_writer.place_file() for each new item,
        - return a SyncResult.
        After a sync completes, the caller will trigger a library scan.
        """
        ...
