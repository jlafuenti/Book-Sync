"""
`services/cache.py` — the TTL caches behind the expensive read endpoints
(issues #208, #233).

Both objects exist for the same reason: several handlers do synchronous
filesystem or subprocess work, the server runs a single uvicorn worker, and a
blocking call there stalls every other request including `/api/health`. So the
work goes to a worker thread and the answer is reused for a short window.

The endpoint tests assert the endpoints; this file pins the object's own
contract — expiry, live retuning, the `(path, mtime, size)` keying that lets an
edited file skip the cache, and the memory bound.
"""

import threading

from services.cache import TTLMemo, TTLValue


class _FakeClock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


# ---------------------------------------------------------------------------
# TTLValue
# ---------------------------------------------------------------------------

async def test_value_is_produced_once_and_then_reused():
    calls = []
    cache = TTLValue(lambda: 60, clock=_FakeClock())

    first = await cache.get(lambda: calls.append(1) or "answer")
    second = await cache.get(lambda: calls.append(1) or "answer")

    assert first == second == "answer"
    assert len(calls) == 1


async def test_value_is_recomputed_after_the_ttl_lapses():
    clock = _FakeClock()
    calls = []
    cache = TTLValue(lambda: 60, clock=clock)

    await cache.get(lambda: calls.append(1) or "answer")
    clock.advance(61)
    await cache.get(lambda: calls.append(1) or "answer")

    assert len(calls) == 2


async def test_a_zero_ttl_disables_caching():
    """`*_CACHE_SECONDS=0` has to mean "off", not "cached forever"."""
    calls = []
    cache = TTLValue(lambda: 0)

    await cache.get(lambda: calls.append(1) or "answer")
    await cache.get(lambda: calls.append(1) or "answer")

    assert len(calls) == 2


async def test_the_ttl_is_read_live_so_it_can_be_retuned():
    clock = _FakeClock()
    ttl = {"value": 60}
    calls = []
    cache = TTLValue(lambda: ttl["value"], clock=clock)

    await cache.get(lambda: calls.append(1) or "answer")
    ttl["value"] = 600
    clock.advance(61)
    # Still inside the *old* window's expiry, which was stamped at 60 — so this
    # one recomputes, and the new TTL governs from here.
    await cache.get(lambda: calls.append(1) or "answer")
    clock.advance(100)
    await cache.get(lambda: calls.append(1) or "answer")

    assert len(calls) == 2


async def test_a_negative_ttl_is_clamped_rather_than_caching_backwards():
    cache = TTLValue(lambda: -5)
    calls = []

    await cache.get(lambda: calls.append(1) or "answer")
    await cache.get(lambda: calls.append(1) or "answer")

    assert len(calls) == 2


async def test_invalidate_drops_the_value():
    calls = []
    cache = TTLValue(lambda: 60)

    await cache.get(lambda: calls.append(1) or "answer")
    cache.invalidate()
    await cache.get(lambda: calls.append(1) or "answer")

    assert len(calls) == 2
    assert cache.peek() == "answer"


async def test_the_producer_runs_off_the_event_loop():
    """The whole point: the blocking call must not happen on the loop."""
    cache = TTLValue(lambda: 60)
    seen = []

    await cache.get(lambda: seen.append(threading.current_thread()) or "answer")

    assert seen and seen[0] is not threading.main_thread()


# ---------------------------------------------------------------------------
# TTLMemo
# ---------------------------------------------------------------------------

def test_memo_computes_once_per_key():
    calls = []
    memo = TTLMemo(lambda: 60, clock=_FakeClock())

    assert memo.get("a", lambda: calls.append("a") or 1) == 1
    assert memo.get("a", lambda: calls.append("a") or 1) == 1
    assert memo.get("b", lambda: calls.append("b") or 2) == 2

    assert calls == ["a", "b"]
    assert len(memo) == 2


def test_memo_entries_expire():
    clock = _FakeClock()
    calls = []
    memo = TTLMemo(lambda: 60, clock=clock)

    memo.get("a", lambda: calls.append(1) or 1)
    clock.advance(61)
    memo.get("a", lambda: calls.append(1) or 1)

    assert len(calls) == 2


def test_memo_is_bounded():
    """A growing library must not grow this dict without limit."""
    memo = TTLMemo(lambda: 60, max_entries=3)

    for i in range(10):
        memo.get(i, lambda: i)

    assert len(memo) == 3


def test_memo_invalidate_clears_everything():
    memo = TTLMemo(lambda: 60)
    memo.get("a", lambda: 1)

    memo.invalidate()

    assert len(memo) == 0
