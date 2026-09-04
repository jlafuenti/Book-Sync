"""Tiny TTL caches for the expensive read endpoints (issues #208, #233).

Several handlers do synchronous filesystem or subprocess work and are reachable
by any authenticated user — some, now, by nobody at all. Each one needs the same
two things, so they live together in one small object:

* the value is produced in a worker thread (``asyncio.to_thread``), so a slow
  NAS mount or a wedged subprocess cannot stall the single uvicorn event loop
  and take login and ``/api/health`` down with it;
* the value is reused for a short TTL, so a page that polls and a hostile loop
  cost the same.

Deliberately not single-flighted: a burst arriving while the value is expired
can start more than one computation. An ``asyncio.Lock`` would fix that but
binds itself to the first event loop it is awaited on, which breaks a test suite
that builds a fresh loop per test — and the per-user buckets in ``rate_limit``
already bound how large such a burst can be.
"""

import asyncio
import time
from threading import Lock
from typing import Callable, Optional, TypeVar

T = TypeVar("T")


class TTLValue:
    """One cached value with an expiry, recomputed off the event loop.

    ``ttl_supplier`` is read on every store rather than captured, so a setting
    retuned at runtime takes effect without a restart. ``clock`` defaults to
    :func:`time.monotonic` (immune to wall-clock jumps) and is injectable for
    tests.
    """

    def __init__(
        self,
        ttl_supplier: Callable[[], float],
        clock: Optional[Callable[[], float]] = None,
    ):
        self._ttl_supplier = ttl_supplier
        self._clock: Callable[[], float] = clock or time.monotonic
        self._lock = Lock()
        self._value: Optional[T] = None
        self._has_value = False
        self._expires_at = 0.0

    @property
    def ttl(self) -> float:
        """Current TTL in seconds; never negative."""
        return max(0.0, float(self._ttl_supplier()))

    def peek(self) -> Optional[T]:
        """The stored value while it is fresh, else ``None``."""
        with self._lock:
            if self._has_value and self._clock() < self._expires_at:
                return self._value
            return None

    def put(self, value: T) -> None:
        with self._lock:
            self._value = value
            self._has_value = True
            self._expires_at = self._clock() + self.ttl

    def invalidate(self) -> None:
        """Drop the stored value. Used by tests and after a config change."""
        with self._lock:
            self._value = None
            self._has_value = False
            self._expires_at = 0.0

    async def get(self, producer: Callable[[], T]) -> T:
        """Return the cached value, or run ``producer`` in a worker thread.

        ``producer`` must be a plain synchronous callable — the whole point is
        that its blocking work happens off the loop.
        """
        cached = self.peek()
        if cached is not None:
            return cached
        value = await asyncio.to_thread(producer)
        self.put(value)
        return value
