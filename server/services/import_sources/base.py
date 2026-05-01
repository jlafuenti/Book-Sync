"""
Base class and shared types for import-source adapters.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Awaitable, Callable, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession


@dataclass
class SyncError:
    """One per-item failure during a sync."""
    title: str
    error: str
    external_id: Optional[str] = None


@dataclass
class SyncResult:
    """Outcome of a single sync run."""
    items_added: int = 0
    items_skipped: int = 0
    added_titles: List[str] = field(default_factory=list)
    errors: List[SyncError] = field(default_factory=list)
    fatal_error: Optional[str] = None  # set if the whole run failed before per-item processing

    @property
    def succeeded(self) -> bool:
        # The run succeeded if it didn't crash entirely. Per-item errors are
        # reported separately so the user sees both adds and failures.
        return self.fatal_error is None


# Adapters call this to push live progress to the DB during a long-running
# sync. The scheduler injects an implementation that updates the
# ImportSource row; in tests the default no-op is fine.
ProgressFn = Callable[[int, int, str], Awaitable[None]]


async def _noop_progress(current: int, total: int, title: str) -> None:
    return None


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
    async def sync(self, db: AsyncSession, progress: ProgressFn = _noop_progress) -> SyncResult:
        """
        Pull any new items into the library. Implementations should:
        - dedupe against existing rows (by external_id / asin / file path),
        - call services.library_writer.place_file() for each new item,
        - call `await progress(current, total, title)` after each item so
          the UI can display live status.
        - return a SyncResult.
        After a sync completes, the caller will trigger a library scan.
        """
        ...
