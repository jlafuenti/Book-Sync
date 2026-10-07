"""
Asking for a fresh transcription of a pair (issues #794, #835).

A pair with a cached transcript reuses it, so queueing it again does nothing.
Recording an admin rejection (`transcript_timing.REJECTED_CHECK_TYPE`) is what
makes the pipeline, realign and the sync-map rebuild set the cached transcript
aside until a newer one replaces it. Shared by the per-pair re-transcribe
endpoint and the bulk word-timing queue.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.library_issue import LibraryCheckResult
from services.transcript_timing import REJECTED_CHECK_TYPE
from utils import utcnow


async def request_retranscription(
    db: AsyncSession,
    pair_id: int,
    *,
    detail: str = "Re-transcription requested",
) -> None:
    """Write (or refresh) the pair's rejection row. Flushes; never commits.

    The caller commits, then queues the pair: the worker can claim the job the
    moment it is queued and must already see the rejection, or it would reuse
    the old transcript (docs/request-transactions.md). The queue priority is
    the caller's business: pass it to `add_to_queue`, not here.
    """
    row = (await db.execute(
        select(LibraryCheckResult).where(
            LibraryCheckResult.item_type == "pair",
            LibraryCheckResult.item_id == pair_id,
            LibraryCheckResult.check_type == REJECTED_CHECK_TYPE,
        )
    )).scalar_one_or_none()
    if row is None:
        row = LibraryCheckResult(item_type="pair", item_id=pair_id,
                                 check_type=REJECTED_CHECK_TYPE)
        db.add(row)
    row.ok = False
    row.detail = detail
    row.checked_at = utcnow()
    await db.flush()
