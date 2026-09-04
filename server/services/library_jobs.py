"""One library-mutating job at a time (issue #202).

`POST /api/library/scan`, `/rescan-all`, `/rehash` and `/enrich-abs` all walk the
whole library, rewrite the same rows and — for the audiobook paths — rewrite the
same embedded tags on disk. Nothing stopped two of them running at once, and the
web proxy makes that likely rather than theoretical: nginx gives up on the
request after a minute while the handler keeps going, so the operator sees an
error on a scan that is still running and clicks again.

Two concurrent walks both read "this path is not in the database yet" for every
file neither has committed. Before issue #256 that produced two rows per book,
two auto-made pairs per book, and a reading position split across them; with the
unique index on `file_path` it produces an `IntegrityError` on every second
insert instead. Neither is a working outcome, so the second job is refused.

Refused, not queued: the second request would otherwise hang past the same proxy
timeout that provoked it, and the caller has nothing useful to do with a job that
has not started. `POST` returns 409 and the operator retries when the first one
is done.

A module-level flag is sufficient, and deliberately not an `asyncio.Lock`:

* the server runs a single uvicorn worker (`entrypoint.sh`), and
  `config.check_single_process()` refuses to boot with more (issue #252), so
  there is exactly one event loop holding this state;
* the check and the set below have no `await` between them, so on one loop they
  cannot interleave;
* a `Lock` would make the second caller *wait*, which is the behaviour being
  avoided.

If the server ever grows a second process, this has to become a row-level claim
in the database along with the rest of the single-process machinery — see
docs/operations.md, "Single process only".
"""

import contextlib
import logging
from typing import Optional

logger = logging.getLogger(__name__)

_state: dict = {"running": None}


class LibraryJobBusy(RuntimeError):
    """Raised when a library job is asked to start while another one holds the guard."""

    def __init__(self, running: str):
        super().__init__(f"library job '{running}' is already running")
        self.running = running


def running_job() -> Optional[str]:
    """The name of the job currently holding the guard, or None."""
    return _state["running"]


def reset() -> None:
    """Drop the guard unconditionally. For tests and startup recovery only."""
    _state["running"] = None


@contextlib.asynccontextmanager
async def exclusive(name: str):
    """Hold the library guard for `name`, or raise `LibraryJobBusy`.

    The guard is released in a `finally`, so a job that raises does not lock the
    library out until the next restart.
    """
    busy = _state["running"]
    if busy is not None:
        raise LibraryJobBusy(busy)
    _state["running"] = name
    logger.info("[library-job] %s started", name)
    try:
        yield
    finally:
        _state["running"] = None
        logger.info("[library-job] %s finished", name)
